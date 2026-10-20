"""Owner管理员生命周期；固定邮箱高风险授权由统一命令入口负责，旧恢复入口明确退役。"""
from django.db.models import F, Q
from .security import normalize_login, password_hash
from .service import Fault, PERMISSIONS, identifier, text_field


def recovery_codes(c, admin_id):
    """旧恢复码生成入口已退役，不产生可绕过邮件的凭据。"""
    raise Fault('AUTH_METHOD_RETIRED', '管理员恢复码已退役，请使用固定邮箱认证', 403)


def revoke(c, admin_id):
    """管理员角色或密码变化后撤销旧会话及未消费的邮件挑战。"""
    c.update('admins', Q(id=admin_id), {'security_version': F('security_version') + 1})
    c.update('sessions', Q(subject_id=admin_id, kind='admin'), {'revoked': 1})
    c.update('admin_auth_challenges', Q(admin_id=admin_id, consumed_at__isnull=True), {'status': 'SUPERSEDED'})


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
        admin_id = identifier()
        c.insert('admins', {'id': admin_id, 'login': login, 'password_hash': hashed, 'otp_secret': '', 'role': role})
        return {'admin_id': admin_id, 'role': role, 'authentication': 'FIXED_EMAIL_CODE'}
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
    raise Fault('NOT_FOUND', 'Unknown administrator action', 404)


def recover_mfa(service, login, password, code):
    """旧本机恢复函数明确失败；保留名称便于旧调用方获得安全错误。"""
    raise Fault('AUTH_METHOD_RETIRED', 'TOTP及本地恢复码入口已退役，请使用固定邮箱认证', 403)
