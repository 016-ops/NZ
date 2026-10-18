"""原生耐久维修与GP出售；沿用固定客户端价格规则，所有资产和通知由调用方事务原子提交。"""
import hashlib,json,math,struct
from functools import lru_cache
from pathlib import Path
from django.db.models import Q
from .native_actions import owned,native_id,apply,event
from .service import Fault,integer,identifier

VERSION='cn2020-item-maintenance-v1'
SHA256='b033a5e1ad58388505ccee8bc9c18b6df64085fbfdd9d54b308fc441ab852513'


@lru_cache(maxsize=1)
def catalog():
    """只接收固定派生目录，运行时内容替换会拒绝而不会静默改变价格。"""
    raw=(Path(__file__).resolve().parents[1]/'config/cn2020-item-maintenance-v1.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=SHA256:raise ValueError('MAINTENANCE_CATALOG_MISMATCH')
    return json.loads(raw)['items']


def f32(value):
    """按当前UnrealScript单精度运算舍入，避免整数边缘处服务端价格与客户端提示不同。"""
    return struct.unpack('<f',struct.pack('<f',value))[0]


def price(definition,durability,*,selling=False):
    """维修使用缺损比例加0.999后截断；出售使用剩余比例的四分之一再向上取整。"""
    maximum=definition['DurableMax'];full=definition['nRepairPrice']
    if selling:
        value=f32(full*.25) if definition['ConsumeType']==0 else f32(f32(f32(durability*.25)/maximum)*full)
        return math.ceil(value)
    return int(f32(f32(f32(f32(maximum-durability)/maximum)*full)+f32(.999)))


def command(service,c,account,action,data):
    """验证本人有效实例后一次扣费/修满或出售退款；失败不产生半份耐久、库存或钱包。"""
    selling=action=='inventory.sell'
    gids=[data.get('gid')] if selling else data.get('gids')
    if not isinstance(gids,list) or not 1<=len(gids)<=100:
        raise Fault('INVALID','维修需要1至100件物品')
    gids=[integer(gid,1001,2**63-1,'gid') for gid in gids]
    if len(set(gids))!=len(gids):raise Fault('INVALID','同一物品不能重复计费')
    changes=[];total=0;results=[]
    for gid in gids:
        item=owned(service,c,account['id'],gid)
        definition=catalog().get(str(native_id(item)))
        if not definition:raise Fault('ITEM_ACTION','缺少物品维修规则')
        old=c.select('native_item_states',Q(inventory_id=item['id'])).fetchone()
        state=dict(old['state']) if old else {}
        maximum=definition['DurableMax']
        durability=integer(state.get('durability',maximum),0,maximum,'durability')
        if selling:
            if not definition['bDroppable'] or definition['bDefaultItem'] or definition['ConsumeType'] not in (0,2):
                raise Fault('ITEM_ACTION','该物品不可出售')
            if c.select('equipment',Q(inventory_id=item['id'])):
                raise Fault('ITEM_IN_USE','请先卸下出售的物品')
            if definition['ConsumeType']==2 and maximum<=0:raise Fault('ITEM_ACTION','耐久规则无效')
            cost=price(definition,durability,selling=True)
            changes.append(dict(type='remove',instance_id=item['id'],quantity=1))
        else:
            # 当前4323项中所有可维修耐久武器均为ConsumeType=2/RepairType=2(GP)。
            # 其他类型的nRepairPrice是续费商品ID，绝不可用作耐久维修金额。
            if definition['ConsumeType']!=2 or definition['RepairType']!=2 or maximum<=0:
                raise Fault('ITEM_ACTION','该物品不使用GP耐久维修')
            cost=price(definition,durability)
            if durability<maximum:
                state['durability']=maximum
                changes.append(dict(type='native_state',instance_id=item['id'],state=state))
        total+=cost
        results.append(dict(gid=gid,item_id=native_id(item),cost=cost))
    integer(total,0,2147483647,'total')
    if total:changes.insert(0,dict(type='currency',code='cn2020:GP',delta=total if selling else -total))
    operations=apply(service,c,account['id'],changes,action)
    result=dict(status='COMMITTED' if changes else 'UNCHANGED',rule_version=VERSION,
        operations=operations,items=results,totals={'cn2020:GP':total})
    if changes:
        receipt=identifier();result['receipt_id']=receipt
        c.insert('native_receipts',dict(id=receipt,account_id=account['id'],kind=action,result=result,
            created_at=service.clock(),confirmed=True))
        event(service,c,account['id'],'inventory.changed',result)
    return result
