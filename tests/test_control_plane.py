"""真实 MySQL 事务回归，覆盖账号、权限、资产、房间、结算及备份不变量。"""
import json
from django.db import DatabaseError, connections
from django.db.models import Q
from django.test import TransactionTestCase, override_settings
from revival.models import DeploymentLock, SchemaVersion, DeploymentMetadata
from unittest.mock import patch
import tempfile
import unittest
from contextlib import closing
from .support import DatabasePool as ThreadPoolExecutor
from pathlib import Path

from revival.assets import deliver_outbox, preview, settle
from revival.rooms import heartbeat, maintain, test_host_ready
from revival.security import token_hash
from revival.service import Fault, Service, identifier


class Base(TransactionTestCase):
    """创建真实 MySQL 合成业务测试基线；每例隔离账号、时钟、会话与资产状态。"""
    def _fixture_teardown(self):
        """仅清理 Django 创建的测试库；TRUNCATE 不触发禁止删除历史记录的触发器。"""
        from revival.models import TABLE_MODELS
        connection = connections['default']
        if connection.settings_dict['NAME'] != connection.settings_dict['TEST']['NAME']:
            raise RuntimeError('Refusing to clean a non-test database')
        with connection.cursor() as cursor:
            cursor.execute('SET FOREIGN_KEY_CHECKS=0')
            try:
                for name in [*TABLE_MODELS, 'deployment_lock']:
                    cursor.execute(f'TRUNCATE TABLE `{name}`')
            finally:
                cursor.execute('SET FOREIGN_KEY_CHECKS=1')

    def setUp(self):
        """建立本例独立业务环境和依赖；所有业务数据使用真实 MySQL 测试库。"""
        super().setUp()
        # 旧业务夹具保留迁移前注册路径；验证码专项另行覆盖开启，生产默认始终开启。
        # 旧资产回归明确使用空仓夹具；新账号初装专项单独启用默认生产策略。
        self.email_fixture_settings = override_settings(NZ_EMAIL_VERIFICATION_REQUIRED=False,NZ_STARTER_EQUIPMENT_ENABLED=False)
        self.email_fixture_settings.enable()
        self.addCleanup(self.email_fixture_settings.disable)
        # 只替换投递端；所有管理员测试仍经过生产随机挑战、密码和一次性事务，不提供免码后门。
        configured = patch('revival.mailing.configured', return_value=True)
        configured.start(); self.addCleanup(configured.stop)
        sender = patch('revival.mailing.send_code')
        sender.start(); self.addCleanup(sender.stop)
        DeploymentLock.objects.get_or_create(pk=1)
        SchemaVersion.objects.get_or_create(version=1)
        self.directory = tempfile.TemporaryDirectory()
        self.path = 'default'
        self.now = 1800000000
        self.s = Service(self.path, test_profile=True, clock=lambda: self.now, test_pve=getattr(self, 'pve_profile', False))
        self.password = 'local-test-password-123'
        self.admin = self.s.bootstrap_admin('owner', self.password)
        self.owner = self.admin_login('owner')['token']

    def tearDown(self):
        """释放本例服务、连接和临时资源；清理由测试生命周期完成，不影响当前业务库。"""
        self.directory.cleanup()
        super().tearDown()

    def meta(self, **data):
        """生成独立请求标识、幂等键和测试原因，再合并该场景的业务参数。"""
        return {'request_id': identifier(), 'idempotency_key': identifier(), 'reason': 'automated synthetic test', **data}

    def command(self, action, data=None, token=None):
        """生成请求元数据并经真实业务服务提交指定命令，返回结果或抛出业务异常。"""
        payload = self.meta(**(data or {}))
        if 'current_password' in payload and 'challenge_id' not in payload:
            payload = self.authorize(action, payload, token)
        return self.s.command(token or self.owner, action, payload)

    def admin_login(self, login='owner', password=None):
        """通过真实取码/确认合同登录；仅从SMTP替身取随机码，未向真实邮箱发送。"""
        from revival.admin_email_auth import request_login_code
        from revival import mailing
        self.now += 61
        password = password or self.password
        challenge = request_login_code(self.s, {'login':login, 'password':password}, 'test-admin-source')
        code = mailing.send_code.call_args.args[1]
        return self.s.login(login, password, kind='admin', challenge_id=challenge['challenge_id'], code=code)

    def authorize(self, action, data, token=None):
        """以最终业务内容申请操作绑定邮件码；夹具不能把一个确认复用到不同动作或参数。"""
        from revival.admin_email_auth import request_step_up_code, AUTH_FIELDS
        from revival import mailing
        # 非限流专项沿用既有游戏时钟；仅把历史挑战的发送时间设为一小时前，避免
        # 领取新操作邮件码时人为推进61秒而使并行房间租约到期。码的过期/消费仍真实校验。
        # 限流专项直接调用request_*，不经此夹具，因此不会绕过实际的发送额度断言。
        with self.s.db.transaction() as c:
            c.table('admin_auth_challenges').filter(purpose='STEP_UP').update(created_at=self.now-3601)
        metadata = {name:data[name] for name in ('request_id','idempotency_key','reason')}
        payload = {key:value for key,value in data.items() if key not in AUTH_FIELDS | set(metadata)}
        challenge = request_step_up_code(self.s, token or self.owner,
            {'action':action,'payload':payload,'metadata':metadata,'current_password':data['current_password']}, 'test-admin-source')
        return dict(data, challenge_id=challenge['challenge_id'], code=mailing.send_code.call_args.args[1])

    def invitation(self, uses=1):
        """用 Owner 创建指定次数和短期限邀请，返回一次性展示的测试令牌。"""
        return self.command('invite.create', {'max_uses': uses, 'expires_at': self.now + 600})

    def player(self, login='player-a', character=True):
        """注册并审批合成玩家，返回账号 ID 与会话；按参数决定是否初始化本地角色。"""
        invite = self.invitation()
        # 该助手只构造历史合成身份；公开邮箱专项调用真实register而不是此夹具。
        with override_settings(NZ_EMAIL_VERIFICATION_REQUIRED=False):
            result = self.s.register(self.meta(login=login, password=self.password, invitation=invite['invitation_token']))
        self.command('user.approve', {'target_account_id': result['account_id']})
        token = self.s.login(login, self.password)['token']
        if character:
            # 此助手构造历史合成角色；正式/onboarding邮箱门禁由注册专项真实验证。
            with override_settings(NZ_EMAIL_VERIFICATION_REQUIRED=False):
                self.command('character.create', {'nickname': login}, token)
        return result['account_id'], token

    def grant(self, account_id, changes, key=None):
        """以 Owner 发放指定资产，可指定幂等键用于重放与并发验证。"""
        data = self.meta(target_account_id=account_id, catalog_version=self.s.catalog['id'], changes=changes)
        if key: data['idempotency_key'] = key
        return self.s.command(self.owner, 'asset.grant', data)

    def balance(self, account_id, code='GP'):
        """从玩家读模型读取指定币种余额，不绕过业务查询接口直接修改数据。"""
        return next(w['balance'] for w in self.s.query(self.owner, 'user', account_id=account_id)['wallets'] if w['currency'] == code)

    def step_up(self):
        """标记需要再认证；command夹具在得到最终业务参数后申请真正的操作绑定码。"""
        return {'current_password': self.password}

    def legacy_purchase_fixture(self, token, **data):
        """仅从领域层构造旧购物账本夹具；公开平台入口仍必须拒绝此动作。

        补偿和并发资产测试需要保留历史购买后已消费余额的状态，不能为夹具重新开放API。
        原生游戏购物车另有独立真实命令专项验证。
        """
        from revival.assets import asset_command
        with self.s.db.transaction() as c:
            actor = self.s._actor(c, token, 'player')
            return asset_command(self.s, c, actor, 'shop.purchase', self.meta(**data))

    def extra_admin(self, role):
        """为权限测试创建指定角色管理员并登录，返回该角色会话。"""
        admin_id = identifier()
        with self.s.db.transaction() as c:
            original = c.select('admins', limit=1).fetchone()
            c.insert('admins', {'id':admin_id, 'login':role, 'password_hash':original['password_hash'],
                               'otp_secret':original['otp_secret'], 'role':role})
        return self.admin_login(role)['token']

    def match(self):
        """建立两名已准备玩家的合成对局，返回参与者、房间 ID 和冻结开始结果。"""
        a, ta = self.player('player-a')
        b, tb = self.player('player-b')
        room = self.command('room.create', {'map_key': 'test-arena'}, ta)['room_id']
        self.command('room.join', {'room_id': room}, tb)
        self.command('room.ready', {'room_id': room, 'ready': True}, ta)
        self.command('room.ready', {'room_id': room, 'ready': True}, tb)
        revision = self.s.query(ta, 'rooms')[0]['revision']
        match = self.command('room.start', {'room_id': room, 'expected_revision': revision}, ta)
        return (a, ta), (b, tb), room, match


class Accounts(Base):
    """验证账号、邀请、MFA、制裁、会话及权限的事务不变量。"""
    def test_concurrent_invitation_use_is_atomic(self):
        """验证场景：并发消费最后一个邀请名额，只允许一个账号成功。"""
        invite = self.invitation()
        def register(i):
            """工作线程尝试消费同一邀请，返回注册结果或错误码，供并发断言核对。"""
            try:
                return self.s.register(self.meta(login=f'user-{i}', password=self.password, invitation=invite['invitation_token']))
            except Fault as e:
                return e.code
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(register, range(10)))
        self.assertEqual(sum(isinstance(x, dict) for x in results), 1)
        self.assertEqual(self.s.query(self.owner, 'invitations')[0]['uses'], 1)

    def test_registration_replay_does_not_consume_invite_again(self):
        """验证场景：重复注册幂等请求返回原账号，不再次消耗邀请。"""
        invite = self.invitation()
        data = self.meta(login='user-one', password=self.password, invitation=invite['invitation_token'])
        first = self.s.register(data)
        for _ in range(10): self.assertEqual(first, self.s.register(data))
        self.assertEqual(self.s.query(self.owner, 'invitations')[0]['uses'], 1)

    def test_expired_and_revoked_invitations_are_rejected(self):
        """验证场景：过期及已撤销邀请均拒绝注册。"""
        invite = self.invitation()
        self.command('invite.revoke', {'invitation_id': invite['invitation_id']})
        with self.assertRaises(Fault):
            self.s.register(self.meta(login='user-one', password=self.password, invitation=invite['invitation_token']))
        invite = self.invitation(); self.now += 601
        with self.assertRaises(Fault):
            self.s.register(self.meta(login='user-one', password=self.password, invitation=invite['invitation_token']))

    def test_pending_and_wrong_password_do_not_authenticate(self):
        """验证场景：待审批账号和错误密码均不能产生有效会话。"""
        invite = self.invitation()
        self.s.register(self.meta(login='user-one', password=self.password, invitation=invite['invitation_token']))
        with self.assertRaises(Fault): self.s.login('user-one', self.password)
        account, token = self.player()
        with self.assertRaises(Fault): self.s.login('player-a', 'incorrect-password')
        with self.assertRaises(Fault): self.s.login('nonexistent', self.password)

    def test_admin_mfa_required_and_cannot_replay(self):
        """管理员必须提交独立邮件挑战，旧TOTP参数不能恢复登录。"""
        with self.assertRaises(Fault): self.s.login('owner', self.password, kind='admin')
        with self.assertRaises(Fault): self.s.login('owner', self.password, kind='admin', otp_code='123456')
        with self.assertRaises(Fault): self.s.bootstrap_admin('another', self.password)

    def test_character_and_assets_are_independent_and_initialize_once(self):
        """验证场景：账号与角色分开初始化，重复初始化不能重置资产。"""
        a, ta = self.player(); b, tb = self.player('player-b')
        original = self.s.query(ta, 'me')
        for _ in range(10): self.command('character.create', {'nickname': 'player-a'}, ta)
        self.assertEqual(original['character'], self.s.query(ta, 'me')['character'])
        self.assertNotEqual(original['game_id'], self.s.query(tb, 'me')['game_id'])
        self.grant(a, [{'type': 'currency', 'code': 'GP', 'delta': 10}])
        self.assertEqual(self.balance(b), 0)
        with self.assertRaises(Fault): self.s.query(ta, 'user', account_id=b)

    def test_local_nickname_validation_uniqueness_and_chinese(self):
        """验证场景：中文昵称合法，非法字符及重复昵称被拒绝。"""
        a, ta = self.player(character=False); b, tb = self.player('player-b', character=False)
        self.command('character.create', {'nickname': '测试玩家'}, ta)
        with self.assertRaises(Fault): self.command('character.create', {'nickname': '测试玩家'}, tb)
        with self.assertRaises(Fault): self.command('character.create', {'nickname': '<script>alert(1)</script>'}, tb)
        with self.assertRaises(Fault): self.command('character.create', {'nickname': '长' * 25}, tb)

    def test_reset_revokes_old_credentials_and_session(self):
        """验证场景：重置密码撤销旧凭据和旧会话。"""
        a, ta = self.player()
        self.command('user.reset', {'target_account_id': a, 'new_password': 'new-local-password-123'})
        with self.assertRaises(Fault): self.s.query(ta, 'me')
        with self.assertRaises(Fault): self.s.login('player-a', self.password)
        self.assertTrue(self.s.login('player-a', 'new-local-password-123')['token'])

    def test_disable_approve_does_not_bypass_disabled(self):
        """验证场景：审批操作不能绕过已停用状态。"""
        a, ta = self.player()
        self.command('user.disable', {'target_account_id': a})
        with self.assertRaises(Fault): self.command('user.approve', {'target_account_id': a})
        with self.assertRaises(Fault): self.s.query(ta, 'me')
        self.command('user.activate', {'target_account_id': a})
        self.assertTrue(self.s.login('player-a', self.password)['token'])

    def test_stacked_sanctions_and_expiry(self):
        """验证场景：叠加制裁独立生效，全部到期后才恢复资格。"""
        a, ta = self.player()
        one = self.command('sanction.create', {'target_account_id': a, 'expires_at': self.now + 60})
        two = self.command('sanction.create', {'target_account_id': a, 'expires_at': None})
        self.command('sanction.revoke', {'sanction_id': two['sanction_id']})
        with self.assertRaises(Fault): self.s.login('player-a', self.password)
        self.now += 61
        self.assertTrue(self.s.login('player-a', self.password)['token'])
        with self.assertRaises(Fault): self.s.query(ta, 'me')

    def test_restart_preserves_mapping_character_ban_and_assets(self):
        """验证场景：重建业务服务后账号映射、角色、制裁及资产保持一致。"""
        a, ta = self.player()
        before = self.s.query(ta, 'me')
        self.grant(a, [{'type': 'currency', 'code': 'GP', 'delta': 100}])
        self.command('sanction.create', {'target_account_id': a})
        self.s = Service(self.path, test_profile=True, clock=lambda: self.now)
        with self.assertRaises(Fault): self.s.login('player-a', self.password)
        after = self.s.query(self.owner, 'user', account_id=a)
        self.assertEqual(before['character'], after['character']); self.assertEqual(self.balance(a), 100)

    def test_session_replacement_and_expiry_remove_old_seat(self):
        """验证场景：会话替换和到期后旧会话不能继续保留有效席位。"""
        a, ta = self.player()
        room = self.command('room.create', {'map_key':'test-arena'}, ta)
        token = self.s.login('player-a', self.password)['token']
        with self.assertRaises(Fault): self.s.query(ta, 'me')
        self.assertIsNone(self.s.query(token, 'me')['room'])
        self.command('room.create', {'map_key':'test-arena'}, token)
        self.now += 3601
        self.assertEqual(maintain(self.s)['expired_members'], 1)

    def test_permission_matrix_and_no_credentials_in_read_models(self):
        """验证场景：角色权限按矩阵拒绝越权，读模型不暴露凭据。"""
        a, ta = self.player()
        for token in (ta, self.extra_admin('users'), self.extra_admin('auditor')):
            with self.assertRaises(Fault):
                self.s.command(token, 'asset.grant', self.meta(target_account_id=a, catalog_version=self.s.catalog['id'], changes=[{'type':'currency','code':'GP','delta':1}]))
        resource = self.extra_admin('resources')
        with self.assertRaises(Fault): self.command('user.reset', {'target_account_id': a, 'new_password': self.password}, resource)
        exported = json.dumps(self.s.query(self.owner, 'user', account_id=a)) + json.dumps(self.s.query(self.owner, 'audit'))
        for sensitive in (self.password, self.owner, ta, 'otp_secret', 'password_hash', 'token_hash'):
            self.assertNotIn(sensitive, exported)


class Assets(Base):
    """验证资产余额、库存、不可改写流水、幂等、补偿及 outbox 的同事务约束。"""
    def test_replay_and_payload_conflict(self):
        """验证场景：相同幂等键和内容返回原结果，内容改变时报冲突。"""
        a, ta = self.player(); data=self.meta(target_account_id=a,catalog_version=self.s.catalog['id'],changes=[{'type':'currency','code':'GP','delta':100}])
        first=self.s.command(self.owner,'asset.grant',data)
        for _ in range(10): self.assertEqual(first,self.s.command(self.owner,'asset.grant',data))
        data['changes'][0]['delta']=101
        with self.assertRaises(Fault): self.s.command(self.owner,'asset.grant',data)
        self.assertEqual(self.balance(a),100)
        self.assertEqual(len(self.s.query(self.owner,'operations',account_id=a)),1)

    def test_unknown_negative_overflow_invalid_duration_and_cross_account(self):
        """验证场景：未知资产、负余额、溢出、非法期限和跨账号装备均拒绝。"""
        a, ta=self.player(); b, tb=self.player('player-b')
        for change in ({'type':'currency','code':'UNKNOWN','delta':1},{'type':'currency','code':'GP','delta':-1},
                       {'type':'currency','code':'GP','delta':True},{'type':'currency','code':'GP','delta':10**30},
                       {'type':'item','item_key':'unknown','quantity':1}, {'type':'item','item_key':'test-token','quantity':-1},
                       {'type':'item','item_key':'test-token','quantity':1,'duration_seconds':0}):
            with self.assertRaises(Fault): self.grant(a,[change])
        item=self.grant(a,[{'type':'item','item_key':'test-token','quantity':1}])
        owned=self.s.query(ta,'me')['inventory'][0]
        with self.assertRaises(Fault): self.command('inventory.consume',{'catalog_version':self.s.catalog['id'],'instance_id':owned['id'],'quantity':1},tb)
        self.assertEqual(self.balance(a),0);self.assertEqual(self.balance(b),0)

    def test_purchase_failure_rolls_back_wallet_and_outbox(self):
        """验证场景：购买失败后钱包、流水和 outbox 全部回滚。"""
        a, ta=self.player()
        self.grant(a,[{'type':'currency','code':'GP','delta':1000},{'type':'item','item_key':'test-rifle','quantity':1}])
        before=self.s.query(self.owner,'outbox')
        with self.assertRaises(Fault): self.command('shop.purchase',{'catalog_version':self.s.catalog['id'],'offer_key':'test-rifle-gp'},ta)
        self.assertEqual(self.balance(a),1000);self.assertEqual(before,self.s.query(self.owner,'outbox'))

    def test_concurrent_grants_and_purchase_preserve_balance(self):
        """验证场景：并发发放和购买保持余额与流水一致。"""
        a, ta=self.player();self.grant(a,[{'type':'currency','code':'GP','delta':1000}])
        def change(i):
            """在并发工作线程提交资产变更，检验共享部署锁下不会丢失更新。"""
            if i==0:return self.legacy_purchase_fixture(ta,catalog_version=self.s.catalog['id'],offer_key='test-rifle-gp')
            return self.grant(a,[{'type':'currency','code':'GP','delta':100}])
        with ThreadPoolExecutor(max_workers=10) as pool:list(pool.map(change,range(10)))
        self.assertEqual(self.balance(a),1400);self.assertEqual(len(self.s.query(ta,'me')['inventory']),1)

    def test_preview_is_read_only_and_commit_revalidates(self):
        """验证场景：预览无持久化写入，确认提交重新校验当前状态。"""
        a,ta=self.player();data={'target_account_id':a,'catalog_version':self.s.catalog['id'],'changes':[{'type':'currency','code':'GP','delta':100}]}
        projected=preview(self.s,self.owner,data)
        self.assertEqual(projected['after']['wallets'][1]['balance'] if projected['after']['wallets'][1]['currency']=='GP' else projected['after']['wallets'][0]['balance'],100)
        self.assertEqual(self.balance(a),0);self.assertEqual(self.s.query(self.owner,'outbox'),[])

    def test_consumption_equipment_expiry_and_limit(self):
        """验证场景：消费数量、装备到期和库存上限依规则执行。"""
        a,ta=self.player()
        self.grant(a,[{'type':'item','item_key':'test-token','quantity':2},{'type':'item','item_key':'test-rifle','quantity':1,'duration_seconds':10}])
        items={i['item_key']:i for i in self.s.query(ta,'me')['inventory']}
        self.command('inventory.equip',{'catalog_version':self.s.catalog['id'],'instance_id':items['test-rifle']['id'],'slot':'primary'},ta)
        self.command('inventory.consume',{'catalog_version':self.s.catalog['id'],'instance_id':items['test-token']['id'],'quantity':2},ta)
        self.now+=11
        with self.assertRaises(Fault):self.command('inventory.equip',{'catalog_version':self.s.catalog['id'],'instance_id':items['test-rifle']['id'],'slot':'primary'},ta)
        self.s.catalog['inventory_limit']=1
        with self.assertRaises(Fault):self.grant(a,[{'type':'item','item_key':'test-token','quantity':1}])

    def test_atomic_audit_failure_and_immutable_ledger(self):
        """验证场景：审计失败回滚业务，数据库拒绝改写或删除历史流水。"""
        a,ta=self.player()
        # 注入审计失败，验证余额、流水和 outbox 全部回滚；真实 MySQL 触发器另行验证。
        with patch.object(self.s, '_audit', side_effect=DatabaseError('injected audit failure')):
            with self.assertRaises(DatabaseError):self.grant(a,[{'type':'currency','code':'GP','delta':100}])
        self.assertEqual(self.balance(a),0);self.assertEqual(self.s.query(self.owner,'outbox'),[])
        self.grant(a,[{'type':'currency','code':'GP','delta':10}])
        with self.assertRaises(DatabaseError):
            with self.s.db.transaction() as c:c.table('asset_entries').update(delta=100)
        with self.assertRaises(DatabaseError):
            with self.s.db.transaction() as c:c.table('audit_events').all().delete()

    def test_post_commit_reply_loss_and_restart_replay(self):
        """验证场景：提交后响应丢失，重启重试返回原结果而不重复发放。"""
        a,ta=self.player();data=self.meta(target_account_id=a,catalog_version=self.s.catalog['id'],changes=[{'type':'currency','code':'GP','delta':10}])
        first=self.s.command(self.owner,'asset.grant',data)
        self.s=Service(self.path,test_profile=True,clock=lambda:self.now)
        self.assertEqual(first,self.s.command(self.owner,'asset.grant',data));self.assertEqual(self.balance(a),10)

    def test_outbox_retry_never_repeats_assets(self):
        """验证场景：通知重试只发送事件，不重复执行资产变更。"""
        a,ta=self.player();self.grant(a,[{'type':'currency','code':'GP','delta':100}])
        def failure(*args):
            """注入发送或持久化失败，确认调用方回滚或保留待重试状态。"""
            raise RuntimeError('secret should not be logged')
        results=deliver_outbox(self.s,failure)
        self.assertFalse(results[0]['delivered']);self.now+=3
        delivered=[]
        def send(key,*args):
            """记录测试发送次数并模拟确认，核对 outbox 重试不会重做资产。"""
            delivered.append(key);return True
        self.assertTrue(deliver_outbox(self.s,send)[0]['delivered']);self.assertEqual(deliver_outbox(self.s,send),[])
        self.assertEqual(self.balance(a),100);self.assertEqual(len(delivered),1)

    def test_spent_compensation_is_review_not_negative(self):
        """验证场景：已消费资产的补偿转人工审核，不能造成负余额。"""
        a,ta=self.player();original=self.grant(a,[{'type':'currency','code':'GP','delta':600}])
        self.legacy_purchase_fixture(ta,catalog_version=self.s.catalog['id'],offer_key='test-rifle-gp')
        result=self.command('asset.compensate',{'operation_id':original['operation_id'],'catalog_version':self.s.catalog['id'],**self.step_up()})
        self.assertEqual(result['status'],'MANUAL_REVIEW');self.assertEqual(self.balance(a),100)

    def test_compensation_success_and_no_double_reversal(self):
        """验证场景：补偿追加关联流水，重复请求不会双重撤销。"""
        a,ta=self.player();original=self.grant(a,[{'type':'currency','code':'GP','delta':50},{'type':'currency','code':'GP','delta':50}])
        result=self.command('asset.compensate',{'operation_id':original['operation_id'],'catalog_version':self.s.catalog['id'],**self.step_up()})
        self.assertEqual(result['status'],'COMMITTED');self.assertEqual(self.balance(a),0)
        with self.assertRaises(Fault):self.command('asset.compensate',{'operation_id':original['operation_id'],'catalog_version':self.s.catalog['id'],**self.step_up()})

    def test_batch_freezes_targets_partial_failure_and_replay(self):
        """验证场景：批量冻结目标，子项失败独立回滚，重放不重复成功子项。"""
        a,ta=self.player();b,tb=self.player('player-b')
        data=self.meta(target_account_ids=[a,b,'missing'],catalog_version=self.s.catalog['id'],changes=[{'type':'currency','code':'GP','delta':10}],**self.step_up())
        data=self.authorize('asset.batch',data)
        first=self.s.command(self.owner,'asset.batch',data)
        self.assertEqual([r['status'] for r in first['results']],['COMMITTED','COMMITTED','REJECTED'])
        self.assertEqual(first,self.s.command(self.owner,'asset.batch',data));self.assertEqual(self.balance(a),10);self.assertEqual(self.balance(b),10)

    def test_large_owner_grants_require_fresh_mfa(self):
        """验证场景：大额 Owner 发放必须重新验证新鲜 MFA。"""
        a,ta=self.player()
        changes=[{'type':'currency','code':'NZ','delta':10000},{'type':'currency','code':'GP','delta':50000}]
        with self.assertRaises(Fault):self.grant(a,changes)
        self.command('asset.grant',{'target_account_id':a,'catalog_version':self.s.catalog['id'],'changes':changes,**self.step_up()})
        self.assertEqual(self.balance(a,'NZ'),10000);self.assertEqual(self.balance(a),50000)


class Rooms(Base):
    """验证席位并发、房间修订、冻结、租约和合成结算隔离。"""
    def test_room_replays_and_host_transfer(self):
        """验证场景：房间命令可幂等重放，房主退出按规则移交。"""
        a,ta=self.player();b,tb=self.player('player-b')
        room=self.command('room.create',{'map_key':'test-arena'},ta)['room_id']
        for _ in range(10):self.command('room.join',{'room_id':room},tb)
        self.assertEqual(self.s.query(tb,'me')['room']['slot'],1)
        self.command('room.leave',{'room_id':room},ta)
        self.assertEqual(self.s.query(tb,'rooms')[0]['owner'],b)

    def test_concurrent_last_seat_and_one_room_per_player(self):
        """验证场景：并发争抢最后席位只成功一次，玩家不能同时加入两房。"""
        players=[self.player(f'player-{i}') for i in range(6)]
        room=self.command('room.create',{'map_key':'test-arena'},players[0][1])['room_id']
        def join(player):
            """在并发工作线程尝试最后席位，返回结果或业务错误以核对只成功一次。"""
            try:return self.command('room.join',{'room_id':room},player[1])
            except Fault as e:return e.code
        with ThreadPoolExecutor(max_workers=5) as pool:results=list(pool.map(join,players[1:]))
        self.assertEqual(sum(isinstance(x,dict) for x in results),3)
        with self.assertRaises(Fault):self.command('room.create',{'map_key':'test-arena'},players[0][1])

    def test_configuration_revision_readiness_and_start_freeze(self):
        """验证场景：配置修订清除准备状态，开始时核对版本并冻结快照。"""
        a,ta=self.player();room=self.command('room.create',{'map_key':'test-arena'},ta)['room_id']
        with self.assertRaises(Fault):self.command('room.start',{'room_id':room,'expected_revision':1},ta)
        self.command('room.ready',{'room_id':room,'ready':True},ta)
        with self.assertRaises(Fault):self.command('room.start',{'room_id':room,'expected_revision':1},ta)
        revision=self.s.query(ta,'rooms')[0]['revision']
        match=self.command('room.start',{'room_id':room,'expected_revision':revision},ta)
        self.assertNotIn('node_token',match);self.assertFalse(match['retail_ready'])
        with self.assertRaises(Fault):self.command('room.configure',{'room_id':room,'map_key':'test-arena','expected_revision':revision},ta)

    def test_allocation_timeout_and_twenty_cancel_cycles(self):
        """验证场景：节点分配超时释放状态，多轮取消不残留席位和租约。"""
        a,ta=self.player();room=self.command('room.create',{'map_key':'test-arena'},ta)['room_id']
        for _ in range(20):
            self.command('room.ready',{'room_id':room,'ready':True},ta)
            revision=self.s.query(ta,'rooms')[0]['revision']
            self.command('room.start',{'room_id':room,'expected_revision':revision},ta)
            self.command('room.cancel',{'room_id':room},ta)
        self.command('room.ready',{'room_id':room,'ready':True},ta)
        revision=self.s.query(ta,'rooms')[0]['revision']
        self.command('room.start',{'room_id':room,'expected_revision':revision},ta)
        self.now+=31
        self.assertEqual(maintain(self.s)['expired_matches'],1)
        with self.s.db.connect() as c:self.assertEqual(c.table('matches').filter(state__in=['STARTING','ACTIVE']).count(),0)

    def test_trusted_synthetic_settlement_and_client_forgery(self):
        """验证场景：仅可信合成节点可以结算，客户端伪造结果被拒绝。"""
        (a,ta),(b,tb),room,match=self.match()
        data={'match_id':match['match_id'],'epoch':match['epoch'],'outcome':'VICTORY'}
        with self.assertRaises(Fault):settle(self.s,ta,data)
        node=test_host_ready(self.s,match['match_id'])
        first=settle(self.s,node['node_token'],data)
        for _ in range(10):self.assertEqual(first,settle(self.s,node['node_token'],data))
        with self.assertRaises(Fault):settle(self.s,node['node_token'],dict(data,outcome='FAILURE'))
        self.assertEqual(self.balance(a),100);self.assertEqual(self.balance(b),100)
        self.assertFalse(first['real_clear_verified'])

    def test_ban_during_match_preserves_other_player_and_no_reward_for_revoked(self):
        """验证场景：对局中封禁只撤销目标资格，其他玩家继续且目标不得奖励。"""
        (a,ta),(b,tb),room,match=self.match();node=test_host_ready(self.s,match['match_id'])
        self.command('sanction.create',{'target_account_id':a})
        with self.assertRaises(Fault):self.s.query(ta,'me')
        self.assertEqual(self.s.query(tb,'rooms')[0]['owner'],b)
        settle(self.s,node['node_token'],{'match_id':match['match_id'],'epoch':match['epoch'],'outcome':'VICTORY'})
        self.assertEqual(self.balance(a),0);self.assertEqual(self.balance(b),100)

    def test_stale_node_lease_and_epoch_rejected(self):
        """验证场景：旧节点租约和 epoch 不得续租或提交结算。"""
        (a,ta),(b,tb),room,match=self.match();node=test_host_ready(self.s,match['match_id'])
        with self.assertRaises(Fault):heartbeat(self.s,node['node_token'],match['match_id'],'wrong-epoch')
        self.now+=31
        with self.assertRaises(Fault):settle(self.s,node['node_token'],{'match_id':match['match_id'],'epoch':match['epoch'],'outcome':'VICTORY'})
        self.assertEqual(self.balance(a),0)

    def test_two_rooms_are_isolated(self):
        """验证场景：两间房间的准备、启动、结算和资产互相隔离。"""
        a,ta=self.player();b,tb=self.player('player-b')
        ra=self.command('room.create',{'map_key':'test-arena'},ta)['room_id']
        rb=self.command('room.create',{'map_key':'test-arena'},tb)['room_id']
        self.command('room.leave',{'room_id':ra},ta)
        self.assertEqual(self.s.query(tb,'me')['room']['room_id'],rb)
        self.assertEqual(len(self.s.query(tb,'rooms')),1)


class Deployment(Base):
    """验证 profile 隔离与 MySQL 逻辑备份恢复核对，不模拟真实客户端兼容性。"""
    def test_backup_integrity_and_profile_isolation(self):
        """验证场景：实际备份恢复一致，数据库不能跨 profile 复用。"""
        a,ta=self.player();self.grant(a,[{'type':'currency','code':'GP','delta':10}])
        backup=Path(self.directory.name)/'backup.sql'
        result=self.s.db.backup(backup)
        self.assertTrue(result['verified']);self.assertGreater(backup.stat().st_size,0)
        with self.assertRaises(ValueError):self.s.db.backup(backup)
        with self.assertRaises(Fault):Service(self.path,test_profile=False)

    def test_default_profile_refuses_guessed_resources_and_native_host(self):
        # 每个测试已经拥有独立空库；在尚未创建业务数据时重置合成 profile 标记。
        """当前环境允许已冻结兼容钱包/物品，仍拒绝未知/合成资源及未验收原生节点。"""
        DeploymentMetadata.objects.all().delete()
        current=Service()
        self.assertIn('cn2020:GP',current.catalog['currencies'])
        self.assertIn('cn2020:100001',current.catalog['items'])
        self.assertNotIn('GP',current.catalog['currencies']);self.assertNotIn('NZ',current.catalog['currencies'])
        self.assertNotIn('cn2020:unconfirmed-item',current.catalog['items'])
        self.assertEqual(current.catalog['native_assets']['evidence'],'COMPATIBILITY_INVENTORY_ONLY')
        self.assertFalse(current.catalog['native_assets']['native_combat_verified'])
        self.assertEqual(current.catalog['maps'],{})
        from revival.assets import currency_value
        for unknown in ('GP','NZ','cn2020:unconfirmed-currency'):
            with self.subTest(currency=unknown),self.assertRaises(Fault):currency_value(current,unknown,1)
        with self.assertRaises(Fault):test_host_ready(current,'anything')
        with self.assertRaises(Fault):settle(current,'anything',{})
        from adapters.cn_current.profile import ClientProfile,CompatibilityBlocked
        profile=ClientProfile(Path(__file__).resolve().parents[1]/'config/cn-current.json')
        with self.assertRaises(CompatibilityBlocked):profile.require('retail_login')
        with self.assertRaises(CompatibilityBlocked):profile.decode(b'\x00\x01')


if __name__=='__main__':unittest.main()
