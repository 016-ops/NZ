"""真实MySQL验证邮件认证、操作绑定及开放注册；仅SMTP投递替换，不发送真实邮件。"""
from unittest.mock import patch
from importlib import import_module
from types import SimpleNamespace
from django.apps import apps
from django.db import connections
from django.db import DatabaseError
from django.db.models import Q
from django.test import override_settings

from revival import admin_email_auth as auth, mailing
from revival.email_verification import request_code, recover
from revival.service import Fault, identifier, Service
from revival.security import token_hash
from .support import DatabasePool
from .test_control_plane import Base


class AdminEmailAuthentication(Base):
    """邮件码必须在密码、用途、会话和最终操作内容均正确时才能一次提交。"""

    def login_challenge(self):
        """从生产取码入口及SMTP替身取得本轮随机码，不读取运行时凭据。"""
        self.now += 61
        result = auth.request_login_code(self.s, {'login':'owner','password':self.password,
                                                'email':'ignored@example.invalid'}, 'authentication-test')
        self.assertEqual(mailing.send_code.call_args.args[0], auth.RECIPIENT)
        return {'challenge_id':result['challenge_id'], 'code':mailing.send_code.call_args.args[1]}

    def test_login_requires_mail_and_consumes_once_even_concurrently(self):
        """旧TOTP及免码配置无效，同一邮件挑战并发最多签发一个会话。"""
        with self.assertRaises(Fault):
            self.s.login('owner', self.password, kind='admin', otp_code='123456')
        challenge = self.login_challenge()
        def attempt(_):
            """两个真实连接竞争最终消费，不把异常当作成功。"""
            try:
                return self.s.login('owner', self.password, kind='admin', **challenge)['kind']
            except Fault as error:
                return error.code
        with DatabasePool(max_workers=2) as pool:
            result = list(pool.map(attempt, range(2)))
        self.assertEqual(result.count('admin'), 1)
        with self.assertRaises(Fault):
            self.s.login('owner', self.password, kind='admin', **challenge)

    def test_wrong_code_budget_expiry_and_resend_survive_restart(self):
        """五次错码、过期、重发替代均持久生效，重建Service不能恢复旧码。"""
        challenge = self.login_challenge()
        wrong = '111111' if challenge['code'] != '111111' else '222222'
        for _ in range(5):
            with self.assertRaises(Fault):
                self.s.login('owner', self.password, kind='admin', **dict(challenge, code=wrong))
        self.s = Service(test_profile=True, clock=lambda:self.now)
        with self.assertRaises(Fault):
            self.s.login('owner', self.password, kind='admin', **challenge)
        second = self.login_challenge()
        third = self.login_challenge()
        with self.assertRaises(Fault):
            self.s.login('owner', self.password, kind='admin', **second)
        self.now += 301
        with self.assertRaises(Fault):
            self.s.login('owner', self.password, kind='admin', **third)

    def test_login_wrong_password_uses_budget_but_other_login_cannot_lock_challenge(self):
        """错密码与错码共用五次失败额度；任意其他/未知账号不能消耗Owner挑战。"""
        self.extra_admin('users')
        challenge = self.login_challenge()
        for login in ('users', 'unknown-login'):
            for _ in range(5):
                with self.assertRaises(Fault):
                    self.s.login(login, self.password, kind='admin', **challenge)
        with self.s.db.transaction() as c:
            self.assertEqual(c.select('admin_auth_challenges', Q(id=challenge['challenge_id'])).fetchone()['attempts'], 0)
        for _ in range(5):
            with self.assertRaises(Fault):
                self.s.login('owner', 'wrong-password', kind='admin', **challenge)
        self.s = Service(test_profile=True, clock=lambda:self.now)
        with self.s.db.transaction() as c:
            self.assertEqual(c.select('admin_auth_challenges', Q(id=challenge['challenge_id'])).fetchone()['attempts'], 5)
        with self.assertRaises(Fault):
            self.s.login('owner', self.password, kind='admin', **challenge)

    def test_smtp_fail_closed_and_failed_password_source_budget_is_durable(self):
        """投递失败不激活挑战；错误密码的来源额度持久保存且不读取真实SMTP。"""
        with patch('revival.mailing.configured', return_value=False), self.assertRaises(Fault) as caught:
            self.login_challenge()
        self.assertEqual(caught.exception.code, 'MAIL_NOT_CONFIGURED')
        self.now += 61
        with patch('revival.mailing.send_code', side_effect=mailing.MailUnavailable('private unexpected text')):
            with self.assertRaises(Fault) as caught:
                auth.request_login_code(self.s, {'login':'owner','password':self.password}, 'failed-smtp')
        self.assertEqual(caught.exception.code, 'MAIL_DELIVERY_FAILED')
        with self.s.db.transaction() as c:
            row = c.select('admin_auth_challenges', Q(status='FAILED')).fetchone()
            self.assertEqual(row['error_code'], 'MAIL_DELIVERY_FAILED')
        for _ in range(20):
            with self.assertRaises(Fault):
                auth.request_login_code(self.s, {'login':'unknown','password':'wrong'}, 'bad-password-source')
        self.s = Service(test_profile=True, clock=lambda:self.now)
        with self.assertRaises(Fault) as caught:
            auth.request_login_code(self.s, {'login':'owner','password':self.password}, 'bad-password-source')
        self.assertEqual(caught.exception.code, 'RATE_LIMIT')

    def test_step_up_binds_session_action_payload_and_replays_only_committed_result(self):
        """操作改变/换会话均不能借码授权；成功同键重试不二次消费或新建管理员。"""
        data = self.authorize('admin.create', self.meta(login='new-admin', new_password=self.password,
                                                      role='users', current_password=self.password))
        with self.assertRaises(Fault):
            self.s.command(self.owner, 'admin.create', dict(data, role='owner'))
        other = self.admin_login()['token']
        with self.assertRaises(Fault):
            self.s.command(other, 'admin.create', data)
        with self.assertRaises(Fault):
            self.s.command(self.owner, 'admin.password.reset', data)
        first = self.s.command(self.owner, 'admin.create', data)
        self.assertEqual(first, self.s.command(self.owner, 'admin.create', dict(data, code='bad', current_password='bad')))
        self.assertNotIn('otp_secret', first)
        self.assertNotIn('recovery_codes', first)
        with self.assertRaises(Fault):
            self.s.command(self.owner, 'admin.create', dict(data, idempotency_key=identifier()))
        with self.s.db.transaction() as c:
            self.assertEqual(c.table('admins').filter(login='new-admin').count(), 1)
            record = c.select('requests', Q(key=data['idempotency_key'])).fetchone()
            self.assertNotIn(data['code'], record['result'])
            self.assertNotIn(self.password, record['result'])

    def test_step_up_audit_failure_rolls_back_consumption_and_state(self):
        """业务审计失败不能消费邮件码或留下管理员；修复后同码同操作可重试。"""
        data = self.authorize('admin.create', self.meta(login='atomic-admin', role='auditor',
            new_password=self.password, current_password=self.password))
        with patch.object(self.s, '_audit', side_effect=DatabaseError('synthetic audit failure')):
            with self.assertRaises(DatabaseError):
                self.s.command(self.owner, 'admin.create', data)
        with self.s.db.transaction() as c:
            self.assertIsNone(c.select('admin_auth_challenges', Q(id=data['challenge_id'])).fetchone()['consumed_at'])
            self.assertFalse(c.table('admins').filter(login='atomic-admin').exists())
        self.assertEqual(self.s.command(self.owner, 'admin.create', data)['role'], 'auditor')

    def test_step_up_purpose_expiry_budget_and_metadata_cannot_be_overridden(self):
        """登录码不能授权操作；五次错码和到期持久失败，混合元数据在发信前拒绝。"""
        data = self.meta(login='bound-admin', role='users', new_password=self.password, current_password=self.password)
        login = self.login_challenge()
        with self.assertRaises(Fault):
            self.s.command(self.owner, 'admin.create', dict(data, **login))
        data = self.authorize('admin.create', data)
        self.assertEqual(mailing.send_code.call_args.kwargs['operation']['action'], 'admin.create')
        self.assertEqual(len(mailing.send_code.call_args.kwargs['operation']['confirmation']), 12)
        wrong = '999999' if data['code'] != '999999' else '888888'
        for _ in range(5):
            with self.assertRaises(Fault):
                self.s.command(self.owner, 'admin.create', dict(data, code=wrong))
        with self.assertRaises(Fault):
            self.s.command(self.owner, 'admin.create', data)
        data = self.authorize('admin.create', self.meta(login='later-admin', role='users', new_password=self.password,
                                                       current_password=self.password))
        self.now += 301
        with self.assertRaises(Fault):
            self.s.command(self.owner, 'admin.create', data)
        before = mailing.send_code.call_count
        with self.assertRaises(Fault) as caught:
            auth.request_step_up_code(self.s, self.owner, {'action':'admin.create', 'current_password':self.password,
                'payload':{'login':'mixed', 'role':'users', 'new_password':self.password},
                'metadata':dict(self.meta(), action='admin.password.reset')}, 'stepup-invalid-structure')
        self.assertEqual(caught.exception.code, 'INVALID')
        self.assertEqual(mailing.send_code.call_count, before)

    def test_policy_migration_clears_admin_credentials_and_preserves_all_player_states(self):
        """0024只切换管理员认证；已验证待审、未验证待审、停用与制裁均保持原样。"""
        verified, _ = self.player('verified-legacy', character=False)
        unverified, _ = self.player('unverified-legacy', character=False)
        disabled, _ = self.player('disabled-legacy', character=False)
        with self.s.db.transaction() as c:
            c.update('admins', Q(id=self.admin['admin_id']), {'otp_secret':'OLDTOTP', 'last_otp_step':99})
            c.insert('admin_recovery', {'digest':token_hash('old-recovery'), 'admin_id':self.admin['admin_id']})
            for account_id, status in ((verified, 'PENDING_REVIEW'), (unverified, 'PENDING_REVIEW'), (disabled, 'DISABLED')):
                c.update('accounts', Q(id=account_id), {'status':status})
                c.insert('player_emails', {'account_id':account_id, 'email':account_id+'@example.test',
                    'created_at':self.now, 'verified_at':None if account_id == unverified else self.now})
            c.insert('sanctions', {'id':identifier(), 'account_id':verified, 'reason':'preserve migration fixture',
                'starts_at':self.now, 'expires_at':None, 'revoked':0})
        migrate = import_module('revival.migrations.0024_email_admin_auth').activate_email_auth
        migrate(apps, SimpleNamespace(connection=connections['default']))
        with self.s.db.transaction() as c:
            admin = c.select('admins', Q(id=self.admin['admin_id'])).fetchone()
            self.assertEqual((admin['otp_secret'], admin['last_otp_step'], admin['security_version']), ('', -1, 2))
            self.assertEqual(c.table('admin_recovery').count(), 0)
            self.assertFalse(c.table('sessions').filter(kind='admin', revoked=0).exists())
            self.assertEqual(c.select('accounts', Q(id=verified)).fetchone()['status'], 'PENDING_REVIEW')
            self.assertEqual(c.select('accounts', Q(id=unverified)).fetchone()['status'], 'PENDING_REVIEW')
            self.assertEqual(c.select('accounts', Q(id=disabled)).fetchone()['status'], 'DISABLED')
            self.assertEqual(c.table('sanctions').filter(account_id=verified, revoked=0).count(), 1)
        with self.assertRaises(Fault):
            self.s.query(self.owner, 'me')

    def test_old_recovery_and_every_platform_shop_entry_are_closed_before_replay(self):
        """旧恢复接口明确退役；平台三动作即使有历史成功回执也拒绝，管理员目录保留。"""
        from revival.administration import recover_mfa
        with self.assertRaises(Fault) as caught:
            recover_mfa(self.s, 'owner', self.password, 'unused-old-recovery')
        self.assertEqual(caught.exception.code, 'AUTH_METHOD_RETIRED')
        for action in ('admin.mfa.reset', 'admin.recovery.rotate'):
            with self.assertRaises(Fault):
                self.command(action, {'admin_id':self.admin['admin_id']})
        account, player = self.player('closed-shop')
        for action in ('shop.purchase', 'player.shop.quote', 'player.shop.purchase'):
            data = self.meta()
            with self.s.db.transaction() as c:
                self.s._remember(c, 'player:'+account, data['idempotency_key'], auth.business_summary(action, data), {'paid':True})
            with self.assertRaises(Fault) as caught:
                self.s.command(player, action, data)
            self.assertEqual(caught.exception.code, 'PLATFORM_SHOP_CLOSED')
        with self.assertRaises(Fault) as caught:
            self.s.query(player, 'player-shop')
        self.assertEqual(caught.exception.code, 'PLATFORM_SHOP_CLOSED')
        self.assertIn('items', self.s.query(self.owner, 'shop-admin'))


@override_settings(NZ_EMAIL_VERIFICATION_REQUIRED=True)
class OpenEmailRegistration(Base):
    """邮箱验证码、ACTIVE账号、角色/钱包初始化必须原子提交，邀请码不再参与。"""
    def setUp(self):
        """Base保留旧合成夹具，本组显式启用实际公开注册策略。"""
        super().setUp()
        self.enabled = override_settings(NZ_EMAIL_VERIFICATION_REQUIRED=True)
        self.enabled.enable(); self.addCleanup(self.enabled.disable)

    def registration(self, email='self-service@example.test', nickname='自助玩家'):
        """为新邮箱申请真实随机验证码，不提供邀请字段或人工审批。"""
        result = request_code(self.s, {'email':email,'purpose':'register'}, 'registration-test')
        return self.meta(email=email, password=self.password, nickname=nickname,
            challenge_id=result['challenge_id'], code=mailing.send_code.call_args.args[1])

    def test_registration_creates_active_verified_character_without_invitation(self):
        """成功即可登录且有角色，同键重试不增加账号、角色或钱包。"""
        data = self.registration()
        result = self.s.register(data)
        self.assertEqual(result['status'], 'ACTIVE')
        self.assertEqual(result, self.s.register(data))
        login = self.s.login(data['email'], self.password)
        self.assertFalse(login['character_required'])
        self.assertEqual(self.s.query(login['token'], 'me')['character']['id'], result['character_id'])
        with self.s.db.transaction() as c:
            self.assertEqual(c.table('invitations').count(), 0)
            self.assertEqual(c.table('characters').count(), 1)

    def test_nickname_or_audit_failure_rolls_back_code_and_account(self):
        """昵称冲突或审计失败不留下账号，不消耗验证码；保持原邮箱和角色完整。"""
        first = self.registration()
        self.s.register(first)
        self.now += 61
        second = self.registration('other-self-service@example.test')
        with self.assertRaises(Fault):
            self.s.register(second)
        with self.s.db.transaction() as c:
            self.assertEqual(c.table('accounts').count(), 1)
            self.assertIsNone(c.select('email_challenges', Q(id=second['challenge_id'])).fetchone()['consumed_at'])
        second['nickname'] = '另一玩家'
        with patch.object(self.s, '_audit', side_effect=DatabaseError('synthetic failure')):
            with self.assertRaises(DatabaseError):
                self.s.register(second)
        self.assertEqual(self.s.register(second)['status'], 'ACTIVE')

    def test_active_verified_onboarding_and_disabled_are_distinct(self):
        """旧ACTIVE verified无角色允许补建；停用账号不因邮箱证明被激活。"""
        data = self.registration()
        result = self.s.register(data)
        with self.s.db.transaction() as c:
            c.delete('wallets', Q(account_id=result['account_id']))
            c.delete('characters', Q(account_id=result['account_id']))
        login = self.s.login(data['email'], self.password)
        self.assertTrue(login['character_required'])
        self.command('character.create', {'nickname':'重新准备角色'}, login['token'])
        self.command('user.disable', {'target_account_id':result['account_id']})
        self.now += 61
        challenge = request_code(self.s, {'email':data['email'],'purpose':'verify'}, 'registration-test')
        recover(self.s, dict(email=data['email'], challenge_id=challenge['challenge_id'], code=mailing.send_code.call_args.args[1]), verify_only=True)
        with self.assertRaises(Fault):
            self.s.login(data['email'], self.password)

    def test_existing_pending_login_verify_and_reset_never_replace_admin_approval(self):
        """历史待审即使已验证邮箱，登录/补验/密码恢复也不改变状态；仍需管理员显式批准。"""
        data = self.registration()
        account = self.s.register(data)
        with self.s.db.transaction() as c:
            c.update('accounts', Q(id=account['account_id']), {'status':'PENDING_REVIEW'})
        with self.assertRaises(Fault) as caught:
            self.s.login(data['email'], self.password)
        self.assertEqual(caught.exception.code, 'ACCOUNT_STATE')
        current_password = self.password
        for purpose in ('verify', 'reset'):
            self.now += 61
            challenge = request_code(self.s, {'email':data['email'], 'purpose':purpose}, 'pending-preservation-test')
            proof = dict(email=data['email'], challenge_id=challenge['challenge_id'], code=mailing.send_code.call_args.args[1])
            if purpose == 'reset':
                current_password = 'pending-reset-password-123'
                proof['password'] = current_password
            recover(self.s, proof, verify_only=purpose == 'verify')
            with self.s.db.transaction() as c:
                self.assertEqual(c.select('accounts', Q(id=account['account_id'])).fetchone()['status'], 'PENDING_REVIEW')
                self.assertEqual(c.select('characters', Q(account_id=account['account_id'])).fetchone()['id'], account['character_id'])
            with self.assertRaises(Fault) as caught:
                self.s.login(data['email'], current_password)
            self.assertEqual(caught.exception.code, 'ACCOUNT_STATE')
        self.command('user.approve', {'target_account_id':account['account_id']})
        self.assertFalse(self.s.login(data['email'], current_password)['character_required'])

    def test_active_onboarding_requires_verified_current_email(self):
        """ACTIVE无角色仍须证明当前邮箱，旧未验证绑定不能直接初始化角色或资产。"""
        data = self.registration()
        account = self.s.register(data)
        with self.s.db.transaction() as c:
            c.delete('wallets', Q(account_id=account['account_id']))
            c.delete('characters', Q(account_id=account['account_id']))
            c.update('player_emails', Q(account_id=account['account_id']), {'verified_at':None})
        session = self.s.login(data['email'], self.password)
        with self.assertRaises(Fault) as caught:
            self.command('character.create', {'nickname':'需邮箱证明的角色'}, session['token'])
        self.assertEqual(caught.exception.code, 'EMAIL_UNVERIFIED')
        self.now += 61
        challenge = request_code(self.s, {'email':data['email'], 'purpose':'verify'}, 'onboarding-verification-test')
        recover(self.s, dict(email=data['email'], challenge_id=challenge['challenge_id'],
                             code=mailing.send_code.call_args.args[1]), verify_only=True)
        self.assertEqual(self.command('character.create', {'nickname':'需邮箱证明的角色'}, session['token'])['state'], 'READY')
