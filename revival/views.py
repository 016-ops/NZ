"""Django HTTP 适配及同源静态文件服务；保留既有请求、响应与错误合同。"""
import logging
import mimetypes
from pathlib import Path
from django.conf import settings
from django.core.exceptions import RequestDataTooBig, SuspiciousOperation
from django.http import HttpResponse
from django.http.multipartparser import MultiPartParserError
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.gzip import gzip_page
from django.db.models import Q
from .assets import preview, settle
from .rooms import heartbeat, maintain
from .security import token_hash
from .service import Fault, Service, identifier
from .middleware import client_address, failure, response


# 只接受 Authorization Bearer 头，不读 Cookie、URL 或本地存储中的令牌。
def token(request):
    """只接受 Authorization Bearer 头，不读 Cookie、URL 或本地存储中的令牌。"""
    value = request.headers.get('Authorization', '')
    if not value.startswith('Bearer '):
        raise Fault('AUTH', 'Bearer session required', 401)
    return value[7:]


@csrf_exempt
# Django HTTP 适配层只负责读取参数、调业务服务和编码结果；业务事务保留在领域层，错误响应不泄露驱动异常。
def endpoint(request, view=None, ticket_id=None, attachment_id=None, media_id=None):
    """Django HTTP 适配层只负责读取参数、调业务服务和编码结果；业务事务保留在领域层，错误响应不泄露驱动异常。"""
    try:
        service = Service(test_profile=settings.NZ_PROFILE == 'synthetic')
        path = request.path
        if request.method == 'GET':
            if attachment_id is not None or media_id is not None:
                # 私图必须逐次带Bearer鉴权；公共封面只查独立表，不提供可切换可见性的参数。
                from .media import read_feedback_attachment, read_public_cover
                payload, mime = (read_feedback_attachment(service, token(request), attachment_id)
                                 if attachment_id is not None else read_public_cover(service, media_id))
                result = HttpResponse(payload, content_type=mime)
                result['Cache-Control'] = 'private, no-store' if attachment_id is not None else 'no-store'
                return result
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
            if path.startswith('/api/public/'):
                # 公开页面只读已发布运营内容，不复用管理员会话或返回邮箱、主机、凭据。
                return response(public_data(service, path.rsplit('/', 1)[-1], request.GET))
            if view is not None:
                filters = {k: v for k, v in request.GET.items()}
                for name in ('limit', 'offset', 'map_id', 'difficulty', 'player_count', 'hours', 'after_gid', 'revision'):
                    if name in filters:
                        filters[name] = int(filters[name])
                maintain(service)
                return response(service.query(token(request), view, **filters))
        elif request.method == 'POST':
            data = request.nz_data
            if getattr(request, 'nz_image_upload', False):
                # 中间件已在读取FILES之前认证；严格限制一张图片与一个幂等上传标识。
                from .media import upload_feedback, upload_public_cover
                if (set(request.POST) != {'upload_key'} or len(request.POST.getlist('upload_key')) != 1
                        or set(request.FILES) != {'file'} or len(request.FILES.getlist('file')) != 1):
                    raise Fault('INVALID', '一次只能提交一张图片和上传标识')
                uploaded = request.FILES['file']
                result = (upload_feedback(service, token(request), ticket_id, uploaded, request.POST['upload_key'])
                          if ticket_id is not None else upload_public_cover(service, token(request), uploaded, request.POST['upload_key']))
            elif path in ('/api/admin/login-code', '/api/admin/step-up-code'):
                from .admin_email_auth import request_login_code, request_step_up_code
                result = (request_login_code(service, data, client_address(request)) if path.endswith('/login-code')
                          else request_step_up_code(service, token(request), data, client_address(request)))
            elif path == '/api/login':
                result = service.login(data.get('login'), data.get('password'), kind=data.get('kind', 'player'),
                                       challenge_id=data.get('challenge_id'), code=data.get('code'))
            elif path == '/api/register':
                if settings.NZ_EMAIL_VERIFICATION_REQUIRED and ('email' not in data or 'login' in data):
                    raise Fault('EMAIL_REQUIRED', '新用户必须通过验证码注册邮箱账号', 403)
                result = service.register(data)
            elif path in ('/api/player/login', '/api/launcher/login'):
                from .security import normalize_email
                try:
                    email = normalize_email(data.get('email'))
                except ValueError:
                    raise Fault('AUTH', 'Invalid credentials', 401)
                result = service.login(email, data.get('password'), kind='player', launcher=path == '/api/launcher/login')
            elif path == '/api/player/view':
                # 本机桥客户端要求对象信封；权限仍由网页玩家会话逐请求验证，不接受游戏凭据冒充。
                allowed = {'me', 'player-inventory', 'campaigns', 'campaign-detail', 'leaderboards', 'feedback', 'feedback-detail'}
                if data.get('view') not in allowed or not isinstance(data.get('query', {}), dict):
                    raise Fault('FORBIDDEN', '玩家读取视图不在允许范围', 403)
                current = token(request)
                with service.db.transaction() as c:
                    service._actor(c, current, 'player')
                query = dict(data.get('query', {}))
                # Launcher从URLSearchParams传递字符串；复用GET的整数合同，不把bool强转成1。
                for key in ('limit', 'offset', 'map_id', 'difficulty', 'player_count', 'after_gid', 'revision'):
                    if key in query:
                        if isinstance(query[key], bool):
                            raise Fault('INVALID', '分页或筛选参数不正确')
                        query[key] = int(query[key])
                result = {'data': service.query(current, data['view'], **query)}
            elif path == '/api/player/register':
                if 'email' not in data or 'login' in data:
                    raise Fault('INVALID', '邮箱注册必须提供 email，不能指定旧登录名')
                result = service.register(data)
            elif path in ('/api/player/email-code', '/api/player/password-reset', '/api/player/email-verify'):
                from .email_verification import request_code, recover
                result = (request_code(service, data, client_address(request))
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
            elif path == '/api/runtime/results':
                from .campaigns import ingest
                result = ingest(service, token(request), data)
            elif path == '/api/monitoring/samples':
                from .monitoring import ingest
                result = ingest(service, token(request), data)
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
    except RequestDataTooBig:
        # multipart文件本身单独限5MiB；超大的文本字段仍受Django原64KiB预算保护。
        return failure(Fault('BODY_SIZE', '表单文字字段超过允许大小', 413))
    except (SuspiciousOperation, MultiPartParserError):
        return failure(Fault('INVALID', '上传表单格式不正确'))
    except (ValueError, TypeError, KeyError):
        return failure(Fault('INVALID', 'Invalid request structure'))
    except Exception:
        incident = identifier()
        logging.getLogger('revival.http').error('Request failed: incident=%s', incident)
        return response({'error': 'INTERNAL', 'message': 'Request failed; assets remain transactional', 'incident_id': incident}, 500)


def public_data(service, name, filters):
    """门户公开读模型只暴露地图、活动和昵称成绩；无可信来源时保持正式榜为空。"""
    from .campaigns import view as campaign_view
    from .portal_operations import filter_play_catalog
    from .native_rooms import catalog
    query = dict(filters.items())
    for key in ('map_id', 'difficulty', 'player_count'):
        if key in query:
            query[key] = int(query[key])
    with service.db.transaction() as c:
        actor = {'kind': 'public'}
        if name in ('campaigns', 'campaign-detail'):
            return campaign_view(service, c, actor, name, query)
        if name == 'leaderboards':
            if not query.get('campaign_id'):
                return {'entries': [], 'finalized': False, 'test_only': service.test_profile, 'native_results_enabled': False}
            result = campaign_view(service, c, actor, 'leaderboards', query)
            result['entries'] = [{k: v for k, v in row.items() if k != 'member_ids'} for row in result['entries']]
            return result
        if name != 'portal':
            raise Fault('NOT_FOUND', '未知公开视图', 404)
        rules = filter_play_catalog(service, c, catalog())
        maps = [dict(map_id=r['map_id'], name=r['name'], mode_id=r['mode_id'], submodes=r['submodes'],
            kind='hunt' if r['mode_id'] == 260 else 'tower_defense', native_loaded=r['native_loaded'],
            combat_verified=r['combat_verified']) for r in rules['maps'].values()]
        return {'profile': service.catalog['id'], 'test_profile': service.test_profile, 'maps': maps,
            'play_policy_revision': rules['play_policy_revision'],
            'campaigns': campaign_view(service, c, actor, 'campaigns', {}),
            'capabilities': {'platform': True, 'native_gameplay': False, 'leaderboards': False}}


# 只暴露 Vite 构建后的首页和 assets 白名单目录，resolve 检查阻止路径穿越；不直接公开 TypeScript 源码或数据库配置。
@gzip_page
def frontend(request, asset=None, **route):
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
