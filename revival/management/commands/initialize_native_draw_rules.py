"""显式初始化缺失的五类抽奖规则；保留管理员已有版本且不创建或启用正式签到。"""
import json
from django.core.management.base import BaseCommand
from django.db.models import Q
from revival.native_draw_defaults import DEFAULT_VERSION, definitions, legacy_definitions, round_definitions
from revival.native_rewards import publish
from revival.service import Service


class Command(BaseCommand):
    """部署操作使用受审计的本地入口，默认只预览；--publish才保存初始兼容规则。"""
    help='Initialize missing versioned native draw rules without replacing existing rules'

    def add_arguments(self, parser):
        """显式发布开关与规则版本防止错误环境悄悄启用其他活动。"""
        parser.add_argument('--publish',action='store_true')
        parser.add_argument('--rules-version',required=True,choices=[DEFAULT_VERSION])
        parser.add_argument('--upgrade-defaults',action='store_true')

    def handle(self, *args, **options):
        """一个MySQL事务保存缺失规则与审计；任一错误回滚整批，不覆盖停用或已编辑规则。"""
        service=Service(test_profile=False)
        results=[]
        with service.db.transaction() as c:
            for namespace,definition in definitions().items():
                current=c.select('native_rule_revisions',Q(namespace=namespace),order=('-revision',),limit=1).fetchone()
                # 只升级完全未编辑且仍启用的v1/v2默认规则；停用或管理员定制内容一律保留。
                upgrade=bool(options['upgrade_defaults'] and current and current['enabled']
                             and current['definition']!=definition
                             and current['definition'] in (legacy_definitions()[namespace],round_definitions()[namespace]))
                if current and not upgrade:
                    results.append({'namespace':namespace,'status':'EXISTING_PRESERVED'})
                    continue
                action='native.rules.publish' if options['publish'] else 'native.rules.preview'
                result=publish(service,c,{'id':'deployment:'+DEFAULT_VERSION},action,
                    {'namespace':namespace,'definition':definition,'enabled':True,'expected_revision':current['revision'] if current else 0})
                service._audit(c,'deployment:'+DEFAULT_VERSION,action,namespace,DEFAULT_VERSION,
                    '用户授权功能补全：初始化明确标注的兼容抽奖规则',{'rule_id':result.get('id'),'version':DEFAULT_VERSION})
                results.append({'namespace':namespace,'status':result['status'],'revision':result['revision']})
        self.stdout.write(json.dumps({'version':DEFAULT_VERSION,'results':results,'checkin_modified':False}))
