"""保留旧命令名给出退役提示，不读取恢复码、配置或数据库。"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """旧TOTP恢复不再能够签发会话或重置管理员认证。"""
    def add_arguments(self, parser):
        """保留原登录名参数以便旧脚本得到明确退出原因。"""
        parser.add_argument('login')

    def handle(self, *args, **options):
        """直接失败，不提示输入旧凭据，也不初始化业务服务。"""
        raise CommandError('TOTP恢复已退役；管理员认证使用原密码及固定邮箱验证码。')
