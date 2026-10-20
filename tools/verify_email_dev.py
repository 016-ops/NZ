"""在已授权 DEV 验证邮箱与原生组件交接；探针账号结束后停用，凭据不写报告。"""
import argparse
import json
from pathlib import Path
import secrets
import time
import uuid
from tools.dev_remote import connect, execute
from tools.verify_dev import UNITS, wait_health
from tools.email_launcher import Client, LoginError, fingerprint, handshake, metadata
from tools.native_email_probe import probe
from revival.security import otp


def command(client, token, action, **values):
    """沿用受认证命令接口，不直接写入业务库或绕过权限、审计和撤销逻辑。"""
    return client.request('/api/command', metadata(action=action, **values), token)


def owner_login(client, owner):
    """使用原有正常 MFA Owner；若刚用过同一时间步，只等下一步后重试一次，不重置凭据。"""
    for attempt in range(2):
        try:
            return client.request('/api/login', {'kind': 'admin', 'login': owner['login'],
                'password': owner['password'], 'otp_code': otp(owner['otp_secret'], int(time.time()) // 30)})['token']
        except LoginError:
            if attempt:
                raise
            time.sleep(31 - int(time.time()) % 30)


def main():
    """实际注册/审批/登录/交接后重启服务，检查同一角色和账号，最终停用测试账号并撤销邀请。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--workspace', required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    workspace = args.workspace.resolve(strict=True)
    client_sha, _ = fingerprint(workspace)
    ssh = connect(args.source, root / 'runtime/dev-known-hosts')
    pending, reports = [], []
    try:
        for mode, port, expected in [('current', 18765, 'cn-current-unverified'), ('pve', 18767, 'test-pve-v1')]:
            if wait_health(port)['profile'] != expected:
                raise RuntimeError('DEV_PROFILE_MISMATCH')
            client = Client(f'http://127.0.0.1:{port}')
            state = client.request('/api/launcher/status')
            if state.get('qq_required') is not False or state.get('native_launch_ready') is not False:
                raise RuntimeError('DEV_CAPABILITY_MISMATCH')
            owner = json.loads((root / f'runtime/dev-owner-{mode}.json').read_text(encoding='utf-8'))
            admin = owner_login(client, owner)
            item = {'client': client, 'owner_token': admin, 'port': port}
            pending.append(item)
            invitation = command(client, admin, 'invite.create', max_uses=1, expires_at=int(time.time()) + 600)
            item['invitation_id'] = invitation['invitation_id']
            email, password = 'email-qa-' + uuid.uuid4().hex + '@example.test', secrets.token_urlsafe(24)
            account = client.request('/api/player/register', metadata(email=email, password=password, invitation=invitation['invitation_token']))
            item.update(account_id=account['account_id'], game_id=account['game_id'], email=email, password=password)
            try:
                client.request('/api/player/login', {'email': email, 'password': password})
            except LoginError as error:
                if str(error) != 'ACCOUNT_STATE':
                    raise
            else:
                raise RuntimeError('PENDING_ACCOUNT_AUTHENTICATED')
            command(client, admin, 'user.approve', target_account_id=account['account_id'])
            player = client.request('/api/player/login', {'email': email.upper(), 'password': password})['token']
            character = command(client, player, 'character.create', nickname='邮箱DEV_' + uuid.uuid4().hex[:10])
            item['character_id'] = character['character_id']

            def native_read(game_id, game_token):
                """将 DEV 实际签发的独立身份在本机独立原生进程读取，不运行游戏。"""
                return probe(workspace, game_id, game_token)

            result = handshake(client, email, password, client_sha, native_self_test=native_read)
            if result['native_ipc_self_test'].get('ipc_read_verified') is not True:
                raise RuntimeError('DEV_NATIVE_IPC_FAILED')
            item['player_token'] = client.request('/api/player/login', {'email': email, 'password': password})['token']
            reports.append({'profile': expected, 'port': port, 'email_registration': True,
                            'pending_rejected': True, 'approved_login': True, 'character_created': True,
                            'handoff_and_native_ipc': result['native_ipc_self_test'], 'game_process_started': False})
        code, _, _ = execute(ssh, 'sudo -n systemctl restart ' + UNITS, timeout=60)
        if code:
            raise RuntimeError('DEV_RESTART_FAILED')
        for item, report in zip(pending, reports):
            wait_health(item['port'])
            client = item['client']
            current = client.request('/api/views/me', token=item['player_token'])
            if current['game_id'] != item['game_id'] or current['character']['id'] != item['character_id']:
                raise RuntimeError('DEV_EMAIL_PERSISTENCE_FAILED')
            relogin = client.request('/api/player/login', {'email': item['email'], 'password': item['password']})
            current = client.request('/api/views/me', token=relogin['token'])
            if current['id'] != item['account_id'] or current['character']['id'] != item['character_id']:
                raise RuntimeError('DEV_RELOGIN_IDENTITY_CHANGED')
            command(client, item['owner_token'], 'user.disable', target_account_id=item['account_id'])
            item['account_disabled'] = True
            try:
                client.request('/api/player/login', {'email': item['email'], 'password': item['password']})
            except LoginError as error:
                if str(error) != 'ACCOUNT_STATE':
                    raise
            else:
                raise RuntimeError('DISABLED_PROBE_ACCOUNT_AUTHENTICATED')
            command(client, item['owner_token'], 'invite.revoke', invitation_id=item['invitation_id'])
            item['invitation_revoked'] = True
            report.update(service_restart_persistence=True, relogin_preserved=True, probe_account_disabled=True,
                          probe_invitation_revoked=True)
        code, output, _ = execute(ssh, 'systemctl is-active ' + UNITS)
        if code or output.split() != ['active'] * 4:
            raise RuntimeError('DEV_SERVICE_NOT_ACTIVE')
        result = {'kind': 'ACTUAL_DEV_EMAIL_AND_LOCAL_NATIVE_IPC_NOT_GAME_ACCEPTANCE', 'passed': True,
                  'profiles': reports, 'services_active': 4, 'lobby_verified': False, 'multiplayer_verified': False}
    except Exception as error:
        # 异常可能含运维细节，只存私有记录；公开报告仅含异常类型和已取得的无秘密检查结果。
        (root / 'runtime/email-dev-verification-error.txt').write_text(type(error).__name__ + ': ' + str(error), encoding='utf-8')
        result = {'kind': 'ACTUAL_DEV_EMAIL_AND_LOCAL_NATIVE_IPC_NOT_GAME_ACCEPTANCE', 'passed': False,
                  'error_type': type(error).__name__, 'completed_profiles': reports}
    finally:
        for item in pending:
            client, token = item['client'], item['owner_token']
            try:
                if item.get('account_id') and not item.get('account_disabled'):
                    command(client, token, 'user.disable', target_account_id=item['account_id'])
                if item.get('invitation_id') and not item.get('invitation_revoked'):
                    command(client, token, 'invite.revoke', invitation_id=item['invitation_id'])
                client.request('/api/logout', {}, token)
            except (OSError, LoginError):
                # 清理失败保留失败状态，不能把仍活动的探针账号报告为已停用。
                result['passed'] = False
                result['probe_cleanup_failed'] = True
        ssh.close()
    (root / 'evidence/email-login-dev-verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
