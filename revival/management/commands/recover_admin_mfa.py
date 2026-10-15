"""管理员失去验证器后的本机恢复入口；不通过命令行参数或 HTTP 传递恢复凭据。"""
import getpass
from urllib.parse import quote
from django.conf import settings
from django.core.management.base import BaseCommand
from revival.administration import recover_mfa
from revival.service import Service


class Command(BaseCommand):
    """要求交互输入密码及未使用的恢复码，成功后仅展示一次新 MFA 和恢复码。"""
    def add_arguments(self, parser):
        """只接收非敏感登录名，凭据从终端的隐藏输入读取。"""
        parser.add_argument('login')

    def handle(self, *args, **options):
        """原子恢复后提示私下保存；旧会话和旧恢复码全部失效。"""
        result = recover_mfa(Service(test_profile=settings.NZ_PROFILE == 'synthetic'), options['login'],
                             getpass.getpass('Administrator password: '), getpass.getpass('Unused recovery code: '))
        self.stdout.write(f"otpauth://totp/NZRevival:{quote(options['login'])}?secret={result['otp_secret']}&issuer=NZRevival")
        self.stdout.write('Store these new recovery codes privately; they are shown once:')
        self.stdout.write('\n'.join(result['recovery_codes']))
