"""为明确列出的验收账号应用诸神配装；不把所有注册玩家自动纳入测试范围。"""
import json
from django.core.management.base import BaseCommand,CommandError
from revival.native_acceptance_loadout import prepare
from revival.service import Service,Fault


class Command(BaseCommand):
    """复用同一MySQL资产事务和幂等回执；默认不执行，必须给出账号及请求理由。"""
    def add_arguments(self,parser):
        """显式账号集合避免误改正常玩家；固定请求ID用于网络结果不确定后的安全重试。"""
        parser.add_argument('--game-ids',nargs='+',type=int,required=True)
        parser.add_argument('--request-id',required=True)
        parser.add_argument('--reason',required=True)
        parser.add_argument('--profile',choices=('zhushen','five-bags','six-bags'),default='zhushen',
                            help='five-bags为五套v2；six-bags追加元素套和鲲满核心v3，均保留旧回执')

    def handle(self,*args,**options):
        """在服务规则目录中执行受控配装；JSON仅含物品、GID和操作号，不返回登录凭据。"""
        try:
            result=prepare(Service(test_profile=False),options['game_ids'],request_id=options['request_id'],
                           reason=options['reason'],profile=options['profile'])
        except Fault as error:
            raise CommandError(error.code) from None
        self.stdout.write(json.dumps(result,ensure_ascii=False))
