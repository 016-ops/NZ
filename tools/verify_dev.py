"""验证实际 DEV 服务、SSH 隧道、认证与重启持久性；不将控制面结果写成原生游戏通过。"""
import argparse
import http.client
import json
from pathlib import Path
import re
import time
import uuid
from tools.dev_remote import connect, execute
from revival.security import otp

UNITS = 'nz-dev-current.service nz-dev-current-maintain.service nz-dev-pve.service nz-dev-pve-maintain.service'


def request(port, path, data=None, token=None, origin=None):
    """仅访问本机 SSH 隧道，凭据在 HTTP 正文和 Bearer 中传输，不进入 URL 或日志。"""
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=30)
    headers = {}
    if data is not None:
        headers.update({'Content-Type': 'application/json', 'X-NZ-Request': '1'})
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if origin:
        headers['Origin'] = origin
    try:
        connection.request('POST' if data is not None else 'GET', path,
                           body=json.dumps(data) if data is not None else None, headers=headers)
        response = connection.getresponse()
        body = response.read()
        value = json.loads(body) if 'application/json' in response.getheader('Content-Type', '') else body.decode('utf-8', errors='replace')
        return response.status, value
    finally:
        connection.close()


def command(port, token, action, **data):
    """每个部署探针使用独立业务幂等键；同一探针的重启验证只读，不重复创建邀请。"""
    data.update(action=action, request_id=uuid.uuid4().hex, idempotency_key=uuid.uuid4().hex, reason='DEV deployment persistence probe')
    status, result = request(port, '/api/command', data, token)
    if status != 200:
        raise RuntimeError('DEV probe command failed with HTTP ' + str(status))
    return result


def wait_health(port):
    """重启后有界等待健康接口；失败持续记录，不把 systemd active 当作 HTTP 通过。"""
    for _ in range(30):
        try:
            code, value = request(port, '/health')
            if code == 200:
                return value
        except OSError:
            pass
        time.sleep(1)
    raise RuntimeError('DEV health did not recover after restart')


def main():
    """先做授权和静态检查，创建短期邀请，重启四个服务，再核对同一记录并撤销探针邀请。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    client = connect(args.source, root / 'runtime/dev-known-hosts')
    profiles, pending = [], []
    try:
        code, out, error = execute(client, 'systemctl is-active ' + UNITS)
        if code or out.split() != ['active'] * 4:
            raise RuntimeError('All four DEV services must be active')
        for mode, port, expected in [('current', 18765, 'cn-current-unverified'), ('pve', 18767, 'test-pve-v1')]:
            health = wait_health(port)
            if health['profile'] != expected or health['retail_compatible'] is not False:
                raise RuntimeError('DEV profile mismatch')
            status, page = request(port, '/')
            if status != 200 or 'NZ Revival' not in page:
                raise RuntimeError('DEV frontend is not available')
            assets = re.findall(r'(?:src|href)="(/assets/[^" ]+)"', page)
            if not assets or any(request(port, asset)[0] != 200 for asset in assets):
                raise RuntimeError('DEV frontend assets are incomplete')
            if request(port, '/api/views/admins')[0] != 401:
                raise RuntimeError('DEV protected view allowed anonymous access')
            if request(port, '/api/login', {}, origin='https://foreign.invalid')[0] != 403:
                raise RuntimeError('DEV origin policy is not enforced')
            owner = json.loads((root / f'runtime/dev-owner-{mode}.json').read_text(encoding='utf-8'))
            status, session = request(port, '/api/login', {'kind': 'admin', 'login': owner['login'],
                                      'password': owner['password'], 'otp_code': otp(owner['otp_secret'], int(time.time()) // 30)})
            if status != 200:
                raise RuntimeError('DEV Owner login failed with HTTP ' + str(status))
            token = session['token']
            for view in ('admins', 'batches', 'reviews', 'rooms', 'pve-runs', 'catalog'):
                if request(port, '/api/views/' + view, token=token)[0] != 200:
                    raise RuntimeError('DEV authenticated view failed')
            invite = command(port, token, 'invite.create', max_uses=1, expires_at=int(time.time()) + 3600)
            pending.append((port, token, invite['invitation_id']))
            profiles.append({'profile': expected, 'port': port, 'frontend_assets_checked': len(assets),
                             'owner_login': True, 'anonymous_denied': True, 'foreign_origin_denied': True,
                             'authenticated_views': 6})
        code, out, error = execute(client, 'sudo -n systemctl restart ' + UNITS, timeout=60)
        if code:
            raise RuntimeError('DEV restart command failed')
        for row, (port, token, invitation) in zip(profiles, pending):
            wait_health(port)
            code, rows = request(port, '/api/views/invitations', token=token)
            if code != 200 or not any(item['id'] == invitation for item in rows):
                raise RuntimeError('DEV committed invitation was not recovered after restart')
            command(port, token, 'invite.revoke', invitation_id=invitation)
            request(port, '/api/logout', {}, token)
            row['service_restart_persistence_verified'] = True
            row['probe_invitation_revoked'] = True
        pending.clear()
        checks = []
        for profile in ('', '--pve-test-profile '):
            code, out, error = execute(client, 'cd /opt/nz-revival/current && sudo -n -u nz-revival env NZ_ENV_FILE=/opt/nz-revival/shared/mysql.env '
                                       '/opt/nz-revival/current/.venv/bin/python -m revival ' + profile + 'check', timeout=120)
            if code:
                raise RuntimeError('DEV MySQL integrity check failed')
            checks.append(json.loads(out))
        code, out, error = execute(client, 'sudo -n /opt/nz-revival/shared/python312/bin/python3.12 --version; '
                                   'sudo -n cat /opt/nz-revival/shared/python-build/source.sha256; '
                                   "ss -ltn | grep -E ':(18765|18767) '")
        report = {'kind': 'ACTUAL_DEV_LINUX_CONTROL_PLANE_AND_SSH_TUNNEL', 'passed': True,
                  'profiles': profiles, 'mysql_checks': checks, 'runtime_and_listeners': out.splitlines(),
                  'retail_game_verified': False, 'multiplayer_verified': False}
    except Exception as error:
        # 具体网络/运维细节留本机私有排错记录；公开结果不回显响应正文、账号或连接参数。
        (root / 'runtime/dev-verification-error.txt').write_text(type(error).__name__ + ': ' + str(error), encoding='utf-8')
        report = {'kind': 'ACTUAL_DEV_LINUX_CONTROL_PLANE_AND_SSH_TUNNEL', 'passed': False, 'error_type': type(error).__name__}
    finally:
        # 中途失败也尽量撤销本轮创建的邀请，保留审计记录，避免悬挂可用探针令牌。
        for port, token, invitation in pending:
            try:
                command(port, token, 'invite.revoke', invitation_id=invitation)
                request(port, '/api/logout', {}, token)
            except (OSError, RuntimeError):
                pass
        client.close()
    (root / 'evidence/m0-m5-dev-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
