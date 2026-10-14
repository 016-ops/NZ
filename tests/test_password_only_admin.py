"""单账号免登录 MFA 例外：只在合成库有效，不放宽注册、密码、停用或高风险再认证。"""
import os
from unittest.mock import patch
from django.db import connections
from django.test import override_settings
from django.core.management.base import CommandError
from pathlib import Path
import json
from revival.test_accounts import create_local_test_admin
from revival.security import password_hash
from revival.service import Fault
from .test_control_plane import Base


class PasswordOnlyAdminTests(Base):
    """验证指定本地测试管理员仅登录免 MFA，仍执行密码、状态和高风险再认证。"""
    def config(self):
        """测试专属账号 ID 和测试数据库名模拟本地私有配置，不影响持久用户测试账号。"""
        return {'NZ_PASSWORD_ONLY_TEST_ADMIN_ID':self.admin['admin_id'],
                'NZ_PASSWORD_ONLY_TEST_ADMIN_DATABASE':connections['default'].settings_dict['NAME']}

    def test_password_only_login_checks_password_and_active_state(self):
        """配置账号可省略验证码重复登录，错误密码或停用仍拒绝。"""
        with patch.dict(os.environ,self.config()):
            self.assertTrue(self.s.login('owner',self.password,kind='admin')['token'])
            with self.assertRaises(Fault): self.s.login('owner','wrong-password',kind='admin')
            with self.s.db.transaction() as repository:
                repository.table('admins').filter(pk=self.admin['admin_id']).update(active=0)
            with self.assertRaises(Fault): self.s.login('owner',self.password,kind='admin')

    def test_scope_cannot_cover_other_admin_database_or_current_profile(self):
        """账号、数据库和 profile 全部匹配才放行，不接受客户端传入开关。"""
        self.extra_admin('users')
        config=self.config()
        with patch.dict(os.environ,config):
            with self.assertRaises(Fault): self.s.login('users',self.password,kind='admin')
            self.s.test_profile=False
            with self.assertRaises(Fault): self.s.login('owner',self.password,kind='admin')
            self.s.test_profile=True
        config['NZ_PASSWORD_ONLY_TEST_ADMIN_DATABASE']='another_database'
        with patch.dict(os.environ,config):
            with self.assertRaises(Fault): self.s.login('owner',self.password,kind='admin')

    def test_short_password_exception_is_not_registration_policy(self):
        """六位密码仅供受控测试账号创建，普通注册及密码哈希默认仍要求十二位。"""
        with self.assertRaises(ValueError): password_hash('abcdef')
        self.assertTrue(password_hash('abcdef',allow_test_short=True).startswith('scrypt$'))
        invite=self.invitation()
        with self.assertRaises(Fault): self.s.register(self.meta(login='short-user',password='abcdef',invitation=invite['invitation_token']))

    def test_high_risk_confirmation_still_requires_mfa(self):
        """用户授权的是登录免验证码，大额操作不能自动继承免 MFA 例外。"""
        account,_=self.player()
        with patch.dict(os.environ,self.config()):
            with self.assertRaises(Fault): self.command('asset.grant',{'target_account_id':account,
                'catalog_version':'test-only-v1','changes':[{'type':'currency','code':'GP','delta':15000}],
                'current_password':self.password})
        self.assertEqual(self.balance(account),0)

    def test_creation_writes_private_configuration_and_refuses_overwrite(self):
        """创建命令只写私有目录，账号哈希入库；重复执行不覆盖已配置账号。"""
        root=Path(self.directory.name)
        with override_settings(BASE_DIR=root,NZ_PROFILE='synthetic'), patch.dict(os.environ,{'NZ_PASSWORD_ONLY_TEST_ADMIN_ID':''}):
            target=create_local_test_admin('demo-user',password='abcdef',password_only=True)
            credentials=json.loads(target.read_text())
            self.assertFalse(credentials['login_mfa_required'])
            self.assertIn('NZ_PASSWORD_ONLY_TEST_ADMIN_ID', (root/'.env.local').read_text())
            with self.s.db.transaction() as repository:
                row=repository.table('admins').get(login='demo-user')
                self.assertNotEqual(row.password_hash,credentials['password'])
            with self.assertRaises(CommandError): create_local_test_admin('another-user',password='abcdef',password_only=True)

    def test_configuration_install_failure_rolls_back_account_and_private_files(self):
        """文件安装失败必须回滚账号与审计，同时保留原环境配置，不留下孤立测试账号。"""
        root=Path(self.directory.name)
        config=root/'.env.local';config.write_text("TEST_KEEP='unchanged'\n")
        with override_settings(BASE_DIR=root,NZ_PROFILE='synthetic'), patch.dict(os.environ,{'NZ_PASSWORD_ONLY_TEST_ADMIN_ID':''}), patch.object(Path,'replace',side_effect=OSError('injected install failure')):
            with self.assertRaises(OSError): create_local_test_admin('rollback-user',password='abcdef',password_only=True)
        with self.s.db.transaction() as repository:
            self.assertFalse(repository.table('admins').filter(login='rollback-user').exists())
        self.assertEqual(config.read_text(),"TEST_KEEP='unchanged'\n")
        self.assertFalse((root/'runtime/demo-admin.json').exists())
