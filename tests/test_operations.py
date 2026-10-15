"""真实 MySQL 验证管理员保护、秘密脱敏、持久批次和复核的事务边界。"""
import json
from django.db.models import Q
from revival.administration import recover_mfa
from revival.batching import run_batches
from revival.security import otp
from revival.service import Fault, Service
from .test_control_plane import Base


class OperationsTests(Base):
    """复用真实账号与时钟，测试安全及重启场景而非仅检查字段存在。"""
    def step(self, **data):
        """为下一次高风险命令提供新 TOTP，避免测试绕过真实再认证。"""
        self.now += 30
        return dict(data, current_password=self.password, otp_code=otp(self.admin['otp_secret'], self.now // 30))

    def test_last_owner_and_permission_are_enforced(self):
        """最后 Owner 的停用/降权回滚，玩家不能通过直接调用创建管理员。"""
        for role, active in [('users', True), ('owner', False)]:
            with self.assertRaises(Fault) as error:
                self.command('admin.update', self.step(admin_id=self.admin['admin_id'], role=role, active=active))
            self.assertEqual(error.exception.code, 'LAST_OWNER')
        _, token = self.player()
        with self.assertRaises(Fault):
            self.command('admin.create', {'login': 'evil-owner', 'role': 'owner', 'new_password': self.password}, token)
        self.assertEqual(len(self.s.query(self.owner, 'admins')), 1)

    def test_admin_secrets_are_one_time_and_role_change_revokes_sessions(self):
        """创建响应丢失重试不泄漏 MFA；角色改变后旧管理员会话立即失效。"""
        data = self.meta(**self.step(login='second-owner', role='owner', new_password=self.password))
        created = self.s.command(self.owner, 'admin.create', data)
        replay = self.s.command(self.owner, 'admin.create', data)
        self.assertNotIn('otp_secret', replay)
        self.assertNotIn('recovery_codes', replay)
        token = self.s.login('second-owner', self.password, kind='admin', otp_code=otp(created['otp_secret'], self.now // 30))['token']
        self.command('admin.update', self.step(admin_id=created['admin_id'], role='auditor', active=True))
        with self.assertRaises(Fault):
            self.s.query(token, 'me')
        with self.s.db.transaction() as c:
            serialized = json.dumps(list(c.select('audit_events')) + list(c.select('requests')))
        self.assertNotIn(created['otp_secret'], serialized)
        self.assertNotIn(created['recovery_codes'][0], serialized)

    def test_recovery_consumes_code_and_revokes_old_session(self):
        """密码加恢复码可恢复 MFA；旧码、旧会话及旧验证码均不再生效。"""
        code = self.admin['recovery_codes'][0]
        with self.assertRaises(Fault):
            recover_mfa(self.s, 'owner', 'wrong-password', code)
        result = recover_mfa(self.s, 'owner', self.password, code)
        with self.assertRaises(Fault):
            recover_mfa(self.s, 'owner', self.password, code)
        with self.assertRaises(Fault):
            self.s.query(self.owner, 'me')
        token = self.s.login('owner', self.password, kind='admin', otp_code=otp(result['otp_secret'], self.now // 30))['token']
        self.assertEqual(self.s.query(token, 'me')['role'], 'owner')

    def test_batch_preview_restart_partial_failure_and_failed_only_retry(self):
        """预览无资产变更；中途重启只处理未完成目标，重试不能扩大到成功目标。"""
        a, _ = self.player('target-a')
        b, _ = self.player('target-b')
        data = {'target_account_ids': [a, 'missing-target', b], 'catalog_version': self.s.catalog['id'],
                'changes': [{'type': 'currency', 'code': 'GP', 'delta': 7}]}
        result = self.command('asset.batch.preview', data)
        self.assertTrue(result['read_only'])
        self.assertEqual(self.balance(a), 0)
        batch = self.command('asset.batch.enqueue', self.step(**data))
        self.assertEqual(run_batches(self.s, 1)['processed'], 1)
        restarted = Service(test_profile=True, clock=lambda: self.now)
        run_batches(restarted)
        run_batches(restarted)
        self.assertEqual((self.balance(a), self.balance(b)), (7, 7))
        view = self.s.query(self.owner, 'batches')[0]
        self.assertEqual(view['status'], 'PARTIAL')
        with self.assertRaises(Fault):
            self.command('asset.batch.retry', self.step(batch_id=batch['batch_id'], target_account_ids=[a]))
        retry = self.command('asset.batch.retry', self.step(batch_id=batch['batch_id']))
        self.assertEqual(retry['frozen_targets'], ['missing-target'])
        run_batches(self.s)
        self.assertEqual(self.balance(a), 7)

    def test_batch_author_revocation_prevents_pending_grants(self):
        """排队后撤销操作者安全版本，尚未执行子项拒绝发放。"""
        account, _ = self.player()
        self.command('asset.batch.enqueue', self.step(target_account_ids=[account], catalog_version=self.s.catalog['id'],
                     changes=[{'type': 'currency', 'code': 'GP', 'delta': 8}]))
        with self.s.db.transaction() as c:
            c.update('admins', Q(id=self.admin['admin_id']), {'security_version': 99})
        run_batches(self.s)
        with self.s.db.transaction() as c:
            self.assertEqual(c.select('wallets', Q(account_id=account, currency='GP')).fetchone()['balance'], 0)
            self.assertEqual(c.select('grant_targets').fetchone()['error'], 'FORBIDDEN')

    def test_manual_review_is_closed_without_rewriting_history(self):
        """已花费发放的人工复核只追加决定，不能改原账本或制造负余额。"""
        account, token = self.player()
        grant = self.grant(account, [{'type': 'currency', 'code': 'GP', 'delta': 500}])
        self.command('shop.purchase', {'offer_key': 'test-rifle-gp', 'catalog_version': self.s.catalog['id']}, token)
        review = self.command('asset.compensate', self.step(operation_id=grant['operation_id'], catalog_version=self.s.catalog['id']))
        self.command('asset.review.resolve', self.step(operation_id=review['operation_id'], resolution='KEEP_ASSETS'))
        self.assertEqual(self.balance(account), 0)
        self.assertEqual(self.s.query(self.owner, 'reviews')[0]['decision']['resolution'], 'KEEP_ASSETS')
        with self.assertRaises(Fault):
            self.command('asset.review.resolve', self.step(operation_id=review['operation_id'], resolution='EXTERNAL_RESOLUTION'))

    def test_worker_audit_failure_rolls_back_and_restart_resumes_once(self):
        """子项审计失败必须同时回滚资产和任务结果，重启后可以安全继续该子项。"""
        from unittest.mock import patch
        account, _ = self.player()
        self.command('asset.batch.enqueue', self.step(target_account_ids=[account], catalog_version=self.s.catalog['id'],
                     changes=[{'type': 'currency', 'code': 'GP', 'delta': 9}]))
        with patch.object(self.s, '_audit', side_effect=RuntimeError('synthetic audit failure')):
            with self.assertRaises(RuntimeError):
                run_batches(self.s)
        self.assertEqual(self.balance(account), 0)
        self.assertEqual(self.s.query(self.owner, 'batches')[0]['targets'][0]['status'], 'PENDING')
        restarted = Service(test_profile=True, clock=lambda: self.now)
        run_batches(restarted)
        run_batches(restarted)
        self.assertEqual(self.balance(account), 9)
