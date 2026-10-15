"""持久发放工作队列及人工复核决定；每目标的资产与执行结果同事务提交。"""
import json
from django.db.models import Q
from .service import Fault, canonical, identifier, integer, text_field


def validate(data):
    """只冻结唯一账号 ID 和增量发放，禁止批次借道消耗或任意资产类型。"""
    targets, changes = data.get('target_account_ids'), data.get('changes')
    if not isinstance(targets, list) or not 1 <= len(targets) <= 500 or any(not isinstance(t, str) or not 1 <= len(t) <= 32 for t in targets) or len(set(targets)) != len(targets):
        raise Fault('INVALID', 'Freeze 1 to 500 unique account IDs')
    if not isinstance(changes, list) or not 1 <= len(changes) <= 20 or any(not isinstance(x, dict) or x.get('type') not in ('currency', 'item') for x in changes):
        raise Fault('INVALID', 'Batch grants only accept currency and item changes')
    return targets, changes


def enqueue(service, c, actor, data, parent=None):
    """冻结参数和操作者安全版本；调度进程重启后继续 PENDING 子项，不重做已提交项。"""
    from .assets import require_catalog
    require_catalog(service, data)
    targets, changes = validate(data)
    batch_id = identifier()
    admin = c.select('admins', Q(id=actor['id'])).fetchone()
    payload = {'changes': changes, 'reason': data['reason'], 'security_version': admin['security_version']}
    c.insert('grant_batches', {'id': batch_id, 'actor_id': actor['id'], 'catalog_version': service.catalog['id'],
             'payload': canonical(payload), 'created_at': service.clock(), 'parent_id': parent})
    for index, target in enumerate(targets):
        c.insert('grant_targets', {'id': identifier(), 'batch_id': batch_id, 'target': target, 'ordinal': index})
    return {'batch_id': batch_id, 'status': 'PENDING', 'frozen_targets': targets}


def batch_command(service, c, actor, action, data):
    """批量预览、排队、失败重试和复核结案统一使用原命令幂等与 Owner 权限。"""
    from .assets import require_catalog, mutate
    if action == 'asset.review.resolve':
        operation = c.select('asset_operations', Q(id=data.get('operation_id'), status='MANUAL_REVIEW')).fetchone()
        if not operation:
            raise Fault('NOT_FOUND', 'Pending manual review does not exist', 404)
        if c.select('review_decisions', Q(operation_id=operation['id'])).fetchone():
            raise Fault('CONFLICT', 'Review already has a final decision', 409)
        resolution = data.get('resolution')
        if resolution not in ('KEEP_ASSETS', 'EXTERNAL_RESOLUTION'):
            raise Fault('INVALID', 'Choose KEEP_ASSETS or EXTERNAL_RESOLUTION; no ledger history will be rewritten')
        review_id = identifier()
        c.insert('review_decisions', {'id': review_id, 'operation_id': operation['id'], 'actor_id': actor['id'],
                 'resolution': resolution, 'reason': data['reason'], 'created_at': service.clock()})
        return {'review_id': review_id, 'operation_id': operation['id'], 'resolution': resolution, 'asset_changes': False}
    if action == 'asset.batch.retry':
        previous = c.select('grant_batches', Q(id=data.get('batch_id'))).fetchone()
        if not previous or previous['status'] not in ('PARTIAL', 'REJECTED'):
            raise Fault('BATCH_STATE', 'Only a completed batch with failures may be retried', 409)
        rejected = [row['target'] for row in c.select('grant_targets', Q(batch_id=previous['id'], status='REJECTED'), order=('ordinal',))]
        requested = data.get('target_account_ids')
        if requested is None:
            requested = rejected
        if not isinstance(requested, list) or any(target not in rejected for target in requested):
            raise Fault('INVALID', 'Retry can include only rejected target IDs')
        payload = json.loads(previous['payload'])
        return enqueue(service, c, actor, dict(data, target_account_ids=requested, changes=payload['changes'],
                                              catalog_version=previous['catalog_version']), parent=previous['id'])
    if action == 'asset.batch.enqueue':
        return enqueue(service, c, actor, data)
    require_catalog(service, data)
    targets, changes = validate(data)
    results = []
    for target in targets:
        c.savepoint('batch_preview')
        try:
            before = service._user_view(c, target)
            mutate(service, c, actor, target, changes, 'batch preview', 'PREVIEW')
            after = service._user_view(c, target)
            results.append({'account_id': target, 'status': 'VALID',
                            'before': {'wallets': before['wallets'], 'inventory': before['inventory']},
                            'after': {'wallets': after['wallets'], 'inventory': after['inventory']}})
        except Fault as error:
            results.append({'account_id': target, 'status': 'REJECTED', 'error': error.code})
        finally:
            c.rollback('batch_preview')
            c.release('batch_preview')
    return {'frozen_targets': targets, 'catalog_version': service.catalog['id'], 'results': results, 'read_only': True}


def run_batches(service, limit=20):
    """每目标单独锁定并提交资产及结果；双维护进程也不能领取同一目标两次。"""
    from .assets import mutate
    count = 0
    for _ in range(integer(limit, 1, 100, 'limit')):
        with service.db.transaction() as c:
            target = c.select('grant_targets', Q(status='PENDING'), order=('batch__created_at', 'batch_id', 'ordinal'), limit=1).fetchone()
            if not target:
                break
            batch = c.select('grant_batches', Q(id=target['batch_id'])).fetchone()
            admin = c.select('admins', Q(id=batch['actor_id'])).fetchone()
            payload = json.loads(batch['payload'])
            c.savepoint('batch_target')
            try:
                if not admin or not admin['active'] or admin['role'] != 'owner' or admin['security_version'] != payload['security_version']:
                    raise Fault('FORBIDDEN', 'Batch author authorization was revoked', 403)
                if service.catalog['id'] != batch['catalog_version']:
                    raise Fault('CATALOG_CONFLICT', 'Frozen batch catalog is no longer active', 409)
                result = mutate(service, c, {'id': admin['id'], 'role': 'owner'}, target['target'], payload['changes'],
                                payload['reason'], 'BATCH:' + batch['id'])
                c.update('grant_targets', Q(id=target['id']), {'status': 'COMMITTED', 'operation_id': result['operation_id']})
                service._audit(c, admin['id'], 'asset.batch.target', target['target'], target['id'], payload['reason'], result)
            except Fault as error:
                c.rollback('batch_target')
                c.update('grant_targets', Q(id=target['id']), {'status': 'REJECTED', 'error': error.code})
            finally:
                c.release('batch_target')
            remaining = c.count('grant_targets', Q(batch_id=batch['id'], status='PENDING')).fetchone()[0]
            if not remaining:
                failed = c.count('grant_targets', Q(batch_id=batch['id'], status='REJECTED')).fetchone()[0]
                passed = c.count('grant_targets', Q(batch_id=batch['id'], status='COMMITTED')).fetchone()[0]
                c.update('grant_batches', Q(id=batch['id']), {'status': 'PARTIAL' if failed and passed else 'REJECTED' if failed else 'COMMITTED'})
            count += 1
    return {'processed': count}


def batches_view(c, limit=50, offset=0):
    """返回持久批次及子项实际状态，不泄漏管理员凭据或工作队列内部参数。"""
    result = []
    for row in c.select('grant_batches', order=('-created_at', 'id'), limit=limit, offset=offset):
        row.pop('payload')
        row['targets'] = list(c.select('grant_targets', Q(batch_id=row['id']), order=('ordinal',)))
        result.append(dict(row))
    return result
