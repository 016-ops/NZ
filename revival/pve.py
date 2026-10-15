"""持久化的合成 PvE 规则执行器；不提供原生渲染、碰撞、地图导航或游戏协议。"""
import hmac
import json
import math
from django.db.models import Q
from .security import token_hash
from .service import Fault, canonical, identifier, integer, text_field


def initial_state(rules, players, now):
    """从冻结规则及名单创建权威状态；所有测试数值明确来自合成规则而非原版推断。"""
    state = {'phase': 'RUNNING', 'stage_index': 0, 'stage_id': rules['stages'][0]['id'], 'attempt_id': identifier(),
             'wave': 0, 'pending': [], 'enemies': {}, 'entity_counter': 0, 'objectives': {}, 'target_hp': rules.get('target_hp'),
             'players': {p['account_id']: {'hp': rules['player_hp'], 'position': [0, 0], 'eligible': True,
                         'equipment': [e['item_key'] for e in p['equipment']], 'last_attack': 0,
                         'last_move': now, 'vehicle': None} for p in players},
             'vehicles': {key: {'driver': None, 'position': [0, 0]} for key in rules.get('vehicles', [])},
             'last_tick': now, 'revive': None, 'failed_at': None}
    advance(state, rules)
    return state


def advance(state, rules):
    """同时检查存活实体、待生成队列及目标；只在全部条件完成后自动推进波次或关卡。"""
    while state['phase'] == 'RUNNING' and not state['pending'] and not state['enemies']:
        stage = rules['stages'][state['stage_index']]
        if state['wave'] < len(stage['waves']):
            wave = stage['waves'][state['wave']]
            state['wave'] += 1
            state['pending'] = [dict(enemy) for enemy in wave]
            return
        if any(not state['objectives'].get(key) for key in stage.get('objectives', {})):
            return
        if state['stage_index'] + 1 == len(rules['stages']):
            state['phase'] = 'VICTORY'
            return
        state['stage_index'] += 1
        state.update(stage_id=rules['stages'][state['stage_index']]['id'], wave=0, objectives={})


def player(state, account_id):
    """攻击和机关触发必须来自存活且有资格的冻结玩家，不能伪造其他房间账号。"""
    value = state['players'].get(account_id)
    if not value or not value['eligible'] or value['hp'] <= 0:
        raise Fault('PLAYER_STATE', 'A living authorized participant is required', 409)
    return value


def fail_if_needed(state, now):
    """目标损坏或全队无法行动时进入限时续局窗口，期间不会发胜利奖励。"""
    if state['phase'] == 'RUNNING' and (state['target_hp'] == 0 or not any(p['eligible'] and p['hp'] > 0 for p in state['players'].values())):
        state.update(phase='FAILED', failed_at=now)


def tick(state, rules, now):
    """服务时钟驱动生成、直线路径测试 AI、仇恨和受击；不采信客户端时间或伤害值。"""
    if state['phase'] == 'FAILED':
        if now >= state['failed_at'] + rules['revive_window_seconds']:
            state['phase'] = 'DEFEAT'
        return
    if state['phase'] != 'RUNNING':
        return
    elapsed = max(0, min(5, now - state['last_tick']))
    if elapsed == 0:
        return
    state['last_tick'] = now
    for _ in range(min(rules['spawn_per_tick'], len(state['pending']))):
        template = state['pending'].pop(0)
        state['entity_counter'] += 1
        entity_id = state['attempt_id'] + ':' + str(state['entity_counter'])
        state['enemies'][entity_id] = dict(template, position=list(template['position']), max_hp=template['hp'], last_hit=now,
                                            threat={}, phase='normal')
    for enemy in state['enemies'].values():
        living = [(key, value) for key, value in state['players'].items() if value['eligible'] and value['hp'] > 0]
        if not living:
            break
        if rules['mode'] == 'defense':
            target_id, target = 'facility', {'position': rules['target_position']}
        else:
            target_id, target = max(living, key=lambda item: (enemy['threat'].get(item[0], 0), -math.dist(item[1]['position'], enemy['position']), item[0]))
        enemy['target'] = target_id
        distance = math.dist(target['position'], enemy['position'])
        if distance > enemy['attack_range']:
            step = min(enemy['speed'] * elapsed, distance)
            enemy['position'] = [round(a + (b - a) * step / distance, 6) for a, b in zip(enemy['position'], target['position'])]
        if math.dist(target['position'], enemy['position']) <= enemy['attack_range'] and now - enemy['last_hit'] >= enemy['attack_period']:
            damage = enemy['attack_damage'] * (2 if enemy['phase'] == 'enraged' else 1)
            if target_id == 'facility':
                state['target_hp'] = max(0, state['target_hp'] - damage)
            else:
                target['hp'] = max(0, target['hp'] - damage)
            enemy['last_hit'] = now
    fail_if_needed(state, now)
    advance(state, rules)


def position(value):
    """只允许有限测试地图内的二维整数坐标，拒绝 NaN、布尔值和无限移动。"""
    if not isinstance(value, list) or len(value) != 2:
        raise Fault('INVALID', 'Expected a two-dimensional position')
    return [integer(v, -100, 100, 'position') for v in value]


def apply_event(service, c, match, state, rules, kind, payload):
    """将白名单节点事件作用到状态；阶段推进和最终胜败始终由规则执行器决定。"""
    schemas = {'tick': set(), 'attack': {'account_id', 'enemy_id'}, 'move': {'account_id', 'position'},
               'objective.activate': {'account_id', 'objective_id'}, 'disconnect': {'account_id'},
               'vehicle.enter': {'account_id', 'vehicle_id'}, 'vehicle.leave': {'account_id', 'vehicle_id'},
               'vehicle.move': {'account_id', 'vehicle_id', 'position'},
               'restart.ready': {'revive_operation_id'}, 'restart.failed': {'revive_operation_id'}}
    if kind not in schemas or not isinstance(payload, dict) or set(payload) != schemas[kind]:
        raise Fault('INVALID', 'Unsupported authoritative event or payload fields')
    now = service.clock()
    if kind.startswith('restart.'):
        revive = state.get('revive')
        if state['phase'] != 'RESTARTING' or not revive or revive['operation_id'] != payload['revive_operation_id']:
            raise Fault('REVIVE_STATE', 'No matching pending restart', 409)
        if kind == 'restart.failed' or now >= revive['deadline']:
            refund(service, c, match, state)
        else:
            # 仅重开当前阶段，使用新 attempt_id；前面阶段不会再次生成奖励事件。
            fresh = initial_state(rules, json.loads(match['snapshot'])['players'], now)
            fresh.update(stage_index=state['stage_index'], stage_id=state['stage_id'], pending=[], wave=0)
            for key, value in state['players'].items():
                fresh['players'][key]['eligible'] = value['eligible']
            fresh['revive'] = dict(revive, status='APPLIED')
            advance(fresh, rules)
            state.clear()
            state.update(fresh)
        return
    if kind == 'tick':
        tick(state, rules, now)
        return
    if state['phase'] != 'RUNNING':
        raise Fault('PVE_STATE', 'Combat is not active', 409)
    account_id = payload['account_id']
    participant = state['players'].get(account_id) if kind == 'disconnect' else player(state, account_id)
    if participant is None:
        raise Fault('PLAYER_STATE', 'Participant does not belong to this match', 409)
    account = service._account(c, account_id)
    if kind != 'disconnect':
        service._active(c, account)
    roster = c.select('match_players', Q(match_id=match['id'], account_id=account_id, eligible=1)).fetchone()
    if kind != 'disconnect' and (not roster or roster['security_version'] != account['security_version']):
        raise Fault('STALE_ROSTER', 'Participant authorization has been revoked', 409)
    if kind == 'attack':
        enemy = state['enemies'].get(payload['enemy_id'])
        if not enemy:
            raise Fault('ENTITY', 'Enemy is no longer alive', 409)
        weapons = [rules['weapons'][key] for key in participant['equipment'] if key in rules['weapons']]
        if not weapons:
            raise Fault('EQUIPMENT', 'No configured weapon in the frozen loadout', 409)
        weapon = weapons[0]
        if now - participant['last_attack'] < weapon['period'] or math.dist(participant['position'], enemy['position']) > weapon['range']:
            raise Fault('ATTACK', 'Attack cooldown or range check failed', 409)
        participant['last_attack'] = now
        enemy['hp'] = max(0, enemy['hp'] - weapon['damage'])
        enemy['threat'][account_id] = enemy['threat'].get(account_id, 0) + weapon['damage']
        if enemy.get('boss') and enemy['hp'] <= enemy['max_hp'] // 2:
            enemy['phase'] = 'enraged'
        if enemy['hp'] == 0:
            state['last_kill'] = {'entity_id': payload['enemy_id'], 'account_id': account_id, 'boss': bool(enemy.get('boss'))}
            del state['enemies'][payload['enemy_id']]
    elif kind in ('move', 'vehicle.move'):
        destination = position(payload['position'])
        # 同一服务时刻的不同事件不能反复领取一秒位移额度；只累计上次提交后的真实时间。
        if math.dist(participant['position'], destination) > rules['player_speed'] * max(0, now - participant['last_move']):
            raise Fault('MOVEMENT', 'Movement exceeds the configured speed', 409)
        if kind == 'vehicle.move':
            vehicle = state['vehicles'].get(payload['vehicle_id'])
            if not vehicle or vehicle['driver'] != account_id:
                raise Fault('VEHICLE', 'Only the assigned driver may move this vehicle', 403)
            vehicle['position'] = destination
        elif participant['vehicle'] is not None:
            raise Fault('VEHICLE', 'Use vehicle movement while driving', 409)
        participant.update(position=destination, last_move=now)
    elif kind == 'objective.activate':
        stage = rules['stages'][state['stage_index']]
        goal = stage.get('objectives', {}).get(payload['objective_id'])
        if goal is None or state['pending'] or state['enemies'] or state['wave'] != len(stage['waves']):
            raise Fault('OBJECTIVE', 'Objective is not yet unlocked', 409)
        if math.dist(participant['position'], goal) > 2:
            raise Fault('OBJECTIVE', 'Participant is too far from the objective', 409)
        state['objectives'][payload['objective_id']] = True
    elif kind.startswith('vehicle.'):
        vehicle = state['vehicles'].get(payload['vehicle_id'])
        if not vehicle:
            raise Fault('VEHICLE', 'Vehicle is not configured')
        if kind == 'vehicle.enter':
            if vehicle['driver'] is not None or participant['vehicle'] is not None or math.dist(participant['position'], vehicle['position']) > 2:
                raise Fault('VEHICLE', 'Vehicle is occupied or out of reach', 409)
            vehicle['driver'], participant['vehicle'] = account_id, payload['vehicle_id']
        elif vehicle['driver'] == account_id:
            vehicle['driver'], participant['vehicle'] = None, None
        else:
            raise Fault('VEHICLE', 'Participant is not the driver', 403)
    elif kind == 'disconnect':
        participant.update(eligible=False, hp=0)
        if participant['vehicle']:
            state['vehicles'][participant['vehicle']]['driver'] = None
            participant['vehicle'] = None
        c.update('match_players', Q(match_id=match['id'], account_id=account_id), {'eligible': 0})
        service._detach(c, account_id)
    fail_if_needed(state, now)
    advance(state, rules)


def events(service, token, data):
    """有序事件与状态/奖励同事务提交；持久重放先校验身份，再读取原结果，不重新结算。"""
    from .rooms import node_identity
    from .nodes import authenticated
    if not service.test_profile:
        raise Fault('COMPATIBILITY_BLOCKED', 'No current-client authoritative event transport exists', 409)
    if set(data) != {'match_id', 'epoch', 'event_id', 'sequence', 'stage_id', 'attempt_id', 'kind', 'payload'} or len(canonical(data).encode()) > 65536:
        raise Fault('INVALID', 'Invalid event envelope')
    event_id = text_field(data['event_id'], 'event_id', 128)
    sequence = integer(data['sequence'], 1, 2147483647, 'sequence')
    digest = token_hash(canonical(data))
    with service.db.transaction() as c:
        match = c.select('matches', Q(id=data['match_id'])).fetchone()
        if not match or not isinstance(token, str) or not hmac.compare_digest(match['node_token_hash'], token_hash(token)) or match['epoch'] != data['epoch']:
            raise Fault('FORBIDDEN', 'Instance identity rejected', 403)
        previous = c.select('match_events', Q(match_id=match['id'], event_id=event_id)).fetchone()
        if previous:
            if previous['payload_hash'] != digest:
                raise Fault('EVENT_CONFLICT', 'Event ID was reused with different content', 409)
            return json.loads(previous['result'])
        match = authenticated(service, c, token, data)
        if match['state'] != 'ACTIVE':
            raise Fault('MATCH_STATE', 'Only an active instance may submit new events', 409)
        latest = c.maximum('match_events', 'sequence', Q(match_id=match['id']), fallback=0).fetchone()[0]
        if sequence != latest + 1:
            raise Fault('EVENT_SEQUENCE', 'Events must be contiguous and ordered', 409)
        run = c.select('pve_runs', Q(match_id=match['id'])).fetchone()
        if not run:
            raise Fault('PVE_STATE', 'No frozen PvE rules for this match', 409)
        state, rules = json.loads(run['state']), json.loads(run['rules'])
        if data['stage_id'] != state['stage_id'] or data['attempt_id'] != state['attempt_id']:
            raise Fault('STALE_STAGE', 'Event refers to an earlier stage or attempt', 409)
        # 制裁/退出可能已经由控制面提交，节点尚未收到通知时也不能继续把旧玩家视为有效目标。
        for account_id, participant in state['players'].items():
            roster = c.select('match_players', Q(match_id=match['id'], account_id=account_id, eligible=1)).fetchone()
            account = service._account(c, account_id)
            try:
                service._active(c, account)
                authorized = bool(roster) and roster['security_version'] == account['security_version']
            except Fault:
                authorized = False
            if not authorized:
                participant.update(eligible=False, hp=0)
                if participant['vehicle']:
                    state['vehicles'][participant['vehicle']]['driver'] = None
                    participant['vehicle'] = None
        fail_if_needed(state, service.clock())
        apply_event(service, c, match, state, rules, data['kind'], data['payload'])
        c.update('pve_runs', Q(match_id=match['id']), {'state': canonical(state)})
        result = {'match_id': match['id'], 'sequence': sequence, 'state': state, 'evidence': 'TEST_PROFILE', 'retail_verified': False}
        if state['phase'] in ('VICTORY', 'DEFEAT'):
            from .assets import settle
            result['settlement'] = settle(service, token, {'match_id': match['id'], 'epoch': match['epoch'],
                                                          'outcome': 'VICTORY' if state['phase'] == 'VICTORY' else 'FAILURE'})
        c.insert('match_events', {'match_id': match['id'], 'event_id': event_id, 'sequence': sequence, 'payload_hash': digest, 'result': canonical(result)})
        return result


def request_revive(service, c, actor, data):
    """续局先原子扣一次道具并冻结重开操作，节点确认成功才恢复战斗；重复请求不再次扣款。"""
    from .assets import mutate, require_catalog
    if not service.test_profile:
        raise Fault('COMPATIBILITY_BLOCKED', 'Current-client revive transport is not verified', 409)
    require_catalog(service, data)
    match = c.select('matches', Q(id=data.get('match_id'), state='ACTIVE', lease_until__gt=service.clock())).fetchone()
    run = c.select('pve_runs', Q(match_id=data.get('match_id'))).fetchone()
    if not match or not run:
        raise Fault('PVE_STATE', 'No active PvE run', 409)
    state, rules = json.loads(run['state']), json.loads(run['rules'])
    roster = c.select('match_players', Q(match_id=match['id'], account_id=actor['id'], eligible=1)).fetchone()
    if not roster or state['phase'] != 'FAILED' or service.clock() >= state['failed_at'] + rules['revive_window_seconds']:
        raise Fault('REVIVE_STATE', 'No eligible restart is available', 409)
    if data.get('stage_id') != state['stage_id'] or data.get('attempt_id') != state['attempt_id']:
        raise Fault('STALE_STAGE', 'Revive refers to an earlier stage or attempt', 409)
    item = c.select('inventory', Q(id=data.get('instance_id'), account_id=actor['id'], item_key=rules['revive_item'], expires_at__isnull=True)).fetchone()
    if not item:
        raise Fault('INVENTORY', 'An owned permanent revive token is required', 409)
    operation = mutate(service, c, actor, actor['id'], [{'type': 'consume', 'instance_id': item['id'], 'quantity': 1}], data['reason'], 'REVIVE:' + match['id'])
    state['revive'] = {'operation_id': operation['operation_id'], 'account_id': actor['id'], 'item_key': item['item_key'],
                       'deadline': service.clock() + 10, 'status': 'PENDING'}
    state['phase'] = 'RESTARTING'
    c.update('pve_runs', Q(match_id=match['id']), {'state': canonical(state)})
    return {'revive_operation_id': operation['operation_id'], 'phase': 'RESTARTING', 'evidence': 'TEST_PROFILE'}


def refund(service, c, match, state):
    """重开失败/超时追加补偿流水；库存无法安全退回时追加待复核，绝不吞掉扣款。"""
    from .assets import mutate
    revive = state['revive']
    c.savepoint('revive_refund')
    try:
        operation = mutate(service, c, {'id': match['instance_id'], 'role': 'node'}, revive['account_id'],
                           [{'type': 'item', 'item_key': revive['item_key'], 'quantity': 1}], 'Restart failed; refund consumed token',
                           'REVIVE_REFUND', linked=revive['operation_id'])
        revive.update(status='REFUNDED', refund_operation_id=operation['operation_id'])
    except Fault:
        c.rollback('revive_refund')
        review_id = identifier()
        c.insert('asset_operations', {'id': review_id, 'account_id': revive['account_id'], 'actor': match['instance_id'],
                 'source': 'REVIVE_REFUND', 'reason': 'Restart failed; inventory refund requires manual review', 'created_at': service.clock(),
                 'status': 'MANUAL_REVIEW', 'linked_operation': revive['operation_id']})
        revive.update(status='MANUAL_REVIEW', review_operation_id=review_id)
    finally:
        c.release('revive_refund')
    state['phase'] = 'FAILED'


def recover_timeouts(service):
    """维护进程在节点消失后也能补偿超时重开，不依赖节点再次上线。"""
    with service.db.transaction() as c:
        count = 0
        # 只检查尚未结算的实例，避免遍历无限增长的历史运行状态。
        for run in c.select('pve_runs', Q(match__state__in=['ACTIVE', 'FAILED', 'STOPPED']) & ~Q(match_id__in=c.table('settlements').values('match_id'))):
            state = json.loads(run['state'])
            if state['phase'] == 'RESTARTING' and state['revive']['deadline'] <= service.clock():
                match = c.select('matches', Q(id=run['match_id'])).fetchone()
                refund(service, c, match, state)
                c.update('pve_runs', Q(match_id=run['match_id']), {'state': canonical(state)})
                count += 1
        return {'refunded_or_reviewed': count}
