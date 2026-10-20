"""请求边界中间件：校验来源、正文和限流，统一 JSON 错误及安全响应头。"""
import json
import logging
import re
import threading
import time
from ipaddress import ip_address
from collections import OrderedDict

from django.conf import settings
from django.core.exceptions import RequestDataTooBig, SuspiciousOperation
from django.http import JsonResponse
from .service import Fault, identifier

LOG = logging.getLogger('revival.http')


def client_address(request):
    """限流使用真实连接IP；仅受信本机反代、精确公网Host同时成立时读取X-Real-IP。

    Nginx模板覆盖该头，Web监听仍为回环。显式开关避免历史本机入口或直连请求
    信任任意转发头；规范化IPv4/IPv6后用作有界桶标识，不保存完整代理链。
    """
    peer = request.META.get('REMOTE_ADDR', 'unknown')
    if (settings.NZ_TRUST_LOOPBACK_PROXY and settings.NZ_PUBLIC_HOST
            and peer in ('127.0.0.1', '::1')
            and request.META.get('HTTP_HOST') == settings.NZ_PUBLIC_HOST):
        forwarded = request.META.get('HTTP_X_REAL_IP')
        if forwarded:
            try:
                return str(ip_address(forwarded))
            except ValueError:
                raise Fault('INVALID', 'Invalid trusted proxy client address') from None
    return peer


# 单进程回环服务的线程安全限流器；不宣称分布式限流能力。
class Limits:
    """单进程回环服务的线程安全限流器；不宣称分布式限流能力。"""
    def __init__(self):
        """初始化当前对象依赖和状态；参数仅保存在对象中，具体持久化由显式事务入口处理。"""
        self.lock = threading.Lock()
        self.buckets = OrderedDict()

    # 用线程锁维护有界时间窗口桶，按 IP/路由限制写请求；最多保留 1024 个桶，避免本地进程无限增长。
    def allow(self, key, maximum=30, window=300):
        """用线程锁维护有界时间窗口桶，按 IP/路由限制写请求；最多保留 1024 个桶，避免本地进程无限增长。"""
        now = time.monotonic()
        with self.lock:
            count, start = self.buckets.pop(key, (0, now))
            if now - start >= window:
                count, start = 0, now
            self.buckets[key] = (count + 1, start)
            while len(self.buckets) > 1024:
                self.buckets.popitem(last=False)
            return count < maximum


# 统一 JSON 编码，允许列表响应并禁止 NaN；保留旧 API 的 Unicode 和错误结构。
def response(value, status=200):
    """统一 JSON 编码，允许列表响应并禁止 NaN；保留旧 API 的 Unicode 和错误结构。"""
    return JsonResponse(value, safe=False, status=status, json_dumps_params={'ensure_ascii': False, 'allow_nan': False})


# 将可预期业务 Fault 转为既有 error/message 和 HTTP 状态，异常细节不会暴露凭据。
def failure(error):
    """将可预期业务 Fault 转为既有 error/message 和 HTTP 状态，异常细节不会暴露凭据。"""
    return response({'error': error.code, 'message': error.message}, error.status)


# 在 JSON 解码时拒绝重复键，防止前后端对同一 payload 取不同值。
def unique_pairs(pairs):
    """在 JSON 解码时拒绝重复键，防止前后端对同一 payload 取不同值。"""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON keys')
        result[key] = value
    return result


# 请求安全边界与响应安全头；业务错误另在视图转换，避免 Django 自动异常处理泄露细节。
class ControlPlaneMiddleware:
    """请求安全边界与响应安全头；业务错误另在视图转换，避免 Django 自动异常处理泄露细节。"""
    def __init__(self, get_response):
        """初始化当前对象依赖和状态；参数仅保存在对象中，具体持久化由显式事务入口处理。"""
        self.get_response = get_response
        self.limits = Limits()

    # 依次执行 Host/Origin、限流和严格 JSON 校验，再调用 Django 路由；拒绝审计独立追加，所有响应统一安全头。
    def __call__(self, request):
        """依次执行 Host/Origin、限流和严格 JSON 校验，再调用 Django 路由；拒绝审计独立追加，所有响应统一安全头。"""
        try:
            hosts = {f'127.0.0.1:{settings.NZ_PORT}', f'localhost:{settings.NZ_PORT}'}
            if settings.NZ_PUBLIC_HOST:
                hosts.add(settings.NZ_PUBLIC_HOST)
            if request.META.get('HTTP_HOST') not in hosts:
                raise Fault('HOST', 'Unexpected Host header', 403)
            origin = request.META.get('HTTP_ORIGIN')
            origins = {f'http://127.0.0.1:{settings.NZ_PORT}', f'http://localhost:{settings.NZ_PORT}'}
            if settings.NZ_PUBLIC_ORIGIN:
                origins.add(settings.NZ_PUBLIC_ORIGIN)
            if origin and origin not in origins:
                raise Fault('ORIGIN', 'Cross-origin requests are rejected', 403)
            if request.method == 'POST':
                auth = request.path in ('/api/login', '/api/register', '/api/player/login', '/api/launcher/login', '/api/player/register',
                    '/api/player/email-code', '/api/player/password-reset', '/api/player/email-verify', '/api/launcher/redeem',
                    '/api/admin/login-code', '/api/admin/step-up-code')
                if not self.limits.allow((client_address(request), request.path), 20 if auth else 120, 300 if auth else 60):
                    raise Fault('RATE_LIMIT', 'Request limit reached; retry later', 429)
                image_upload = request.path == '/api/admin/media' or re.fullmatch(r'/api/feedback/[a-f0-9]{32}/attachments', request.path)
                expected_type = 'multipart/form-data' if image_upload else 'application/json'
                if request.content_type != expected_type or request.headers.get('X-NZ-Request') != '1':
                    raise Fault('CSRF', 'JSON content type and X-NZ-Request: 1 are required', 403)
                if request.headers.get('Transfer-Encoding'):
                    raise Fault('INVALID', 'Transfer encoding is not accepted')
                size = int(request.META.get('CONTENT_LENGTH') or '0')
                maximum = 5 * 1024 * 1024 + 65536 if image_upload else 65536
                if not 1 <= size <= maximum:
                    raise Fault('BODY_SIZE', '图片最大5 MiB；普通请求最大64 KiB', 413)
                if image_upload:
                    # 上传认证必须早于FILES解析，防止匿名请求先消耗解码和临时文件资源。
                    from .views import token
                    from .service import Service
                    service = Service(test_profile=settings.NZ_PROFILE == 'synthetic')
                    with service.db.transaction() as c:
                        actor = service._actor(c, token(request))
                        if request.path == '/api/admin/media':
                            from .media import _cover_permission
                            _cover_permission(actor)
                        else:
                            from .feedback import ticket
                            ticket(c, actor, request.path.split('/')[3], write=True)
                    request.nz_image_upload = True
                    request.nz_data = {}
                else:
                    request.nz_data = json.loads(request.body, object_pairs_hook=unique_pairs,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite number')))
                    if not isinstance(request.nz_data, dict):
                        raise Fault('INVALID', 'JSON object required')
            result = self.get_response(request)
        except Fault as error:
            result = failure(error)
        except RequestDataTooBig:
            result = failure(Fault('BODY_SIZE', '请求正文或表单字段过大', 413))
        except SuspiciousOperation:
            result = failure(Fault('INVALID', '请求或上传表单格式不正确'))
        except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
            result = failure(Fault('INVALID', 'Invalid request structure'))
        except Exception:
            incident = identifier()
            LOG.error('Request failed: incident=%s', incident)
            result = response({'error': 'INTERNAL', 'message': 'Request failed; assets remain transactional', 'incident_id': incident}, 500)
        if request.method == 'POST' and result.status_code >= 400:
            try:
                body = json.loads(result.content)
                if body.get('error') not in ('RATE_LIMIT', 'BODY_SIZE'):
                    from .service import Service
                    Service(test_profile=settings.NZ_PROFILE == 'synthetic').record_rejection(request.path, identifier(), body.get('error', 'INTERNAL'))
            except Exception:
                LOG.error('Rejection audit failed')
        if 'Cache-Control' not in result:
            result['Cache-Control'] = 'no-store'
        result['X-Content-Type-Options'] = 'nosniff'
        result['Referrer-Policy'] = 'no-referrer'
        result['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return result
