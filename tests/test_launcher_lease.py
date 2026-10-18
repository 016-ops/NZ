"""真实MySQL验证Launcher短租约：跨小时、绝对上限、失联过期与审计末尾回滚。"""
from unittest.mock import patch
from django.db import DatabaseError
from django.db.models import Q
from revival import launcher
from revival.security import new_token, token_hash
from revival.service import Fault, Service
from .test_control_plane import Base


class LauncherLeaseTests(Base):
    """仅使用隔离测试账号；不启动客户端，不将租约验证等同于大厅或多人验收。"""

    def session(self, leased=True):
        """创建批准的测试邮箱账号，按同一真实票据/兑换链申请短租约或旧合同会话。"""
        invite = self.invitation()
        account = self.s.register(self.meta(email='lease@example.test', password=self.password,
                                            invitation=invite['invitation_token']))
        self.command('user.approve', {'target_account_id': account['account_id']})
        source = self.s.login('lease@example.test', self.password)['token']
        verifier = new_token()
        scope = dict(protocol=launcher.PROTOCOL, client_sha256='a'*64)
        ticket = launcher.issue(self.s, source, self.meta(**scope, verifier_sha256=token_hash(verifier)))
        game = launcher.redeem(self.s, dict(scope, ticket=ticket['ticket'], verifier=verifier,
            **({'session_lease': launcher.LEASE_VERSION} if leased else {})))
        return source, game, dict(scope, session_lease=launcher.LEASE_VERSION)

    def test_renew_crosses_old_hour_limit_and_survives_service_restart(self):
        """每两分钟续期越过旧一小时限制，重建服务仍可从MySQL验证同一凭据及绝对上限。"""
        source, game, scope = self.session()
        started = self.now
        for _ in range(31):
            self.now += 120
            lease = launcher.renew(self.s, game['game_token'], scope)
            self.assertEqual(lease['expires_at'], self.now+180)
        self.assertGreater(self.now, started+3600)
        restarted = Service(test_profile=True, clock=lambda: self.now)
        self.assertTrue(launcher.identity(restarted, game['game_token'], scope)['account_id'])
        self.assertTrue(self.s.query(source, 'me')['id'])
        self.assertEqual(lease['lease_max_expires_at'], started+12*3600)

    def test_stopped_heartbeat_expires_source_and_game_without_revival(self):
        """启动器崩溃/断电不再续期后，三分钟边界同时拒绝来源、游戏及迟到续期。"""
        source, game, scope = self.session()
        self.now += 180
        for action in (lambda: self.s.query(source, 'me'),
                       lambda: launcher.identity(self.s, game['game_token'], scope),
                       lambda: launcher.renew(self.s, game['game_token'], scope)):
            with self.assertRaises(Fault) as caught:
                action()
            self.assertEqual(caught.exception.code, 'AUTH')

    def test_absolute_limit_and_wrong_client_cannot_extend(self):
        """客户端绑定错误拒绝，接近总时限只续到上限，不能无限延长一次登录。"""
        _, game, scope = self.session()
        with self.assertRaises(Fault):
            launcher.renew(self.s, game['game_token'], dict(scope, client_sha256='b'*64))
        maximum = self.now+200
        with self.s.db.transaction() as c:
            c.update('game_sessions', Q(digest=token_hash(game['game_token'])), {'lease_max_expires_at': maximum})
        self.now += 120
        self.assertEqual(launcher.renew(self.s, game['game_token'], scope)['expires_at'], maximum)
        self.now = maximum
        with self.assertRaises(Fault):
            launcher.renew(self.s, game['game_token'], scope)

    def test_audit_failure_rolls_back_both_expiries(self):
        """事务最后审计失败后来源与游戏期限均保持旧值，重试才一起更新。"""
        source, game, scope = self.session()
        previous = game['expires_at']
        self.now += 60
        with patch.object(self.s, '_audit', side_effect=DatabaseError('test failure')):
            with self.assertRaises(DatabaseError):
                launcher.renew(self.s, game['game_token'], scope)
        with self.s.db.transaction() as c:
            self.assertEqual(c.select('sessions', Q(token_hash=token_hash(source))).fetchone()['expires_at'], previous)
            self.assertEqual(c.select('game_sessions', Q(digest=token_hash(game['game_token']))).fetchone()['expires_at'], previous)
        self.assertEqual(launcher.renew(self.s, game['game_token'], scope)['expires_at'], self.now+180)

    def test_legacy_session_and_revoked_source_cannot_renew(self):
        """旧客户端保持原一小时合同，不能隐式变成长会话；来源注销后租约也立即拒绝。"""
        source, game, scope = self.session(leased=False)
        self.assertEqual(game['expires_at'], self.now+3600)
        with self.assertRaises(Fault) as caught:
            launcher.renew(self.s, game['game_token'], scope)
        self.assertEqual(caught.exception.code, 'LEASE_NOT_ENABLED')
        with self.s.db.transaction() as c:
            c.update('sessions', Q(token_hash=token_hash(source)), {'revoked': 1})
        with self.assertRaises(Fault) as caught:
            launcher.renew(self.s, game['game_token'], scope)
        self.assertEqual(caught.exception.code, 'AUTH')
