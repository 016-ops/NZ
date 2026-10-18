"""工程分解/制造事务；成本沿用固定配置，缺失机制使用显式版本化兼容规则。"""
from collections import Counter
import secrets
from django.db.models import Q
from .native_actions import owned, native_id, consume_materials, item_rewards, apply, event
from .native_features_catalog import engineering_catalog
from .service import Fault, identifier, integer

RULE_VERSION='cn2020-engineering-compat-v1'
# 配置缺少分解数量、部件转换比和随机概率。兼容规则明确：每种配置产出各1，部件5进1/1退5；
# 制造期限按小时，熟练度每成功一次加1、达到配置上限停止增长；冷却0秒。随机专利描述的4把黄金武器等概率。
POLICY=dict(decompose_each_output=1,material_ratio=5,duration_unit='hours',proficiency_per_success=1,
            cooldown_seconds=0,random_recipe_pool=[100465,100474,100542,100543],random_weight='equal')


def snapshot(service,c,account):
    """读取本人熟练度及完整配方，显式返回自定义规则供启动器/管理端解释；不更改任何状态。"""
    return dict(rule_version=RULE_VERSION,policy=POLICY,catalog=engineering_catalog(),
                progress=list(c.select('native_engineering_progress',Q(account_id=account['id']))))


def selected_items(service,c,account,gids,maximum):
    """解析有界本人GID列表，逐项检查期限和装备状态；重复计数在物料转换中由调用方合并。"""
    if not isinstance(gids,list) or not 1<=len(gids)<=maximum:
        raise Fault('INVALID','工程操作数量超限')
    for gid in gids:
        integer(gid,1001,2**63-1,'gid')
    rows={gid:owned(service,c,account['id'],gid) for gid in gids}
    if c.select('equipment',Q(inventory_id__in=[r['id'] for r in rows.values()])):
        raise Fault('ITEM_IN_USE','请先卸下工程材料',409)
    return rows


def command(service,c,account,action,data):
    """扣料、扣费、随机结果、产出、熟练度、回执和通知由外层同一MySQL事务原子提交。"""
    if data.get('rule_version')!=RULE_VERSION:
        raise Fault('RULE_CONFLICT','请刷新工程规则',409)
    catalog=engineering_catalog();changes=[];rewards=[];details={}
    if action=='engineering.decompose':
        gids=data.get('gids');rows=selected_items(service,c,account,gids,60)
        if len(rows)!=len(gids):
            raise Fault('INVALID','装备分解不能重复引用同一实例')
        # 原生界面明确禁止VIPTAKE来源工程分解；服务端同样核对实例流水，不能只靠按钮隐藏。
        from .native_pve_benefits import gain_types
        if gain_types(c,account['id'],[row['id'] for row in rows.values()]):
            raise Fault('ITEM_ACTION','百宝箱领用武器不能工程分解',409)
        kinds={r['item_id']:r['type'] for r in catalog['decomposable']}
        quantities=Counter()
        for row in rows.values():
            kind=kinds.get(native_id(row))
            if kind is None:
                raise Fault('ITEM_ACTION','该物品不属于工程分解目录',409)
            changes.append(dict(type='remove',instance_id=row['id'],quantity=1))
            quantities.update(catalog['decompose_outputs'][str(kind)])
        rewards=[dict(item_id=i,quantity=n) for i,n in quantities.items()]
    elif action=='engineering.material_decompose':
        gids=data.get('gids');rows=selected_items(service,c,account,gids,30)
        # 固定金→银→铜→铁；映射按物品ID，不能依赖JSON对象排序。
        previous={100765:100764,100766:100765,100767:100766}
        quantities=Counter()
        for gid,count in Counter(gids).items():
            row=rows[gid];target=previous.get(native_id(row))
            if target is None:
                raise Fault('ITEM_ACTION','该部件不能继续分解',409)
            changes.append(dict(type='remove',instance_id=row['id'],quantity=count))
            quantities[target]+=count*POLICY['material_ratio']
        rewards=[dict(item_id=i,quantity=n) for i,n in quantities.items()]
    elif action=='engineering.material_compose':
        target=integer(data.get('item_id'),1,2147483647,'item_id')
        previous={100765:100764,100766:100765,100767:100766}
        if target not in previous:
            raise Fault('ITEM_ACTION','该部件没有合成配方',409)
        changes=consume_materials(service,c,account['id'],[dict(item_id=previous[target],quantity=POLICY['material_ratio'])])
        rewards=[dict(item_id=target,quantity=1)]
    elif action=='engineering.compose':
        recipe=next((r for r in catalog['recipes'] if r['nSyntheticPropID']==data.get('recipe_id')),None)
        if recipe is None:
            raise Fault('ITEM_ACTION','未知工程配方',409)
        count=integer(data.get('count',1),1,5,'count')
        mode=integer(data.get('mode'),1,2,'mode')
        # 隐藏/锁定是当前客户端展示配置，服务端保留全部明确配方；不能把隐藏当作完成或删除定义。
        # 90002等配方明确列出数量0的部件，保留目录原值但不将0提交给严格扣料入口。
        changes=consume_materials(service,c,account['id'],[dict(m,quantity=m['quantity']*count) for m in recipe['materials'] if m['quantity']>0])
        if mode==2 and recipe['PriceValueSucceed_NZ']:
            changes.append(dict(type='currency',code='cn2020:TP',delta=-recipe['PriceValueSucceed_NZ']*count))
        query=Q(account_id=account['id'],recipe_id=recipe['nSyntheticPropID'])
        saved=c.select('native_engineering_progress',query).fetchone()
        level=saved['proficiency'] if saved else 0
        successes=0;maximum=recipe['ProficientDegree_Max']
        rate=recipe['MakeWithNZSuccRate' if mode==2 else 'DirectMakeSuccRate']
        for _ in range(count):
            if secrets.randbelow(100)>=rate:
                continue
            stage=0 if level*2<maximum else 1 if level<maximum else 2
            hours=recipe['HighAvaibleTimeArray' if mode==2 else 'NormalAvaibleTimeArray'][stage]
            item=recipe['nItemID'] or secrets.choice(POLICY['random_recipe_pool'])
            rewards.append(dict(item_id=item,quantity=1,duration_seconds=hours*3600))
            successes+=1;level=min(maximum,level+1)
        values=dict(proficiency=level,attempts=(saved['attempts'] if saved else 0)+count)
        if saved:
            c.update('native_engineering_progress',query,values)
        else:
            c.insert('native_engineering_progress',dict(values,id=identifier(),account_id=account['id'],recipe_id=recipe['nSyntheticPropID']))
        details=dict(recipe_id=recipe['nSyntheticPropID'],mode=mode,count=count,successes=successes,proficiency=level,cooldown_until=0)
    else:
        raise Fault('NOT_FOUND','未知工程操作',404)
    if rewards:
        changes.extend(item_rewards(service,rewards))
    operations=apply(service,c,account['id'],changes,action)
    result=dict(receipt_id=identifier(),rule_version=RULE_VERSION,status='COMMITTED',operations=operations,rewards=rewards,**details)
    c.insert('native_receipts',dict(id=result['receipt_id'],account_id=account['id'],kind=action,
                                  result=result,created_at=service.clock(),confirmed=True))
    event(service,c,account['id'],'engineering.completed',result)
    return result
