"""按明确兼容版本将既有GM的当前客户端钱包补至上限，不创建身份或绕过资产流水。"""
import json
from django.core.management.base import BaseCommand, CommandError
from revival.native_wallet import WALLET_VERSION, maximize
from revival.service import Service, Fault


class Command(BaseCommand):
    """仅受控本地命令接收账号、规则版本和审计理由，不对玩家HTTP开放。"""
    help = 'Top up an existing native GM wallet to compatibility limits'

    def add_arguments(self, parser):
        """账号及版本必须显式提供，以免意外作用于默认或全体用户。"""
        parser.add_argument('--account-id',required=True)
        parser.add_argument('--rules-version',required=True,choices=[WALLET_VERSION])
        parser.add_argument('--reason',required=True)

    def handle(self, *args, **options):
        """返回不含身份凭据的真实提交摘要；失败用固定代码并保留已有数据库状态。"""
        try:
            result=maximize(Service(test_profile=False),options['account_id'],
                            rules_version=options['rules_version'],reason=options['reason'])
        except Fault as error:
            raise CommandError(error.code) from None
        self.stdout.write(json.dumps(result,ensure_ascii=False))
