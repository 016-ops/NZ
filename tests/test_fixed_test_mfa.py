"""旧固定码配置已退役；无论合成profile还是绑定账号均不能绕过邮件挑战。"""
import os
from unittest.mock import patch
from django.db import connections
from django.core.management import call_command
from django.core.management.base import CommandError
from revival.security import token_hash
from revival.service import Fault
from .test_control_plane import Base


class FixedTestMfaTests(Base):
    """遗留环境变量和CLI必须失败关闭，不提供可重放固定码认证。"""
    def test_old_fixed_environment_cannot_authenticate_or_authorize(self):
        """数据库和账号完全匹配仍拒绝旧码；权限检查不能由本地环境变量绕过。"""
        config = {'NZ_FIXED_TEST_ADMIN_ID':self.admin['admin_id'],
                  'NZ_FIXED_TEST_ADMIN_DATABASE':connections['default'].settings_dict['NAME'],
                  'NZ_FIXED_TEST_ADMIN_OTP_HASH':token_hash('987654')}
        account, _ = self.player()
        with patch.dict(os.environ, config):
            with self.assertRaises(Fault):
                self.s.login('owner', self.password, kind='admin', otp_code='987654')
            data = self.meta(target_account_id=account, catalog_version='test-only-v1',
                changes=[{'type':'currency','code':'GP','delta':15000}],
                current_password=self.password, otp_code='987654')
            with self.assertRaises(Fault):
                self.s.command(self.owner, 'asset.grant', data)
        self.assertEqual(self.balance(account), 0)

    def test_fixed_cli_is_retired_before_any_account_or_file_write(self):
        """旧命令保留清晰错误，不能重新安装固定码账号。"""
        with self.assertRaisesMessage(CommandError, 'AUTH_METHOD_RETIRED'):
            call_command('create_test_admin', 'unused-test-admin')
        with self.s.db.transaction() as c:
            self.assertFalse(c.table('admins').filter(login='unused-test-admin').exists())
