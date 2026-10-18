"""角色隐藏身体部件的持久挂接和协议投影；原生 Location=-1 与内部唯一槽分别处理。"""
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from .native_features_catalog import avatar_catalog

VERSION = 'cn2020-avatar-body-compat-v1'


@lru_cache(maxsize=1)
def catalog():
    """冻结 296 个原始默认手臂/身体资源；运行中不能悄悄更改家族或资源类型。"""
    raw = (Path(__file__).resolve().parents[1] / 'config/cn2020-avatar-body-compat-v1.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != 'f308ca0fe60e0fd94c271a224b3d4f987eb76427a0a27aac108dbb0d8a5c486a':
        raise ValueError('AVATAR_BODY_CATALOG_MISMATCH')
    return json.loads(raw)


def parts(role_id):
    """按原始资源家族取默认身体，不根据角色名称、商品行号或客户端请求猜测。"""
    role = avatar_catalog()['roles'].get(str(role_id))
    return [r for r in catalog()['parts'].values() if role and r['family'] == role['family']]


def compatible(item, parent):
    """同一家族的隐藏默认部件才允许跟随本人角色；不能挂到根、背包或其他家族。"""
    part = catalog()['parts'].get(str(item.get('item_id')))
    role = avatar_catalog()['roles'].get(str(parent.get('item_id')))
    return bool(part and role and item.get('location') == -1 and part['family'] == role['family'])


def slot(item_id, parent_gid):
    """内部为手臂和身体保留不同槽；body1/body2 不是发给客户端的虚构 Location。"""
    part = catalog()['parts'][str(item_id)]
    return f"native:{parent_gid}:body{part['resource_type']}"


def wire_binding(item, owned):
    """从持久关系生成原生 OwnerGID/Location；损坏关系拒绝，两个身体资源均保持 -1。"""
    value = item.get('equipment_slot')
    if not value:
        return 0, -1
    fields = value.split(':')
    if len(fields) != 3 or fields[0] != 'native':
        raise ValueError('INVALID_NATIVE_EQUIPMENT_SLOT')
    if fields[1] == 'bag':
        if item['location'] != 12 or fields[2] != str(item['item_id']):
            raise ValueError('INVALID_NATIVE_BACKPACK_SLOT')
        return 1, 12
    parent_gid = int(fields[1])
    if fields[2].startswith('body'):
        if (not compatible(item, owned.get(parent_gid, {})) or value != slot(item['item_id'], parent_gid)):
            raise ValueError('INVALID_NATIVE_BODY_PARENT')
        return parent_gid, -1
    location = int(fields[2])
    if location != item['location']:
        raise ValueError('NATIVE_EQUIPMENT_LOCATION_MISMATCH')
    return parent_gid, location


def attach(service, c, account_id, role_instances):
    """调用方事务内补齐真实部件及唯一父关系；缺项走账本、重试无新增、同期限随角色续费。"""
    from django.db.models import Q
    from .catalogs import NATIVE_INVENTORY_VERSION
    from .native_actions import apply, event, item_rewards
    from .native_inventory import advance_revision
    from .service import Fault, identifier

    if not role_instances:
        return []
    active = Q(expires_at__isnull=True) | Q(expires_at__gt=service.clock())
    roles = c.select('inventory', Q(account_id=account_id, id__in=role_instances) & active, order=('id',))
    changes, operations = [], []
    for role in roles:
        definitions = parts(int(role['item_key'].removeprefix('cn2020:')))
        if not definitions:
            continue
        binding = c.select('native_inventory_bindings', Q(inventory_id=role['id'], rules_version=NATIVE_INVENTORY_VERSION)).fetchone()
        if not binding:
            raise Fault('NATIVE_CATALOG_CONFLICT', '角色缺少稳定 GID', 409)
        parent = binding['id']+1000
        for definition in definitions:
            key = 'cn2020:'+str(definition['item_id'])
            position = slot(definition['item_id'], parent)
            existing = c.select('equipment', Q(account_id=account_id, slot=position)).fetchone()
            item = c.select('inventory', Q(account_id=account_id, id=existing['inventory_id'])).fetchone() if existing else None
            if existing and (not item or item['item_key'] != key):
                raise Fault('NATIVE_CATALOG_CONFLICT', '角色身体部件关系冲突', 409)
            if item and item['expires_at'] == role['expires_at']:
                continue
            if item and item['expires_at'] is not None and role['expires_at'] is not None and item['expires_at'] < role['expires_at']:
                duration = role['expires_at']-max(service.clock(), item['expires_at'])
                operations.extend(apply(service, c, account_id, [dict(type='renew', instance_id=item['id'],
                    duration_seconds=duration, expires_at_exact=role['expires_at'])], 'native.avatar.body'))
            else:
                # 复用同期限、未挂接的本人部件；独立角色实例不能共享同一个身体 GID。
                used = c.table('equipment').filter(account_id=account_id).values_list('inventory_id', flat=True)
                query = Q(account_id=account_id, item_key=key, expires_at=role['expires_at']) & ~Q(id__in=used)
                item = c.select('inventory', query, order=('id',), limit=1).fetchone()
                if not item:
                    duration = None if role['expires_at'] is None else role['expires_at']-service.clock()
                    grants=item_rewards(service, [dict(item_id=definition['item_id'], duration_seconds=duration)])
                    if role['expires_at'] is not None:
                        grants[0]['expires_at_exact']=role['expires_at']
                    operations.extend(apply(service, c, account_id, grants, 'native.avatar.body'))
                    item = c.select('inventory', query, order=('id',), limit=1).fetchone()
                c.equip(account_id, position, item['id'])
            child = c.select('native_inventory_bindings', Q(inventory_id=item['id'], rules_version=NATIVE_INVENTORY_VERSION)).fetchone()
            if not child:
                raise Fault('NATIVE_CATALOG_CONFLICT', '身体部件缺少稳定 GID', 409)
            changes.append(dict(instance_id=item['id'], gid=child['id']+1000, target=parent,
                location=-1, equipment_slot=position, body_rule_version=VERSION))
    if changes:
        # 挂接和账本共用外层事务；最后事件失败时连同新部件、期限、GID 与父关系一起回滚。
        advance_revision(c, account_id, [r['instance_id'] for r in changes])
        payload = dict(rule_version=VERSION, equipment=changes, operations=operations)
        service._audit(c, account_id, 'native.avatar.body', account_id, identifier(), 'prepare owned role body parts', payload)
        event(service, c, account_id, 'native.avatar.body', payload)
        changes[0]['operations'] = operations
    return changes


def prepare_equipped(service, c, account_id):
    """游戏凭据兑换时向前补齐旧账号正在使用的角色；不发新角色、不覆盖旧装饰或重写旧回执。"""
    if not service.catalog.get('native_assets'):
        return []
    roles = list(c.table('equipment').filter(account_id=account_id,
        slot__in=('native:1:10', 'native:1:11', 'native:1:64')).values_list('inventory_id', flat=True))
    return attach(service, c, account_id, roles)
