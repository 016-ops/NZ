"""邮箱所有权验证码与玩家密码恢复；邀请码/审批继续保留，验证码状态同业务事务提交。"""
import hashlib
import hmac
import re
import secrets
from django.conf import settings
from django.db.models import Q, F
from .security import email_registration_proof, normalize_email, password_hash, token_hash
from .service import Fault, identifier, text_field
from . import mailing


def proof(challenge_id, email, purpose, code):
    """域分隔的服务端 HMAC 阻止仅凭数据库摘要离线穷举六位码；密钥轮换使未用码失效。"""
    value = '\0'.join(('nz-email-code-v1', challenge_id, email, purpose, code)).encode()
    return hmac.new(settings.SECRET_KEY.encode(), value, hashlib.sha256).hexdigest()


def consume(service, c, data, email, purpose):
    """在调用方事务内验证并消费；错误次数的持久提交由 check_code 独立入口负责。"""
    row = c.select('email_challenges', Q(id=data.get('challenge_id'))).fetchone()
    code = data.get('code')
    valid = (row and row['email'] == email and row['purpose'] == purpose
             and row['status'] == 'SENT' and row['consumed_at'] is None
             and row['expires_at'] > service.clock() and row['attempts'] < 5
             and isinstance(code, str) and re.fullmatch('[0-9]{6}', code)
             and hmac.compare_digest(row['digest'], proof(row['id'], email, purpose, code)))
    if not valid:
        raise Fault('EMAIL_CODE', '验证码无效、已用或过期，请重新申请', 403)
    if purpose in ('reset', 'verify'):
        identity = c.select('player_emails', Q(email=email)).fetchone()
        account = c.select('accounts', Q(id=identity['account_id'])).fetchone() if identity else None
        if (not account or row['account_id'] != account['id']
                or row['security_version'] != account['security_version']):
            raise Fault('EMAIL_CODE', '验证码无效、已用或过期，请重新申请', 403)
    c.update('email_challenges', Q(id=row['id']), {'consumed_at': service.clock()})
    return row


def check_code(service, data, email, purpose):
    """失败计数独立持久化；成功只预检，最终业务事务再次校验以消除并发重复消费。"""
    failure = None
    with service.db.transaction() as c:
        c.savepoint('email_precheck')
        try:
            consume(service, c, data, email, purpose)
        except Fault as error:
            failure = error
        finally:
            c.rollback('email_precheck')
        if failure:
            c.update('email_challenges', Q(id=data.get('challenge_id'), email=email, purpose=purpose,
                                           attempts__lt=5, consumed_at__isnull=True), {'attempts': F('attempts') + 1})
    if failure:
        raise failure


def request_code(service, data, remote_addr):
    """持久邮箱/IP/部署额度先预留，再锁外投递；未知地址同样收码，但不能据此创建恢复目标。"""
    try:
        email = normalize_email(data.get('email'))
    except ValueError:
        raise Fault('INVALID', '请填写有效邮箱地址')
    purpose = data.get('purpose')
    if purpose not in ('register', 'reset', 'verify'):
        raise Fault('INVALID', 'Unsupported email verification purpose')
    if not mailing.configured():
        raise Fault('MAIL_NOT_CONFIGURED', '邮件服务尚未配置完成，请稍后再试', 503)
    now, challenge_id = service.clock(), identifier()
    ip_digest = proof('source', service.catalog['id'], 'limit', str(remote_addr))
    code = f'{secrets.randbelow(1000000):06d}'
    with service.db.transaction() as c:
        query = c.table('email_challenges')
        if (query.filter(email=email, created_at__gt=now-60).exists()
                or query.filter(email=email, created_at__gt=now-3600).count() >= 5
                or query.filter(ip_digest=ip_digest, created_at__gt=now-3600).count() >= 20
                or query.filter(created_at__gt=now-3600).count() >= 100):
            raise Fault('RATE_LIMIT', '验证码请求过于频繁，请稍后再试', 429)
        if purpose == 'register':
            invitation = text_field(data.get('invitation'), 'invitation', 128)
            invite = c.select('invitations', Q(token_hash=token_hash(invitation))).fetchone()
            if not invite or invite['revoked'] or invite['expires_at'] <= now or invite['uses'] >= invite['max_uses']:
                raise Fault('INVITATION', 'Invitation is invalid, expired, revoked, or exhausted', 403)
        identity = c.select('player_emails', Q(email=email)).fetchone()
        account = c.select('accounts', Q(id=identity['account_id'])).fetchone() if identity else None
        # 禁用和封禁不靠密码恢复解除；仍发送所有权证明，恢复后仍受原状态与制裁约束。
        c.update('email_challenges', Q(email=email, purpose=purpose, consumed_at__isnull=True), {'status': 'SUPERSEDED'})
        c.insert('email_challenges', {'id': challenge_id, 'email': email, 'purpose': purpose,
            'digest': proof(challenge_id, email, purpose, code), 'ip_digest': ip_digest,
            'account_id': account['id'] if account else None,
            'security_version': account['security_version'] if account else None,
            'created_at': now, 'expires_at': now+600, 'status': 'PENDING'})
    error = None
    try:
        mailing.send_code(email, code, purpose)
    except mailing.MailUnavailable as caught:
        error = str(caught)
    with service.db.transaction() as c:
        # 新申请可能已取代正在投递的挑战，不得重新激活旧码；SMTP 超时状态不自动重试。
        c.update('email_challenges', Q(id=challenge_id, status='PENDING'),
                 {'status': 'FAILED' if error else 'SENT', 'error_code': error or ''})
    # 无论邮箱是否存在或供应商是否拒收，响应相同；错误留受控运维状态，不向访客枚举账号。
    return {'challenge_id': challenge_id, 'expires_in': 600, 'retry_after': 60,
            'message': '如地址符合本次操作条件，验证码将发送至该邮箱，请检查收件箱。'}


def recover(service, data, *, verify_only=False):
    """验证码恢复密码/验证旧邮箱；安全版本、会话撤销和审计与消费同事务，封禁状态不变。"""
    try:
        email = normalize_email(data.get('email'))
        hashed = None if verify_only else password_hash(data.get('password'))
    except ValueError as error:
        raise Fault('INVALID', str(error))
    purpose = 'verify' if verify_only else 'reset'
    # 网页发送完整请求元数据，响应丢失后允许恢复同一结果；旧调用方不带元数据仍保持一次性消费。
    # 持久摘要使用服务端 HMAC，既不保存验证码/新密码，也不允许凭数据库摘要离线穷举它们。
    metadata = service._metadata(data) if any(name in data for name in ('request_id','idempotency_key','reason')) else None
    summary, scope = None, 'email.' + purpose + ':' + token_hash(email)
    if metadata:
        summary = {'email':email, 'challenge_id':text_field(data.get('challenge_id'),'challenge_id',32),
                   'code_proof':proof(str(data.get('challenge_id')),email,purpose,str(data.get('code'))),
                   'password_proof':None if verify_only else email_registration_proof(data['password'])}
        with service.db.transaction() as c:
            previous = service._prior(c,scope,metadata[1],summary)
            if previous is not None:
                return previous
    try:
        check_code(service, data, email, purpose)
    except Fault:
        # 并发同键请求可能在前次查询之后成功消费验证码；再查一次原结果，不能重复修改密码。
        if metadata:
            with service.db.transaction() as c:
                previous = service._prior(c,scope,metadata[1],summary)
                if previous is not None:
                    return previous
        raise
    result = {'email_verified': True, 'password_reset': not verify_only,
              'message': '邮箱已验证' if verify_only else '密码已更新，请重新登录；账号审批和封禁状态保持不变。'}
    with service.db.transaction() as c:
        if metadata:
            previous = service._prior(c,scope,metadata[1],summary)
            if previous is not None:
                return previous
        row = consume(service, c, data, email, purpose)
        c.update('player_emails', Q(account_id=row['account_id']), {'verified_at': service.clock()})
        if not verify_only:
            c.update('accounts', Q(id=row['account_id']), {'password_hash': hashed})
            service._revoke(c, row['account_id'], identifier())
            c.update('email_challenges', Q(account_id=row['account_id'], consumed_at__isnull=True), {'status': 'SUPERSEDED'})
        service._audit(c, row['account_id'], 'email.verify' if verify_only else 'account.password.recover',
                       row['account_id'], metadata[0] if metadata else identifier(), 'email ownership code', {'challenge_id': row['id']})
        if metadata:
            service._remember(c,scope,metadata[1],summary,result)
    return result
