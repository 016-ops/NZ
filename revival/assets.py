"""统一钱包、库存与不可改写流水；购物、补偿、合成结算及 outbox 共用事务。"""
from django.db.models import F, Q
import json

from .service import Fault, canonical, identifier, integer, text_field
from .security import token_hash


# 提交必须携带当前目录版本；预览以后目录变化时拒绝提交，防止以旧价格或旧资源规则写入。
def require_catalog(service, data):
    """提交必须携带当前目录版本；预览以后目录变化时拒绝提交，防止以旧价格或旧资源规则写入。"""
    if data.get('catalog_version') != service.catalog['id']:
        raise Fault('CATALOG_CONFLICT', 'Catalog version must match the current service', 409)


# 只接受已验证币种与非零整数增量；上下界来自 profile 目录，不猜测真实客户端币种。
def currency_value(service, code, delta):
    """只接受已验证币种与非零整数增量；上下界来自 profile 目录，不猜测真实客户端币种。"""
    definition = service.catalog['currencies'].get(code)
    if definition is None:
        raise Fault('UNVERIFIED_RESOURCE', 'Unknown or unverified currency')
    integer(delta, -definition['max'], definition['max'], 'delta')
    if delta == 0:
        raise Fault('INVALID', 'Delta must not be zero')
    return definition


# 统一资产入口：逐项验证余额、库存容量、期限与归属，写版本化余额/实例及不可改写流水，最后追加 outbox；任一项失败由外层回滚全部改动。
def mutate(service, c, actor, account_id, changes, reason, source, linked=None):
    """统一资产入口：逐项验证余额、库存容量、期限与归属，写版本化余额/实例及不可改写流水，最后追加 outbox；任一项失败由外层回滚全部改动。"""
    service._account(c, account_id)
    if not c.select('characters', Q(account_id=account_id)).fetchone():
        raise Fault('CHARACTER', 'Target has no local character', 409)
    if not isinstance(changes, list) or not 1 <= len(changes) <= 20:
        raise Fault('INVALID', 'Provide 1 to 20 asset changes')
    if actor.get('role') == 'resources' and sum(abs(x['delta']) for x in changes
                                                if isinstance(x, dict) and x.get('type') == 'currency'
                                                and type(x.get('delta')) is int) > 10000:
        raise Fault('LIMIT', 'Resource administrator per-operation currency limit is 10000', 403)
    operation_id = identifier()
    c.insert('asset_operations', {'id': operation_id, 'account_id': account_id, 'actor': actor['id'], 'source': source, 'reason': reason, 'created_at': service.clock(), 'status': 'COMMITTED', 'linked_operation': linked})
    for index, change in enumerate(changes):
        kind = change.get('type') if isinstance(change, dict) else None
        instance_id = None
        detail = {}
        if kind == 'currency':
            code, delta = change.get('code'), change.get('delta')
            definition = currency_value(service, code, delta)
            if actor.get('role') == 'resources' and abs(delta) > 10000:
                raise Fault('LIMIT', 'Resource administrator per-change limit is 10000', 403)
            wallet = c.select('wallets', (Q(account_id=account_id) & Q(currency=code))).fetchone()
            before = wallet['balance'] if wallet else 0
            after = before + delta
            if not definition['min'] <= after <= definition['max']:
                raise Fault('BALANCE', 'Insufficient funds or currency range exceeded', 409)
            if wallet:
                c.update('wallets', (Q(account_id=account_id) & Q(currency=code)), {'balance': after, 'version': F('version') + 1})
            else:
                c.insert('wallets', {'account_id': account_id, 'currency': code, 'balance': after, 'version': 1})
            resource = code
            detail = {'version_before': wallet['version'] if wallet else 0, 'version_after': wallet['version'] + 1 if wallet else 1}
        elif kind == 'item':
            key = change.get('item_key')
            definition = service.catalog['items'].get(key)
            if definition is None:
                raise Fault('UNVERIFIED_RESOURCE', 'Unknown or unverified item')
            delta = integer(change.get('quantity'), 1, definition['stack'], 'quantity')
            duration = change.get('duration_seconds')
            if duration is None:
                if not definition['permanent']:
                    raise Fault('INVALID', 'Item requires a finite duration')
                expires = None
            else:
                integer(duration, 1, definition['duration_max'], 'duration_seconds')
                expires = service.clock() + duration
            owned = c.select('inventory', ((Q(account_id=account_id) & Q(item_key=key)) & (Q(expires_at__isnull=True) | Q(expires_at__gt=service.clock()))), order=('id',), limit=1).fetchone()
            if owned and definition['exclusive']:
                raise Fault('EXCLUSIVE', 'An active exclusive instance already exists', 409)
            stack = c.select('inventory', ((Q(account_id=account_id) & Q(item_key=key)) & Q(expires_at=expires)), order=('id',), limit=1).fetchone() if not definition['exclusive'] else None
            if stack:
                before, instance_id = stack['quantity'], stack['id']
                after = before + delta
                if after > definition['stack']:
                    raise Fault('INVENTORY_FULL', 'Stack limit exceeded', 409)
                c.update('inventory', Q(id=instance_id), {'quantity': after, 'version': F('version') + 1})
            else:
                if c.count('inventory', Q(account_id=account_id)).fetchone()[0] >= service.catalog['inventory_limit']:
                    raise Fault('INVENTORY_FULL', 'No capacity; request rejected without asset changes', 409)
                instance_id, before, after = identifier(), 0, delta
                c.insert('inventory', {'id': instance_id, 'account_id': account_id, 'item_key': key, 'quantity': delta, 'expires_at': expires})
            resource = key
            detail = {'expires_at': expires, 'permanent': expires is None, 'bound': True, 'catalog_version': service.catalog['id']}
        elif kind == 'consume':
            instance_id = change.get('instance_id')
            item = c.select('inventory', (Q(id=instance_id) & Q(account_id=account_id))).fetchone()
            if not item or (item['expires_at'] is not None and item['expires_at'] <= service.clock()):
                raise Fault('INVENTORY', 'Active owned instance required', 409)
            definition = service.catalog['items'].get(item['item_key'])
            if not definition or not definition.get('consumable'):
                raise Fault('INVALID', 'Item is not a confirmed consumable')
            delta = -integer(change.get('quantity'), 1, item['quantity'], 'quantity')
            before, after, resource = item['quantity'], item['quantity'] + delta, item['item_key']
            if after:
                c.update('inventory', Q(id=instance_id), {'quantity': after, 'version': F('version') + 1})
            else:
                c.delete('equipment', Q(inventory_id=instance_id))
                c.delete('inventory', Q(id=instance_id))
            detail = {'expires_at': item['expires_at'], 'bound': bool(item['bound']), 'version_before': item['version']}
        else:
            raise Fault('INVALID', 'Unsupported asset change')
        c.insert('asset_entries', {'operation_id': operation_id, 'entry_no': index, 'kind': kind, 'resource': resource, 'instance_id': instance_id, 'delta': delta, 'before_value': before, 'after_value': after})
        c.insert('asset_entry_details', {'operation_id': operation_id, 'entry_no': index, 'detail': canonical(detail)})
    service._outbox(c, 'assets:' + operation_id, 'assets.changed', account_id, {'operation_id': operation_id, 'account_id': account_id})
    return {'operation_id': operation_id, 'status': 'COMMITTED', 'sync_status': 'PENDING', 'evidence': service.catalog['evidence']}


# 按发放、批量、补偿、购买、消耗和装备分派；批量每个目标有独立保存点，冻结目标列表且成功子项不再次发放。
def asset_command(service, c, actor, action, data):
    """按发放、批量、补偿、购买、消耗和装备分派；批量每个目标有独立保存点，冻结目标列表且成功子项不再次发放。"""
    require_catalog(service, data)
    if action == 'asset.grant':
        if not isinstance(data.get('changes'), list) or any(not isinstance(x, dict) or x.get('type') not in ('currency', 'item') for x in data['changes']):
            raise Fault('INVALID', 'Grants only accept currency/item changes')
        return mutate(service, c, actor, data.get('target_account_id'), data.get('changes'), data['reason'], 'ADMIN')
    if action == 'asset.batch':
        # 兼容旧同步批次，但禁止通过发放接口调用 consume 等其他资产动作。
        from .batching import validate
        validate(data)
        targets = data.get('target_account_ids')
        if not isinstance(targets, list) or not 1 <= len(targets) <= 20 or len(set(targets)) != len(targets):
            raise Fault('INVALID', 'Freeze 1 to 20 unique targets')
        batch_id = identifier()
        results = []
        for i, target in enumerate(targets):
            c.savepoint(f'target_{i}')
            try:
                result = mutate(service, c, actor, target, data.get('changes'), data['reason'], 'BATCH:' + batch_id)
                results.append(dict(result, account_id=target))
            except Fault as error:
                c.rollback(f'target_{i}')
                results.append({'account_id': target, 'status': 'REJECTED', 'error': error.code})
            finally:
                c.release(f'target_{i}')
        return {'batch_id': batch_id, 'frozen_targets': targets, 'results': results,
                'retry_policy': 'Resubmit only rejected target IDs using a new key; same key returns the original batch'}
    if action == 'asset.compensate':
        original = c.select('asset_operations', Q(id=data.get('operation_id'))).fetchone()
        if not original or original['source'] not in ('ADMIN',) or original['status'] != 'COMMITTED':
            raise Fault('COMPENSATION', 'Only committed individual grants can be reversed automatically', 409)
        if c.select('asset_operations', Q(linked_operation_id=original['id'])).fetchone():
            raise Fault('CONFLICT', 'Operation already has a compensation or review record', 409)
        changes, totals = [], {}
        for entry in c.select('asset_entries', Q(operation_id=original['id']), order=('entry_no',)):
            if entry['kind'] != 'currency' or entry['delta'] <= 0:
                return manual_review(service, c, actor, original, data['reason'], 'Item provenance or non-positive grants require review')
            totals[entry['resource']] = totals.get(entry['resource'], 0) + entry['delta']
        for code, amount in totals.items():
            wallet = c.select('wallets', (Q(account_id=original['account_id']) & Q(currency=code)), columns=['balance']).fetchone()
            if not wallet or wallet['balance'] < amount:
                return manual_review(service, c, actor, original, data['reason'], 'Grant has been spent; cannot create a negative balance')
            changes.append({'type': 'currency', 'code': code, 'delta': -amount})
        return mutate(service, c, actor, original['account_id'], changes, data['reason'], 'COMPENSATION', original['id'])
    if action == 'shop.purchase':
        offer = service.catalog['offers'].get(data.get('offer_key'))
        if not offer:
            raise Fault('UNVERIFIED_RESOURCE', 'Unknown or unverified shop offer')
        return mutate(service, c, actor, actor['id'], [
            {'type': 'currency', 'code': offer['currency'], 'delta': -offer['price']},
            {'type': 'item', 'item_key': offer['item'], 'quantity': offer['quantity']},
        ], data['reason'], 'PURCHASE')
    if action == 'inventory.consume':
        return mutate(service, c, actor, actor['id'], [{'type': 'consume', 'instance_id': data.get('instance_id'), 'quantity': data.get('quantity')}], data['reason'], 'CONSUME')
    if action == 'inventory.equip':
        item = c.select('inventory', (Q(id=data.get('instance_id')) & Q(account_id=actor['id']))).fetchone()
        if not item or (item['expires_at'] is not None and item['expires_at'] <= service.clock()):
            raise Fault('INVENTORY', 'Active owned instance required', 409)
        definition = service.catalog['items'].get(item['item_key'])
        if not definition or not definition.get('slot') or data.get('slot') != definition['slot']:
            raise Fault('INVALID', 'Invalid equipment slot')
        c.equip(actor['id'], definition['slot'], item['id'])
        return {'instance_id': item['id'], 'slot': definition['slot'], 'retail_sync': 'UNIMPLEMENTED', 'match_policy': 'NEXT_MATCH_SNAPSHOT'}
    raise Fault('NOT_FOUND', 'Unknown asset action', 404)


# 补偿无法自动证明安全时只追加人工复核操作，不制造负余额、不删除历史流水，也不假装已经追回资产。
def manual_review(service, c, actor, original, reason, explanation):
    """补偿无法自动证明安全时只追加人工复核操作，不制造负余额、不删除历史流水，也不假装已经追回资产。"""
    operation_id = identifier()
    c.insert('asset_operations', {'id': operation_id, 'account_id': original['account_id'], 'actor': actor['id'], 'source': 'COMPENSATION', 'reason': reason, 'created_at': service.clock(), 'status': 'MANUAL_REVIEW', 'linked_operation': original['id']})
    return {'operation_id': operation_id, 'status': 'MANUAL_REVIEW', 'explanation': explanation}


# 调用真实发放校验并读取前后状态，然后无条件回滚保存点；预览不扣款、不占库存，提交时仍重新校验。
def preview(service, token, data):
    """在保存点执行实际资产校验及写入路径后全部回滚，返回前后状态供确认；预览不能提交流水或 outbox。"""
    with service.db.transaction() as c:
        actor = service._actor(c, token, 'asset.preview')
        require_catalog(service, data)
        before = service._user_view(c, data.get('target_account_id'))
        c.savepoint('preview')
        try:
            mutate(service, c, actor, data.get('target_account_id'), data.get('changes'), 'preview', 'PREVIEW')
            after = service._user_view(c, data.get('target_account_id'))
            return {'before': before, 'after': after, 'catalog_version': service.catalog['id'], 'evidence': service.catalog['evidence'],
                    'policy': 'Incremental operation revalidates at commit; absolute balance writes are disabled'}
        finally:
            c.rollback('preview')
            c.release('preview')


# 仅接收受信合成节点的 outcome，奖励取自开始时冻结规则；按节点身份、租约、epoch 和玩家安全版本核验，重复结果只读取原结算。
def settle(service, node_token, data):
    """仅接收受信合成节点的 outcome，奖励取自开始时冻结规则；按节点身份、租约、epoch 和玩家安全版本核验，重复结果只读取原结算。"""
    from .rooms import node_identity
    if not service.test_profile:
        raise Fault('COMPATIBILITY_BLOCKED', 'No trusted native result source exists for the current client', 409)
    match_id, epoch = data.get('match_id'), data.get('epoch')
    outcome = data.get('outcome')
    if outcome not in ('VICTORY', 'FAILURE') or set(data) != {'match_id', 'epoch', 'outcome'}:
        raise Fault('INVALID', 'Only outcome is accepted; reward amounts are determined by the frozen rules')
    digest = token_hash(canonical(data))
    with service.db.transaction() as c:
        match = node_identity(service, c, node_token, match_id, epoch)
        previous = c.select('settlements', Q(match_id=match_id)).fetchone()
        if previous:
            if previous['payload_hash'] != digest:
                raise Fault('SETTLEMENT_CONFLICT', 'Match already settled with different outcome', 409)
            return json.loads(previous['result'])
        if match['state'] != 'ACTIVE':
            raise Fault('MATCH_STATE', 'Only an active instance may settle', 409)
        snapshot = json.loads(match['snapshot'])
        if snapshot.get('pve_rules'):
            run = c.select('pve_runs', Q(match_id=match_id)).fetchone()
            phase = json.loads(run['state'])['phase'] if run else None
            if phase != ('VICTORY' if outcome == 'VICTORY' else 'DEFEAT'):
                raise Fault('RESULT_SOURCE', 'PvE results must follow the persisted authoritative rule state', 409)
        rewards = []
        for player in c.select('match_players', Q(match_id=match_id)):
            account = service._account(c, player['account_id'])
            allowed = bool(player['eligible']) and player['security_version'] == account['security_version'] and account['status'] == 'ACTIVE'
            if allowed:
                try:
                    service._active(c, account)
                except Fault:
                    allowed = False
            if outcome == 'VICTORY' and allowed and snapshot['reward']:
                reward = mutate(service, c, {'id': match['instance_id'], 'role': 'node'}, player['account_id'],
                                [{'type': 'currency', 'code': code, 'delta': value} for code, value in snapshot['reward'].items()],
                                'Synthetic authoritative outcome', 'MATCH:' + match_id)
                rewards.append(dict(reward, account_id=player['account_id']))
        result = {'match_id': match_id, 'outcome': outcome, 'rewards': rewards, 'evidence': 'TEST_PROFILE', 'real_clear_verified': False}
        c.insert('settlements', {'match_id': match_id, 'payload_hash': digest, 'result': canonical(result)})
        c.update('matches', Q(id=match_id), {'state': 'STOPPED'})
        c.update('rooms', (Q(id=match['room_id']) & ~Q(state='CLOSED')), {'state': 'OPEN', 'match_id': None, 'revision': F('revision') + 1})
        c.update('room_members', Q(room_id=match['room_id']), {'ready': 0})
        service._audit(c, match['instance_id'], 'match.settle', match_id, identifier(), 'Synthetic host event', result)
        return result


# 事务外调用 sender，只有显式 True 才确认送达；失败只更新重试时间，永远不重新执行资产事务，接收端必须按 event_key 去重。
def deliver_outbox(service, sender, limit=50):
    """在事务外调用 sender，以 event_key 去重；仅显式确认后标记送达，失败安排重试且不重复执行资产变更。"""
    with service.db.transaction() as c:
        # 需要逐实例确认的通知不能由通用 sender 的一个布尔值提前标记全局送达。
        pending = c.table('node_deliveries').filter(status='PENDING').values('event_id')
        rows = [dict(r) for r in c.select('outbox', (~Q(status='DELIVERED') & Q(next_retry__lte=service.clock()) & ~Q(id__in=pending)), order=('id',), limit=integer(limit, 1, 100, 'limit'))]
    results = []
    for event in rows:
        try:
            acknowledged = sender(event['event_key'], event['type'], event['account_id'], json.loads(event['payload']))
            if acknowledged is not True:
                raise RuntimeError('Transport did not acknowledge the event')
        except Exception:
            # 发送器异常可能含凭据，仅持久化固定错误码，不能记录任意异常正文。
            with service.db.transaction() as c:
                c.update('outbox', (Q(id=event['id']) & ~Q(status='DELIVERED')), {'attempts': F('attempts') + 1, 'next_retry': service.clock() + min(300, 2 ** min(8, event['attempts'] + 1)), 'last_error': 'DELIVERY_NOT_ACKNOWLEDGED'})
            results.append({'id': event['id'], 'delivered': False})
        else:
            with service.db.transaction() as c:
                c.update('outbox', (Q(id=event['id']) & ~Q(status='DELIVERED')), {'status': 'DELIVERED', 'attempts': F('attempts') + 1, 'delivered_at': service.clock(), 'last_error': None})
            results.append({'id': event['id'], 'delivered': True})
    return results
