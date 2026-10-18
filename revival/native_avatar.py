"""角色默认装饰的事务内挂接；只使用本人已有有效实例，不增发、不覆盖已有搭配或转移他人资产。"""
from django.db.models import Q
from .native_features_catalog import avatar_catalog


def default_components(role_id):
    """返回固定家族各槽唯一默认装饰；隐藏身体由独立资源规则和持久关系处理。"""
    rules=avatar_catalog()
    role=rules['roles'].get(str(role_id))
    return [r for r in rules['components'].values() if role and r['default'] and r['family']==role['family']]


def attach_owned_defaults(service,c,account_id,role_instances):
    """调用者持有业务事务；优先同期限未挂接组件，保持GID及旧角色子树，返回准确变更。"""
    if not role_instances:
        return []
    from .native_inventory import advance_revision
    from .native_actions import event
    from .service import identifier
    from .catalogs import NATIVE_INVENTORY_VERSION
    active=Q(expires_at__isnull=True)|Q(expires_at__gt=service.clock())
    roles=c.select('inventory',Q(account_id=account_id,id__in=role_instances)&active,order=('id',))
    result=[]
    for role in roles:
        defaults=default_components(int(role['item_key'].removeprefix('cn2020:')))
        if not defaults:
            continue
        binding=c.select('native_inventory_bindings',Q(inventory_id=role['id'],rules_version=NATIVE_INVENTORY_VERSION)).fetchone()
        if not binding:
            continue
        parent=binding['id']+1000
        for definition in defaults:
            slot=f"native:{parent}:{definition['location']}"
            if c.select('equipment',Q(account_id=account_id,slot=slot)):
                continue  # 已有搭配包括非默认装饰，不能被重登/重复购买静默覆盖。
            used=c.table('equipment').filter(account_id=account_id).values_list('inventory_id',flat=True)
            candidates=c.select('inventory',Q(account_id=account_id,item_key='cn2020:'+str(definition['item_id']))&active&~Q(id__in=used),order=('id',))
            candidates=sorted(candidates,key=lambda r:(r['expires_at']!=role['expires_at'],r['id']))
            if not candidates:
                continue  # 不凭角色权益凭空发放组件；商品或明确初装规则负责发放。
            item=candidates[0]
            child=c.select('native_inventory_bindings',Q(inventory_id=item['id'],rules_version=NATIVE_INVENTORY_VERSION)).fetchone()
            if not child:
                continue
            c.equip(account_id,slot,item['id'])
            result.append(dict(instance_id=item['id'],gid=child['id']+1000,target=parent,location=definition['location']))
    if result:
        advance_revision(c,account_id,[r['instance_id'] for r in result])
        request=identifier()
        payload=dict(rule_version=avatar_catalog()['version'],equipment=result)
        service._audit(c,account_id,'native.avatar.defaults',account_id,request,'attach owned default components',payload)
        event(service,c,account_id,'native.avatar.equipped',payload)
    from .native_avatar_body import attach
    return result + attach(service,c,account_id,role_instances)
