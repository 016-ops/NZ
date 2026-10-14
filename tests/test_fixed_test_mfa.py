"""用户要求的固定测试验证码必须只对唯一合成账号放行，不能污染正常 MFA 边界。"""
import os
from unittest.mock import patch
from django.db import connections
from revival.security import otp, token_hash
from revival.service import Fault
from .test_control_plane import Base


class FixedTestMfaTests(Base):
    """验证固定验证码例外严格绑定指定测试管理员、合成 profile 和数据库。"""
    def config(self):
        """选择当前 TOTP 窗口绝不接受的码，确保测试证明的是固定码例外而非偶然真实验证码。"""
        actual = {otp(self.admin['otp_secret'],self.now//30+offset) for offset in (-1,0,1)}
        code = next(str(number) for number in range(987650,987660) if str(number) not in actual)
        return code, {'NZ_FIXED_TEST_ADMIN_ID':self.admin['admin_id'],
            'NZ_FIXED_TEST_ADMIN_DATABASE':connections['default'].settings_dict['NAME'],
            'NZ_FIXED_TEST_ADMIN_OTP_HASH':token_hash(code)}

    def test_reusable_code_keeps_password_and_active_checks(self):
        """相同固定码跨多次登录可用，但错误密码和停用状态不能借此放行。"""
        code, config = self.config()
        with patch.dict(os.environ,config):
            first=self.s.login('owner',self.password,kind='admin',otp_code=code)
            self.now+=300
            second=self.s.login('owner',self.password,kind='admin',otp_code=code)
            self.assertNotEqual(first['token'],second['token'])
            with self.assertRaises(Fault): self.s.login('owner','wrong-password',kind='admin',otp_code=code)
            with self.s.db.transaction() as repository:
                repository.table('admins').filter(pk=self.admin['admin_id']).update(active=0)
            with self.assertRaises(Fault): self.s.login('owner',self.password,kind='admin',otp_code=code)

    def test_different_admin_and_database_never_bypass_mfa(self):
        """三重限定必须同时成立；其他管理员及换库后的相同 ID 都不能使用固定码。"""
        self.extra_admin('users')
        code, config = self.config()
        with patch.dict(os.environ,config):
            with self.assertRaises(Fault): self.s.login('users',self.password,kind='admin',otp_code=code)
        config['NZ_FIXED_TEST_ADMIN_DATABASE']='another_database'
        with patch.dict(os.environ,config):
            with self.assertRaises(Fault): self.s.login('owner',self.password,kind='admin',otp_code=code)

    def test_current_profile_and_wrong_code_are_rejected(self):
        """当前客户端模式完全关闭此例外；错误固定码仍需要有效且未消费的正常 TOTP。"""
        code, config = self.config()
        with patch.dict(os.environ,config):
            with self.assertRaises(Fault): self.s.login('owner',self.password,kind='admin',otp_code='bad')
            self.s.test_profile=False
            with self.assertRaises(Fault): self.s.login('owner',self.password,kind='admin',otp_code=code)

    def test_high_risk_synthetic_confirmation_can_reuse_fixed_code(self):
        """这个账号的合成高风险确认也支持固定码，但仍要求当前密码、授权、目录及完整事务。"""
        account,_ = self.player()
        code, config = self.config()
        with patch.dict(os.environ,config):
            for _ in range(2):
                self.command('asset.grant',{'target_account_id':account,'catalog_version':'test-only-v1',
                    'changes':[{'type':'currency','code':'GP','delta':15000}],
                    'current_password':self.password,'otp_code':code})
        self.assertEqual(self.balance(account),30000)

    def test_normal_mfa_still_rejects_replay_when_unconfigured(self):
        """关闭固定码配置后，同一真实动态时间步仍不能重复使用。"""
        with patch.dict(os.environ,{'NZ_FIXED_TEST_ADMIN_ID':'','NZ_FIXED_TEST_ADMIN_DATABASE':'','NZ_FIXED_TEST_ADMIN_OTP_HASH':''}):
            with self.assertRaises(Fault): self.s.login('owner',self.password,kind='admin',otp_code=otp(self.admin['otp_secret'],self.now//30))
