"""真实 MySQL 验证邮箱所有权、密码恢复及并发边界；投递通过替身捕获，绝不向真实玩家发送测试。"""
from unittest.mock import patch
from django.db import DatabaseError
from django.db.models import Q
from django.test import override_settings, Client
from django.test import SimpleTestCase
from .test_control_plane import Base
from .support import DatabasePool
from revival.email_verification import request_code, recover
from revival.mailing import MailUnavailable
from revival.service import Fault, Service
from revival.launcher import issue, redeem, identity
from revival.security import token_hash
from adapters.email_protocol import PROTOCOL


class EmailVerification(Base):
    """所有码都走生产挑战服务；仅 SMTP 投递替换，摘要、时间、事务及会话均在真实 MySQL 验证。"""
    def setUp(self):
        """启用与生产一致的强制验证，捕获本例邮件，不使用固定万能验证码。"""
        super().setUp()
        enabled = override_settings(NZ_EMAIL_VERIFICATION_REQUIRED=True, NZ_PROFILE='synthetic', NZ_PORT=8765)
        enabled.enable(); self.addCleanup(enabled.disable)
        config = patch('revival.mailing.configured', return_value=True)
        config.start(); self.addCleanup(config.stop)
        sender = patch('revival.mailing.send_code')
        self.sender = sender.start(); self.addCleanup(sender.stop)
        self.email = 'ownership@example.test'
        self.invite = self.invitation(10)['invitation_token']

    def challenge(self, purpose='register', email=None):
        """通过生产发码入口取得挑战 ID，并从本例 SMTP 替身取得仅用于断言的实际随机码。"""
        address = email or self.email
        result = request_code(self.s, {'email':address, 'purpose':purpose, 'invitation':self.invite}, '127.0.0.1')
        return {'email':address, 'challenge_id':result['challenge_id'], 'code':self.sender.call_args.args[1]}

    def register_verified(self):
        """有效邮箱码即可开通并初始化角色，旧邀请字段即使存在也不消耗名额。"""
        data = self.meta(**self.challenge(), password=self.password, invitation=self.invite, nickname='邮箱恢复角色')
        account = self.s.register(data)
        self.command('user.approve', {'target_account_id':account['account_id']})
        token = self.s.login(self.email, self.password)['token']
        return account, token, data

    def test_registration_requires_code_replays_and_opens_without_approval(self):
        """无验证禁止注册；成功一次性建角色即登录，同键不重建且不消耗旧邀请。"""
        with self.assertRaises(Fault):
            self.s.register(self.meta(email=self.email, password=self.password, invitation=self.invite))
        data = self.meta(**self.challenge(), password=self.password, invitation=self.invite, nickname='验证码玩家')
        first = self.s.register(data)
        self.assertTrue(first['email_verified'])
        self.assertEqual(first, self.s.register(data))
        self.assertEqual(self.s.query(self.owner, 'invitations')[0]['uses'], 0)
        self.assertFalse(self.s.login(self.email, self.password)['character_required'])
        with self.assertRaises(Fault):
            self.s.register(self.meta(**{k:v for k,v in data.items() if k not in ('idempotency_key','request_id','reason')}))

    def test_attempts_expiry_purpose_address_and_resend_persist(self):
        """错误次数重建服务后仍保留；目的和地址隔离；重发旧码及过期码均失效。"""
        data = self.challenge()
        bad = dict(data, code='999999' if data['code'] != '999999' else '111111', password=self.password, invitation=self.invite)
        for _ in range(5):
            with self.assertRaises(Fault): self.s.register(self.meta(**bad))
        with self.s.db.transaction() as c:
            self.assertEqual(c.select('email_challenges', Q(id=data['challenge_id'])).fetchone()['attempts'], 5)
        self.s = Service(test_profile=True, clock=lambda:self.now)
        with self.assertRaises(Fault): self.s.register(self.meta(**data, password=self.password, invitation=self.invite))
        self.now += 61
        second = self.challenge()
        with self.assertRaises(Fault): recover(self.s, dict(second,password=self.password))
        with self.assertRaises(Fault): self.s.register(self.meta(**dict(second,email='other@example.test'), password=self.password, invitation=self.invite))
        self.now += 601
        with self.assertRaises(Fault): self.s.register(self.meta(**second, password=self.password, invitation=self.invite))

    def test_reset_revokes_web_and_game_preserves_character_assets(self):
        """恢复密码同事务撤销旧 Web/游戏授权，角色映射与账本不变，重登使用新密码。"""
        account, token, _ = self.register_verified()
        self.command('character.create', {'nickname':'邮箱恢复角色'}, token)
        self.grant(account['account_id'], [{'type':'currency','code':'GP','delta':7}])
        issued = issue(self.s, token, self.meta(protocol=PROTOCOL,client_sha256='a'*64,verifier_sha256=token_hash('v'*43)))
        game = redeem(self.s, dict(protocol=PROTOCOL,client_sha256='a'*64,ticket=issued['ticket'],verifier='v'*43))
        self.now += 61
        data = dict(self.challenge('reset'),password='a-new-password-12345')
        self.assertTrue(recover(self.s, data)['password_reset'])
        for operation in (lambda:self.s.query(token,'me'), lambda:identity(self.s,game['game_token'],dict(protocol=PROTOCOL,client_sha256='a'*64)),
                          lambda:self.s.login(self.email,self.password),lambda:recover(self.s,data)):
            with self.assertRaises(Fault): operation()
        fresh = self.s.login(self.email,data['password'])['token']
        view = self.s.query(fresh,'me')
        self.assertEqual(view['game_id'],account['game_id'])
        self.assertEqual(view['character']['nickname'],'邮箱恢复角色')
        self.assertEqual(self.balance(account['account_id']),7)

    def test_concurrent_reset_consumes_once_and_audit_rollback(self):
        """并发恢复只成功一次；审计失败不能留密码/消费半事务，失败后原码仍能重试。"""
        self.register_verified(); self.now += 61
        data = dict(self.challenge('reset'),password='changed-password-98765')
        with patch.object(self.s,'_audit',side_effect=DatabaseError('test rollback')):
            with self.assertRaises(DatabaseError): recover(self.s,data)
        self.s.login(self.email,self.password)
        def attempt(_):
            """在独立数据库连接中竞争消费同一个恢复码，返回业务码供唯一性断言。"""
            try: return recover(self.s,data)['password_reset']
            except Fault as caught: return caught.code
        with DatabasePool(max_workers=2) as pool:
            results = list(pool.map(attempt,range(2)))
        self.assertEqual(results.count(True),1)

    def test_reset_response_replay_survives_service_restart_without_reapplying(self):
        """同键相同请求恢复丢失响应，不二次撤销会话；换密码或验证码同键冲突，新键不能复用旧码。"""
        account, _, _ = self.register_verified(); self.now += 61
        data = self.meta(**self.challenge('reset'),password='recover-retry-password-123')
        first = recover(self.s,data)
        fresh = self.s.login(self.email,data['password'])['token']
        self.s = Service(test_profile=True,clock=lambda:self.now)
        self.assertEqual(recover(self.s,data),first)
        self.s.query(fresh,'me')
        with self.s.db.transaction() as c:
            self.assertEqual(c.table('audit_events').filter(action='account.password.recover',target=account['account_id']).count(),1)
            request=c.select('requests',Q(key=data['idempotency_key'])).fetchone()
            self.assertNotIn(data['password'],str(request))
            self.assertNotIn(data['code'],request['result'])
        for changed in (dict(data,password='a-different-password-456'),dict(data,code='abcdef')):
            with self.assertRaises(Fault) as caught:
                recover(self.s,changed)
            self.assertEqual(caught.exception.code,'IDEMPOTENCY_CONFLICT')
        with self.assertRaises(Fault):
            recover(self.s,dict(data,idempotency_key='another-recovery-attempt'))

    def test_same_key_concurrent_reset_returns_one_durable_result(self):
        """同键并发均取得同一成功结果，密码、安全版本及审计只有一次实际变化。"""
        account, _, _ = self.register_verified(); self.now += 61
        data = self.meta(**self.challenge('reset'),password='concurrent-retry-password-123')
        with self.s.db.transaction() as c:
            before=c.select('accounts',Q(id=account['account_id'])).fetchone()['security_version']
        with DatabasePool(max_workers=2) as pool:
            results=list(pool.map(lambda _:recover(self.s,data),range(2)))
        self.assertEqual(results[0],results[1])
        with self.s.db.transaction() as c:
            self.assertEqual(c.select('accounts',Q(id=account['account_id'])).fetchone()['security_version'],before+1)
            self.assertEqual(c.table('audit_events').filter(action='account.password.recover',target=account['account_id']).count(),1)

    def test_recovery_does_not_activate_disabled_account_and_unknown_has_same_response(self):
        """恢复不解除停用；未知邮箱同样投递但无可恢复账号，响应结构不泄露存在性。"""
        account, _, _ = self.register_verified()
        self.command('user.disable',{'target_account_id':account['account_id']})
        self.now += 61
        known = self.challenge('reset')
        unknown = self.challenge('reset','unknown@example.test')
        self.assertEqual(set(known),set(unknown))
        recover(self.s,dict(known,password='disabled-new-password-123'))
        with self.assertRaises(Fault): self.s.login(self.email,'disabled-new-password-123')
        with self.assertRaises(Fault): recover(self.s,dict(unknown,password='unknown-password-1234'))

    def test_delivery_failure_quota_and_no_plaintext_persistence(self):
        """失败也占冷却额度；服务商异常不入库，重启不能绕过额度，验证码正文不持久化。"""
        self.sender.side_effect = MailUnavailable('MAIL_DELIVERY_FAILED')
        result = request_code(self.s,dict(email=self.email,purpose='register',invitation=self.invite),'127.0.0.1')
        with self.s.db.transaction() as c:
            row = c.select('email_challenges',Q(id=result['challenge_id'])).fetchone()
            self.assertEqual(row['status'],'FAILED')
            self.assertEqual(len(row['digest']),64)
        with self.assertRaises(Fault) as caught: self.challenge()
        self.assertEqual(caught.exception.code,'RATE_LIMIT')
        for _ in range(4):
            self.now += 61; self.challenge()
        self.now += 61
        with self.assertRaises(Fault): self.challenge()

    def test_existing_binding_verify_and_email_replacement_invalidate_old_reset(self):
        """迁移前邮箱可以补验；管理员换绑提升安全版本，旧邮箱码不能重置新身份。"""
        account, _, _ = self.register_verified(); self.now += 61
        with self.s.db.transaction() as c:
            c.update('player_emails',Q(account_id=account['account_id']),{'verified_at':None})
        data = self.challenge('verify')
        self.assertTrue(recover(self.s,data,verify_only=True)['email_verified'])
        self.now += 61
        old = dict(self.challenge('reset'),password='should-not-work-12345')
        self.command('user.email.set',dict(target_account_id=account['account_id'],email='new@example.test',**self.step_up()))
        with self.assertRaises(Fault): recover(self.s,old)

    def test_http_boundary_rejects_legacy_bypass_and_missing_provider(self):
        """公共旧注册接口不能绕过邮箱；发信服务缺失必须明确失败，不能假称已发送。"""
        client = Client(HTTP_HOST='127.0.0.1:8765')
        payload = self.meta(login='legacy-bypass',password=self.password,invitation=self.invite)
        response = client.post('/api/register',payload,content_type='application/json',HTTP_X_NZ_REQUEST='1')
        self.assertEqual(response.status_code,403)
        with patch('revival.mailing.configured',return_value=False):
            response = client.post('/api/player/email-code',dict(email=self.email,purpose='reset'),content_type='application/json',HTTP_X_NZ_REQUEST='1')
        self.assertEqual(response.status_code,503)

    def test_concurrent_send_reserves_only_one_quota_slot(self):
        """两个工作线程同时发码只有一个获准投递，持久冷却不依赖单进程内存。"""
        def attempt(_):
            """独立连接争用同一邮箱发送名额，只返回获准状态或业务限流码。"""
            try:
                request_code(self.s,dict(email=self.email,purpose='register',invitation=self.invite),'127.0.0.1')
                return True
            except Fault as caught: return caught.code
        with DatabasePool(max_workers=2) as pool:
            results=list(pool.map(attempt,range(2)))
        self.assertEqual(results.count(True),1)
        self.assertEqual(results.count('RATE_LIMIT'),1)
        self.sender.assert_called_once()

    def test_backup_with_boolean_game_session_roundtrips(self):
        """有真实布尔列值的启动票据及游戏会话必须能在临时 MySQL 库恢复并逐表一致。"""
        from pathlib import Path
        account,token,_=self.register_verified()
        issued=issue(self.s,token,self.meta(protocol=PROTOCOL,client_sha256='a'*64,verifier_sha256=token_hash('v'*43)))
        redeem(self.s,dict(protocol=PROTOCOL,client_sha256='a'*64,ticket=issued['ticket'],verifier='v'*43))
        result=self.s.db.backup(Path(self.directory.name)/'email-boolean-backup.sql')
        self.assertTrue(result['verified'])
        self.assertEqual(result['tables']['game_sessions'],1)
        self.assertEqual(result['tables']['email_challenges'],1)


class AliyunTransport(SimpleTestCase):
    """不实际发信，验证 SMTP 必须使用证书校验 TLS、单收件人及脱敏失败合同。"""
    @override_settings(NZ_MAIL_PROVIDER='aliyun-smtp',NZ_MAIL_HOST='smtpdm.aliyun.com',NZ_MAIL_PORT=465,
                       NZ_MAIL_FROM='noreply@example.test',NZ_MAIL_USERNAME='noreply@example.test',
                       NZ_MAIL_PASSWORD='test-smtp-secret',NZ_MAIL_SENDER_NAME='NZ Test')
    def test_tls_single_recipient_and_failure_redaction(self):
        """生产路径显式启用主机名和证书验证，连接异常只返回固定码，不含邮件或密码。"""
        import ssl
        from revival import mailing
        with patch('revival.mailing.smtplib.SMTP_SSL') as transport:
            transport.return_value.__enter__.return_value.send_message.return_value={}
            mailing.send_code('recipient@example.test','123456','register')
            context=transport.call_args.kwargs['context']
            self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)
            self.assertEqual(transport.call_args.args,('smtpdm.aliyun.com',465))
            self.assertEqual(transport.return_value.__enter__.return_value.send_message.call_args.kwargs['to_addrs'],['recipient@example.test'])
        with patch('revival.mailing.smtplib.SMTP_SSL',side_effect=OSError('private failure text')):
            with self.assertRaises(MailUnavailable) as caught:
                mailing.send_code('recipient@example.test','123456','reset')
        self.assertEqual(str(caught.exception),'MAIL_DELIVERY_FAILED')

    @override_settings(NZ_MAIL_PROVIDER='aliyun-smtp',NZ_MAIL_HOST='attacker.example.test',NZ_MAIL_PORT=25,
                       NZ_MAIL_FROM='noreply@example.test',NZ_MAIL_USERNAME='noreply@example.test',NZ_MAIL_PASSWORD='test-secret')
    def test_rejects_unknown_endpoint_and_plaintext(self):
        """错误区域或明文端口在任何认证材料发出前被拒绝。"""
        from revival import mailing
        with patch('revival.mailing.smtplib.SMTP_SSL') as transport:
            with self.assertRaises(MailUnavailable): mailing.send_code('recipient@example.test','123456','register')
            transport.assert_not_called()
