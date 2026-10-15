"""单局节点凭据、就绪和逐事件确认；当前仅开放合成配置，不冒充原生接入。"""
import hmac
import json
from django.db.models import F, Q
from .security import new_token, token_hash
from .service import Fault, canonical


def enroll(service, c, actor, data):
    """Owner 为尚未启动的一局生成一次性展示的短期节点凭据；真实实例路径未证实时拒绝。"""
    if not service.test_profile:
        raise Fault('COMPATIBILITY_BLOCKED', 'Current-client native startup and transport are not verified', 409)
    match = c.select('matches', Q(id=data.get('match_id'), state='STARTING', lease_until__gt=service.clock())).fetchone()
    if not match or c.select('node_tickets', Q(match_id=data.get('match_id'))).fetchone():
        raise Fault('MATCH_STATE', 'A fresh unclaimed reservation is required', 409)
    token = new_token()
    c.insert('node_tickets', {'match_id': match['id'], 'token_hash': token_hash(token),
             'expires_at': match['lease_until'], 'evidence': 'TEST_PROFILE'})
    c.update('matches', Q(id=match['id']), {'node_token_hash': token_hash(token)})
    return {'match_id': match['id'], 'instance_id': match['instance_id'], 'epoch': match['epoch'],
            'snapshot_hash': token_hash(match['snapshot']), 'map_key': json.loads(match['snapshot'])['map_key'],
            'node_token': token, 'evidence': 'TEST_PROFILE', 'retail_ready': False}


def ready(service, token, data):
    """票据、实例、epoch、快照和健康声明全部匹配才就绪；声明仅作为合成契约证据。"""
    from .rooms import node_identity
    if not service.test_profile:
        raise Fault('COMPATIBILITY_BLOCKED', 'No verified native readiness probe exists', 409)
    if set(data) != {'match_id', 'instance_id', 'epoch', 'snapshot_hash', 'map_key', 'health'}:
        raise Fault('INVALID', 'Readiness must identify the exact frozen reservation')
    with service.db.transaction() as c:
        match = node_identity(service, c, token, data['match_id'], data['epoch'])
        ticket = c.select('node_tickets', Q(match_id=match['id'])).fetchone()
        snapshot = json.loads(match['snapshot'])
        if not ticket or data['instance_id'] != match['instance_id'] or data['snapshot_hash'] != token_hash(match['snapshot']) or data['map_key'] != snapshot['map_key']:
            raise Fault('STALE_INSTANCE', 'Readiness does not match the reservation', 409)
        health = data['health']
        if not isinstance(health, dict) or set(health) != {'map_loaded', 'world_ready', 'network_ready'} or any(value is not True for value in health.values()):
            raise Fault('NODE_HEALTH', 'Map, world and network must all be ready', 409)
        digest = token_hash(canonical(data))
        if ticket['ready_hash']:
            if match['state'] != 'ACTIVE':
                raise Fault('MATCH_STATE', 'Completed reservations cannot become ready again', 409)
            if ticket['ready_hash'] != digest:
                raise Fault('CONFLICT', 'Readiness already acknowledged with different content', 409)
        else:
            if match['state'] != 'STARTING' or ticket['expires_at'] <= service.clock():
                raise Fault('MATCH_STATE', 'Reservation is no longer startable', 409)
            for player in snapshot['players']:
                account = service._account(c, player['account_id'])
                service._active(c, account)
                if account['security_version'] != player['security_version'] or not c.select('match_players', Q(match_id=match['id'], account_id=account['id'], eligible=1)).fetchone():
                    raise Fault('STALE_ROSTER', 'Roster authorization changed before readiness', 409)
            c.update('node_tickets', Q(match_id=match['id']), {'ready_hash': digest})
            c.update('matches', Q(id=match['id']), {'state': 'ACTIVE', 'lease_until': service.clock() + 30})
            c.update('rooms', Q(id=match['room_id']), {'state': 'RUNNING', 'revision': F('revision') + 1})
            if snapshot.get('pve_rules'):
                from .pve import initial_state
                state = initial_state(snapshot['pve_rules'], snapshot['players'], service.clock())
                c.insert('pve_runs', {'match_id': match['id'], 'rules': canonical(snapshot['pve_rules']), 'state': canonical(state)})
        run = c.select('pve_runs', Q(match_id=match['id'])).fetchone()
        return {'match_id': match['id'], 'state': 'ACTIVE', 'pve': json.loads(run['state']) if run else None,
                'evidence': 'TEST_PROFILE', 'retail_ready': False}


def pending(service, token, data):
    """只返回该节点被指派的事件；已停止节点可在剩余租约内确认撤销，但不能续租或战斗。"""
    with service.db.transaction() as c:
        match = authenticated(service, c, token, data)
        ids = c.table('node_deliveries').filter(match_id=match['id'], status='PENDING').values('event_id')
        rows = c.select('outbox', Q(id__in=ids), columns=['event_key', 'type', 'account_id', 'payload'], order=('id',), limit=50)
        return [dict(row, payload=json.loads(row['payload'])) for row in rows]


def authenticated(service, c, token, data):
    """验证节点令牌和当前 epoch，不接受管理员或玩家 Bearer，限制为票据实例。"""
    from .rooms import node_identity
    if not service.test_profile:
        raise Fault('COMPATIBILITY_BLOCKED', 'No current-client node transport is verified', 409)
    match = node_identity(service, c, token, data.get('match_id'), data.get('epoch'))
    if not c.select('node_tickets', Q(match_id=match['id'], ready_hash__gt='')).fetchone():
        raise Fault('FORBIDDEN', 'Node readiness has not been acknowledged', 403)
    return match


def acknowledge(service, token, data):
    """确认事件版本及归属后更新该实例的记录；只有全部目标确认才标记全局送达。"""
    with service.db.transaction() as c:
        match = authenticated(service, c, token, data)
        event = c.select('outbox', Q(event_key=data.get('event_key'))).fetchone()
        delivery = c.select('node_deliveries', Q(event_id=event['id'], match_id=match['id'])).fetchone() if event else None
        if not delivery:
            raise Fault('FORBIDDEN', 'This event was not assigned to this node', 403)
        payload = json.loads(event['payload'])
        required = 'security_version' if event['type'] == 'security.revoked' else 'operation_id'
        if data.get(required) != payload.get(required) or required not in payload:
            raise Fault('ACK_CONFLICT', 'Acknowledgement does not match the delivered version', 409)
        if delivery['status'] != 'ACKED':
            c.update('node_deliveries', Q(event_id=event['id'], match_id=match['id']),
                     {'status': 'ACKED', 'acknowledged_at': service.clock()})
        if not c.count('node_deliveries', Q(event_id=event['id'], status='PENDING')).fetchone()[0]:
            c.update('outbox', Q(id=event['id']), {'status': 'DELIVERED', 'delivered_at': service.clock(), 'last_error': None})
        return {'event_key': event['event_key'], 'node_acknowledged': True, 'evidence': 'TEST_PROFILE', 'retail_sync_verified': False}
