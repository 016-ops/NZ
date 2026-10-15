"""通过自建邮箱服务完成启动交接诊断；原生适配未完成时不运行游戏或腾讯启动器。"""
import argparse
import getpass
import http.client
import json
import hashlib
from pathlib import Path
import secrets
import re
from urllib.parse import urlsplit
import uuid
from adapters.email_protocol import PROTOCOL
from tools.client_workspace import digest
from tools.native_recovery import validate


class LoginError(Exception):
    """只携带安全错误码，避免 CLI 异常回显含密码、会话或票据的请求正文。"""


class Client:
    """回环 HTTP 客户端；不接受 URL 凭据、查询参数或跳转，不将密码发到重定向目标。"""
    def __init__(self, url):
        """限定用户已选择的 SSH 隧道/本机服务，禁止明文凭据直接发到外部主机。"""
        parsed = urlsplit(url)
        if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost')
                or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')
                or not parsed.port or not 1 <= parsed.port <= 65535):
            raise ValueError('Use a loopback HTTP URL with an explicit port, including for the DEV SSH tunnel')
        self.host, self.port = parsed.hostname, parsed.port

    def request(self, path, data=None, token=None):
        """单次请求使用独立连接与内存令牌；不自动重试、持久化凭据或解析第三方跳转。"""
        connection = http.client.HTTPConnection(self.host, self.port, timeout=15)
        headers = {'Content-Type': 'application/json', 'X-NZ-Request': '1'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        try:
            connection.request('POST' if data is not None else 'GET', path,
                               json.dumps(data).encode('utf-8') if data is not None else None, headers)
            result = connection.getresponse()
            body = result.read(65537)
            if len(body) > 65536:
                raise LoginError('RESPONSE_TOO_LARGE')
            if result.status != 200:
                try:
                    code = json.loads(body).get('error', 'HTTP_' + str(result.status))
                except (ValueError, AttributeError):
                    code = 'HTTP_' + str(result.status)
                safe_code = code if isinstance(code, str) and re.fullmatch('[A-Z][A-Z_0-9]{0,63}', code) else 'REQUEST_REJECTED'
                raise LoginError(safe_code)
            value = json.loads(body)
            if not isinstance(value, dict):
                raise LoginError('INVALID_RESPONSE')
            return value
        finally:
            connection.close()


def fingerprint(workspace):
    """核对登记副本与 Win32 入口摘要；这是候选文件指纹，不是完整原生能力或来源认证。"""
    metadata, recovery = validate(workspace)
    entry = workspace / 'Binaries/Win32/TGame.exe'
    expected = next((row['after_sha256'] for row in (recovery or {}).get('files', [])
                     if row['path'] == 'Binaries/Win32/TGame.exe'), None)
    if expected is None:
        expected = digest(Path(metadata['source']) / 'Binaries/Win32/TGame.exe')
    if digest(entry) != expected:
        raise ValueError('Registered client entry hash mismatch')
    return expected, metadata


def handshake(client, email, password, client_sha, *, native_self_test=None):
    """执行真实邮箱登录/票据/兑换/身份读取，诊断结束注销源会话，使临时游戏令牌失效。"""
    state = client.request('/api/launcher/status')
    if state.get('protocol') != PROTOCOL or not state.get('email_password'):
        raise LoginError('PROTOCOL_UNSUPPORTED')
    session = client.request('/api/player/login', {'email': email, 'password': password})
    try:
        verifier = secrets.token_urlsafe(32)
        ticket = client.request('/api/launcher/tickets', {'protocol': PROTOCOL, 'client_sha256': client_sha,
            'verifier_sha256': hashlib.sha256(verifier.encode()).hexdigest(), 'request_id': uuid.uuid4().hex,
            'idempotency_key': uuid.uuid4().hex, 'reason': 'independent email launcher handshake diagnostic'}, session['token'])
        proof = {'protocol': PROTOCOL, 'client_sha256': client_sha, 'ticket': ticket['ticket'], 'verifier': verifier}
        game = client.request('/api/launcher/redeem', proof)
        identity = client.request('/api/game/session', {'protocol': PROTOCOL, 'client_sha256': client_sha}, game['game_token'])
        result = {'protocol': PROTOCOL, 'email_login': True, 'single_use_handoff': True,
                'account_id': identity['account_id'], 'game_id': identity['game_id'], 'profile': identity['profile'],
                'native_launch_ready': False, 'game_process_started': False, 'qq_credentials_used': False}
        if native_self_test is not None:
            # 受控本机适配器只接收已验证的本地身份与短期游戏凭据，不接触邮箱密码或玩家 Bearer。
            result['native_ipc_self_test'] = native_self_test(identity['game_id'], game['game_token'])
        return result
    finally:
        # 诊断不是长驻游戏进程；源会话撤销后，已经兑换的游戏会话也不能继续授权。
        client.request('/api/logout', {}, session['token'])


def main():
    """邮箱和密码仅交互输入；原生桥未就绪时只接受显式诊断，不能把成功握手显示为进游戏。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--handshake-only', action='store_true')
    parser.add_argument('--proxy-self-test', action='store_true', help='Read the independent identity through the current Win32 proxy in a separate process; does not start the game')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not args.handshake_only:
        parser.error('Native client adapter is not implemented; only --handshake-only is available')
    root = args.workspace.resolve(strict=True)
    client_sha, metadata = fingerprint(root)
    if args.output and (args.output.exists() or any(args.output.resolve().is_relative_to(path)
                       for path in (root, Path(metadata['source'])))):
        parser.error('Output must be a new file outside both client directories')
    client = Client(args.server)
    email = input('邮箱：').strip()
    password = getpass.getpass('独立账号密码：')
    native = None
    if args.proxy_self_test:
        from tools.native_email_probe import probe
        # 闭包绑定已核对的工作副本，服务端响应不能选择 DLL 路径、其他 PID 或执行文件。
        def native(game_id, game_token):
            """把受认证本地身份送入当前副本的独立 IPC 自测；不会启动完整游戏。"""
            return probe(root, game_id, game_token)
    try:
        result = handshake(client, email, password, client_sha, native_self_test=native)
    except LoginError as error:
        raise SystemExit('Email launcher request failed: ' + str(error)) from None
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True))
    if args.proxy_self_test and not result['native_ipc_self_test'].get('ipc_read_verified'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
