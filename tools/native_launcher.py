"""本机 React Launcher 的 Django 服务：内存登录、单会话启动、阶段反馈与可验证清理。

只监听回环，浏览器仅持有随机的本机操作能力；业务凭据不返回前端、不写磁盘。
客户端目录和服务器入口均由受控本机代码选择，HTTP 输入不能选择程序或服务地址。
"""
import ctypes
from datetime import datetime
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import sys
import threading
import time
import uuid
import webbrowser
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from tools.email_launcher import Client, LoginError, fingerprint
from tools.start_native import ROOT, WORKSPACE, launcher_connection, pending_recovery, single_launcher

PORT = 18769
VERSION = 'cn2020-launcher-v1'
CONTROLLER = None
LEASE_VERSION = 'launcher-lease-v1'


class SessionLease:
    """在内存中续期短游戏租约；停止或进程崩溃后服务器自动失效，不保存恢复令牌。"""

    def __init__(self, client, token, scope, initial, stop_game):
        """绑定本轮凭据与取消事件；不允许无租约的旧响应静默进入可长期运行状态。"""
        self.client, self.token, self.scope, self.stop_game = client, token, scope, stop_game
        self.closed = threading.Event()
        self.error = None
        self.accept(initial)
        self.thread = threading.Thread(target=self.run, daemon=True)

    def accept(self, result):
        """严格核对租约版本及整数到期时间，服务响应异常不能被当成续期成功。"""
        expiry, maximum = result.get('expires_at'), result.get('lease_max_expires_at')
        if (result.get('session_lease') != LEASE_VERSION or type(expiry) is not int
                or type(maximum) is not int or not 0 < expiry <= maximum):
            raise RuntimeError('INVALID_SESSION_LEASE')
        self.expires_at, self.maximum = expiry, maximum

    def start(self):
        """兑换后立即开始续期，覆盖较慢的全仓读取与原生启动阶段。"""
        self.thread.start()

    def run(self):
        """网络短故障在租约内重试；认证拒绝或到期立即停止本轮游戏，不能保持假在线。"""
        delay = 0
        while not self.closed.wait(delay):
            try:
                self.accept(self.client.request('/api/game/session/renew',
                    dict(self.scope, session_lease=LEASE_VERSION), self.token))
                delay = 30
            except Exception as error:
                if isinstance(error, LoginError) and str(error) == 'AUTH' or time.time() >= self.expires_at-20:
                    self.error = 'SESSION_LEASE_EXPIRED' if time.time() >= self.expires_at-20 else 'SESSION_REVOKED'
                    self.stop_game.set()
                    return
                delay = 5

    def close(self):
        """先停止续期再注销来源，防止退出流程与续期写入交错。"""
        self.closed.set()
        if self.thread.ident is not None:
            self.thread.join()
        self.token = None


def safe_error(error):
    """只允许固定格式业务错误码；网络/解析异常只显示类别，不泄漏请求、路径或凭据。"""
    value = str(error)
    return value if isinstance(error, (LoginError, ValueError, RuntimeError)) and re.fullmatch(
        r'[A-Z][A-Z_0-9]{0,63}', value) else type(error).__name__.upper()


def process_still_exists(pid):
    """恢复前保守检查旧 PID；无法查询也视为仍在运行，不冒险覆盖活跃游戏配置。"""
    if type(pid) is not int or pid < 1:
        raise ValueError('INVALID_RECOVERY_PROCESS')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if handle:
        kernel.CloseHandle(handle)
        return True
    return ctypes.get_last_error() != 87


def recover_configurations(root=ROOT, workspace=WORKSPACE):
    """仅恢复已登记且无人运行的工作副本变体；外部修改、损坏计划或活跃 PID 均阻止启动。"""
    from tools.native_config_guard import NativeConfigGuard
    for name in pending_recovery(root):
        output = root/'evidence/private'/name
        pids_file = output/'owned-pids.json'
        if pids_file.exists():
            pids = json.loads(pids_file.read_text(encoding='utf-8'))
            if not isinstance(pids, list) or len(pids) > 128 or any(process_still_exists(pid) for pid in pids):
                raise RuntimeError('PREVIOUS_GAME_STILL_RUNNING')
        result = NativeConfigGuard.from_plan(workspace, output).restore()
        if not result['configuration_restored']:
            raise RuntimeError('CONFIGURATION_CONFLICT')


class LauncherController:
    """一个启动器拥有一个账号会话和一个工作线程；重入请求不重复登录或兑换票据。"""

    def __init__(self, client=None):
        """测试可注入本机 API 客户端；真实入口始终固定到当前 DEV 的本机安全隧道。"""
        self.client = client
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.close_requested = threading.Event()
        self.worker = None
        self.connection = None
        self.session = self.game_token = None
        self.client_sha = None
        self.account = None
        self.output = None
        self.phase, self.error, self.busy = 'READY', None, False
        self.events = []
        self.cleanup_pending = False
        self.graphics_mode = 'frame-copy'
        self.last_seen = time.monotonic()
        self.capability = secrets.token_urlsafe(32)

    def update(self, phase):
        """固定阶段及时间用于诊断；保留有界记录，协议成功不写入大厅成功标记。"""
        with self.lock:
            self.phase = phase
            self.events.append({'phase': phase, 'at': datetime.now().strftime('%H:%M:%S')})
            self.events = self.events[-60:]

    def status(self):
        """只返回界面所需状态，不返回邮箱、服务器地址、账号 ID 或任何凭据。"""
        with self.lock:
            from tools.launcher_runtime import native_start_status
            return dict(version=VERSION, phase=self.phase, busy=self.busy, error=self.error,
                authenticated=self.session is not None, nickname=(self.account or {}).get('nickname'),
                cleanup_pending=self.cleanup_pending, graphics_mode=self.graphics_mode, run=self.output.name if self.output else None,
                events=list(self.events), game_active=self.busy and self.phase not in (
                    'LOGGING_IN', 'CONNECTING', 'RECOVERING'), lobby_verified=False, native_start=native_start_status())

    def portal(self, operation, data):
        """受限玩家业务代理；页面只指定枚举动作，不指定远端路径、账号令牌或管理员命令。"""
        with self.lock:
            if self.close_requested.is_set() or self.cleanup_pending or not self.session or self.client is None:
                raise RuntimeError('LOGIN_REQUIRED')
            client, session = self.client, self.session
        if operation == 'view':
            allowed = {'me', 'player-inventory', 'player-shop', 'campaigns', 'leaderboards', 'portal', 'public-portal', 'public-leaderboards'}
            if set(data) - {'view', 'query'} or data.get('view') not in allowed or not isinstance(data.get('query', {}), dict):
                raise ValueError('VIEW_NOT_ALLOWED')
            query = data.get('query', {})
            if set(query) - {'limit', 'offset', 'search', 'after_gid', 'revision', 'campaign_id', 'map_id', 'difficulty', 'player_count'}:
                raise ValueError('QUERY_NOT_ALLOWED')
            if data['view'] == 'portal':
                return client.request('/api/public/portal')
            if data['view'].startswith('public-'):
                from urllib.parse import urlencode
                route = '/api/public/' + data['view'][7:]
                return client.request(route + ('?' + urlencode(query) if query else ''))
            return client.request('/api/player/view', {'view': data['view'], 'query': query}, session)['data']
        if operation != 'command' or set(data) != {'action', 'payload', 'metadata'}:
            raise ValueError('COMMAND_NOT_ALLOWED')
        if data['action'] not in {'player.shop.quote', 'player.shop.purchase', 'campaign.checkin', 'campaign.claim'}:
            raise ValueError('COMMAND_NOT_ALLOWED')
        if (not isinstance(data['payload'], dict) or not isinstance(data['metadata'], dict)
                or set(data['metadata']) != {'request_id', 'idempotency_key', 'reason'}):
            raise ValueError('INVALID_COMMAND')
        payload = dict(data['payload'], **data['metadata'], action=data['action'])
        return client.request('/api/command', payload, session)

    def submit(self, action, data):
        """校验请求后在后台执行；停止仅设置本轮 Event，重复点击不生成第二个任务。"""
        with self.lock:
            if self.close_requested.is_set():
                raise RuntimeError('LAUNCHER_CLOSED')
            if action == 'stop':
                self.stop.set()
                return
            if self.busy:
                raise RuntimeError('LAUNCHER_BUSY')
            if action not in ('login', 'start', 'logout', 'recover', 'close'):
                raise ValueError('UNKNOWN_LAUNCHER_ACTION')
            if action == 'login':
                if self.session is not None or self.cleanup_pending:
                    raise RuntimeError('LOGOUT_REQUIRED')
                if (set(data) != {'email', 'password'} or not isinstance(data['email'], str)
                        or not 3 <= len(data['email']) <= 254 or not isinstance(data['password'], str)
                        or not 1 <= len(data['password']) <= 256):
                    raise ValueError('INVALID_LOGIN_INPUT')
            elif action == 'start':
                if set(data) - {'graphics_mode'} or data.get('graphics_mode', 'frame-copy') not in ('system', 'd3d9ex-flip', 'frame-copy'):
                    raise ValueError('UNEXPECTED_LAUNCHER_ARGUMENTS')
            elif data:
                raise ValueError('UNEXPECTED_LAUNCHER_ARGUMENTS')
            if action == 'start' and (self.session is None or self.cleanup_pending):
                raise RuntimeError('LOGIN_REQUIRED')
            if action == 'start':
                self.graphics_mode = data.get('graphics_mode', 'frame-copy')
            self.busy, self.error = True, None
            self.stop.clear()
            self.worker = threading.Thread(target=self._execute, args=(action, data), daemon=False)
            self.worker.start()

    def _connect(self):
        """创建到已登记 DEV 的专属 SSH 隧道；无法验证连接时不退回本机测试服务。"""
        if self.connection is None:
            self.update('CONNECTING')
            connection = launcher_connection()
            self.client = connection.__enter__()
            self.connection = connection

    def _execute(self, action, data):
        """统一记录安全错误并释放输入；清理失败保留内存令牌供用户重试注销。"""
        try:
            if action == 'login':
                self._connect()
                self.update('LOGGING_IN')
                try:
                    self.session = self.client.request('/api/launcher/login', data)['token']
                finally:
                    data.clear()
                current = self.client.request('/api/views/me', token=self.session)
                character = current.get('character')
                if current.get('email_verified') is not True or not isinstance(character, dict):
                    raise RuntimeError('VERIFIED_ACCOUNT_AND_CHARACTER_REQUIRED')
                self.account = dict(account_id=current['id'], game_id=current['game_id'],
                    character_id=character['id'], nickname=character['nickname'])
                self.update('LOGGED_IN')
            elif action == 'start':
                self._run_game()
            elif action == 'recover':
                self.update('RECOVERING')
                recover_configurations()
                if self.cleanup_pending:
                    self._logout()
                self.update('LOGGED_IN' if self.session else 'READY')
            else:
                self._logout()
                self.update('CLOSED' if action == 'close' else 'READY')
                if action == 'close':
                    self.close_requested.set()
        except Exception as error:
            code = safe_error(error)
            if action == 'login' and self.session is not None:
                try:
                    self._logout()
                except Exception:
                    code = 'SESSION_CLEANUP_PENDING'
            with self.lock:
                self.error = code
            self.update('ERROR')
        finally:
            data.clear()
            with self.lock:
                self.busy = False

    def _logout(self):
        """退出后回读来源和游戏凭据；网络失败保留清理标记，不能宣称已经撤销。"""
        from tools.native_email_boot import revoke_owned_dev_session
        if self.session is None:
            return
        self.update('CLEANING')
        cleanup = revoke_owned_dev_session(self.client, self.session,
            game_token=self.game_token, client_sha=self.client_sha)
        self.cleanup_pending = not cleanup['verified']
        if self.output and self.output.is_dir():
            (self.output/'session-cleanup.json').write_text(json.dumps(cleanup, indent=2), encoding='utf-8')
        if self.cleanup_pending:
            raise RuntimeError('SESSION_CLEANUP_PENDING')
        self.session = self.game_token = self.account = None

    def _run_game(self):
        """固定启动合同：先验证副本和身份，再兑换票据并运行，最终必经配置与凭据清理。"""
        from tools.launcher_runtime import require_native_client
        require_native_client()
        from adapters.email_protocol import PROTOCOL
        from tools.native_email_boot import current_dev_player_info, probe
        from tools.native_settings_bridge import NativeSettingsBridge
        from tools.native_inventory_bridge import NativeInventoryBridge
        from tools.native_feature_bridge import NativeFeatureBridge
        self.output = ROOT/'evidence/private'/('launcher-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        self.update('RECOVERING')
        result = None
        lease = None
        try:
            recover_configurations()
            self.update('CLIENT_CHECK')
            self.client_sha, _ = fingerprint(WORKSPACE)
            self._check_stop()
            self.update('AUTHORIZING')
            verifier = secrets.token_urlsafe(32)
            scope = dict(protocol=PROTOCOL, client_sha256=self.client_sha)
            ticket = self.client.request('/api/launcher/tickets', dict(scope,
                verifier_sha256=hashlib.sha256(verifier.encode()).hexdigest(),
                request_id=uuid.uuid4().hex, idempotency_key=uuid.uuid4().hex,
                reason='local unified launcher'), self.session)
            # 兑换应答不确定时不重放；finally 撤销来源会话，同时使可能创建的游戏凭据失效。
            game = self.client.request('/api/launcher/redeem', dict(scope, verifier=verifier,
                ticket=ticket['ticket'], session_lease=LEASE_VERSION))
            self.game_token = game['game_token']
            lease = SessionLease(self.client, self.game_token, scope, game, self.stop)
            lease.start()
            identity = self.client.request('/api/game/session', scope, self.game_token)
            verification, nickname = current_dev_player_info(self.client, self.session, self.account, identity,
                code_page=ctypes.WinDLL('kernel32').GetACP())
            self._check_stop()
            self.update('LOADING_INVENTORY')
            settings = NativeSettingsBridge(self.client, self.game_token, self.client_sha)
            inventory = NativeInventoryBridge(self.client, self.game_token, self.client_sha, identity['game_id'], paged=True)
            inventory.features = NativeFeatureBridge(inventory,
                ROOT/'runtime/native-operations'/f"{identity['game_id']}.json")
            self._check_stop()
            result = probe(WORKSPACE, identity['game_id'], self.game_token, self.output,
                redirect_game_network=True, verify_native_challenge=True, capture_application=True,
                serve_geo_ping=True, serve_geo_zone=True, capture_zone_sequence=True, serve_zone_login=True,
                serve_player_define=True, player_info_nickname=nickname, player_props_empty=True, windowed=True,
                serve_detailed_channel=True, native_settings=settings, serve_zone_hints=True,
                defer_player_info_after_version=True, continuous_zone=True, native_inventory=inventory,
                manual_test=True, stop_requested=self.stop, progress=self.update, record_packets=False,
                graphics_compat=self.graphics_mode == 'd3d9ex-flip',
                frame_copy=self.graphics_mode == 'frame-copy', startup_timeout=180)
            result['server_email_identity_verification'] = verification
            if lease.error:
                raise RuntimeError(lease.error)
            if not result.get('game_process_started'):
                # 已核实的图形上下文失败应保留具体诊断码；不能误导为客户端文件或账号故障。
                # 只允许本地探针定义的固定码，未知报告仍使用通用启动错误，避免泄漏原始异常。
                raise RuntimeError('D3D9_UNAVAILABLE_IN_LAUNCH_CONTEXT' if result.get('blocker')==
                    'D3D9_UNAVAILABLE_IN_LAUNCH_CONTEXT' else 'CLIENT_DID_NOT_START')
            if not result.get('cleanup_verified') or not result.get('configuration_restored'):
                raise RuntimeError('CONFIGURATION_CLEANUP_PENDING')
            if result.get('startup_error'):
                raise RuntimeError(result['startup_error'])
        finally:
            if lease is not None:
                lease.close()
            # 即使本地配置/图形检查失败，也撤销本次登录；下次启动重新验证账号状态。
            self._logout()
            if self.output.is_dir():
                manifest = dict(version=VERSION, events=self.events, configuration_pending=bool(pending_recovery()),
                    credentials_revoked=not self.cleanup_pending, lobby_verified=False,
                    client_sha256=self.client_sha, game_process_started=bool(result and result.get('game_process_started')))
                (self.output/'launcher-summary.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        self.update('EXITED')

    def _check_stop(self):
        """在兑换及启动边界响应取消，不会在取消后继续创建新的游戏进程。"""
        if self.stop.is_set():
            raise RuntimeError('START_CANCELLED')

    def dispose(self):
        """服务退出时等候自有游戏清理；最后关闭自己建立的隧道，不抹去失败记录。"""
        self.stop.set()
        if self.worker:
            self.worker.join()
        try:
            self._logout()
        finally:
            if self.connection:
                self.connection.__exit__(None, None, None)


def endpoint(request, resource=''):
    """本机 Django 入口：精确 Host/Origin、随机能力和 JSON 校验共同阻止跨站启动。"""
    from django.http import HttpResponse, JsonResponse
    controller = CONTROLLER
    origin = f'http://127.0.0.1:{PORT}'
    if request.META.get('HTTP_HOST') != f'127.0.0.1:{PORT}':
        return JsonResponse({'error': 'HOST_REJECTED'}, status=403)
    if request.META.get('HTTP_ORIGIN') not in (None, origin):
        return JsonResponse({'error': 'ORIGIN_REJECTED'}, status=403)
    if resource.startswith('api/'):
        if not controller or not hmac.compare_digest(request.headers.get('X-NZ-Launcher', ''), controller.capability):
            return JsonResponse({'error': 'LAUNCHER_ACCESS_REQUIRED'}, status=403)
        controller.last_seen = time.monotonic()
        action = resource[4:]
        if action == 'status' and request.method == 'GET':
            response = JsonResponse(controller.status())
        elif request.method == 'POST' and request.headers.get('Content-Type', '').split(';')[0] == 'application/json':
            try:
                # 在访问 body 前拒绝过大长度，Django 全局上限进一步约束缺失/伪造长度。
                if int(request.META.get('CONTENT_LENGTH') or 0) > 8192:
                    raise ValueError('REQUEST_TOO_LARGE')
                data = json.loads(request.body)
                if not isinstance(data, dict):
                    raise ValueError('INVALID_REQUEST')
                if action in ('view', 'command'):
                    result = controller.portal(action, data)
                    response = JsonResponse(result, safe=isinstance(result, dict))
                else:
                    controller.submit(action, data)
                    response = JsonResponse({'accepted': True}, status=202)
            except (ValueError, RuntimeError, LoginError) as error:
                response = JsonResponse({'error': safe_error(error)}, status=409)
        else:
            response = JsonResponse({'error': 'METHOD_REJECTED'}, status=405)
    elif request.method == 'GET':
        from tools.launcher_runtime import resource_root
        dist = resource_root()/'admin/dist'
        if resource in ('', '/'):
            target = dist/'index.html'
        elif resource.startswith('assets/'):
            target = (dist/resource).resolve()
            if not target.is_relative_to((dist/'assets').resolve()) or target.suffix.lower() not in ('.js', '.css', '.woff2', '.png', '.jpg', '.jpeg', '.webp'):
                return HttpResponse(status=404)
        else:
            return HttpResponse(status=404)
        if not target.is_file():
            return HttpResponse('Launcher 界面尚未构建。', status=503)
        response = HttpResponse(target.read_bytes(), content_type=mimetypes.guess_type(target)[0] or 'application/octet-stream')
    else:
        response = HttpResponse(status=405)
    response['Cache-Control'] = 'no-store'
    response['Referrer-Policy'] = 'no-referrer'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = "default-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; style-src 'self' 'unsafe-inline'"
    return response


class LocalServer(ThreadingMixIn, WSGIServer):
    """请求并发仅用于状态轮询；业务写入由控制器串行化，慢登录不会阻塞停止按钮。"""
    daemon_threads = True

    def get_request(self):
        """限制本机异常连接的读取时间，避免无限占用工作线程。"""
        connection, address = super().get_request()
        connection.settimeout(10)
        return connection, address


class QuietHandler(WSGIRequestHandler):
    """禁止访问日志回显 URL 或认证输入；诊断只保存固定阶段和安全错误码。"""
    def log_message(self, *args):
        """有意关闭通用访问日志，避免把浏览器环境信息写入启动证据。"""


def watch_desktop(stream, requested, disconnected):
    """只读取父 Tauri 的继承管道；窗口关闭或父进程崩溃均请求有序清理，不接收执行参数。"""
    try:
        for line in stream:
            if line.strip() == 'close':
                requested.set()
    finally:
        # EOF 比 PID 轮询可靠：父进程正常退出和崩溃都会关闭本轮唯一写端。
        disconnected.set()
        requested.set()


def main():
    """启动 Django 本机服务；Tauri 模式通过继承管道交接能力，窗口整个生命周期持有锁。"""
    global CONTROLLER
    from django.conf import settings
    from django.core.wsgi import get_wsgi_application
    from django.urls import re_path
    from tools.native_launcher_window import save_window, reopen_window
    global urlpatterns
    urlpatterns = [re_path(r'^launcher(?:/(?P<resource>.*))?$', endpoint),
                   re_path(r'^(?P<resource>assets/.*)$', endpoint)]
    if not settings.configured:
        settings.configure(DEBUG=False, SECRET_KEY=secrets.token_urlsafe(32), ALLOWED_HOSTS=['127.0.0.1'],
            ROOT_URLCONF=__name__, INSTALLED_APPS=[], MIDDLEWARE=[], DATA_UPLOAD_MAX_MEMORY_SIZE=8192)
    os.chdir(ROOT)
    (ROOT/'runtime').mkdir(exist_ok=True)
    window_record = ROOT/'runtime/native-launcher-window.bin'
    desktop = sys.argv[1:] == ['--desktop']
    if sys.argv[1:] and not desktop:
        raise ValueError('INVALID_LAUNCHER_MODE')
    if not desktop and reopen_window(window_record, PORT):
        return
    controller = CONTROLLER = LauncherController()
    with single_launcher():
        with make_server('127.0.0.1', PORT, get_wsgi_application(), LocalServer, QuietHandler) as server:
            server.timeout = 0.5
            url = f'http://127.0.0.1:{PORT}/launcher#access={controller.capability}'
            requested, disconnected = threading.Event(), threading.Event()
            if desktop:
                # 能力只进入父进程私有 stdout 管道，不出现在命令行、文件或通用访问日志中。
                print(json.dumps({'version': VERSION, 'url': url}), flush=True)
                threading.Thread(target=watch_desktop, args=(sys.stdin, requested, disconnected), daemon=True).start()
            else:
                save_window(window_record, controller.capability)
                webbrowser.open(url)
            try:
                closed_at = None
                while True:
                    server.handle_request()
                    if requested.is_set() and not controller.close_requested.is_set():
                        controller.stop.set()
                        if not controller.busy:
                            requested.clear()
                            controller.submit('close', {})
                    if disconnected.is_set() and not controller.busy and controller.error:
                        # 父窗口已消失，保留错误后进入 dispose 再次撤销，不留下无人管理的服务。
                        break
                    if controller.close_requested.is_set():
                        # 给页面留出最后一次轮询机会，确认清理完成后再关闭监听。
                        closed_at = closed_at or time.monotonic()
                        if time.monotonic()-closed_at > (0.5 if desktop else 4):
                            break
                    # 未登录且无游戏时，关闭页面后两分钟自动退出，不留下无用后台进程。
                    if not desktop and not controller.busy and not controller.session and time.monotonic()-controller.last_seen > 120:
                        break
            finally:
                try:
                    controller.dispose()
                finally:
                    if not desktop:
                        window_record.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        (ROOT/'runtime/native-launcher-error.log').write_text(safe_error(error), encoding='utf-8')
        raise SystemExit(1) from None
