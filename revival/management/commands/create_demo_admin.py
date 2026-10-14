"""免登录 MFA 测试管理员入口；密码交互或标准输入传入，不接受命令行明文密码参数。"""
import getpass
import sys
from django.core.management.base import BaseCommand
from revival.test_accounts import create_local_test_admin


class Command(BaseCommand):
    """Django 管理命令入口；复用本模块业务处理，参数校验失败以 CommandError 终止。"""
    help='Create a synthetic-only password-login test administrator'

    def add_arguments(self,parser):
        """标准输入开关方便本地受控配置，账号名仍按正常登录规则验证。"""
        parser.add_argument('login')
        parser.add_argument('--password-stdin',action='store_true')

    def handle(self,*args,**options):
        """合成模式、唯一账号和文件回退都由共享创建入口校验，成功后只返回私有文件路径。"""
        password=sys.stdin.readline().rstrip('\r\n') if options['password_stdin'] else getpass.getpass('Test administrator password: ')
        target=create_local_test_admin(options['login'],password=password,password_only=True)
        self.stdout.write(f'TEST_PROFILE password-only administrator created. Private credentials: {target}')
        self.stdout.write('Restart the service to load the account-scoped configuration.')
