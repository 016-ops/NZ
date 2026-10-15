"""Owner 管理管理员及一次性 MFA 恢复；高风险授权由统一命令入口再次认证。"""
import base64
import secrets
from django.db.models import F, Q
from .security import normalize_login, password_hash, token_hash, verify_password
from .service import Fault, PERMISSIONS, identifier, text_field


def recovery_codes(c, admin_id):
    """替换恢复码并仅返回一次明文；随机码具备高熵，数据库只保留 SHA256。"""
    codes = [secrets.token_urlsafe(24) for _ in range(8)]
    c.delete('admin_recovery', Q(admin_id=admin_id))
    for code in codes:
        c.insert('admin_recovery', {'digest': token_hash(code), 'admin_id': admin_id})
    return codes


def revoke(c, admin_id):
    """管理员角色、密码或 MFA 变化后立即使旧会话失效。"""
    c.update('admins', Q(id=admin_id), {'security_version': F('security_version') + 1})
    c.update('sessions', Q(subject_id=admin_id, kind='admin'), {'revoked': 1})


def admin_command(service, c, actor, action, data):
    """创建、调整、停用和重置管理员；最后一个可用 Owner 不能被降权或停用。"""
    if action == 'admin.create':
        try:
            login, hashed = normalize_login(data.get('login')), password_hash(data.get('new_password'))
        except ValueError as error:
            raise Fault('INVALID', str(error))
        role = data.get('role')
        if role not in PERMISSIONS:
            raise Fault('INVALID', 'Unknown administrator role')
        if c.select('admins', Q(login=login)).fetchone():
            raise Fault('CONFLICT', 'Administrator already exists', 409)
        admin_id, secret = identifier(), base64.b32encode(secrets.token_bytes(20)).decode('ascii')
        c.insert('admins', {'id': admin_id, 'login': login, 'password_hash': hashed, 'otp_secret': secret, 'role': role})
        return {'admin_id': admin_id, 'role': role, 'otp_secret': secret, 'recovery_codes': recovery_codes(c, admin_id)}
    target = c.select('admins', Q(id=data.get('admin_id'))).fetchone()
    if not target:
        raise Fault('NOT_FOUND', 'Administrator does not exist', 404)
    if action == 'admin.update':
        role, active = data.get('role'), data.get('active')
        if role not in PERMISSIONS or type(active) is not bool:
            raise Fault('INVALID', 'Provide a valid role and boolean active')
        if target['role'] == 'owner' and target['active'] and (role != 'owner' or not active):
            if c.count('admins', Q(role='owner', active=1)).fetchone()[0] <= 1:
                raise Fault('LAST_OWNER', 'The final active Owner cannot be disabled or demoted', 409)
        c.update('admins', Q(id=target['id']), {'role': role, 'active': int(active)})
        revoke(c, target['id'])
        return {'admin_id': target['id'], 'role': role, 'active': active}
    if action == 'admin.password.reset':
        try:
            hashed = password_hash(data.get('new_password'))
        except ValueError as error:
            raise Fault('INVALID', str(error))
        c.update('admins', Q(id=target['id']), {'password_hash': hashed})
        revoke(c, target['id'])
        return {'admin_id': target['id'], 'sessions_revoked': True}
    if action in ('admin.mfa.reset', 'admin.recovery.rotate'):
        result = {'admin_id': target['id'], 'recovery_codes': recovery_codes(c, target['id'])}
        if action == 'admin.mfa.reset':
            secret = base64.b32encode(secrets.token_bytes(20)).decode('ascii')
            c.update('admins', Q(id=target['id']), {'otp_secret': secret, 'last_otp_step': -1})
            result['otp_secret'] = secret
            revoke(c, target['id'])
        return result
    raise Fault('NOT_FOUND', 'Unknown administrator action', 404)


def recover_mfa(service, login, password, code):
    """仅本机管理命令调用：密码和未用恢复码共同核验，恢复后替换 MFA 与全部恢复码。"""
    try:
        login = normalize_login(login)
    except ValueError:
        raise Fault('AUTH', 'Invalid recovery credentials', 401)
    code = text_field(code, 'recovery_code', 128)
    with service.db.transaction() as c:
        admin = c.select('admins', Q(login=login, active=1)).fetchone()
        recovery = c.select('admin_recovery', Q(digest=token_hash(code), used_at__isnull=True)).fetchone()
        if not admin or not recovery or recovery['admin_id'] != admin['id'] or not verify_password(password, admin['password_hash']):
            raise Fault('AUTH', 'Invalid recovery credentials', 401)
        secret = base64.b32encode(secrets.token_bytes(20)).decode('ascii')
        c.update('admins', Q(id=admin['id']), {'otp_secret': secret, 'last_otp_step': -1})
        revoke(c, admin['id'])
        codes = recovery_codes(c, admin['id'])
        service._audit(c, admin['id'], 'admin.mfa.recover', admin['id'], identifier(), 'Local password and one-time recovery code', {})
        return {'admin_id': admin['id'], 'otp_secret': secret, 'recovery_codes': codes}
