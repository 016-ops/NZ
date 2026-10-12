"""Unified wallet/inventory ledger with outbox, shopping, and synthetic settlement."""
import json

from .service import Fault, canonical, identifier, integer, text_field
from .security import token_hash


def require_catalog(service, data):
    if data.get('catalog_version') != service.catalog['id']:
        raise Fault('CATALOG_CONFLICT', 'Catalog version must match the current service', 409)


def currency_value(service, code, delta):
    definition = service.catalog['currencies'].get(code)
    if definition is None:
        raise Fault('UNVERIFIED_RESOURCE', 'Unknown or unverified currency')
    integer(delta, -definition['max'], definition['max'], 'delta')
    if delta == 0:
        raise Fault('INVALID', 'Delta must not be zero')
    return definition


def mutate(service, c, actor, account_id, changes, reason, source, linked=None):
    service._account(c, account_id)
    if not c.execute('SELECT 1 FROM characters WHERE account_id=?', (account_id,)).fetchone():
        raise Fault('CHARACTER', 'Target has no local character', 409)
    if not isinstance(changes, list) or not 1 <= len(changes) <= 20:
        raise Fault('INVALID', 'Provide 1 to 20 asset changes')
    if actor.get('role') == 'resources' and sum(abs(x['delta']) for x in changes
                                                if isinstance(x, dict) and x.get('type') == 'currency'
                                                and type(x.get('delta')) is int) > 10000:
        raise Fault('LIMIT', 'Resource administrator per-operation currency limit is 10000', 403)
    operation_id = identifier()
    c.execute('INSERT INTO asset_operations VALUES(?,?,?,?,?,?,?,?)',
              (operation_id, account_id, actor['id'], source, reason, service.clock(), 'COMMITTED', linked))
    for index, change in enumerate(changes):
        kind = change.get('type') if isinstance(change, dict) else None
        instance_id = None
        detail = {}
        if kind == 'currency':
            code, delta = change.get('code'), change.get('delta')
            definition = currency_value(service, code, delta)
            if actor.get('role') == 'resources' and abs(delta) > 10000:
                raise Fault('LIMIT', 'Resource administrator per-change limit is 10000', 403)
            wallet = c.execute('SELECT * FROM wallets WHERE account_id=? AND currency=?', (account_id, code)).fetchone()
            before = wallet['balance'] if wallet else 0
            after = before + delta
            if not definition['min'] <= after <= definition['max']:
                raise Fault('BALANCE', 'Insufficient funds or currency range exceeded', 409)
            if wallet:
                c.execute('UPDATE wallets SET balance=?,version=version+1 WHERE account_id=? AND currency=?', (after, account_id, code))
            else:
                c.execute('INSERT INTO wallets(account_id,currency,balance,version) VALUES(?,?,?,1)', (account_id, code, after))
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
            owned = c.execute('SELECT * FROM inventory WHERE account_id=? AND item_key=? '
                              'AND (expires_at IS NULL OR expires_at>?) ORDER BY id LIMIT 1', (account_id, key, service.clock())).fetchone()
            if owned and definition['exclusive']:
                raise Fault('EXCLUSIVE', 'An active exclusive instance already exists', 409)
            stack = c.execute('SELECT * FROM inventory WHERE account_id=? AND item_key=? AND expires_at IS ? ORDER BY id LIMIT 1', (account_id, key, expires)).fetchone() if not definition['exclusive'] else None
            if stack:
                before, instance_id = stack['quantity'], stack['id']
                after = before + delta
                if after > definition['stack']:
                    raise Fault('INVENTORY_FULL', 'Stack limit exceeded', 409)
                c.execute('UPDATE inventory SET quantity=?,version=version+1 WHERE id=?', (after, instance_id))
            else:
                if c.execute('SELECT COUNT(*) FROM inventory WHERE account_id=?', (account_id,)).fetchone()[0] >= service.catalog['inventory_limit']:
                    raise Fault('INVENTORY_FULL', 'No capacity; request rejected without asset changes', 409)
                instance_id, before, after = identifier(), 0, delta
                c.execute('INSERT INTO inventory(id,account_id,item_key,quantity,expires_at) VALUES(?,?,?,?,?)', (instance_id, account_id, key, delta, expires))
            resource = key
            detail = {'expires_at': expires, 'permanent': expires is None, 'bound': True, 'catalog_version': service.catalog['id']}
        elif kind == 'consume':
            instance_id = change.get('instance_id')
            item = c.execute('SELECT * FROM inventory WHERE id=? AND account_id=?', (instance_id, account_id)).fetchone()
            if not item or (item['expires_at'] is not None and item['expires_at'] <= service.clock()):
                raise Fault('INVENTORY', 'Active owned instance required', 409)
            definition = service.catalog['items'].get(item['item_key'])
            if not definition or not definition.get('consumable'):
                raise Fault('INVALID', 'Item is not a confirmed consumable')
            delta = -integer(change.get('quantity'), 1, item['quantity'], 'quantity')
            before, after, resource = item['quantity'], item['quantity'] + delta, item['item_key']
            if after:
                c.execute('UPDATE inventory SET quantity=?,version=version+1 WHERE id=?', (after, instance_id))
            else:
                c.execute('DELETE FROM equipment WHERE inventory_id=?', (instance_id,))
                c.execute('DELETE FROM inventory WHERE id=?', (instance_id,))
            detail = {'expires_at': item['expires_at'], 'bound': bool(item['bound']), 'version_before': item['version']}
        else:
            raise Fault('INVALID', 'Unsupported asset change')
        c.execute('INSERT INTO asset_entries VALUES(?,?,?,?,?,?,?,?)',
                  (operation_id, index, kind, resource, instance_id, delta, before, after))
        c.execute('INSERT INTO asset_entry_details VALUES(?,?,?)', (operation_id, index, canonical(detail)))
    service._outbox(c, 'assets:' + operation_id, 'assets.changed', account_id, {'operation_id': operation_id, 'account_id': account_id})
    return {'operation_id': operation_id, 'status': 'COMMITTED', 'sync_status': 'PENDING', 'evidence': service.catalog['evidence']}


def asset_command(service, c, actor, action, data):
    require_catalog(service, data)
    if action == 'asset.grant':
        if not isinstance(data.get('changes'), list) or any(not isinstance(x, dict) or x.get('type') not in ('currency', 'item') for x in data['changes']):
            raise Fault('INVALID', 'Grants only accept currency/item changes')
        return mutate(service, c, actor, data.get('target_account_id'), data.get('changes'), data['reason'], 'ADMIN')
    if action == 'asset.batch':
        targets = data.get('target_account_ids')
        if not isinstance(targets, list) or not 1 <= len(targets) <= 20 or len(set(targets)) != len(targets):
            raise Fault('INVALID', 'Freeze 1 to 20 unique targets')
        batch_id = identifier()
        results = []
        for i, target in enumerate(targets):
            c.execute(f'SAVEPOINT target_{i}')
            try:
                result = mutate(service, c, actor, target, data.get('changes'), data['reason'], 'BATCH:' + batch_id)
                results.append(dict(result, account_id=target))
            except Fault as error:
                c.execute(f'ROLLBACK TO target_{i}')
                results.append({'account_id': target, 'status': 'REJECTED', 'error': error.code})
            finally:
                c.execute(f'RELEASE target_{i}')
        return {'batch_id': batch_id, 'frozen_targets': targets, 'results': results,
                'retry_policy': 'Resubmit only rejected target IDs using a new key; same key returns the original batch'}
    if action == 'asset.compensate':
        original = c.execute('SELECT * FROM asset_operations WHERE id=?', (data.get('operation_id'),)).fetchone()
        if not original or original['source'] not in ('ADMIN',) or original['status'] != 'COMMITTED':
            raise Fault('COMPENSATION', 'Only committed individual grants can be reversed automatically', 409)
        if c.execute('SELECT 1 FROM asset_operations WHERE linked_operation=?', (original['id'],)).fetchone():
            raise Fault('CONFLICT', 'Operation already has a compensation or review record', 409)
        changes, totals = [], {}
        for entry in c.execute('SELECT * FROM asset_entries WHERE operation_id=? ORDER BY entry_no', (original['id'],)):
            if entry['kind'] != 'currency' or entry['delta'] <= 0:
                return manual_review(service, c, actor, original, data['reason'], 'Item provenance or non-positive grants require review')
            totals[entry['resource']] = totals.get(entry['resource'], 0) + entry['delta']
        for code, amount in totals.items():
            wallet = c.execute('SELECT balance FROM wallets WHERE account_id=? AND currency=?', (original['account_id'], code)).fetchone()
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
        item = c.execute('SELECT * FROM inventory WHERE id=? AND account_id=?', (data.get('instance_id'), actor['id'])).fetchone()
        if not item or (item['expires_at'] is not None and item['expires_at'] <= service.clock()):
            raise Fault('INVENTORY', 'Active owned instance required', 409)
        definition = service.catalog['items'].get(item['item_key'])
        if not definition or not definition.get('slot') or data.get('slot') != definition['slot']:
            raise Fault('INVALID', 'Invalid equipment slot')
        c.execute('INSERT INTO equipment VALUES(?,?,?) ON CONFLICT(account_id,slot) DO UPDATE SET inventory_id=excluded.inventory_id', (actor['id'], definition['slot'], item['id']))
        return {'instance_id': item['id'], 'slot': definition['slot'], 'retail_sync': 'UNIMPLEMENTED', 'match_policy': 'NEXT_MATCH_SNAPSHOT'}
    raise Fault('NOT_FOUND', 'Unknown asset action', 404)


def manual_review(service, c, actor, original, reason, explanation):
    operation_id = identifier()
    c.execute('INSERT INTO asset_operations VALUES(?,?,?,?,?,?,?,?)',
              (operation_id, original['account_id'], actor['id'], 'COMPENSATION', reason, service.clock(), 'MANUAL_REVIEW', original['id']))
    return {'operation_id': operation_id, 'status': 'MANUAL_REVIEW', 'explanation': explanation}


def preview(service, token, data):
    """Run the actual validation/transaction code in a savepoint, then undo it."""
    with service.db.transaction() as c:
        actor = service._actor(c, token, 'asset.preview')
        require_catalog(service, data)
        before = service._user_view(c, data.get('target_account_id'))
        c.execute('SAVEPOINT preview')
        try:
            mutate(service, c, actor, data.get('target_account_id'), data.get('changes'), 'preview', 'PREVIEW')
            after = service._user_view(c, data.get('target_account_id'))
            return {'before': before, 'after': after, 'catalog_version': service.catalog['id'], 'evidence': service.catalog['evidence'],
                    'policy': 'Incremental operation revalidates at commit; absolute balance writes are disabled'}
        finally:
            c.execute('ROLLBACK TO preview')
            c.execute('RELEASE preview')


def settle(service, node_token, data):
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
        previous = c.execute('SELECT * FROM settlements WHERE match_id=?', (match_id,)).fetchone()
        if previous:
            if previous['payload_hash'] != digest:
                raise Fault('SETTLEMENT_CONFLICT', 'Match already settled with different outcome', 409)
            return json.loads(previous['result'])
        if match['state'] != 'ACTIVE':
            raise Fault('MATCH_STATE', 'Only an active instance may settle', 409)
        snapshot = json.loads(match['snapshot'])
        rewards = []
        for player in c.execute('SELECT * FROM match_players WHERE match_id=?', (match_id,)):
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
        c.execute('INSERT INTO settlements VALUES(?,?,?)', (match_id, digest, canonical(result)))
        c.execute("UPDATE matches SET state='STOPPED' WHERE id=?", (match_id,))
        c.execute("UPDATE rooms SET state='OPEN',match_id=NULL,revision=revision+1 WHERE id=? AND state!='CLOSED'", (match['room_id'],))
        c.execute('UPDATE room_members SET ready=0 WHERE room_id=?', (match['room_id'],))
        service._audit(c, match['instance_id'], 'match.settle', match_id, identifier(), 'Synthetic host event', result)
        return result


def deliver_outbox(service, sender, limit=50):
    """Sender must implement idempotent delivery by event key. Failure never repeats assets."""
    with service.db.transaction() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM outbox WHERE status!='DELIVERED' AND next_retry<=? ORDER BY id LIMIT ?", (service.clock(), integer(limit, 1, 100, 'limit')))]
    results = []
    for event in rows:
        try:
            acknowledged = sender(event['event_key'], event['type'], event['account_id'], json.loads(event['payload']))
            if acknowledged is not True:
                raise RuntimeError('Transport did not acknowledge the event')
        except Exception:
            # No arbitrary exception text from a transport goes into credential-bearing logs.
            with service.db.transaction() as c:
                c.execute("UPDATE outbox SET attempts=attempts+1,next_retry=?,last_error='DELIVERY_NOT_ACKNOWLEDGED' WHERE id=? AND status!='DELIVERED'",
                          (service.clock() + min(300, 2 ** min(8, event['attempts'] + 1)), event['id']))
            results.append({'id': event['id'], 'delivered': False})
        else:
            with service.db.transaction() as c:
                c.execute("UPDATE outbox SET status='DELIVERED',attempts=attempts+1,delivered_at=?,last_error=NULL WHERE id=? AND status!='DELIVERED'", (service.clock(), event['id']))
            results.append({'id': event['id'], 'delivered': True})
    return results
