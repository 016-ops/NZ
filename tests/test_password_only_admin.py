"""旧免码管理员入口已退役；普通密码策略与固定邮箱认证统一生效。"""
import os
from unittest.mock import patch
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections
from pathlib import Path
from revival.test_accounts import create_local_test_admin
from revival.security import password_hash
from revival.service import Fault
from .test_control_plane import Base


class PasswordOnlyAdminTests(Base):
    """历史配置、CLI、直接工具调用均不得创建免邮件认证例外。"""
    def test_password_only_environment_cannot_authenticate(self):
        """旧合成账号白名单即使完整匹配，也必须提交真正的一次性邮件码。"""
        config = {'NZ_PASSWORD_ONLY_TEST_ADMIN_ID':self.admin['admin_id'],
                  'NZ_PASSWORD_ONLY_TEST_ADMIN_DATABASE':connections['default'].settings_dict['NAME']}
        with patch.dict(os.environ, config):
            with self.assertRaises(Fault):
                self.s.login('owner', self.password, kind='admin')
            self.assertTrue(self.admin_login()['token'])

    def test_old_creation_paths_have_no_side_effects_or_password_read(self):
        """退役发生在读取标准输入、私有配置、写文件或插入管理员之前。"""
        with patch('builtins.input', side_effect=AssertionError('must not read password')):
            with self.assertRaisesMessage(CommandError, 'AUTH_METHOD_RETIRED'):
                call_command('create_demo_admin', 'demo-unused', password_stdin=True)
        with self.assertRaisesMessage(CommandError, 'AUTH_METHOD_RETIRED'):
            create_local_test_admin('demo-unused', password='abcdef', password_only=True)
        with self.s.db.transaction() as c:
            self.assertFalse(c.table('admins').filter(login='demo-unused').exists())
        self.assertFalse((Path(self.directory.name)/'runtime/demo-admin.json').exists())

    def test_normal_password_policy_remains_strong(self):
        """邮件验证不会放宽普通注册或管理员创建的密码强度。"""
        with self.assertRaises(ValueError):
            password_hash('abcdef')
        with self.assertRaises(Fault):
            self.s.register(self.meta(login='short-user', password='abcdef',
                invitation=self.invitation()['invitation_token']))
