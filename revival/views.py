"""Django HTTP 适配及同源静态文件服务；保留既有请求、响应与错误合同。"""
import logging
import mimetypes
from pathlib import Path
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.gzip import gzip_page
from django.db.models import Q
from .assets import preview, settle
from .rooms import heartbeat, maintain
from .security import token_hash
from .service import Fault, Service, identifier
from .middleware import failure, response


# 只接受 Authorization Bearer 头，不读 Cookie、URL 或本地存储中的令牌。
def token(request):
    """只接受 Authorization Bearer 头，不读 Cookie、URL 或本地存储中的令牌。"""
    value = request.headers.get('Authorization', '')
    if not value.startswith('Bearer '):
        raise Fault('AUTH', 'Bearer session required', 401)
    return value[7:]


@csrf_exempt
# Django HTTP 适配层只负责读取参数、调业务服务和编码结果；业务事务保留在领域层，错误响应不泄露驱动异常。
def endpoint(request, view=None):
    """Django HTTP 适配层只负责读取参数、调业务服务和编码结果；业务事务保留在领域层，错误响应不泄露驱动异常。"""
    try:
        service = Service(test_profile=settings.NZ_PROFILE == 'synthetic')
        path = request.path
        if request.method == 'GET':
            if path == '/health':
                return response({'status': 'ok', 'profile': service.catalog['id'], 'retail_compatible': False})
            if path == '/api/status':
                from .launcher import capabilities
                return response({'profile': service.catalog['id'], 'evidence': service.catalog['evidence'],
                    'retail': {'login': 'BLOCKED', 'native_host': 'BLOCKED', 'inventory_sync': 'BLOCKED'}, 'test_profile': service.test_profile,
                    'independent_login': capabilities()})
            if path == '/api/launcher/status':
                from .launcher import capabilities
                return response(capabilities())
            if view is not None:
                filters = {k: v for k, v in request.GET.items()}
                for name in ('limit', 'offset'):
                    if name in filters:
                        filters[name] = int(filters[name])
                maintain(service)
                return response(service.query(token(request), view, **filters))
        elif request.method == 'POST':
            data = request.nz_data
            if path == '/api/login':
                result = service.login(data.get('login'), data.get('password'), kind=data.get('kind', 'player'), otp_code=data.get('otp_code'))
            elif path == '/api/register':
                if settings.NZ_EMAIL_VERIFICATION_REQUIRED and ('email' not in data or 'login' in data):
                    raise Fault('EMAIL_REQUIRED', '新用户必须通过验证码注册邮箱账号', 403)
                result = service.register(data)
            elif path == '/api/player/login':
                from .security import normalize_email
                try:
                    email = normalize_email(data.get('email'))
                except ValueError:
                    raise Fault('AUTH', 'Invalid credentials', 401)
                result = service.login(email, data.get('password'), kind='player')
            elif path == '/api/player/register':
                if 'email' not in data or 'login' in data:
                    raise Fault('INVALID', '邮箱注册必须提供 email，不能指定旧登录名')
                result = service.register(data)
            elif path in ('/api/player/email-code', '/api/player/password-reset', '/api/player/email-verify'):
                from .email_verification import request_code, recover
                result = (request_code(service, data, request.META.get('REMOTE_ADDR', 'unknown'))
                          if path == '/api/player/email-code' else
                          recover(service, data, verify_only=path == '/api/player/email-verify'))
            elif path in ('/api/launcher/tickets', '/api/launcher/redeem', '/api/game/session', '/api/game/session/renew'):
                from .launcher import issue, redeem, identity, renew
                if path == '/api/launcher/tickets':
                    result = issue(service, token(request), data)
                elif path == '/api/launcher/redeem':
                    result = redeem(service, data)
                elif path == '/api/game/session/renew':
                    result = renew(service, token(request), data)
                else:
                    result = identity(service, token(request), data)
            elif path in ('/api/game/settings/get', '/api/game/settings/set'):
                from .native_settings import load, save
                result = (load if path.endswith('/get') else save)(service, token(request), data)
            elif path in ('/api/game/inventory/get', '/api/game/inventory/equip'):
                from .native_inventory import load, equip
                result = (load if path.endswith('/get') else equip)(service, token(request), data)
            elif path in ('/api/game/command', '/api/game/view'):
                from .native_features import command, query
                # 统一对象信封支持列表视图，现有回环客户端不接受裸JSON数组。
                result = command(service,token(request),data) if path.endswith('/command') else {'data':query(service,token(request),data)}
            elif path == '/api/command':
                maintain(service)
                result = service.command(token(request), data.get('action'), data)
            elif path == '/api/asset-preview':
                result = preview(service, token(request), data)
            elif path == '/api/node/heartbeat':
                result = heartbeat(service, token(request), data.get('match_id'), data.get('epoch'))
            elif path in ('/api/node/ready', '/api/node/events', '/api/node/pending', '/api/node/ack'):
                from .nodes import ready, pending, acknowledge
                from .pve import events
                handler = {'/api/node/ready': ready, '/api/node/events': events, '/api/node/pending': pending, '/api/node/ack': acknowledge}[path]
                result = handler(service, token(request), data)
            elif path == '/api/node/settlement':
                result = settle(service, token(request), data)
            elif path == '/api/logout':
                current = token(request)
                with service.db.transaction() as c:
                    actor = service._actor(c, current)
                    c.update('sessions', Q(token_hash=token_hash(current)), {'revoked': 1})
                    if actor['kind'] == 'player':
                        service._detach(c, actor['id'])
                result = {'logged_out': True}
            else:
                raise Fault('NOT_FOUND', 'Route not found', 404)
            return response(result)
        raise Fault('NOT_FOUND', 'Route not found', 404)
    except Fault as error:
        return failure(error)
    except (ValueError, TypeError, KeyError):
        return failure(Fault('INVALID', 'Invalid request structure'))
    except Exception:
        incident = identifier()
        logging.getLogger('revival.http').error('Request failed: incident=%s', incident)
        return response({'error': 'INTERNAL', 'message': 'Request failed; assets remain transactional', 'incident_id': incident}, 500)


# 只暴露 Vite 构建后的首页和 assets 白名单目录，resolve 检查阻止路径穿越；不直接公开 TypeScript 源码或数据库配置。
@gzip_page
def frontend(request, asset=None):
    """只压缩公开的构建文件，登录令牌和一次性邀请响应不参与此压缩路径。"""
    if request.method not in ('GET', 'HEAD'):
        return failure(Fault('NOT_FOUND', 'Route not found', 404))
    root = (settings.BASE_DIR / 'admin/dist').resolve()
    target = (root / ('assets/' + asset if asset else 'index.html')).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return failure(Fault('NOT_FOUND', 'Build the React console before serving it', 404))
    mime = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
    return HttpResponse(target.read_bytes(), content_type=mime)


# 未知路由沿用 JSON NOT_FOUND/404，避免 Django 默认 HTML 错误页破坏接口契约。
def not_found(request, exception=None):
    """未知路由沿用 JSON NOT_FOUND/404，避免 Django 默认 HTML 错误页破坏接口契约。"""
    return failure(Fault('NOT_FOUND', 'Route not found', 404))
