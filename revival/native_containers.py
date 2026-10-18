"""仓库宝箱的冻结规则与原子开启；原生协议号由配置映射，奖励不能由客户端指定。"""
import hashlib
import json
import secrets
from collections import Counter
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def catalog():
    """桥接和业务端共享已校验的配置；导入时不依赖 Django 初始化。"""
    raw=(Path(__file__).resolve().parents[1]/'config/cn2020-container-compat-v2.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!='79b724843a9dbc2b700d79e0cd413c90587a2a13f36c08447912fde19846a364':
        raise ValueError('CONTAINER_CATALOG_MISMATCH')
    return json.loads(raw)


def is_container(protocol):
    """识别全部原生宝箱入口；没有奖励定义的入口明确拒绝，不误走武器加工。"""
    return any(row['ProtocolValue']==protocol for row in catalog()['containers'])


def open_container(service, c, account, data):
    """在外层鉴权幂等事务中扣箱、发奖并冻结领取快照；任一步失败全部回滚。

    鼠年礼包保留三张换肤卡；37个有原始候选的猎场箱各选一项，概率缺失采用明确的
    等概率兼容规则。开奖、扣箱、入库、回执和事件都在同一事务，不信任客户端预测。
    """
    from django.db.models import Q
    from .native_actions import apply, consume_materials, item_rewards, event
    from .native_features_catalog import item_definition
    from .service import Fault, identifier, integer
    rules=catalog()
    protocol=integer(data.get('container_protocol'),0,65535,'container_protocol')
    requested_version=data.get('rule_version')
    if requested_version not in (rules['version'],rules['previous_version']):
        raise Fault('RULE_CONFLICT','礼包规则版本不一致',409)
    # 旧桥接仍可提交未变化的307配方，不能借旧版本访问后来新增的随机奖池。
    if requested_version==rules['previous_version'] and protocol!=307:
        raise Fault('RULE_CONFLICT','此礼包需要新版规则',409)
    recipe=rules['recipes'].get(str(protocol))
    if not recipe:
        raise Fault('ITEM_ACTION','礼包奖励尚未配置',409)
    count=integer(data.get('count'),1,100,'count')
    draws=[]
    if recipe.get('mode')=='random_one':
        pool=recipe['pool']
        for _ in range(count):
            ticket=secrets.randbelow(sum(row['weight'] for row in pool))
            for selected in pool:
                ticket-=selected['weight']
                if ticket<0:
                    break
            draws.append(dict(key=selected['key'],rewards=selected['rewards']))
        selected_rewards=[reward for draw in draws for reward in draw['rewards']]
    else:
        selected_rewards=[dict(row,quantity=row.get('quantity',1)*count) for row in recipe['rewards']]
    # 按物品和期限合并本次产出；计数材料保存本次增量，独立装备仍由资产层逐件创建GID。
    totals=Counter()
    for row in selected_rewards:
        totals[(row['item_id'],row.get('duration_seconds'))]+=row.get('quantity',1)
    rewards=[dict(item_id=item_id,duration_seconds=duration,quantity=quantity)
             for (item_id,duration),quantity in totals.items()]
    reward_changes=item_rewards(service,rewards)
    # A376 最多表达100个产物；必须在扣材料之前验证，不允许提交后才发现编码溢出。
    if len(reward_changes)>100:
        raise Fault('LIMIT','一次开启的奖励超过客户端上限',409)
    changes=consume_materials(service,c,account['id'],[dict(item_id=recipe['item_id'],quantity=count)])
    operations=apply(service,c,account['id'],changes+reward_changes,'inventory.open')
    # 只投影本次正向发奖流水；不能把扣箱条目或材料合并后的总持有量当成新奖励。
    awarded=Counter()
    for entry in c.table('asset_entries').filter(operation_id__in=operations,kind='item',delta__gt=0):
        awarded[entry.instance_id]+=entry.delta
    rows=c.select('inventory',Q(id__in=awarded,account_id=account['id']),order=('id',))
    expected=Counter()
    for row in rewards:
        expected[row['item_id']]+=row['quantity']
    remaining=dict(expected);grants=[]
    for row in rows:
        item_id=int(row['item_key'].split(':')[1])
        if item_id not in expected:
            continue
        binding=c.select('native_inventory_bindings',Q(inventory_id=row['id'])).fetchone()
        if not binding:
            raise ValueError('CONTAINER_REWARD_BINDING_MISSING')
        definition=item_definition(item_id)
        quantity=awarded[row['id']]
        if service.catalog['items'][row['item_key']]['storage_mode']=='instance' and quantity!=1:
            raise ValueError('CONTAINER_INSTANCE_QUANTITY_MISMATCH')
        remaining[item_id]-=quantity
        # 保存本次奖励数量，不使用材料合并后的总持有量；重放不依赖仍然存在的原实例。
        grants.append(dict(definition,gid=binding['id']+1000,instance_id=row['id'],quantity=quantity,
                           expires_at=row['expires_at'],state={},equipment_slot=None))
    if any(remaining.values()) or not 1<=len(grants)<=100:
        raise ValueError('CONTAINER_REWARD_PROJECTION_MISMATCH')
    result=dict(status='COMMITTED',receipt_id=identifier(),rule_version=requested_version,container_protocol=protocol,
                item_id=recipe['item_id'],count=count,rewards=rewards,grants=grants,operations=operations)
    if draws:
        result['draws']=draws
    # confirmed 表示资产已完成发放，不表示原生奖励弹层已被用户阅读。
    c.insert('native_receipts',dict(id=result['receipt_id'],account_id=account['id'],kind='inventory.open',
                                  result=result,created_at=service.clock(),confirmed=True))
    event(service,c,account['id'],'inventory.opened',result)
    return result
