"""固定验证码测试管理员命令；与免码账号共用受限配置及失败回退实现。"""
from django.core.management.base import BaseCommand
from revival.test_accounts import create_local_test_admin


class Command(BaseCommand):
    """Django 管理命令入口；复用本模块业务处理，参数校验失败以 CommandError 终止。"""
    help='Create a synthetic-only administrator with a reusable fixed test OTP'

    def add_arguments(self,parser):
        """密码与固定码由安全随机源生成，避免凭据进入命令行参数。"""
        parser.add_argument('login',nargs='?',default='testadmin')

    def handle(self,*args,**options):
        """创建成功后只给出私有凭据文件位置，已有固定码配置拒绝覆盖。"""
        target=create_local_test_admin(options['login'])
        self.stdout.write(f'TEST_PROFILE administrator created. Private credentials: {target}')
        self.stdout.write('Restart the service to load the account-scoped configuration.')
