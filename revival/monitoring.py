"""独立主机采样、带宽差分和管理员趋势；缺测/重启/计数器回退绝不显示健康零值。"""
import math
import re
from django.db.models import F, Q, Sum
from django.db.models.functions import Floor
from .security import new_token, token_hash
from .service import Fault, canonical, identifier, integer, text_field

RAW_DAYS = 7
AGGREGATE_DAYS = 30
STALE_SECONDS = 60


def number(value, minimum, maximum, name):
    """接受有限实数，拒绝 bool、NaN 与超界值，防止 JSON 非法指标污染趋势。"""
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise Fault('INVALID', f'{name} 指标不合法')
    return value


def thresholds(data):
    """只允许可解释的主机百分比阈值；未知带宽容量不推算利用率。"""
    if not isinstance(data, dict) or set(data) - {'cpu_percent', 'memory_percent', 'disk_percent', 'link_mbps'}:
        raise Fault('INVALID', '监控阈值字段不合法')
    result = {'cpu_percent': 85, 'memory_percent': 90, 'disk_percent': 90}
    for key, value in data.items():
        result[key] = number(value, 0.01, 1000000 if key == 'link_mbps' else 100, key)
    return result


def command(service, c, actor, action, data):
    """Owner 登记/停用节点；新凭据仅首次响应返回，不写入审计或幂等结果。"""
    if actor.get('kind') != 'admin' or actor.get('role') != 'owner':
        raise Fault('FORBIDDEN', '只有 Owner 可以管理监控节点', 403)
    if action == 'monitor.node.enroll':
        platform = data.get('platform')
        if platform not in ('linux', 'windows', 'darwin'):
            raise Fault('INVALID', '请选择 linux/windows/darwin')
        node_id, secret = identifier(), new_token()
        c.insert('monitor_nodes', {'id': node_id, 'name': text_field(data.get('name'), 'name', 80),
            'platform': platform, 'token_digest': token_hash(secret), 'thresholds': thresholds(data.get('thresholds', {})),
            'created_at': service.clock()})
        return {'node_id': node_id, 'node_token': secret, 'revision': 1, 'token_shown_once': True}
    node = c.select('monitor_nodes', Q(id=data.get('node_id'))).fetchone()
    if not node:
        raise Fault('NOT_FOUND', '监控节点不存在', 404)
    if data.get('expected_revision') != node['revision']:
        raise Fault('REVISION_CONFLICT', '节点配置已更新，请刷新', 409)
    enabled = data.get('enabled', node['enabled'])
    if type(enabled) is not bool:
        raise Fault('INVALID', 'enabled 必须为布尔值')
    values = {'enabled': enabled, 'revision': node['revision'] + 1,
        'thresholds': thresholds(data.get('thresholds', node['thresholds']))}
    c.update('monitor_nodes', Q(id=node['id']), values)
    return dict(node_id=node['id'], **values)


def ingest(service, token, data):
    """只接受节点专用 Bearer；严格测量合同、同序号去重与聚合共用现有 MySQL 事务锁。"""
    with service.db.transaction() as c:
        node = c.select('monitor_nodes', Q(token_digest=token_hash(token), enabled=True)).fetchone()
        if not node:
            raise Fault('AUTH', '无效或已停用的监控凭据', 401)
        allowed = {'node_id', 'boot_id', 'sequence', 'measured_at', 'cpu_percent', 'memory_total', 'memory_used',
            'rx_bytes', 'tx_bytes', 'disks', 'services'}
        if set(data) - allowed or data.get('node_id') != node['id']:
            raise Fault('INVALID', '监控采样字段或节点不匹配')
        boot = data.get('boot_id')
        if not isinstance(boot, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', boot):
            raise Fault('INVALID', 'boot_id 不合法')
        sequence = integer(data.get('sequence'), 0, 2**63-1, 'sequence')
        digest = token_hash(canonical(data))
        existing = c.select('monitor_samples', Q(node_id=node['id'], boot_id=boot, sequence=sequence)).fetchone()
        if existing:
            if existing['payload_hash'] != digest:
                raise Fault('SAMPLE_CONFLICT', '同序号的监控内容冲突', 409)
            return {'accepted': True, 'duplicate': True}
        measured = integer(data.get('measured_at'), service.clock()-300, service.clock()+30, 'measured_at')
        cpu = number(data.get('cpu_percent'), 0, 100, 'cpu_percent')
        total = integer(data.get('memory_total'), 1, 2**63-1, 'memory_total')
        used = integer(data.get('memory_used'), 0, total, 'memory_used')
        rx = integer(data.get('rx_bytes'), 0, 2**63-1, 'rx_bytes')
        tx = integer(data.get('tx_bytes'), 0, 2**63-1, 'tx_bytes')
        disks, services = data.get('disks', []), data.get('services', {})
        if not isinstance(disks, list) or len(disks) > 16 or not isinstance(services, dict) or len(services) > 16:
            raise Fault('INVALID', '磁盘/服务测量数量不合法')
        clean_disks = []
        for disk in disks:
            if not isinstance(disk, dict) or set(disk) != {'name', 'total', 'used'}:
                raise Fault('INVALID', '磁盘测量字段不合法')
            disk_total = integer(disk['total'], 1, 2**63-1, 'disk.total')
            disk_used = integer(disk['used'], 0, disk_total, 'disk.used')
            clean_disks.append({'name': text_field(disk['name'], 'disk.name', 80), 'total': disk_total,
                'used': disk_used, 'percent': round(disk_used/disk_total*100, 2)})
        clean_services = {}
        for name, state in services.items():
            name = text_field(name, 'service.name', 80)
            if state not in ('UP', 'DOWN', 'UNKNOWN'):
                raise Fault('INVALID', '服务状态不合法')
            clean_services[name] = state
        previous = c.select('monitor_samples', Q(node_id=node['id']), order=('-id',), limit=1).fetchone()
        rx_bps = tx_bps = None
        network_state = 'SAMPLING'
        if previous and previous['boot_id'] == boot:
            if sequence <= previous['sequence'] or measured <= previous['measured_at']:
                raise Fault('SAMPLE_ORDER', '监控序号和时刻必须递增', 409)
            old = previous['metrics']
            elapsed = measured-previous['measured_at']
            if rx >= old['rx_bytes'] and tx >= old['tx_bytes'] and elapsed <= STALE_SECONDS:
                rx_bps, tx_bps = (rx-old['rx_bytes'])/elapsed, (tx-old['tx_bytes'])/elapsed
                network_state = 'READY'
            else:
                network_state = 'COUNTER_RESET' if rx < old['rx_bytes'] or tx < old['tx_bytes'] else 'SAMPLING'
        metrics = {'cpu_percent': cpu, 'memory_total': total, 'memory_used': used,
            'memory_percent': round(used/total*100, 2), 'rx_bytes': rx, 'tx_bytes': tx,
            'rx_bps': rx_bps, 'tx_bps': tx_bps, 'network_state': network_state,
            'disks': clean_disks, 'services': clean_services}
        c.insert('monitor_samples', {'node_id': node['id'], 'boot_id': boot, 'sequence': sequence,
            'measured_at': measured, 'received_at': service.clock(), 'payload_hash': digest, 'metrics': metrics})
        minute = measured//60*60
        bucket = c.table('monitor_minutes').filter(node_id=node['id'], minute=minute).first()
        if bucket is None:
            bucket = c.table('monitor_minutes').create(node_id=node['id'], minute=minute)
        bucket.count += 1
        bucket.cpu_sum += cpu
        bucket.memory_sum += metrics['memory_percent']
        if rx_bps is not None:
            bucket.rx_sum += rx_bps
            bucket.tx_sum += tx_bps
            bucket.network_count += 1
        bucket.save(using=c.alias)
        # 分钟边界附近的小量清理不影响其他节点，原始样本与聚合分别执行保留策略。
        if sequence % 4 == 0:
            c.table('monitor_samples').filter(node_id=node['id'], received_at__lt=service.clock()-RAW_DAYS*86400).delete()
            c.table('monitor_minutes').filter(node_id=node['id'], minute__lt=service.clock()-AGGREGATE_DAYS*86400).delete()
        return {'accepted': True, 'duplicate': False, 'network_state': network_state}


def query(service, c, actor, filters):
    """仅管理员查看主机趋势；失联时清空当前指标，历史仍保留真实测量值。"""
    if actor.get('kind') != 'admin':
        raise Fault('FORBIDDEN', '主机监控仅管理员可见', 403)
    nodes = []
    for row in c.select('monitor_nodes', order=('name', 'id'), limit=100):
        latest = c.select('monitor_samples', Q(node_id=row['id']), order=('-id',), limit=1).fetchone()
        state = 'DISABLED' if not row['enabled'] else 'NO_DATA' if latest is None else (
            'STALE' if max(service.clock()-latest['received_at'], service.clock()-latest['measured_at']) >= STALE_SECONDS else 'ONLINE')
        metrics = latest['metrics'] if latest else {}
        alerts = []
        if state == 'ONLINE':
            for key in ('cpu_percent', 'memory_percent'):
                if metrics[key] >= row['thresholds'].get(key, 100):
                    alerts.append(key)
            if any(d['percent'] >= row['thresholds'].get('disk_percent', 100) for d in metrics.get('disks', [])):
                alerts.append('disk_percent')
            if any(value == 'DOWN' for value in metrics.get('services', {}).values()):
                alerts.append('services')
        current = metrics if state == 'ONLINE' else {key: None for key in metrics}
        nodes.append({'id': row['id'], 'name': row['name'], 'platform': row['platform'], 'enabled': bool(row['enabled']),
            'revision': row['revision'], 'thresholds': row['thresholds'], 'last_seen': latest['received_at'] if latest else None,
            'measured_at': latest['measured_at'] if latest else None,
            'state': state, 'alerts': alerts, **current})
    samples = []
    node_id = filters.get('node_id')
    if node_id:
        if not any(n['id'] == node_id for n in nodes):
            raise Fault('NOT_FOUND', '监控节点不存在', 404)
        if filters.get('resolution') == 'minute':
            hours = integer(int(filters.get('hours', 24)), 1, AGGREGATE_DAYS*24, 'hours')
            if hours > 24:
                # 长窗口在数据库按小时加权汇总，30天仍完整展示，不截断成最后24小时。
                rows = list(c.table('monitor_minutes').filter(node_id=node_id, minute__gte=service.clock()-hours*3600)
                    .annotate(hour=Floor(F('minute')/3600)).values('hour').annotate(count=Sum('count'),
                        cpu_sum=Sum('cpu_sum'), memory_sum=Sum('memory_sum'), rx_sum=Sum('rx_sum'),
                        tx_sum=Sum('tx_sum'), network_count=Sum('network_count')).order_by('hour'))
                rows = [dict(r, minute=int(r['hour'])*3600) for r in rows]
            else:
                rows = list(reversed(c.select('monitor_minutes', Q(node_id=node_id, minute__gte=service.clock()-hours*3600), order=('-minute',), limit=1441)))
            samples = [{'at': r['minute'], 'cpu_percent': r['cpu_sum']/r['count'], 'memory_percent': r['memory_sum']/r['count'],
                'rx_bps': r['rx_sum']/r['network_count'] if r['network_count'] else None,
                'tx_bps': r['tx_sum']/r['network_count'] if r['network_count'] else None} for r in rows]
        else:
            samples = [dict(at=r['measured_at'], **r['metrics']) for r in reversed(c.select('monitor_samples',
                Q(node_id=node_id), order=('-id',), limit=240))]
    return {'nodes': nodes, 'samples': samples, 'retention': {'raw_days': RAW_DAYS, 'aggregate_days': AGGREGATE_DAYS}}


def prune(service):
    """维护器按小时清理离线节点的过期数据，保留期不依赖节点继续上报。"""
    with service.db.transaction() as c:
        marker = c.select('deployment_metadata', Q(key='monitor_retention_at')).fetchone()
        if marker and int(marker['value']) > service.clock()-3600:
            return
        c.table('monitor_samples').filter(received_at__lt=service.clock()-RAW_DAYS*86400).delete()
        c.table('monitor_minutes').filter(minute__lt=service.clock()-AGGREGATE_DAYS*86400).delete()
        values = {'value': str(service.clock())}
        if marker:
            c.update('deployment_metadata', Q(key='monitor_retention_at'), values)
        else:
            c.insert('deployment_metadata', dict(key='monitor_retention_at', **values))
