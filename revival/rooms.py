"""本地房间、席位、修订冻结与合成节点租约；不操作原客户端战斗。"""
from django.db.models import F, Q
import json
import hmac

from .security import new_token, token_hash
from .service import Fault, canonical, identifier, integer, text_field


# 释放指定玩家席位，按最小席位转移房主；最后成员离开时关闭房间并停止未完成实例，全部修改由调用者事务保护。
def leave_room(service, c, account_id, room_id):
    """释放指定玩家席位，按最小席位转移房主；最后成员离开时关闭房间并停止未完成实例，全部修改由调用者事务保护。"""
    room = c.select('rooms', Q(id=room_id)).fetchone()
    c.delete('room_members', (Q(account_id=account_id) & Q(room_id=room_id)))
    remaining = c.select('room_members', Q(room_id=room_id), columns=['account_id'], order=('slot',), limit=1).fetchone()
    if not remaining:
        c.update('rooms', Q(id=room_id), {'state': 'CLOSED', 'revision': F('revision') + 1})
        if room['match_id']:
            c.update('matches', (Q(id=room['match_id']) & Q(state__in=['STARTING','ACTIVE'])), {'state': 'STOPPED'})
    elif room['owner'] == account_id:
        c.update('rooms', Q(id=room_id), {'owner': remaining['account_id'], 'revision': F('revision') + 1})
    else:
        c.update('rooms', Q(id=room_id), {'revision': F('revision') + 1})


# 保持建房、占席、准备、配置、取消及开始的状态机；开始前核对 revision 和全部成员资格，再冻结目录、玩家安全版本与有效装备。
def room_command(service, c, actor, action, data):
    """保持建房、占席、准备、配置、取消及开始的状态机；开始前核对 revision 和全部成员资格，再冻结目录、玩家安全版本与有效装备。"""
    account_id = actor['id']
    if not c.select('characters', Q(account_id=account_id)).fetchone():
        raise Fault('CHARACTER', 'Initialize a local character first', 409)
    room_id = data.get('room_id')
    if action == 'room.create':
        if c.select('room_members', Q(account_id=account_id)).fetchone():
            raise Fault('ALREADY_IN_ROOM', 'Player already belongs to a room', 409)
        map_key = text_field(data.get('map_key'), 'map_key', 100)
        definition = service.catalog['maps'].get(map_key)
        if definition is None:
            raise Fault('COMPATIBILITY_BLOCKED', 'No verified current-client map/seat rules', 409)
        room_id = identifier()
        c.insert('rooms', {'id': room_id, 'owner': account_id, 'map_key': map_key, 'capacity': definition['capacity'], 'state': 'OPEN'})
        c.insert('room_members', {'account_id': account_id, 'room_id': room_id, 'slot': 0, 'ready': 0, 'joined_at': service.clock()})
        return {'room_id': room_id, 'revision': 1, 'state': 'OPEN', 'evidence': service.catalog['evidence']}
    room = c.select('rooms', Q(id=room_id)).fetchone()
    if room is None or room['state'] == 'CLOSED':
        raise Fault('NOT_FOUND', 'Room is closed or missing', 404)
    member = c.select('room_members', Q(account_id=account_id)).fetchone()
    if action == 'room.join':
        if member:
            if member['room_id'] == room_id:
                return {'room_id': room_id, 'slot': member['slot'], 'revision': room['revision']}
            raise Fault('ALREADY_IN_ROOM', 'Leave the current room first', 409)
        if room['state'] != 'OPEN':
            raise Fault('ROOM_STATE', 'Room is not open', 409)
        slots = {r['slot'] for r in c.select('room_members', Q(room_id=room_id), columns=['slot'])}
        free = [slot for slot in range(room['capacity']) if slot not in slots]
        if not free:
            raise Fault('ROOM_FULL', 'No free seats', 409)
        c.insert('room_members', {'account_id': account_id, 'room_id': room_id, 'slot': free[0], 'ready': 0, 'joined_at': service.clock()})
        c.update('rooms', Q(id=room_id), {'revision': F('revision') + 1})
        return {'room_id': room_id, 'slot': free[0], 'revision': room['revision'] + 1}
    if not member or member['room_id'] != room_id:
        raise Fault('FORBIDDEN', 'Not a member of this room', 403)
    if action == 'room.leave':
        c.update('match_players', (Q(account_id=account_id) & Q(match_id=room['match_id'])), {'eligible': 0})
        leave_room(service, c, account_id, room_id)
        return {'left': True, 'room_id': room_id}
    if action == 'room.ready':
        if room['state'] != 'OPEN' or type(data.get('ready')) is not bool:
            raise Fault('ROOM_STATE', 'Ready requires an open room and boolean value', 409)
        c.update('room_members', Q(account_id=account_id), {'ready': int(data['ready'])})
        c.update('rooms', Q(id=room_id), {'revision': F('revision') + 1})
        return {'room_id': room_id, 'ready': data['ready'], 'revision': room['revision'] + 1}
    if room['owner'] != account_id:
        raise Fault('FORBIDDEN', 'Only the current room owner can manage the room', 403)
    if action == 'room.cancel':
        if room['state'] not in ('ALLOCATING', 'LOADING'):
            raise Fault('ROOM_STATE', 'Cannot cancel a running or open room', 409)
        c.update('matches', Q(id=room['match_id']), {'state': 'STOPPED'})
        c.update('rooms', Q(id=room_id), {'state': 'OPEN', 'match_id': None, 'revision': F('revision') + 1})
        c.update('room_members', Q(room_id=room_id), {'ready': 0})
        return {'room_id': room_id, 'state': 'OPEN', 'revision': room['revision'] + 1}
    if room['state'] != 'OPEN':
        raise Fault('ROOM_STATE', 'Room configuration is frozen after start', 409)
    if data.get('expected_revision') != room['revision']:
        raise Fault('REVISION_CONFLICT', 'Room changed; refresh before retrying', 409)
    if action == 'room.configure':
        map_key = text_field(data.get('map_key'), 'map_key', 100)
        definition = service.catalog['maps'].get(map_key)
        if definition is None:
            raise Fault('COMPATIBILITY_BLOCKED', 'Map is unverified', 409)
        seats = c.maximum('room_members', 'slot', Q(room_id=room_id)).fetchone()[0]
        if seats is not None and seats >= definition['capacity']:
            raise Fault('ROOM_FULL', 'New map cannot hold current seats', 409)
        c.update('rooms', Q(id=room_id), {'map_key': map_key, 'capacity': definition['capacity'], 'revision': F('revision') + 1})
        c.update('room_members', Q(room_id=room_id), {'ready': 0})
        return {'room_id': room_id, 'revision': room['revision'] + 1}
    if action == 'room.start':
        members = list(c.select('room_members', Q(room_id=room_id), order=('slot',)))
        if not members or any(not p['ready'] for p in members):
            raise Fault('NOT_READY', 'All members must be ready', 409)
        players = []
        for member in members:
            account = service._account(c, member['account_id'])
            service._active(c, account)
            players.append({'account_id': account['id'], 'security_version': account['security_version'],
                            'slot': member['slot'], 'equipment': [dict(r) for r in c.equipment_snapshot(account['id'], service.clock())]})
        match_id, instance_id, epoch = identifier(), identifier(), identifier()
        snapshot = {'catalog': service.catalog['id'], 'map_key': room['map_key'], 'revision': room['revision'],
                    'players': players, 'reward': service.catalog.get('reward', {}), 'evidence': service.catalog['evidence']}
        c.insert('matches', {'id': match_id, 'room_id': room_id, 'instance_id': instance_id, 'epoch': epoch, 'state': 'STARTING', 'snapshot': canonical(snapshot), 'node_token_hash': '', 'created_at': service.clock(), 'lease_until': service.clock() + 30})
        for player in players:
            c.insert('match_players', {'match_id': match_id, 'account_id': player['account_id'], 'security_version': player['security_version']})
        c.update('rooms', Q(id=room_id), {'state': 'ALLOCATING', 'match_id': match_id, 'revision': F('revision') + 1})
        return {'match_id': match_id, 'instance_id': instance_id, 'epoch': epoch, 'state': 'STARTING',
                'retail_ready': False, 'evidence': service.catalog['evidence']}
    raise Fault('NOT_FOUND', 'Unknown room action', 404)


# 只对显式合成 profile 开放的私有测试注入，不注册 HTTP 路由；生成独立节点令牌及租约，不能代表真实游戏实例。
def test_host_ready(service, match_id):
    """仅合成测试 harness 可以注入节点就绪；HTTP 没有开放该入口，真实节点接入仍未实现。"""
    if not service.test_profile:
        raise Fault('COMPATIBILITY_BLOCKED', 'No native current-client host is verified', 409)
    with service.db.transaction() as c:
        match = c.select('matches', Q(id=match_id)).fetchone()
        if not match or match['state'] != 'STARTING' or match['lease_until'] <= service.clock():
            raise Fault('MATCH_STATE', 'Reservation is no longer startable', 409)
        token = new_token()
        c.update('matches', Q(id=match_id), {'state': 'ACTIVE', 'node_token_hash': token_hash(token), 'lease_until': service.clock() + 30})
        c.update('rooms', Q(id=match['room_id']), {'state': 'RUNNING', 'revision': F('revision') + 1})
        return {'node_token': token, 'match_id': match_id, 'instance_id': match['instance_id'], 'epoch': match['epoch'], 'evidence': 'TEST_PROFILE'}


# 清理没有有效会话的席位，并使超时实例失败、房间重新开放；与注册、制裁、心跳共用部署锁，避免维护和请求互相覆盖。
def maintain(service):
    """清理没有有效会话的席位，并使超时实例失败、房间重新开放；与注册、制裁、心跳共用部署锁，避免维护和请求互相覆盖。"""
    with service.db.transaction() as c:
        # Expired sessions cannot retain a seat. This is a local test policy, not a recovered retail rule.
        members = list(c.expired_members(service.clock()))
        for member in members:
            service._detach(c, member['account_id'])
        expired = list(c.select('matches', (Q(state__in=['STARTING','ACTIVE']) & Q(lease_until__lte=service.clock())), columns=['id', 'room_id']))
        for match in expired:
            c.update('matches', Q(id=match['id']), {'state': 'FAILED'})
            c.update('rooms', (Q(id=match['room_id']) & ~Q(state='CLOSED')), {'state': 'OPEN', 'match_id': None, 'revision': F('revision') + 1})
            c.update('room_members', Q(room_id=match['room_id']), {'ready': 0})
        return {'expired_matches': len(expired), 'expired_members': len(members)}


# 身份、epoch 与未过期租约核验通过后，仅 ACTIVE 实例可以续租；失联后不能凭旧令牌恢复授权。
def heartbeat(service, token, match_id, epoch):
    """身份、epoch 与未过期租约核验通过后，仅 ACTIVE 实例可以续租；失联后不能凭旧令牌恢复授权。"""
    with service.db.transaction() as c:
        match = node_identity(service, c, token, match_id, epoch)
        if match['state'] != 'ACTIVE':
            raise Fault('MATCH_STATE', 'Only active hosts can renew leases', 409)
        c.update('matches', Q(id=match_id), {'lease_until': service.clock() + 30})
        return {'lease_until': service.clock() + 30}


# 对节点令牌摘要进行恒定时间比较，随后校验 epoch 和期限；玩家会话不能伪造节点心跳或结算。
def node_identity(service, c, token, match_id, epoch):
    """对节点令牌摘要进行恒定时间比较，随后校验 epoch 和期限；玩家会话不能伪造节点心跳或结算。"""
    match = c.select('matches', Q(id=match_id)).fetchone()
    if not match or not isinstance(token, str) or not match['node_token_hash'] or not hmac.compare_digest(token_hash(token), match['node_token_hash']):
        raise Fault('FORBIDDEN', 'Instance identity rejected', 403)
    if match['epoch'] != epoch or match['lease_until'] <= service.clock():
        raise Fault('STALE_INSTANCE', 'Instance epoch or lease is stale', 409)
    return match
