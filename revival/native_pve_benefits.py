"""当前构建的百宝箱成长和三类功能卡规则；持久资产与原生战斗效果分别验收。"""
from functools import lru_cache
import hashlib
import json
from pathlib import Path

VERSION='cn2020-pve-benefits-v1'
FUNCTIONAL_VERSION='cn2020-pve-functional-v2'


def functional_items():
    """保留v1成长/领用规则，独立扩充已核实的102000全能体验卡根槽。

    原ItemLibrary摘要741963c2中该卡ResID1=1371537408、ResID2=589825，
    对应FIT_ATDCard/ExperienceCard_All(9)，原Location=-1、期限型ConsumeType=1。
    -1是此卡的真实根槽，不得改成专家卡36而把专家卡挤下，也不开放其他-1物品。
    """
    return [dict(row) for row in catalog()['functional_items']]+[dict(item_id=102000,location=-1)]


def gain_types(c,account_id,instance_ids):
    """批量从本人不可改写的发放流水及已确认回执还原VIP领用来源，不改历史资产。

    同ID的商店购买、GM永久权益仍是普通来源48；只有本次实例确由已提交百宝箱
    配方产生才投影35。分页和增量共用此规则，重登无需重新发奖或回填旧回执。
    """
    from django.db.models import Q
    ids=set(instance_ids)
    if not ids:
        return {}
    definitions={f"cn2020:{row['nDrawItemID']}":row for row in catalog()['privileges'] if row['nDrawItemID']>0}
    entries=list(c.table('asset_entries').filter(instance_id__in=ids,kind='item',delta__gt=0,
        resource__in=definitions,operation__account_id=account_id,operation__source='pve.store.claim',
        operation__status='COMMITTED').values('operation_id','instance_id','resource'))
    if not entries:
        return {}
    # 每个候选操作必须同时出现在本人同版本成功回执中，不能因相同武器ID误标其他实例。
    condition=Q(pk__in=[])
    for operation_id in {row['operation_id'] for row in entries}:
        condition |= Q(result__operations__contains=[operation_id])
    receipts=c.table('native_receipts').filter(condition,account_id=account_id,kind='pve.store.claim',
        confirmed=True,result__status='COMMITTED',result__rule_version=VERSION).values_list('result',flat=True)
    accepted=set()
    for result in receipts:
        for resource,definition in definitions.items():
            if (result.get('benefit_id')==definition['nItemID'] and result.get('prop_ids')==[definition['nDrawItemID']]
                    and result.get('rewards')==[dict(item_id=definition['nDrawItemID'],duration_seconds=definition['nDrawAvailHours']*3600)]):
                accepted.update((operation_id,resource) for operation_id in result['operations'])
    return {row['instance_id']:35 for row in entries if (row['operation_id'],row['resource']) in accepted}


@lru_cache(maxsize=1)
def catalog():
    """核对原配置派生规则摘要，禁止服务运行时静默替换等级区间或功能卡槽。"""
    raw=(Path(__file__).resolve().parents[1]/'config/cn2020-pve-benefits-v1.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!='1469180a8e97bc850d5a3e3689fcac1fe383b513b0c3499cb4286d8db615c51f':
        raise ValueError('PVE_BENEFITS_CATALOG_MISMATCH')
    return json.loads(raw)


def functional(item):
    """仅开放已核实的猎魔/塔防卡及其准确槽位；同槽其他物品不能借此扩大权限。"""
    return (item.get('consume_type') in (0,1) and not item.get('hidden')
            and any(row['item_id']==item.get('item_id') and row['location']==item.get('location')
                    for row in functional_items()))


def level(points):
    """按本构建原始区间计算1至3级；布尔、负数及越界值拒绝，不截断成成功。"""
    if type(points) is not int:
        raise ValueError('PVE_STORE_POINTS_INVALID')
    match=[row['nLevel'] for row in catalog()['levels'] if row['nMinPoint']<=points<=row['nMaxPoint']]
    if len(match)!=1:
        raise ValueError('PVE_STORE_POINTS_INVALID')
    return match[0]


def vip_info(items,now):
    """从本人库存快照计算原生VipInfo；过期不生效，成长仍保留，多个旧实例不叠加。

    永久资产在数据库保持NULL；VIP接口只能表达有符号32位时间，按显式兼容规则发送
    2147483647，不冒充客户端支持无限日期。PVP成长未实现，字段保持零。
    """
    points=0;end=0
    for item in items:
        if item.get('item_id')!=catalog()['store_item_id']:
            continue
        store=item.get('state',{}).get('pve_store')
        if store is not None:
            if not isinstance(store,dict) or store.get('version')!=VERSION:
                raise ValueError('PVE_STORE_STATE_VERSION_INVALID')
            value=store.get('points');level(value)
            points=max(points,value)
        expiry=item.get('expires_at')
        if expiry is not None and (type(expiry) is not int or expiry<=0):
            raise ValueError('PVE_STORE_EXPIRY_INVALID')
        if expiry is None or expiry>now:
            end=max(end,catalog()['permanent_vip_wire_end'] if expiry is None else min(expiry,2147483647))
    return dict(VipPVEPoint=points,VipPVEEndTime=end,VipPVPPoint=0,VipPVPEndTime=0)


def snapshot(service,c,account):
    """只在已经鉴权的事务内读取本人相关资产，供查询与结算核验，不给客户端写成长接口。"""
    from django.db.models import Q
    cards=functional_items()
    ids=[catalog()['store_item_id']]+[row['item_id'] for row in cards]
    rows=c.select('inventory',Q(account_id=account['id'],item_key__in=[f'cn2020:{value}' for value in ids]))
    states=dict(c.table('native_item_states').filter(inventory_id__in=[row['id'] for row in rows]).values_list('inventory_id','state'))
    equipment=dict(c.table('equipment').filter(account_id=account['id'],inventory_id__in=[row['id'] for row in rows]).values_list('inventory_id','slot'))
    items=[dict(row,item_id=int(row['item_key'].split(':')[1]),state=states.get(row['id'],{})) for row in rows]
    vip=vip_info(items,service.clock())
    # 业务有效性始终看真实期限，不能让2038年的32位线上投影边界使永久资产失效。
    active=any(row['item_id']==catalog()['store_item_id'] and
               (row['expires_at'] is None or row['expires_at']>service.clock()) for row in items)
    return dict(rule_version=VERSION,functional_rule_version=FUNCTIONAL_VERSION,level=level(vip['VipPVEPoint']),active=active,
                vip_info=vip,functional_items=[dict(item_id=row['item_id'],equipped=equipment.get(row['id'])==
                 f"native:1:{next(value['location'] for value in cards if value['item_id']==row['item_id'])}")
                 for row in items if row['item_id']!=catalog()['store_item_id']
                 and (row['expires_at'] is None or row['expires_at']>service.clock())])


def claim(service,c,account,data):
    """按实收Seq权益编号和PropIds真实武器列表领取；有效持有期间不重复发放，过期可再领。

    第71轮实收Seq=10014、PropIds=[100482]；参数组合必须与冻结配方完全相同。
    原说明允许有效期内不限次数领用，不能把历史领取标记变成终身限领一次。
    当前仍持有有效奖励武器时使用原生HAD_TAKEN拒绝；重试同一业务请求始终返回原回执。
    """
    from django.db.models import Q
    from .native_actions import apply,event,item_rewards
    from .service import Fault,identifier
    props=data.get('prop_ids');benefit_id=data.get('benefit_id')
    if (not isinstance(props,list) or not 1<=len(props)<=32
            or any(type(value) is not int for value in props) or len(set(props))!=len(props)):
        raise Fault('PVE_STORE_PROP','百宝箱领取列表无效',409)
    status=snapshot(service,c,account)
    if not status['active']:
        raise Fault('PVE_STORE_LEVEL','需要有效闯关百宝箱',409)
    definitions={row['nItemID']:row for row in catalog()['privileges'] if row['nDrawItemID']>0}
    definition=definitions.get(benefit_id) if type(benefit_id) is int else None
    if not definition or props!=[definition['nDrawItemID']]:
        raise Fault('PVE_STORE_PROP','权益编号和奖励武器不匹配',409)
    if status['level']<definition['nMinLevel']:
        raise Fault('PVE_STORE_LEVEL','闯关百宝箱等级不足',409)
    if c.select('inventory',Q(account_id=account['id'],item_key=f"cn2020:{definition['nDrawItemID']}") &
                (Q(expires_at__isnull=True)|Q(expires_at__gt=service.clock())),limit=1):
        raise Fault('PVE_STORE_TAKEN','仍持有有效的该武器，无需重复领取',409)
    boxes=c.select('inventory',Q(account_id=account['id'],item_key=f"cn2020:{catalog()['store_item_id']}"),order=('id',))
    saved={row['inventory_id']:row['state'] for row in c.select('native_item_states',Q(inventory_id__in=[box['id'] for box in boxes]))}
    # 历史标记保留用于审计和旧状态兼容；是否可再次领取由当前实际有效库存决定。
    claimed=set()
    for box in boxes:
        store=saved.get(box['id'],{}).get('pve_store',{})
        values=store.get('claimed',[])
        if not isinstance(values,list) or any(type(value) is not int or value not in definitions for value in values):
            raise ValueError('PVE_STORE_CLAIM_STATE_INVALID')
        claimed.update(values)
    box=max(boxes,key=lambda row:saved.get(row['id'],{}).get('pve_store',{}).get('points',0))
    state=dict(saved.get(box['id'],{}))
    store=dict(state.get('pve_store',{}),version=VERSION,points=status['vip_info']['VipPVEPoint'],claimed=sorted(claimed|{benefit_id}))
    state['pve_store']=store
    rewards=[dict(item_id=definition['nDrawItemID'],duration_seconds=definition['nDrawAvailHours']*3600)]
    changes=item_rewards(service,rewards)+[dict(type='native_state',instance_id=box['id'],state=state,allow_expired=True)]
    operations=apply(service,c,account['id'],changes,'pve.store.claim')
    result=dict(status='COMMITTED',rule_version=VERSION,receipt_id=identifier(),benefit_id=benefit_id,prop_ids=props,rewards=rewards,operations=operations)
    c.insert('native_receipts',dict(id=result['receipt_id'],account_id=account['id'],kind='pve.store.claim',
                                  result=result,created_at=service.clock(),confirmed=True))
    event(service,c,account['id'],'pve.store.claimed',result)
    return result
