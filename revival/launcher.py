"""自建邮箱身份到游戏桥的版本化交接；不签发腾讯凭据，不冒充已接通原客户端协议。"""
import hmac
import re
from django.db.models import Q
from .security import new_token, token_hash
from .service import Fault, identifier, text_field
from adapters.email_protocol import PROTOCOL

TICKET_SECONDS = 60
LEASE_VERSION = 'launcher-lease-v1'
LEASE_SECONDS = 180
LEASE_MAX_SECONDS = 12 * 3600


def capabilities():
    """返回实际独立协议能力；原生桥尚未验证时始终明确关闭游戏启动就绪标记。"""
    from .mailing import configured
    from django.conf import settings
    return {'protocol': PROTOCOL, 'identity_provider': 'independent-email',
            'email_password': True, 'email_ownership_verification': 'CONFIGURED' if configured() else 'NOT_CONFIGURED',
            'email_verification_required': settings.NZ_EMAIL_VERIFICATION_REQUIRED,
            'password_recovery': 'EMAIL_CODE',
            'one_time_handoff': True, 'native_client_adapter': 'UNIMPLEMENTED',
            'session_lease': LEASE_VERSION,
            'native_settings_storage': 'CURRENT_TDR_OPAQUE_V1',
            'native_launch_ready': False, 'qq_required': False}


def require_protocol(data):
    """独立协议只接受确切版本，避免将腾讯/PH 原生消息误当成本服务的登录请求。"""
    if data.get('protocol') != PROTOCOL:
        raise Fault('PROTOCOL', 'Unsupported independent login protocol', 409)


def sha256_field(data, field):
    """检查声明的摘要格式；客户端指纹用于绑定请求，不能充当二进制来源认证。"""
    value = data.get(field)
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise Fault('INVALID', 'Invalid ' + field)
    return value


def source_session(service, c, row):
    """每次兑换/授权重查源会话、安全版本、邮箱身份及制裁，登出和封禁立即使交接失效。"""
    session = c.select('sessions', Q(token_hash=row['session_id'])).fetchone()
    account = c.select('accounts', Q(id=row['account_id'])).fetchone()
    if (not session or session['kind'] != 'player' or session['subject_id'] != row['account_id']
            or session['revoked'] or session['expires_at'] <= service.clock() or not account
            or account['security_version'] != row['security_version']
            or session['security_version'] != row['security_version']
            or not c.select('player_emails', Q(account_id=row['account_id'])).fetchone()):
        raise Fault('AUTH', 'Independent game session is expired or revoked', 401)
    service._active(c, account)
    return account, session


def issue(service, token, data):
    """已登录邮箱玩家申请 60 秒一次性票据；只存摘要，响应丢失须重新申请，不重放明文凭据。"""
    require_protocol(data)
    client_sha = sha256_field(data, 'client_sha256')
    verifier_sha = sha256_field(data, 'verifier_sha256')
    request_id, _, reason = service._metadata(data)
    with service.db.transaction() as c:
        actor = service._actor(c, token, 'player')
        account = service._account(c, actor['id'])
        if not c.select('player_emails', Q(account_id=actor['id'])).fetchone():
            raise Fault('EMAIL_REQUIRED', '请先由管理员为旧玩家账号绑定邮箱', 409)
        session = c.select('sessions', Q(token_hash=token_hash(token))).fetchone()
        ticket, launch_id = new_token(), identifier()
        expires = min(service.clock() + TICKET_SECONDS, session['expires_at'])
        # 新申请替换未消费票据；已运行游戏只在成功兑换新票据后撤销，失败申请不打断它。
        c.update('launch_tickets', Q(account_id=actor['id'], consumed_at__isnull=True, revoked=False), {'revoked': True})
        c.insert('launch_tickets', {'digest': token_hash(ticket), 'id': launch_id, 'account_id': actor['id'],
            'session_id': session['token_hash'], 'security_version': account['security_version'],
            'profile': service.catalog['id'], 'client_sha256': client_sha, 'verifier_sha256': verifier_sha,
            'created_at': service.clock(), 'expires_at': expires})
        service._audit(c, actor['id'], 'launcher.ticket.issue', launch_id, request_id, reason,
                       {'protocol': PROTOCOL, 'client_sha256': client_sha, 'expires_at': expires})
        return {'protocol': PROTOCOL, 'ticket': ticket, 'launch_id': launch_id, 'expires_at': expires,
                'native_launch_ready': False}


def redeem(service, data):
    """启动器凭票据及原始随机证明兑换独立游戏会话；并发至多成功一次，未知结果不能重放。"""
    require_protocol(data)
    lease_mode = data.get('session_lease')
    if lease_mode not in (None, LEASE_VERSION):
        raise Fault('PROTOCOL', 'Unsupported launcher lease version', 409)
    client_sha = sha256_field(data, 'client_sha256')
    ticket = text_field(data.get('ticket'), 'ticket', 128)
    verifier = data.get('verifier')
    if not isinstance(verifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', verifier):
        raise Fault('AUTH', 'Invalid launch proof', 401)
    with service.db.transaction() as c:
        row = c.select('launch_tickets', Q(digest=token_hash(ticket))).fetchone()
        if (not row or row['revoked'] or row['expires_at'] <= service.clock()
                or row['profile'] != service.catalog['id'] or row['client_sha256'] != client_sha
                or not hmac.compare_digest(row['verifier_sha256'], token_hash(verifier))):
            raise Fault('AUTH', 'Launch ticket is invalid, expired or revoked', 401)
        account, session = source_session(service, c, row)
        if row['consumed_at'] is not None:
            raise Fault('TICKET_CONSUMED', 'Launch ticket was already redeemed; request a new ticket', 409)
        # 旧账号可能只有角色与装饰，缺少原生第一人称手臂资源。与本次有效票据兑换
        # 共用事务向前补齐本人正在使用的角色，不重置初装回执或已有库存。
        from .native_avatar_body import prepare_equipped
        prepare_equipped(service, c, account['id'])
        game_token = new_token()
        expires = min(service.clock() + 3600, session['expires_at'])
        lease_max = None
        if lease_mode is not None:
            # 显式租约缩短本轮来源与游戏凭据；崩溃后停止续期，最多三分钟即失效。
            # 后续续期重新检查制裁/安全版本，单次登录最多十二小时，不允许复活已过期凭据。
            lease_max = service.clock() + LEASE_MAX_SECONDS
            expires = min(service.clock() + LEASE_SECONDS, session['expires_at'])
            c.update('sessions', Q(token_hash=row['session_id']), {'expires_at': expires})
        c.update('game_sessions', Q(account_id=account['id'], revoked=False), {'revoked': True})
        c.insert('game_sessions', {'digest': token_hash(game_token), 'account_id': account['id'],
            'session_id': row['session_id'], 'launch_id': row['digest'], 'security_version': row['security_version'],
            'profile': row['profile'], 'client_sha256': client_sha, 'created_at': service.clock(), 'expires_at': expires,
            'lease_max_expires_at': lease_max})
        c.update('launch_tickets', Q(digest=row['digest']), {'consumed_at': service.clock()})
        service._audit(c, account['id'], 'launcher.ticket.redeem', row['id'], identifier(),
                       'independent email handoff', {'protocol': PROTOCOL, 'expires_at': expires})
        return {'protocol': PROTOCOL, 'game_token': game_token, 'launch_id': row['id'], 'expires_at': expires,
                'native_launch_ready': False, 'session_lease': lease_mode, 'lease_max_expires_at': lease_max}


def renew(service, game_token, data):
    """为仍有效的游戏租约续期；来源/游戏同事务更新，过期、封禁、改密、重登均不能续活。"""
    if data.get('session_lease') != LEASE_VERSION:
        raise Fault('PROTOCOL', 'Launcher lease version is required', 409)
    with service.db.transaction() as c:
        row, account = authorize_game_session(service, c, game_token, data)
        maximum = row['lease_max_expires_at']
        if maximum is None:
            raise Fault('LEASE_NOT_ENABLED', 'This session did not opt into a renewable lease', 409)
        if maximum <= service.clock():
            raise Fault('AUTH', 'Launcher login duration limit reached', 401)
        expires = min(service.clock() + LEASE_SECONDS, maximum)
        c.update('sessions', Q(token_hash=row['session_id']), {'expires_at': expires})
        c.update('game_sessions', Q(digest=row['digest']), {'expires_at': expires})
        service._audit(c, account['id'], 'launcher.lease.renew', row['digest'], identifier(),
                       'active launcher lease', {'expires_at': expires, 'lease_max_expires_at': maximum})
        return {'protocol': PROTOCOL, 'session_lease': LEASE_VERSION, 'expires_at': expires,
                'lease_max_expires_at': maximum}


def authorize_game_session(service, c, game_token, data):
    """在调用方同一事务内重新验证游戏凭据、来源会话、制裁和客户端绑定，返回会话与账号。

    设置读取/写入与身份查询共用本检查；玩家/管理员Bearer不能冒用独立游戏凭据。
    不在事务外缓存授权结果，避免改密、退出、封禁后仍可写入设置。
    """
    require_protocol(data)
    client_sha = sha256_field(data, 'client_sha256')
    row = c.select('game_sessions', Q(digest=token_hash(game_token))).fetchone()
    if (not row or row['revoked'] or row['expires_at'] <= service.clock()
            or row['profile'] != service.catalog['id'] or row['client_sha256'] != client_sha):
        raise Fault('AUTH', 'Invalid independent game session', 401)
    account, _ = source_session(service, c, row)
    return row, account


def identity(service, game_token, data):
    """仅供自建游戏桥解析独立游戏会话，不返回邮箱/密码，不授权管理员命令或可信结算。"""
    with service.db.transaction() as c:
        row, account = authorize_game_session(service, c, game_token, data)
        character = c.select('characters', Q(account_id=account['id'])).fetchone()
        return {'protocol': PROTOCOL, 'account_id': account['id'], 'game_id': account['game_id'],
                'character': {'id': character['id'], 'nickname': character['nickname']} if character else None,
                'expires_at': row['expires_at'], 'profile': row['profile'], 'native_client_adapter': 'UNIMPLEMENTED',
                'identity_namespace': 'nz-local-account', 'native_launch_ready': False}
