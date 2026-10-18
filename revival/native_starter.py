"""当前账号初始装备策略；与角色创建共用事务，保留既有实例、装备选择和流水。"""
from django.db.models import Q
from .native_actions import apply,item_rewards,event
from .native_inventory import advance_revision
from .service import identifier

VERSION='cn2020-starter-loadout-v3'
BAGS=(100026,100063,100035,100036,100037,100933)


def restore_default_knives(service,c,account_id):
    """换刀/移动刀后补齐已启用背包的空刀槽；复用空闲默认刀，旧账号不足时原子发放独立实例。"""
    result=[]
    bags=c.select('equipment',Q(account_id=account_id,slot__startswith='native:bag:'))
    for bag in bags:
        binding=c.select('native_inventory_bindings',Q(inventory_id=bag['inventory_id'])).fetchone()
        target=binding['id']+1000;slot=f'native:{target}:2'
        if c.select('equipment',Q(account_id=account_id,slot=slot)):
            continue
        used=c.table('equipment').filter(account_id=account_id).values_list('inventory_id',flat=True)
        query=Q(account_id=account_id,item_key='cn2020:100013',expires_at__isnull=True)&~Q(id__in=used)
        item=c.select('inventory',query,order=('id',),limit=1).fetchone()
        if not item:
            apply(service,c,account_id,item_rewards(service,[dict(item_id=100013)]),'equipment.default_knife')
            item=c.select('inventory',query,order=('id',),limit=1).fetchone()
        c.equip(account_id,slot,item['id'])
        knife=c.select('native_inventory_bindings',Q(inventory_id=item['id'])).fetchone()
        result.append(dict(instance_id=item['id'],gid=knife['id']+1000,target=target))
    if result:advance_revision(c,account_id,[r['instance_id'] for r in result])
    return result


def ensure(service,c,account_id):
    """一次性补齐两角色、六包及每包独立默认刀；仅1号包补主副雷，不覆盖任何已有槽。"""
    prior=c.select('native_receipts',Q(account_id=account_id,kind='starter.loadout')).fetchone()
    if prior:
        return prior['result']
    operations=[];changed=[]
    def acquire(item_id):
        """优先复用永久未装备实例；没有可复用项才经统一资产账本新建稳定GID。"""
        used=c.table('equipment').filter(account_id=account_id).values_list('inventory_id',flat=True)
        row=c.select('inventory',Q(account_id=account_id,item_key='cn2020:'+str(item_id),expires_at__isnull=True)&~Q(id__in=used),order=('id',),limit=1).fetchone()
        if not row:
            operations.extend(apply(service,c,account_id,item_rewards(service,[dict(item_id=item_id)]),'starter.loadout'))
            row=c.select('inventory',Q(account_id=account_id,item_key='cn2020:'+str(item_id),expires_at__isnull=True)&~Q(id__in=used),order=('id',),limit=1).fetchone()
        return row
    def equip(item_id,slot):
        """已有槽位始终保留；新增槽使用本人实例，返回稳定GID供背包子树挂接。"""
        existing=c.select('equipment',Q(account_id=account_id,slot=slot)).fetchone()
        row=c.select('inventory',Q(id=existing['inventory_id'])).fetchone() if existing else acquire(item_id)
        if not existing:
            c.equip(account_id,slot,row['id']);changed.append(row['id'])
        binding=c.select('native_inventory_bindings',Q(inventory_id=row['id'])).fetchone()
        return binding['id']+1000
    from .native_avatar import default_components
    roles=[]
    for role_id,role_slot in ((100216,10),(100217,11)):
        role_gid=equip(role_id,f'native:1:{role_slot}')
        role=c.select('native_inventory_bindings',Q(id=role_gid-1000)).fetchone()
        roles.append(role['inventory_id'])
        # 新账号的万飞/万阳连同配置中的默认头发和墨镜一起装备，避免只发头像却缺少外观。
        for component in default_components(role_id):
            equip(component['item_id'],f"native:{role_gid}:{component['location']}")
    from .native_avatar_body import attach
    body=attach(service,c,account_id,roles)
    operations.extend(operation for row in body for operation in row.get('operations',[]))
    for index,item_id in enumerate(BAGS):
        gid=equip(item_id,'native:bag:'+str(item_id))
        equip(100013,f'native:{gid}:2')
        if index==0:
            for weapon,slot in ((100292,0),(100009,1),(100010,3)):
                equip(weapon,f'native:{gid}:{slot}')
    if changed:
        advance_revision(c,account_id,changed)
    result=dict(rule_version=VERSION,status='COMMITTED',operations=operations,backpacks=len(BAGS))
    c.insert('native_receipts',dict(id=identifier(),account_id=account_id,kind='starter.loadout',result=result,
                                  created_at=service.clock(),confirmed=True))
    event(service,c,account_id,'starter.equipped',result)
    return result
