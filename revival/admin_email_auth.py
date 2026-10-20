"""固定收件邮箱的管理员登录及操作级再认证；发送额度、错误次数、消费均持久化。

SMTP只能在部署事务锁外执行；投递失败保持关闭，超时不重发。登录与高风险用途独立，
后者额外绑定当前管理员会话和完整业务摘要，邮箱码不能被拿到另一个操作或会话使用。
"""
import hashlib
import hmac
import re
import secrets

from django.conf import settings
from django.db.models import F, Q

from . import mailing
from .security import normalize_login, token_hash, verify_password
from .service import DUMMY_PASSWORD_HASH, Fault, canonical, identifier

RECIPIENT = '2633034921@qq.com'
AUTH_FIELDS = {'current_password', 'otp_code', 'challenge_id', 'code'}


def _proof(*parts):
    """域分隔HMAC使低熵验证码及操作内的新密码不能通过数据库摘要离线猜测。"""
    value = '\0'.join(('nz-admin-email-v1', *map(str, parts))).encode('utf-8')
    return hmac.new(settings.SECRET_KEY.encode('utf-8'), value, hashlib.sha256).hexdigest()


def business_summary(action, data):
    """命令幂等只保存业务摘要；当前密码/验证码/挑战不参与持久摘要，新密码使用HMAC。"""
    result = {key: value for key, value in data.items()
              if key not in AUTH_FIELDS | {'request_id', 'idempotency_key'}}
    for key in ('new_password', 'password'):
        if key in result:
            result[key] = {'secret_proof': _proof('command-secret', key, canonical(result[key]))}
    return dict(result, action=action)


def operation_hash(action, data):
    """操作绑定保留幂等键和原因；仅请求追踪ID及可变化的认证材料不属于业务内容。"""
    return _proof('operation', action, data.get('idempotency_key'), canonical(business_summary(action, data)))


def _admin(c, login, password):
    """未知账号也执行慢密码校验；返回结果仅供内存认证，不向调用者枚举存在性。"""
    try:
        normalized = normalize_login(login)
    except ValueError:
        normalized = 'invalid-login'
    admin = c.select('admins', Q(login=normalized)).fetchone()
    correct = verify_password(password, admin['password_hash'] if admin else DUMMY_PASSWORD_HASH)
    return admin if correct and admin and admin['active'] else None


def _request(service, data, remote_addr, *, token=None):
    """预留独立持久额度后锁外投递；错误密码也记录来源尝试，重启不能清空暴力尝试额度。"""
    purpose = 'STEP_UP' if token is not None else 'LOGIN'
    if not mailing.configured():
        raise Fault('MAIL_NOT_CONFIGURED', '管理员邮件服务尚未配置，无法完成认证', 503)
    if purpose == 'STEP_UP':
        from .service import COMMANDS
        if data.get('action') not in COMMANDS:
            raise Fault('NOT_FOUND', '未知高风险操作', 404)
        if not isinstance(data.get('payload'), dict) or not isinstance(data.get('metadata'), dict):
            raise Fault('INVALID', '高风险验证需要原操作和完整元数据')
        if (set(data['metadata']) != {'request_id', 'idempotency_key', 'reason'}
                or set(data['payload']) & (AUTH_FIELDS | {'action', 'request_id', 'idempotency_key', 'reason'})):
            raise Fault('INVALID', '高风险验证的业务参数与请求元数据不能混合或覆盖')
        command = {**data['payload'], **data['metadata'], 'action': data['action']}
        service._metadata(command)
        if not service.high_risk(command['action'], command):
            raise Fault('INVALID', '此操作不属于高风险确认范围')
    else:
        command = {}
    now, challenge_id, code = service.clock(), identifier(), f'{secrets.randbelow(1000000):06d}'
    source = _proof('source', service.catalog['id'], str(remote_addr)[:256])
    failure = None
    with service.db.transaction() as c:
        query = c.table('admin_auth_challenges')
        if (query.filter(ip_digest=source, created_at__gt=now-300).count() >= 20
                or query.filter(ip_digest=source, created_at__gt=now-3600).count() >= 60):
            raise Fault('RATE_LIMIT', '管理员验证码请求过于频繁，请稍后再试', 429)
        if purpose == 'STEP_UP':
            actor = service._actor(c, token, '*')
            admin = c.select('admins', Q(id=actor['id'])).fetchone()
            valid = admin['active'] and verify_password(data.get('current_password'), admin['password_hash'])
            subject = _proof('subject', admin['login'])
        else:
            admin = _admin(c, data.get('login'), data.get('password'))
            valid = admin is not None
            subject = _proof('subject', str(data.get('login', '')).strip().lower())
        values = {'id': challenge_id, 'admin_id': admin['id'] if admin else None,
            'subject_digest': subject, 'purpose': purpose, 'session_hash': token_hash(token) if token else '',
            'operation_hash': operation_hash(command['action'], command) if command else '',
            'security_version': admin['security_version'] if admin else None,
            'ip_digest': source, 'created_at': now, 'expires_at': now+300}
        if not valid:
            values.update(status='REJECTED', error_code='AUTH')
            c.insert('admin_auth_challenges', values)
            failure = Fault('AUTH', '管理员账号或密码不正确', 401)
        else:
            sent = query.exclude(status='REJECTED')
            own = sent.filter(subject_digest=subject, purpose=purpose)
            if (own.filter(created_at__gt=now-60).exists()
                    or own.filter(created_at__gt=now-3600).count() >= 10
                    or sent.filter(created_at__gt=now-3600).count() >= 100):
                raise Fault('RATE_LIMIT', '管理员验证码请求过于频繁，请稍后再试', 429)
            c.update('admin_auth_challenges', Q(admin_id=admin['id'], purpose=purpose,
                consumed_at__isnull=True, status__in=['PENDING', 'SENT']), {'status': 'SUPERSEDED'})
            values['digest'] = _proof('code', challenge_id, admin['id'], purpose, RECIPIENT, code)
            c.insert('admin_auth_challenges', values)
    if failure:
        raise failure
    error = None
    try:
        if token:
            mailing.send_code(RECIPIENT, code, 'admin_step_up', operation={
                'action':command['action'], 'confirmation':values['operation_hash'][:12]})
        else:
            mailing.send_code(RECIPIENT, code, 'admin_login')
    except mailing.MailUnavailable as caught:
        error = str(caught) if str(caught) in ('MAIL_NOT_CONFIGURED', 'MAIL_DELIVERY_FAILED', 'MAIL_REJECTED') else 'MAIL_DELIVERY_FAILED'
    with service.db.transaction() as c:
        # 只完成本轮仍PENDING的挑战；另一线程已替代的旧码不能被迟到SMTP结果重新激活。
        c.update('admin_auth_challenges', Q(id=challenge_id, status='PENDING'),
                 {'status': 'FAILED' if error else 'SENT', 'error_code': error or ''})
    if error:
        raise Fault('MAIL_DELIVERY_FAILED', '管理员验证码未能投递，请稍后重新申请', 503)
    result = {'challenge_id': challenge_id, 'expires_in': 300, 'retry_after': 60,
              'recipient_hint': '263****921@qq.com'}
    if token:
        result['operation_summary'] = {'action':command['action'], 'confirmation':values['operation_hash'][:12]}
    return result


def request_login_code(service, data, remote_addr):
    """密码验证后仅向固定管理员邮箱发登录码；请求中的email字段不会改变收件人。"""
    return _request(service, data, remote_addr)


def request_step_up_code(service, token, data, remote_addr):
    """仅有效Owner会话可以申请绑定本次业务内容的独立高风险码。"""
    return _request(service, data, remote_addr, token=token)


def _validate(service, c, data, *, admin, purpose, token=None, action=None):
    """只校验不消费，供持久错误计数与最终同事务消费共用；不接受旧TOTP或恢复码。"""
    row = c.select('admin_auth_challenges', Q(id=data.get('challenge_id'))).fetchone()
    code = data.get('code')
    valid = (row and admin and admin['active'] and row['admin_id'] == admin['id']
        and row['security_version'] == admin['security_version'] and row['purpose'] == purpose
        and row['status'] == 'SENT' and row['consumed_at'] is None and row['expires_at'] > service.clock()
        and row['attempts'] < 5 and isinstance(code, str) and re.fullmatch('[0-9]{6}', code)
        and hmac.compare_digest(row['digest'], _proof('code', row['id'], admin['id'], purpose, RECIPIENT, code)))
    if purpose == 'STEP_UP':
        valid = (valid and row['session_hash'] == token_hash(token)
            and hmac.compare_digest(row['operation_hash'], operation_hash(action, data))
            and verify_password(data.get('current_password'), admin['password_hash']))
    if not valid:
        raise Fault('ADMIN_EMAIL_CODE' if data.get('challenge_id') else 'REAUTH_REQUIRED',
                    '请使用原密码及本次有效的管理员邮箱验证码', 403)
    return row


def check(service, data, *, login=None, token=None, action=None):
    """失败次数在独立事务内提交；成功仅预检，最终消费再次检查以拒绝并发和重放。"""
    failure = None
    with service.db.transaction() as c:
        if token is None:
            admin = _admin(c, login, data.get('password'))
            # 密码错误也消耗本次挑战的失败预算，但必须先用规范化账号匹配挑战归属；
            # 不能将任意login提交到别人挑战ID来锁定该挑战。认证本身仍只使用上面的密码结果。
            attempt_admin = admin
            if attempt_admin is None:
                try:
                    normalized = normalize_login(login)
                except ValueError:
                    normalized = None
                attempt_admin = c.select('admins', Q(login=normalized)).fetchone() if normalized else None
        else:
            actor = service._actor(c, token, '*')
            admin = c.select('admins', Q(id=actor['id'])).fetchone()
            attempt_admin = admin
        try:
            _validate(service, c, data, admin=admin, purpose='STEP_UP' if token else 'LOGIN', token=token, action=action)
        except Fault as caught:
            failure = caught
            if attempt_admin:
                c.update('admin_auth_challenges', Q(id=data.get('challenge_id'), admin_id=attempt_admin['id'],
                    purpose='STEP_UP' if token else 'LOGIN',
                    attempts__lt=5, consumed_at__isnull=True), {'attempts': F('attempts')+1})
    if failure:
        raise failure


def consume(service, c, data, *, admin, token=None, action=None):
    """验证码消费与管理员会话/高风险业务、审计在调用方同一事务，失败全部回滚。"""
    row = _validate(service, c, data, admin=admin, purpose='STEP_UP' if token else 'LOGIN', token=token, action=action)
    c.update('admin_auth_challenges', Q(id=row['id']), {'consumed_at': service.clock()})
