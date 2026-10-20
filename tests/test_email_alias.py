"""真实 MySQL 回归邮箱换绑后的重新注册、随机隐藏名及历史幂等；SMTP 始终使用替身。"""
import secrets
import threading
from unittest.mock import patch

from django.db.models import Q
from django.test import Client, override_settings

from revival.email_verification import check_code, request_code
from revival.security import token_hash
from revival.service import Fault
from .support import DatabasePool
from .test_control_plane import Base


class EmailAliasTests(Base):
    """仅在 Django 独立测试库创建账号，验证随机内部别名不能改变业务身份或公开认证门禁。"""

    def setUp(self):
        """启用真实验证码校验，截获投递以免读取或发送任何真实邮件。"""
        super().setUp()
        enabled = override_settings(NZ_EMAIL_VERIFICATION_REQUIRED=True, NZ_PROFILE='synthetic', NZ_PORT=8765)
        enabled.enable()
        self.addCleanup(enabled.disable)
        configured = patch('revival.mailing.configured', return_value=True)
        configured.start()
        self.addCleanup(configured.stop)
        sender = patch('revival.mailing.send_code')
        self.sender = sender.start()
        self.addCleanup(sender.stop)

    def registration(self, email, invitation, password=None, nickname=None):
        """申请生产挑战并读取本例 SMTP 替身中的随机码，构造可重放的完整注册请求。"""
        challenge = request_code(self.s, {'email': email, 'purpose': 'register', 'invitation': invitation}, '127.0.0.1')
        return self.meta(email=email, password=password or self.password, invitation=invitation,
                         nickname=nickname or '邮箱玩家'+secrets.token_hex(5),
                         challenge_id=challenge['challenge_id'], code=self.sender.call_args.args[1])

    def test_released_legacy_email_can_register_without_rewriting_old_account(self):
        """旧哈希隐藏名保留时，换绑后的旧邮箱仍可注册；历史重放、角色、资产和旧身份保持隔离。"""
        old_email = 'released@example.test'
        invitation = self.invitation(uses=2)['invitation_token']
        old_request = self.registration(old_email, invitation, nickname='换绑保留角色')
        original = self.s.register(old_request)
        legacy_login = 'm_' + token_hash(old_email)[:30]
        # 只在独立测试库模拟升级前已经存在的哈希隐藏名；生产实现禁止重写这些历史列。
        with self.s.db.transaction() as c:
            c.update('accounts', Q(id=original['account_id']), {'login': legacy_login})
        self.command('user.approve', {'target_account_id': original['account_id']})
        old_token = self.s.login(old_email, self.password)['token']
        self.command('character.create', {'nickname': '换绑保留角色'}, old_token)
        self.grant(original['account_id'], [{'type': 'currency', 'code': 'GP', 'delta': 7}])
        before = self.s.query(old_token, 'me')
        self.command('user.email.set', {'target_account_id': original['account_id'], 'email': 'retained@example.test', **self.step_up()})
        self.now += 61
        new_password = 'new-owner-password-456'
        new_request = self.registration(old_email, invitation, new_password)
        replacement = self.s.register(new_request)
        self.assertNotEqual(replacement['account_id'], original['account_id'])
        self.assertNotEqual(replacement['game_id'], original['game_id'])
        self.assertEqual(self.s.register(old_request), original)
        self.assertEqual(self.s.register(new_request), replacement)
        retained = self.s.query(self.s.login('retained@example.test', self.password)['token'], 'me')
        for key in ('id', 'login', 'game_id', 'character', 'wallets', 'inventory', 'equipment'):
            self.assertEqual(retained[key], before[key])
        self.assertEqual(retained['login'], legacy_login)
        self.command('user.approve', {'target_account_id': replacement['account_id']})
        fresh = self.s.query(self.s.login(old_email, new_password)['token'], 'me')
        self.assertNotEqual(fresh['character']['id'], before['character']['id'])
        self.assertTrue(all(row['balance'] == 0 for row in fresh['wallets']))
        self.assertNotEqual(fresh['login'], legacy_login)
        self.assertEqual(self.s.query(self.owner, 'invitations')[0]['uses'], 0)
        for hidden in (legacy_login, fresh['login']):
            with self.assertRaises(Fault):
                self.s.login(hidden, self.password if hidden == legacy_login else new_password)
        self.sender.assert_called()
        # 两次注册及一次Owner换绑确认，共三次真正的随机邮件挑战。
        self.assertEqual(self.sender.call_count, 3)

    def test_random_alias_retries_collision_and_public_login_still_requires_email(self):
        """随机名碰到旧用户名时重新取名，公开登录不能凭隐藏名绕过邮箱身份。"""
        occupied = 'm_' + 'a' * 30
        legacy_id, _ = self.player(occupied, character=False)
        data = self.registration('random@example.test', self.invitation()['invitation_token'])
        with patch('revival.service.secrets.token_hex', side_effect=['a' * 30, 'b' * 30]) as random_name:
            account = self.s.register(data)
            self.assertEqual(self.s.register(data), account)
        self.assertEqual(random_name.call_count, 2)
        with self.s.db.transaction() as c:
            actual = c.select('accounts', Q(id=account['account_id'])).fetchone()['login']
            self.assertEqual(c.select('accounts', Q(id=legacy_id)).fetchone()['login'], occupied)
        self.assertEqual(actual, 'm_' + 'b' * 30)
        self.assertRegex(actual, r'^m_[0-9a-f]{30}$')
        self.command('user.approve', {'target_account_id': account['account_id']})
        client = Client(HTTP_HOST='127.0.0.1:8765')
        for path, identity in (('/api/login', {'login': actual}), ('/api/player/login', {'email': actual})):
            result = client.post(path, dict(identity, password=self.password), content_type='application/json', HTTP_X_NZ_REQUEST='1')
            self.assertEqual(result.status_code, 401)
        self.assertTrue(self.s.login(data['email'], self.password)['token'])

    def test_concurrent_registration_replay_allocates_one_random_identity(self):
        """同一验证码与幂等键并发提交只分配一个随机隐藏名，不多建账号或消费邀请。"""
        data = self.registration('concurrent@example.test', self.invitation()['invitation_token'])
        with patch('revival.service.secrets.token_hex', wraps=secrets.token_hex) as random_name:
            with DatabasePool(max_workers=2) as pool:
                results = list(pool.map(self.s.register, [data, data]))
        self.assertEqual(results[0], results[1])
        self.assertEqual(random_name.call_count, 1)
        with self.s.db.transaction() as c:
            self.assertEqual(c.count('accounts')[0][0], 1)
            self.assertEqual(c.count('player_emails')[0][0], 1)
        self.assertEqual(self.s.query(self.owner, 'invitations')[0]['uses'], 0)
        self.sender.assert_called_once()

    def test_registration_replay_when_peer_commits_before_code_precheck(self):
        """固定交错：重试先读到无幂等结果，另一请求提交后再预检已消费码，仍须返回原成功结果。"""
        data = self.registration('interleaved@example.test', self.invitation()['invitation_token'])
        delayed = dict(data, request_id='delayed-registration-precheck')
        waiting, committed = threading.Event(), threading.Event()

        def guarded_check(service, payload, email, purpose):
            """只暂停指定重试的预检；此处不持有数据库锁，让另一连接完成实际业务提交。"""
            if payload['request_id'] == delayed['request_id']:
                waiting.set()
                if not committed.wait(30):
                    raise AssertionError('Peer registration did not commit before the bounded wait')
            return check_code(service, payload, email, purpose)

        def commit_peer():
            """等重试通过第一次幂等查询，再提交账号并释放预检；异常时也解除等待避免泄漏线程。"""
            if not waiting.wait(30):
                raise AssertionError('Delayed registration did not reach its code precheck')
            try:
                return self.s.register(data)
            finally:
                committed.set()

        with patch('revival.email_verification.check_code', side_effect=guarded_check):
            with DatabasePool(max_workers=2) as pool:
                retry = pool.submit(self.s.register, delayed)
                peer = pool.submit(commit_peer)
                expected = peer.result(timeout=40)
                self.assertEqual(retry.result(timeout=40), expected)
        with self.s.db.transaction() as c:
            self.assertEqual(c.count('accounts')[0][0], 1)
            self.assertEqual(c.table('audit_events').filter(action='account.register').count(), 1)
        self.assertEqual(self.s.query(self.owner, 'invitations')[0]['uses'], 0)
