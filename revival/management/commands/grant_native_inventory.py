"""在受控服务主机补齐指定已有玩家的全部兼容库存，不创建账号、邮件或后台权限。"""
import json
from django.core.management.base import BaseCommand, CommandError
from revival.catalogs import NATIVE_INVENTORY_VERSION
from revival.native_inventory import grant_full_inventory
from revival.service import Fault, Service


class Command(BaseCommand):
    """接受明确账号ID与理由，在统一事务内执行可重试的永久权益补齐。"""
    help = 'Grant all versioned compatibility items to an existing player'

    def add_arguments(self, parser):
        """账号必须显式提供；规则版本不允许隐式切换，理由进入审计。"""
        parser.add_argument('--account-id', required=True)
        parser.add_argument('--rules-version', required=True, choices=[NATIVE_INVENTORY_VERSION])
        parser.add_argument('--reason', required=True)

    def handle(self, *args, **options):
        """只输出无凭据的发放摘要；任何验证或事务错误由管理命令以失败码返回。"""
        try:
            result = grant_full_inventory(Service(test_profile=False), options['account_id'],
                rules_version=options['rules_version'], reason=options['reason'])
        except Fault as error:
            raise CommandError(error.code) from None
        self.stdout.write(json.dumps(result, ensure_ascii=False))
