"""旧固定码/免码管理员命令已退役；不读取标准输入、私有配置或生成例外凭据。"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """保留命令名及参数兼容，在读取密码或连接数据库前明确拒绝。"""
    help = 'Retired: administrators require password and fixed-mailbox email code'

    def add_arguments(self, parser):
        """识别旧调用形式；标准输入开关不再消费任何密码内容。"""
        parser.add_argument('login', nargs='?', default='testadmin')

    def handle(self, *args, **options):
        """合成模式与正式模式统一邮件认证，不再保留固定码或免码路径。"""
        raise CommandError('AUTH_METHOD_RETIRED: use bootstrap_admin and email authentication')
