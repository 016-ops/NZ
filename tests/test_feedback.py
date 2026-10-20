"""真实MySQL反馈/私有图片回归；测试素材内存生成，不连接邮件、游戏或远端。"""
from io import BytesIO
from contextlib import contextmanager
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.test import Client, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Q

from revival import media
from revival.service import Fault, identifier
from .support import DatabasePool
from .test_control_plane import Base


def picture(color='red'):
    """仅在内存创建微小PNG测试图；不依赖用户截图或原始游戏素材。"""
    from PIL import Image
    stream = BytesIO()
    Image.new('RGB', (24, 16), color).save(stream, format='PNG')
    return stream.getvalue()


class FeedbackTests(Base):
    """验证归属、CAS、四态、角色、图片总限及文件/事务回滚一致性。"""
    def setUp(self):
        """为每例提供独立私有媒体目录，测试文件不会进入发布资源。"""
        super().setUp()
        self.images = tempfile.TemporaryDirectory()
        self.addCleanup(self.images.cleanup)
        self.media_settings = override_settings(NZ_MEDIA_ROOT=self.images.name)
        self.media_settings.enable()
        self.addCleanup(self.media_settings.disable)
        self.aid, self.token = self.player()

    def create(self):
        """从玩家真实命令创建工单和第一条消息。"""
        return self.command('feedback.create', {'title': '画面显示建议', 'body': '这是纯文本\n第二行'}, self.token)

    def detail(self, row, token=None):
        """读取完整工单，不通过数据库绕过访问权限。"""
        return self.s.query(token or self.token, 'feedback-detail', ticket_id=row['ticket_id'])

    def test_create_retry_and_other_player_isolation(self):
        """相同请求重试只创建一单；其他玩家既看不到列表也不能按ID读取对话。"""
        data = self.meta(title='反馈主题', body='反馈内容')
        first = self.s.command(self.token, 'feedback.create', data)
        self.assertEqual(first, self.s.command(self.token, 'feedback.create', data))
        listing = self.s.query(self.token, 'feedback')
        self.assertEqual((listing['total'], listing['tickets'][0]['message_count']), (1, 1))
        _, other = self.player('feedback-other')
        self.assertEqual(self.s.query(other, 'feedback')['total'], 0)
        with self.assertRaises(Fault) as error:
            self.detail(first, other)
        self.assertEqual(error.exception.code, 'NOT_FOUND')

    def test_state_cas_reopen_and_closed_read_only(self):
        """已解决回复自动重开，陈旧修订不追加消息，关闭之后不能回复或改变状态。"""
        row = self.create()
        resolved = self.command('feedback.status', {'ticket_id': row['ticket_id'], 'expected_revision': 1, 'state': 'RESOLVED'})
        with self.assertRaises(Fault) as error:
            self.command('feedback.reply', {'ticket_id': row['ticket_id'], 'expected_revision': 1, 'body': '旧修订'}, self.token)
        self.assertEqual(error.exception.code, 'VERSION_CONFLICT')
        reply = self.command('feedback.reply', {'ticket_id': row['ticket_id'], 'expected_revision': resolved['revision'], 'body': '仍有问题'}, self.token)
        self.assertEqual(reply['state'], 'OPEN')
        self.assertEqual([message['body'] for message in self.detail(row)['messages']], ['这是纯文本\n第二行', '仍有问题'])
        closed = self.command('feedback.status', {'ticket_id': row['ticket_id'], 'expected_revision': reply['revision'], 'state': 'CLOSED'})
        for action, values in (('feedback.reply', {'body': '不能追加'}), ('feedback.status', {'state': 'OPEN'})):
            with self.assertRaises(Fault) as error:
                self.command(action, dict(ticket_id=row['ticket_id'], expected_revision=closed['revision'], **values))
            self.assertEqual(error.exception.code, 'FEEDBACK_CLOSED')
        self.assertEqual(len(self.detail(row)['messages']), 2)
        with self.assertRaises(Fault):
            media.upload_feedback(self.s, self.token, row['ticket_id'], picture(), identifier())

    def test_users_manage_auditor_read_resources_denied(self):
        """反馈处理权与资产运营分离；审计角色可读，资源角色不能枚举或查看私图。"""
        row = self.create()
        users, auditor, resources = (self.extra_admin(role) for role in ('users', 'auditor', 'resources'))
        self.assertEqual(self.s.query(users, 'feedback-admin')['total'], 1)
        self.assertEqual(self.s.query(auditor, 'feedback-admin')['total'], 1)
        self.assertEqual(len(self.detail(row, auditor)['messages']), 1)
        with self.assertRaises(Fault):
            self.s.query(resources, 'feedback-admin')
        with self.assertRaises(Fault):
            self.detail(row, resources)
        with self.assertRaises(Fault):
            self.command('feedback.reply', {'ticket_id': row['ticket_id'], 'expected_revision': 1, 'body': '只读不可回复'}, auditor)
        reply = self.command('feedback.reply', {'ticket_id': row['ticket_id'], 'expected_revision': 1, 'body': '已经收到建议'}, users)
        self.assertEqual(reply['revision'], 2)

    def test_attachment_idempotency_total_limit_and_private_public_split(self):
        """同图同键不占第二个名额；总量3张，其他玩家及公共媒体GET不能读取私图。"""
        row, key, raw = self.create(), identifier(), picture()
        image = media.upload_feedback(self.s, self.token, row['ticket_id'], raw, key)
        again = media.upload_feedback(self.s, self.token, row['ticket_id'], raw, key)
        self.assertEqual(image['id'], again['id'])
        with self.assertRaises(Fault) as error:
            media.upload_feedback(self.s, self.token, row['ticket_id'], picture('blue'), key)
        self.assertEqual(error.exception.code, 'IDEMPOTENCY_CONFLICT')
        for _ in range(2):
            media.upload_feedback(self.s, self.token, row['ticket_id'], raw, identifier())
        with self.assertRaises(Fault) as error:
            media.upload_feedback(self.s, self.token, row['ticket_id'], raw, identifier())
        self.assertEqual(error.exception.code, 'ATTACHMENT_LIMIT')
        _, other = self.player('image-other')
        with self.assertRaises(Fault):
            media.read_feedback_attachment(self.s, other, image['id'])
        with self.assertRaises(Fault):
            media.read_public_cover(self.s, image['id'])
        payload, mime = media.read_feedback_attachment(self.s, self.token, image['id'])
        self.assertEqual(mime, 'image/webp')
        self.assertGreater(len(payload), 10)
        self.assertEqual(self.detail(row)['attachment_count'], 3)

    def test_concurrent_uploads_cannot_exceed_three(self):
        """四个独立连接同时上传只能提交三张，文件数量必须与持久记录一致。"""
        row, raw = self.create(), picture()
        def upload(_):
            """线程捕获预期业务拒绝，非业务异常继续使测试失败。"""
            try:
                return media.upload_feedback(self.s, self.token, row['ticket_id'], raw, identifier())['id']
            except Fault as error:
                return error.code
        with DatabasePool(max_workers=4) as pool:
            values = list(pool.map(upload, range(4)))
        self.assertEqual(values.count('ATTACHMENT_LIMIT'), 1)
        self.assertEqual(self.detail(row)['attachment_count'], 3)
        self.assertEqual(len(list(Path(self.images.name).rglob('*.webp'))), 3)

    def test_storage_or_audit_failure_preserves_body_and_removes_partial_upload(self):
        """附件失败不回滚前一个已提交正文；审计/事务失败不留下附件记录或孤儿文件。"""
        row = self.create()
        with patch.object(self.s, '_audit', side_effect=RuntimeError('test audit unavailable')):
            with self.assertRaises(RuntimeError):
                media.upload_feedback(self.s, self.token, row['ticket_id'], picture(), identifier())
        detail = self.detail(row)
        self.assertEqual((detail['message_count'], detail['attachment_count'], detail['revision']), (1, 0, 1))
        self.assertEqual(list(Path(self.images.name).rglob('*.webp')), [])

    def test_cover_upload_is_public_and_restricted_to_campaign_roles(self):
        """公共封面只供活动运营角色创建，同键换图拒绝，私图读取接口不能混用封面ID。"""
        raw, key = picture(), identifier()
        cover = media.upload_public_cover(self.s, self.owner, raw, key)
        self.assertEqual(cover, media.upload_public_cover(self.s, self.owner, raw, key))
        with self.assertRaises(Fault):
            media.upload_public_cover(self.s, self.token, raw, identifier())
        with self.assertRaises(Fault):
            media.upload_public_cover(self.s, self.extra_admin('users'), raw, identifier())
        with self.assertRaises(Fault):
            media.read_feedback_attachment(self.s, self.token, cover['id'])
        payload, mime = media.read_public_cover(self.s, cover['id'])
        self.assertEqual(mime, 'image/webp')
        self.assertGreater(len(payload), 10)

    def test_commit_acknowledgement_loss_keeps_committed_file_for_retry(self):
        """数据库已提交但应答异常时，不可将已引用文件误删；同键重试恢复原结果。"""
        row, raw, key = self.create(), picture(), identifier()
        original, completed = self.s.db.transaction, []
        @contextmanager
        def uncertain_transaction():
            """第二个事务是真正上传；在它提交以后模拟客户端未收到提交应答。"""
            with original() as c:
                yield c
            completed.append(True)
            if len(completed) == 2:
                raise RuntimeError('test commit acknowledgement lost')
        with patch.object(self.s.db, 'transaction', uncertain_transaction):
            with self.assertRaises(RuntimeError):
                media.upload_feedback(self.s, self.token, row['ticket_id'], raw, key)
        retried = media.upload_feedback(self.s, self.token, row['ticket_id'], raw, key)
        payload, _ = media.read_feedback_attachment(self.s, self.token, retried['id'])
        self.assertGreater(len(payload), 10)
        self.assertEqual(self.detail(row)['attachment_count'], 1)

    def test_role_downgrade_is_checked_before_replaying_reply_receipt(self):
        """users降为resources后，即使携旧成功幂等键也必须403，不能靠缓存绕过新权限。"""
        row, users = self.create(), self.extra_admin('users')
        data = self.meta(ticket_id=row['ticket_id'], expected_revision=1, body='已收到')
        self.s.command(users, 'feedback.reply', data)
        with self.s.db.transaction() as c:
            actor = self.s._actor(c, users)
            c.update('admins', Q(id=actor['id']), {'role': 'resources'})
        with self.assertRaises(Fault) as error:
            self.s.command(users, 'feedback.reply', data)
        self.assertEqual(error.exception.code, 'FORBIDDEN')

    def http(self):
        """创建真实中间件/路由客户端；仅覆盖合成profile及回环来源，不绕过Bearer校验。"""
        configuration = override_settings(NZ_PROFILE='synthetic', NZ_PORT=8875,
            NZ_PUBLIC_HOST='', NZ_PUBLIC_ORIGIN='', NZ_TRUST_LOOPBACK_PROXY=False)
        configuration.enable()
        self.addCleanup(configuration.disable)
        return Client(HTTP_HOST='127.0.0.1:8875', HTTP_X_NZ_REQUEST='1')

    def upload(self, client, path, token=None, *, key=None, raw=None, **extra):
        """构造真实multipart文件，不伪造已解码对象；每次新建文件流保证重试正文相同。"""
        data = {'upload_key': key or identifier(),
                'file': SimpleUploadedFile('screen.png', raw if raw is not None else picture(), content_type='image/png')}
        data.update(extra)
        headers = {'HTTP_AUTHORIZATION': 'Bearer ' + token} if token else {}
        return client.post(path, data, **headers)

    def test_http_multipart_retry_private_get_and_player_view_envelope(self):
        """真实HTTP上传可重试；私图仅归属会话可读，并保持no-store/nosniff与桥接对象信封。"""
        row, key, client = self.create(), identifier(), self.http()
        path = '/api/feedback/' + row['ticket_id'] + '/attachments'
        first = self.upload(client, path, self.token, key=key)
        self.assertEqual(first.status_code, 200, first.content)
        image = first.json()
        again = self.upload(client, path, self.token, key=key)
        self.assertEqual(again.status_code, 200, again.content)
        self.assertEqual(again.json()['id'], image['id'])
        self.assertEqual(image['ticket_revision'], 2)
        self.assertNotIn('token', image['url'])
        private = client.get(image['url'], HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(private.status_code, 200, private.content)
        self.assertEqual(private['Content-Type'], 'image/webp')
        self.assertEqual(private['Cache-Control'], 'private, no-store')
        self.assertEqual(private['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(client.get(image['url']).status_code, 401)
        _, other = self.player('http-other')
        self.assertEqual(client.get(image['url'], HTTP_AUTHORIZATION='Bearer ' + other).status_code, 404)
        self.assertEqual(client.get('/api/media/' + image['id']).status_code, 404)
        viewed = client.post('/api/player/view', {'view': 'feedback-detail', 'query': {'ticket_id': row['ticket_id']}},
            content_type='application/json', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(viewed.status_code, 200, viewed.content)
        self.assertEqual(viewed.json()['data']['attachment_count'], 1)
        paged = client.post('/api/player/view', {'view': 'feedback', 'query': {'offset': '0', 'limit': '1'}},
            content_type='application/json', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(paged.status_code, 200, paged.content)
        self.assertEqual((paged.json()['data']['limit'], len(paged.json()['data']['tickets'])), (1, 1))
        invalid_page = client.post('/api/player/view', {'view': 'feedback', 'query': {'limit': True}},
            content_type='application/json', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(invalid_page.status_code, 400, invalid_page.content)

    def test_http_upload_auth_precedes_decode_and_strict_multipart(self):
        """匿名先拒绝且不解码；伪图片、额外字段、重复上传键和多文件都不能留下附件。"""
        row, client = self.create(), self.http()
        path = '/api/feedback/' + row['ticket_id'] + '/attachments'
        with patch('revival.media.normalize_image') as decode:
            rejected = self.upload(client, path, raw=b'not an image')
        self.assertEqual(rejected.status_code, 401)
        decode.assert_not_called()
        for data in (
            {'raw': b'not an image'},
            {'unknown': 'extra field'},
            {'upload_key': ['first', 'second']},
            {'file': [SimpleUploadedFile('a.png', picture()), SimpleUploadedFile('b.png', picture())]},
        ):
            result = self.upload(client, path, self.token, **data)
            self.assertEqual(result.status_code, 400, result.content)
        # 大量非文件表单内容仍受Django的64KiB门禁；不能落入500内部错误。
        bulky = self.upload(client, path, self.token, upload_key='x' * (128 * 1024))
        self.assertIn(bulky.status_code, (400, 413), bulky.content)
        malformed = client.post(path, b'broken multipart body', content_type='multipart/form-data; boundary=""',
            HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(malformed.status_code, 400, malformed.content)
        self.assertEqual(self.detail(row)['attachment_count'], 0)
        self.assertEqual(list(Path(self.images.name).rglob('*.webp')), [])

    def test_http_file_limit_keeps_normal_json_limit_and_cover_namespace(self):
        """仅上传入口允许较大正文，单图5MiB门禁生效；公共封面不能经私图接口或玩家创建。"""
        row, client = self.create(), self.http()
        path = '/api/feedback/' + row['ticket_id'] + '/attachments'
        oversized = self.upload(client, path, self.token, raw=b'x' * (5 * 1024 * 1024 + 1))
        self.assertEqual(oversized.status_code, 413, oversized.content)
        normal = client.post('/api/command', {'action': 'feedback.create', 'body': 'x' * 65536},
            content_type='application/json', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(normal.status_code, 413, normal.content)
        self.assertEqual(self.upload(client, '/api/admin/media', self.token).status_code, 403)
        cover = self.upload(client, '/api/admin/media', self.owner)
        self.assertEqual(cover.status_code, 200, cover.content)
        record = cover.json()
        public = client.get(record['url'])
        self.assertEqual(public.status_code, 200, public.content)
        self.assertEqual(public['Content-Type'], 'image/webp')
        self.assertEqual(client.get('/api/feedback/attachments/' + record['id'], HTTP_AUTHORIZATION='Bearer ' + self.token).status_code, 404)
        self.assertEqual(self.detail(row)['attachment_count'], 0)

    def test_http_public_activity_detail_hides_draft_and_keeps_rewards_frozen(self):
        """公开slug接口真实路由可访问已发布详情，草稿不泄露，展示编辑不产生奖励领取记录。"""
        saved = self.command('campaign.save', {'title': '页面测试', 'definition': {
            'starts_at': self.now - 60, 'ends_at': self.now + 86400,
            'timezone': 'Asia/Shanghai', 'rewards': [{
                'key': 'day-one', 'title': '签到奖励', 'type': 'CHECKIN_DAYS', 'threshold': 1,
                'changes': [{'type': 'item', 'item_key': 'test-token', 'quantity': 1}],
            }],
        }})
        self.command('campaign.presentation.save', {'campaign_id': saved['id'], 'expected_revision': 1,
            'presentation': {'slug': 'http-opening', 'title': '开服活动', 'summary': '公开活动简介'}})
        client = self.http()
        hidden = client.get('/api/public/campaign-detail', {'slug': 'http-opening'})
        self.assertEqual(hidden.status_code, 404, hidden.content)
        self.command('campaign.publish', {'campaign_id': saved['id']})
        detail = client.get('/api/public/campaign-detail', {'slug': 'http-opening'})
        self.assertEqual(detail.status_code, 200, detail.content)
        record = detail.json()
        self.assertEqual(record['presentation']['slug'], 'http-opening')
        self.assertNotIn('progress', record)
        with self.s.db.transaction() as c:
            self.assertEqual(c.table('campaign_claims').count(), 0)
