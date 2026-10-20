"""发行 Launcher 的资源、用户数据目录与公开 HTTPS/回环连接入口。

仓库研究模式保留旧入口。发行进程只读取包内公开地址，不寻找 SSH 文件、不保存账号
密码或游戏令牌。资源置于 PyInstaller 只读目录，运行日志及恢复记录置于当前用户
LOCALAPPDATA；发行包不携带完整客户端，原生启动在兑换票据前明确阻止。
"""
from contextlib import contextmanager
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import sys
import time
from urllib.parse import urlsplit


PACKAGE_VERSION = 'nz-launcher-package-v1'
STATE_DIRECTORY = 'NZRevivalLauncher'


def packaged():
    """仅以 PyInstaller 的 frozen 标志识别发行进程，不接受网页或环境中的模式开关。"""
    return bool(getattr(sys, 'frozen', False))


def resource_root():
    """返回只读资源根；PyInstaller 的 _MEIPASS 已包含 config 和 admin/dist。"""
    return Path(sys._MEIPASS).resolve() if packaged() else Path(__file__).resolve().parents[1]


def distribution_root():
    """固定 onedir 的 service/ 子目录布局，不从工作目录或命令行寻找可执行程序。"""
    return Path(sys.executable).resolve().parent.parent if packaged() else resource_root()


def state_root(*, create=False):
    """发行数据使用当前 Windows 用户 AppData；源代码模式保持原仓库 runtime 布局。"""
    if not packaged():
        root = resource_root()
    else:
        base = os.environ.get('LOCALAPPDATA')
        if not base or not Path(base).is_absolute():
            raise RuntimeError('LAUNCHER_STATE_UNAVAILABLE')
        root = Path(base) / STATE_DIRECTORY
    if create:
        root.mkdir(parents=True, exist_ok=True)
        (root / 'runtime').mkdir(exist_ok=True)
        (root / 'evidence/private').mkdir(parents=True, exist_ok=True)
    return root


def workspace_root():
    """保留研究副本默认位置；发行目录仅为未配置占位，不能据此启动未知客户端。"""
    return state_root() / ('runtime/native-client' if packaged() else 'runtime/native-recovery/coherent-2020/nz')


def native_start_status():
    """如实声明发行包没有完整原生客户端/通道验收，仓库研究模式保持原有独立验收。"""
    return {'available': not packaged(), 'blocker': 'PACKAGE_NATIVE_CLIENT_UNAVAILABLE' if packaged() else None,
            'mode': 'portable-platform' if packaged() else 'repository-research',
            'native_combat_verified': False}


def require_native_client():
    """仅发行模式阻止原生票据兑换；不把本机登录和平台购物称为原生实玩通过。"""
    if packaged():
        raise RuntimeError('PACKAGE_NATIVE_CLIENT_UNAVAILABLE')
    return workspace_root()


def validate_public_config(config):
    """仅接受公开 HTTPS 或明确回环隧道；拒绝 URL 凭据、路径、跳转配置及未知秘密字段。"""
    allowed = {'schema_version', 'api_base_url', 'loopback_host_port'}
    if not isinstance(config, dict) or set(config) - allowed or config.get('schema_version') != PACKAGE_VERSION:
        raise ValueError('INVALID_PUBLIC_LAUNCHER_CONFIG')
    address = config.get('api_base_url')
    if not isinstance(address, str) or len(address) > 2048:
        raise ValueError('INVALID_PUBLIC_LAUNCHER_CONFIG')
    try:
        url = urlsplit(address)
        port = url.port
    except ValueError:
        raise ValueError('INVALID_PUBLIC_LAUNCHER_CONFIG') from None
    if (not url.hostname or url.username or url.password or url.query or url.fragment
            or url.path not in ('', '/') or '\\' in address or any(ord(c) < 33 for c in address)):
        raise ValueError('INVALID_PUBLIC_LAUNCHER_CONFIG')
    loopback = url.scheme == 'http' and url.hostname == '127.0.0.1' and port is not None
    if not loopback and url.scheme != 'https':
        raise ValueError('HTTPS_OR_EXPLICIT_LOOPBACK_REQUIRED')
    if port is not None and not 1 <= port <= 65535:
        raise ValueError('INVALID_PUBLIC_LAUNCHER_CONFIG')
    host_port = config.get('loopback_host_port')
    if host_port is not None and (not loopback or type(host_port) is not int or not 1 <= host_port <= 65535):
        raise ValueError('INVALID_PUBLIC_LAUNCHER_CONFIG')
    return dict(config, api_base_url=address.rstrip('/'))


def load_public_config(path=None):
    """读取包根的公开配置，严格限制大小；不回退到 DEV SSH 配置或旧凭据文件。"""
    target = Path(path) if path is not None else (distribution_root() / 'launcher-config.json' if packaged()
                                                  else resource_root() / 'config/launcher-public.example.json')
    if not target.is_file() or target.stat().st_size > 8192:
        raise RuntimeError('PUBLIC_LAUNCHER_CONFIG_MISSING')
    return validate_public_config(json.loads(target.read_text(encoding='utf-8')))


class PublicApiClient:
    """兼容既有 Client.request 的公开网关传输；TLS 使用系统验证，绝不追随重定向。"""

    def __init__(self, config):
        """复制受控配置而不接受页面 URL；连接/请求令牌只保存在当前内存。"""
        self.config = validate_public_config(config)
        self.url = urlsplit(self.config['api_base_url'])
        self.last_request_timing = None

    def request(self, path, data=None, token=None):
        """执行一次有界请求，无自动重试；拒绝外部目标路径并仅回传安全业务错误码。"""
        from tools.email_launcher import LoginError
        if not isinstance(path, str) or not path.startswith('/') or path.startswith('//') or '\\' in path:
            raise LoginError('INVALID_REQUEST_PATH')
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or parsed.fragment or any(ord(c) < 32 for c in path):
            raise LoginError('INVALID_REQUEST_PATH')
        started = time.perf_counter()
        self.last_request_timing = None
        if self.url.scheme == 'https':
            connection = http.client.HTTPSConnection(self.url.hostname, self.url.port or 443,
                                                       timeout=15, context=ssl.create_default_context())
        else:
            connection = http.client.HTTPConnection(self.url.hostname, self.url.port, timeout=15)
        headers = {'Content-Type': 'application/json', 'X-NZ-Request': '1'}
        if self.config.get('loopback_host_port') is not None:
            headers['Host'] = '127.0.0.1:' + str(self.config['loopback_host_port'])
        if token:
            headers['Authorization'] = 'Bearer ' + token
        try:
            connection.request('POST' if data is not None else 'GET', path,
                               json.dumps(data).encode('utf-8') if data is not None else None, headers)
            result = connection.getresponse()
            headers_at = time.perf_counter()
            # 网页me信封保留全仓合同，普通分页/命令仍有较小上限，避免GM全目录账号被64KiB误拒。
            full_inventory = parsed.path in ('/api/views/me', '/api/game/inventory/get') or (
                parsed.path == '/api/player/view' and isinstance(data, dict) and data.get('view') == 'me')
            limit = 8 * 1024 * 1024 if full_inventory else 2 * 1024 * 1024
            body = result.read(limit + 1)
            body_at = time.perf_counter()
            if len(body) > limit:
                raise LoginError('RESPONSE_TOO_LARGE')
            if result.status != 200:
                try:
                    code = json.loads(body).get('error', 'HTTP_' + str(result.status))
                except (ValueError, AttributeError):
                    code = 'HTTP_' + str(result.status)
                raise LoginError(code if isinstance(code, str) and re.fullmatch('[A-Z][A-Z_0-9]{0,63}', code)
                                 else 'REQUEST_REJECTED')
            value = json.loads(body)
            if not isinstance(value, (dict, list)):
                raise LoginError('INVALID_RESPONSE')
            self.last_request_timing = {'response_bytes': len(body),
                                        'headers_ms': round((headers_at - started) * 1000, 3),
                                        'body_ms': round((body_at - headers_at) * 1000, 3),
                                        'decode_ms': round((time.perf_counter() - body_at) * 1000, 3),
                                        'total_ms': round((time.perf_counter() - started) * 1000, 3)}
            return value
        finally:
            connection.close()

    def feedback_upload(self, ticket_id, payload, mime, upload_key, token):
        """发行包复用同一固定附件合同；图片不是JSON请求，不能通过request偷换远端路径。"""
        from tools.email_launcher import Client
        return Client.feedback_upload(self, ticket_id, payload, mime, upload_key, token)

    def feedback_image(self, attachment_id, token):
        """发行包按已鉴权的附件ID读取私图；不会把远端Bearer返回WebView。"""
        from tools.email_launcher import Client
        return Client.feedback_image(self, attachment_id, token)

    def public_cover_image(self, media_id):
        """公共活动封面复用固定ID/有界WebP合同，不向网关发送玩家Bearer。"""
        from tools.email_launcher import Client
        return Client.public_cover_image(self, media_id)

    def _feedback_media(self, method, path, token, body=None, content_type=None):
        """共享有界编码、错误、MIME及禁止重定向规则，连接目标仍由本类公开配置决定。"""
        from tools.email_launcher import Client
        return Client._feedback_media(self, method, path, token, body, content_type)

    def _feedback_connection(self):
        """与普通发行请求相同的TLS/回环边界；只允许系统证书验证，没有不安全降级开关。"""
        if self.url.scheme == 'https':
            connection = http.client.HTTPSConnection(self.url.hostname, self.url.port or 443,
                timeout=20, context=ssl.create_default_context())
        else:
            connection = http.client.HTTPConnection(self.url.hostname, self.url.port, timeout=20)
        headers = {'X-NZ-Request': '1'}
        if self.config.get('loopback_host_port') is not None:
            headers['Host'] = '127.0.0.1:' + str(self.config['loopback_host_port'])
        return connection, headers


@contextmanager
def configured_connection():
    """使用操作者公开配置的网关或预建隧道；不创建 SSH 通道、不读取其私钥或密码。"""
    client = PublicApiClient(load_public_config())
    health = client.request('/health')
    if not isinstance(health, dict) or health.get('status') != 'ok' or health.get('profile') != 'cn-current-unverified':
        raise RuntimeError('CURRENT_SERVICE_NOT_READY')
    yield client


def startup_error_code(error):
    """启动失败仅保留业务码或导入模块名，不依赖可能正好导入失败的 Launcher 模块。

    模块名属于公共代码元数据，严格限制点分标识符；异常正文可能含 URL、账号、
    本机路径或临时能力，不能直接记录，也不能用作缺失模块名的回退来源。
    """
    module = getattr(error, 'name', None)
    if (isinstance(error, ModuleNotFoundError) and isinstance(module, str) and len(module) <= 192
            and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*', module)):
        return 'IMPORT_MISSING_' + module
    value = str(error)
    if isinstance(error, (ValueError, RuntimeError)) and re.fullmatch(r'[A-Z][A-Z_0-9]{0,63}', value):
        return value
    category = type(error).__name__.upper()
    return category if re.fullmatch(r'[A-Z][A-Z_0-9]{0,63}', category) else 'STARTUP_FAILED'


def main():
    """PyInstaller 专用服务入口；准备用户目录后复用原父管道握手及清理生命周期。"""
    root = state_root(create=True)
    try:
        load_public_config()
        from tools.native_launcher import main as serve
        serve()
    except Exception as error:
        (root / 'runtime/native-launcher-error.log').write_text(startup_error_code(error), encoding='utf-8')
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
