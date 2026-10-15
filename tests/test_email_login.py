"""独立邮箱及启动交接的真实 MySQL 回归；不以协议测试宣称原客户端大厅通过。"""
import json
import threading
from unittest.mock import patch
from django.db import DatabaseError
from django.db.models import Q
from django.test import override_settings
from django.core.wsgi import get_wsgi_application
from revival import launcher
from revival.security import new_token, normalize_email, token_hash
from revival.service import Fault, Service, canonical
from revival.server import local_server
from .test_control_plane import Base
from .support import DatabasePool
from . import test_http as http_support


class EmailTests(Base):
    """验证邮箱规范化、审批、迁移不丢资产以及数据库唯一性和审计原子性。"""
    def email_player(self, email='player@example.test', character=False):
        """使用邀请创建并批准邮箱玩家；返回账号信息和独立玩家会话。"""
        invitation = self.invitation()
        result = self.s.register(self.meta(email=email, password=self.password, invitation=invitation['invitation_token']))
        self.command('user.approve', {'target_account_id': result['account_id']})
        session = self.s.login(email, self.password)
        if character:
            self.command('character.create', {'nickname': '邮箱角色'}, session['token'])
        return result, session

    def proof(self, token):
        """申请实际绑定随机证明的票据，返回只有启动器内存持有的兑换材料。"""
        verifier = new_token()
        ticket = launcher.issue(self.s, token, self.meta(protocol=launcher.PROTOCOL, client_sha256='a' * 64,
                                                       verifier_sha256=token_hash(verifier)))
        return ticket, {'protocol': launcher.PROTOCOL, 'client_sha256': 'a' * 64,
                        'ticket': ticket['ticket'], 'verifier': verifier}

    def test_email_normalization_boundaries_and_aliases(self):
        """邮箱大小写统一，但加号/点别名不合并；拒绝注入、超长本地名及非 ASCII 本地部分。"""
        self.assertEqual(normalize_email(' Player.Name+Tag@Example.Test '), 'player.name+tag@example.test')
        self.assertNotEqual(normalize_email('a+b@example.test'), normalize_email('ab@example.test'))
        self.assertTrue(normalize_email('a' * 64 + '@example.test'))
        for bad in (None, 'player-a', 'x@localhost', 'x..y@example.test', 'a' * 65 + '@example.test',
                    '用户@example.test', 'x@example.test\r\nBcc:a@example.test', 'x@[127.0.0.1]'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_email(bad)

    def test_long_email_registration_replay_pending_and_persistence(self):
        """长于旧用户名的邮箱可持久化，重放不重复消费邀请，审批前仍拒绝登录。"""
        email = 'long-email-player-name+private-test@example.test'
        invite = self.invitation()
        data = self.meta(email=email, password=self.password, invitation=invite['invitation_token'])
        result = self.s.register(data)
        self.assertEqual(self.s.register(data), result)
        with self.assertRaises(Fault):
            self.s.login(email, self.password)
        self.command('user.approve', {'target_account_id': result['account_id']})
        restored = Service(test_profile=True, clock=lambda: self.now)
        token = restored.login(email.upper(), self.password)['token']
        me = restored.query(token, 'me')
        self.assertEqual(me['email'], email)
        self.assertFalse(me['email_verified'])
        self.assertEqual(me['game_id'], result['game_id'])
        self.assertEqual(self.s.query(self.owner, 'invitations')[0]['uses'], 1)
        with self.assertRaises(Fault):
            self.s.login(me['login'], self.password)
        with self.assertRaises(Fault):
            self.s.login(email, 'wrong-password')

    def test_concurrent_case_variants_create_one_account_and_one_invitation_use(self):
        """独立 MySQL 连接并发注册大小写不同邮箱，唯一约束和事务只允许一个成功。"""
        invite = self.invitation(uses=2)
        def register(email):
            """线程只提交真实注册事务，返回业务结果或预期唯一冲突。"""
            try:
                return self.s.register(self.meta(email=email, password=self.password, invitation=invite['invitation_token']))
            except Fault as error:
                return error.code
        with DatabasePool(max_workers=2) as pool:
            results = list(pool.map(register, ['Player@Example.Test', 'player@example.test']))
        self.assertEqual(sum(isinstance(row, dict) for row in results), 1)
        self.assertIn('CONFLICT', results)
        with self.s.db.transaction() as c:
            self.assertEqual(c.count('accounts')[0][0], 1)
            self.assertEqual(c.count('player_emails')[0][0], 1)
        self.assertEqual(self.s.query(self.owner, 'invitations')[0]['uses'], 1)

    def test_email_retry_digest_is_not_a_fast_database_only_password_verifier(self):
        """注册幂等仍可重放，但新邮箱记录不能沿用可通过数据库已知字段快速猜测密码的摘要。"""
        invitation = self.invitation()
        email = 'digest@example.test'
        data = self.meta(email=email, password=self.password, invitation=invitation['invitation_token'])
        result = self.s.register(data)
        login = 'm_' + token_hash(email)[:30]
        old_summary = {'login': login, 'email': email, 'password_digest': token_hash(self.password),
                       'invite_hash': token_hash(invitation['invitation_token'])}
        with self.s.db.transaction() as c:
            stored = c.select('requests', Q(scope='register:' + login, key=data['idempotency_key'])).fetchone()
        self.assertNotEqual(stored['payload_hash'], token_hash(canonical(old_summary)))
        self.assertEqual(self.s.register(data), result)

    def test_legacy_binding_preserves_game_id_assets_and_revokes_old_alias_and_session(self):
        """旧账号改邮箱沿用账号/角色/资产；旧会话与旧用户名不能继续认证。"""
        account, token = self.player()
        self.grant(account, [{'type': 'currency', 'code': 'GP', 'delta': 125}])
        previous = self.s.query(token, 'me')
        self.command('user.email.set', {'target_account_id': account, 'email': 'Bound@Example.Test', **self.step_up()})
        with self.assertRaises(Fault):
            self.s.query(token, 'me')
        with self.assertRaises(Fault):
            self.s.login('player-a', self.password)
        current = self.s.query(self.s.login('bound@example.test', self.password)['token'], 'me')
        for field in ('id', 'game_id', 'character', 'wallets', 'inventory', 'equipment'):
            self.assertEqual(current[field], previous[field])
        self.assertEqual(self.balance(account), 125)
        self.assertEqual(self.s.query(self.owner, 'users', search='bound@')[0]['id'], account)

    def test_binding_conflict_permission_and_audit_failure_roll_back(self):
        """邮箱不能抢占其他玩家身份，玩家不能绑定他人邮箱，审计失败不能留下半次迁移。"""
        first, player = self.email_player()
        second, _ = self.player('legacy-two')
        with self.assertRaises(Fault):
            self.command('user.email.set', {'target_account_id': second, 'email': first['email']}, player['token'])
        with self.assertRaises(Fault):
            self.command('user.email.set', {'target_account_id': second, 'email': first['email'], **self.step_up()})
        with patch.object(self.s, '_audit', side_effect=DatabaseError('audit unavailable')):
            with self.assertRaises(DatabaseError):
                self.command('user.email.set', {'target_account_id': second, 'email': 'second@example.test', **self.step_up()})
        self.assertTrue(self.s.login('legacy-two', self.password)['token'])
        with self.s.db.transaction() as c:
            self.assertIsNone(c.select('player_emails', Q(account_id=second)).fetchone())

    def test_ticket_proof_protocol_and_privilege_separation(self):
        """正确邮箱会话可交接本地身份；错误证明/版本及跨权限使用全部拒绝。"""
        account, session = self.email_player(character=True)
        _, data = self.proof(session['token'])
        for changed in ({'verifier': new_token()}, {'client_sha256': 'b' * 64}, {'protocol': 'tencent'}):
            with self.assertRaises(Fault):
                launcher.redeem(self.s, dict(data, **changed))
        game = launcher.redeem(self.s, data)
        identity = launcher.identity(self.s, game['game_token'], data)
        self.assertEqual(identity['account_id'], account['account_id'])
        self.assertEqual(identity['game_id'], account['game_id'])
        self.assertEqual(identity['character']['nickname'], '邮箱角色')
        self.assertNotIn('email', identity)
        self.assertFalse(identity['native_launch_ready'])
        with self.assertRaises(Fault):
            self.s.query(game['game_token'], 'me')
        with self.assertRaises(Fault):
            launcher.identity(self.s, session['token'], data)
        with self.assertRaises(Fault):
            self.proof(self.owner)
        _, legacy = self.player('legacy-ticket')
        with self.assertRaises(Fault):
            self.proof(legacy)

    def test_concurrent_redemption_and_restart_never_replay_secret(self):
        """并发兑换仅一人取得新凭据，重建服务仍识别已消费；数据库不保存明文秘密。"""
        _, session = self.email_player()
        _, data = self.proof(session['token'])
        def redeem(_):
            """使用独立连接重放同票据，成功凭据只出现在这一调用的内存结果。"""
            try:
                return launcher.redeem(self.s, data)
            except Fault as error:
                return error.code
        with DatabasePool(max_workers=6) as pool:
            results = list(pool.map(redeem, range(6)))
        successes = [row for row in results if isinstance(row, dict)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(results.count('TICKET_CONSUMED'), 5)
        restarted = Service(test_profile=True, clock=lambda: self.now)
        with self.assertRaises(Fault) as error:
            launcher.redeem(restarted, data)
        self.assertEqual(error.exception.code, 'TICKET_CONSUMED')
        self.assertTrue(launcher.identity(restarted, successes[0]['game_token'], data)['account_id'])
        with self.s.db.transaction() as c:
            stored = json.dumps([*c.select('launch_tickets'), *c.select('game_sessions'), *c.select('audit_events')])
        for secret in (data['ticket'], data['verifier'], session['token'], successes[0]['game_token'], self.password):
            self.assertNotIn(secret, stored)

    def test_ticket_expiry_replacement_game_session_revocation_and_client_binding(self):
        """替换和过期票据拒绝；新游戏会话撤销旧会话，注销源会话立即使交接失效。"""
        _, session = self.email_player()
        _, old = self.proof(session['token'])
        _, current = self.proof(session['token'])
        with self.assertRaises(Fault):
            launcher.redeem(self.s, old)
        self.now += 60
        with self.assertRaises(Fault):
            launcher.redeem(self.s, current)
        _, data = self.proof(session['token'])
        first = launcher.redeem(self.s, data)
        with self.assertRaises(Fault):
            launcher.identity(self.s, first['game_token'], dict(data, client_sha256='b' * 64))
        _, data = self.proof(session['token'])
        second = launcher.redeem(self.s, data)
        with self.assertRaises(Fault):
            launcher.identity(self.s, first['game_token'], data)
        with self.s.db.transaction() as c:
            c.update('sessions', Q(token_hash=token_hash(session['token'])), {'revoked': 1})
        with self.assertRaises(Fault):
            launcher.identity(self.s, second['game_token'], data)

    def test_ban_relogin_and_game_expiry_are_rechecked(self):
        """封禁、安全版本、重登撤销和游戏会话期限都必须在每次授权重新检查。"""
        account, session = self.email_player()
        _, data = self.proof(session['token'])
        game = launcher.redeem(self.s, data)
        new = self.s.login(account['email'], self.password)
        with self.assertRaises(Fault):
            launcher.identity(self.s, game['game_token'], data)
        _, data = self.proof(new['token'])
        game = launcher.redeem(self.s, data)
        self.command('sanction.create', {'target_account_id': account['account_id']})
        with self.assertRaises(Fault):
            launcher.identity(self.s, game['game_token'], data)
        with self.assertRaises(Fault):
            self.s.login(account['email'], self.password)

    def test_redemption_and_issue_audit_failures_are_atomic(self):
        """审计写失败回滚票据消费、会话和旧票据撤销；恢复后原票据仍能成功兑换。"""
        _, session = self.email_player()
        _, data = self.proof(session['token'])
        with patch.object(self.s, '_audit', side_effect=DatabaseError('audit unavailable')):
            with self.assertRaises(DatabaseError):
                self.proof(session['token'])
            with self.assertRaises(DatabaseError):
                launcher.redeem(self.s, data)
        with self.s.db.transaction() as c:
            row = c.select('launch_tickets', Q(digest=token_hash(data['ticket']))).fetchone()
            self.assertIsNone(row['consumed_at'])
            self.assertFalse(row['revoked'])
            self.assertEqual(c.count('game_sessions')[0][0], 0)
        self.assertTrue(launcher.redeem(self.s, data)['game_token'])


class EmailHttpTests(Base):
    """经真实 Waitress/Django HTTP 验证邮箱登录与启动交接，不以内部函数冒充网络通过。"""
    request = http_support.HttpTests.request

    def setUp(self):
        """启动随机回环端口，与部署端相同的 Host、Origin 和严格 JSON 请求边界。"""
        super().setUp()
        self.server = local_server(get_wsgi_application(), port=0)
        self.overrides = override_settings(NZ_PROFILE='synthetic', NZ_PORT=int(self.server.effective_port))
        self.overrides.enable()
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()

    def tearDown(self):
        """关闭本例 HTTP 服务及独立数据库连接，不操作已有本机或 DEV 实例。"""
        self.server.task_dispatcher.shutdown()
        self.server.close()
        self.thread.join(timeout=5)
        self.overrides.disable()
        super().tearDown()

    def test_real_http_email_approval_handoff_and_logout(self):
        """通过真实 HTTP 完成邮箱注册、审批后登录、单次兑换、身份读取和注销撤销。"""
        invite = self.invitation()
        data = self.meta(email='http-player@example.test', password=self.password, invitation=invite['invitation_token'])
        status, body, _ = self.request('/api/player/register', data)
        self.assertEqual(status, 200)
        account = json.loads(body)
        self.assertEqual(self.request('/api/player/login', {'email': data['email'], 'password': self.password})[0], 403)
        self.command('user.approve', {'target_account_id': account['account_id']})
        status, body, _ = self.request('/api/player/login', {'email': data['email'].upper(), 'password': self.password, 'kind': 'admin'})
        self.assertEqual(status, 200)
        session = json.loads(body)
        self.assertEqual(session['kind'], 'player')
        verifier = new_token()
        data = self.meta(protocol=launcher.PROTOCOL, client_sha256='a' * 64, verifier_sha256=token_hash(verifier))
        headers = {'Authorization': 'Bearer ' + session['token']}
        status, body, _ = self.request('/api/launcher/tickets', data, headers)
        self.assertEqual(status, 200)
        ticket = json.loads(body)
        proof = dict(protocol=launcher.PROTOCOL, client_sha256='a' * 64, verifier=verifier, ticket=ticket['ticket'])
        status, body, _ = self.request('/api/launcher/redeem', proof)
        self.assertEqual(status, 200)
        game = json.loads(body)
        game_headers = {'Authorization': 'Bearer ' + game['game_token']}
        status, body, _ = self.request('/api/game/session', proof, game_headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['game_id'], account['game_id'])
        self.assertEqual(self.request('/api/views/me', headers=game_headers)[0], 401)
        self.assertEqual(self.request('/api/launcher/redeem', proof)[0], 409)
        self.assertEqual(self.request('/api/logout', {}, headers)[0], 200)
        self.assertEqual(self.request('/api/game/session', proof, game_headers)[0], 401)

    def test_email_endpoint_rejects_username_bypass_and_preserves_native_boundary(self):
        """专用邮箱入口不能退回用户名/QQ 凭据，状态接口明确原生桥尚未实现。"""
        self.assertEqual(self.request('/api/player/register', self.meta(login='old-name', password=self.password))[0], 400)
        self.assertEqual(self.request('/api/player/login', {'email': 'old-name', 'password': self.password})[0], 401)
        self.assertEqual(self.request('/api/player/login', {}, {'Origin': 'https://foreign.invalid'})[0], 403)
        status, body, _ = self.request('/api/launcher/status')
        self.assertEqual(status, 200)
        state = json.loads(body)
        self.assertFalse(state['qq_required'])
        self.assertFalse(state['native_launch_ready'])
        self.assertEqual(state['native_client_adapter'], 'UNIMPLEMENTED')
