"""请求边界中间件：校验来源、正文和限流，统一 JSON 错误及安全响应头。"""
import json
import logging
import threading
import time
from collections import OrderedDict

from django.conf import settings
from django.http import JsonResponse
from .service import Fault, identifier

LOG = logging.getLogger('revival.http')


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
            if request.META.get('HTTP_HOST') not in hosts:
                raise Fault('HOST', 'Unexpected Host header', 403)
            origin = request.META.get('HTTP_ORIGIN')
            if origin and origin not in {'http://' + host for host in hosts}:
                raise Fault('ORIGIN', 'Cross-origin requests are rejected', 403)
            if request.method == 'POST':
                auth = request.path in ('/api/login', '/api/register', '/api/player/login', '/api/player/register',
                    '/api/player/email-code', '/api/player/password-reset', '/api/player/email-verify', '/api/launcher/redeem')
                if not self.limits.allow((request.META.get('REMOTE_ADDR'), request.path), 20 if auth else 120, 300 if auth else 60):
                    raise Fault('RATE_LIMIT', 'Request limit reached; retry later', 429)
                if request.content_type != 'application/json' or request.headers.get('X-NZ-Request') != '1':
                    raise Fault('CSRF', 'JSON content type and X-NZ-Request: 1 are required', 403)
                if request.headers.get('Transfer-Encoding'):
                    raise Fault('INVALID', 'Transfer encoding is not accepted')
                size = int(request.META.get('CONTENT_LENGTH') or '0')
                if not 1 <= size <= 65536:
                    raise Fault('BODY_SIZE', 'Request body limit is 64 KiB', 413)
                request.nz_data = json.loads(request.body, object_pairs_hook=unique_pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite number')))
                if not isinstance(request.nz_data, dict):
                    raise Fault('INVALID', 'JSON object required')
            result = self.get_response(request)
        except Fault as error:
            result = failure(error)
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
        result['Cache-Control'] = 'no-store'
        result['X-Content-Type-Options'] = 'nosniff'
        result['Referrer-Policy'] = 'no-referrer'
        result['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return result
