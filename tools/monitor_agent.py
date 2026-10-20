"""Linux/Windows 主机监控 Agent；凭据从环境读取，只向固定 HTTPS 或本机隧道上报。"""
import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """拒绝重定向，防止监控 Bearer 被转发到非预期服务器。"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """返回 None 令 urllib 抛出 HTTPError，主循环仅记录状态码。"""
        return None


def endpoint(value):
    """禁止用户信息、查询参数与外部明文 HTTP；允许现有 DEV SSH 回环隧道。"""
    parsed = urlsplit(value)
    if (parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname
            or parsed.path not in ('', '/') or parsed.scheme not in ('http', 'https')
            or parsed.scheme == 'http' and parsed.hostname not in ('127.0.0.1', 'localhost', '::1')):
        raise ValueError('MONITOR_URL_MUST_BE_HTTPS_OR_LOOPBACK')
    return value.rstrip('/') + '/api/monitoring/samples'


def transport(url):
    """回环隧道必须直连，不让系统HTTP代理接管本机端点；公网HTTPS沿用运维代理设置。"""
    handlers = [NoRedirect()]
    if urlsplit(url).hostname in ('127.0.0.1', 'localhost', '::1'):
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


def sample(psutil, node_id, boot_id, sequence, interfaces, service_names):
    """测量实际 CPU/内存/磁盘和指定网卡计数；无可用外部网卡时拒绝伪造带宽。"""
    memory = psutil.virtual_memory()
    disks = []
    seen = set()
    for partition in psutil.disk_partitions(all=False):
        if partition.device in seen or len(disks) >= 16:
            continue
        try:
            disk = psutil.disk_usage(partition.mountpoint)
        except (OSError, PermissionError):
            continue
        seen.add(partition.device)
        disks.append({'name': partition.mountpoint, 'total': disk.total, 'used': disk.used})
    counters = psutil.net_io_counters(pernic=True, nowrap=False)
    stats = psutil.net_if_stats()
    selected = interfaces or [name for name in counters if name in stats and stats[name].isup
        and not name.lower().startswith(('lo', 'loopback', 'veth', 'docker', 'br-', 'utun'))]
    if not selected or any(name not in counters for name in selected):
        raise ValueError('MONITOR_INTERFACE_UNAVAILABLE')
    services = {}
    for name in service_names:
        try:
            if platform.system() == 'Windows':
                state = psutil.win_service_get(name).status()
                services[name] = 'UP' if state == 'running' else 'DOWN'
            elif platform.system() == 'Linux':
                result = subprocess.run(['systemctl', 'is-active', '--quiet', name], timeout=5,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                services[name] = 'UP' if result.returncode == 0 else 'DOWN'
            else:
                services[name] = 'UNKNOWN'
        except (OSError, ValueError, subprocess.TimeoutExpired):
            services[name] = 'UNKNOWN'
    return {'node_id': node_id, 'boot_id': boot_id, 'sequence': sequence, 'measured_at': int(time.time()),
        'cpu_percent': psutil.cpu_percent(interval=1), 'memory_total': memory.total,
        'memory_used': memory.total-memory.available,
        'rx_bytes': sum(counters[name].bytes_recv for name in selected),
        'tx_bytes': sum(counters[name].bytes_sent for name in selected), 'disks': disks, 'services': services}


def main():
    """每15秒采样；响应不确定时重发同一冻结正文，避免产生重复聚合记录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--node-id', required=True)
    parser.add_argument('--interval', type=int, default=15)
    parser.add_argument('--interfaces', default='')
    parser.add_argument('--services', default='')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    url = endpoint(args.url)
    token = os.environ.get('NZ_MONITOR_TOKEN', '')
    if not token or not re.fullmatch(r'[A-Za-z0-9_-]{20,128}', token) or not re.fullmatch(r'[a-f0-9]{32}', args.node_id):
        parser.error('需要有效 NZ_MONITOR_TOKEN 环境变量及节点ID，禁止把凭据放在命令行')
    if not 5 <= args.interval <= 60:
        parser.error('interval 必须为5至60秒')
    names = [s for s in args.services.split(',') if s]
    if len(names) > 16 or any(not re.fullmatch(r'[A-Za-z0-9_.@-]{1,80}', name) for name in names):
        parser.error('服务名称不合法')
    import psutil
    opener = transport(url)
    boot, sequence = uuid.uuid4().hex, 0
    while True:
        started = time.monotonic()
        data = sample(psutil, args.node_id, boot, sequence,
            [s for s in args.interfaces.split(',') if s], names)
        frozen = json.dumps(data, allow_nan=False, separators=(',', ':')).encode()
        succeeded = False
        for attempt in range(3):
            request = urllib.request.Request(url, data=frozen, method='POST', headers={
                'Authorization': 'Bearer ' + token, 'X-NZ-Request': '1', 'Content-Type': 'application/json'})
            try:
                with opener.open(request, timeout=10) as response:
                    result = json.loads(response.read(8192))
                    if result.get('accepted') is not True:
                        raise ValueError('MONITOR_RESPONSE_INVALID')
                succeeded = True
                break
            except urllib.error.HTTPError as error:
                # 不打印响应正文、URL、请求头或 token；认证失败需要重新登记。
                print('MONITOR_HTTP_' + str(error.code), file=sys.stderr)
                if error.code in (401, 403):
                    return 1
            except (OSError, ValueError):
                print('MONITOR_SEND_FAILED', file=sys.stderr)
            if attempt < 2:
                time.sleep(2)
        if args.once:
            return 0 if succeeded else 1
        sequence += 1
        time.sleep(max(0, args.interval-(time.monotonic()-started)))


if __name__ == '__main__':
    raise SystemExit(main())
