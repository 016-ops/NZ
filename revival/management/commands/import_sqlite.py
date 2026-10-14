"""显式导入入口，默认不会扫描或自动转换任何旧文件。"""
import json
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from revival.importer import import_sqlite


class Command(BaseCommand):
    """Django 管理命令入口；复用本模块业务处理，参数校验失败以 CommandError 终止。"""
    help = 'Read-only legacy SQLite import into an empty MySQL business database'

    # 仅注册本命令需要的参数，profile 与数据库连接在 Django 初始化前由环境配置确定。
    def add_arguments(self, parser):
        """仅注册本命令需要的参数，profile 与数据库连接在 Django 初始化前由环境配置确定。"""
        parser.add_argument('source')
        parser.add_argument('--dry-run', action='store_true')

    # 导入报告只包含核对结果；拒绝条件输出固定描述，不输出源凭据或数据库驱动数据。
    def handle(self, *args, **options):
        """只读指定 SQLite 源并核对空 MySQL 目标；dry-run 回滚，失败转为无敏感信息的命令错误。"""
        try:
            report = import_sqlite(options['source'], dry_run=options['dry_run'], test_profile=settings.NZ_PROFILE=='synthetic')
        except Exception as error:
            # 驱动异常可能带数据，只输出类型；预期校验异常只包含固定描述。
            raise CommandError(str(error) if isinstance(error, ValueError) else f'Import rolled back: {type(error).__name__}') from None
        self.stdout.write(json.dumps(report, ensure_ascii=False))
