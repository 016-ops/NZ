"""本地凭据摘要、邮箱规范化与scrypt密码；管理员认证统一由独立邮件挑战负责。"""
import hashlib
import hmac
import re
import secrets
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.conf import settings


def token_hash(value):
    """返回令牌的 SHA-256 摘要；持久化及查找使用摘要，不保存明文 Bearer。"""
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def new_token():
    """生成不可预测的 URL 安全随机凭据，供会话及邀请使用。"""
    return secrets.token_urlsafe(32)


def password_hash(password):
    """保留既有scrypt格式；所有新密码统一12–128字符，旧短密码摘要仍能正常验证。"""
    minimum = 12
    if not isinstance(password, str) or not minimum <= len(password) <= 128:
        raise ValueError(f'Password must contain {minimum} to 128 characters')
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=32768, r=8, p=3, maxmem=128 * 1024 * 1024)
    return 'scrypt$32768$8$3$' + salt.hex() + '$' + digest.hex()


def verify_password(password, encoded):
    """按旧 scrypt 参数验证密码并恒定时间比较；格式不支持或损坏时返回 False。"""
    if not isinstance(password, str) or len(password) > 128:
        return False
    try:
        scheme, n, r, p, salt, expected = encoded.split('$')
        if (scheme, n, r, p) != ('scrypt', '32768', '8', '3'):
            return False
        candidate = hashlib.scrypt(password.encode('utf-8'), salt=bytes.fromhex(salt),
                                   n=32768, r=8, p=3, maxmem=128 * 1024 * 1024)
        return hmac.compare_digest(candidate.hex(), expected)
    except (ValueError, TypeError):
        return False


def normalize_login(value):
    """校验 ASCII 登录名长度和字符集并转小写；不改变昵称的大小写与中文语义。"""
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{3,32}', value):
        raise ValueError('Local web login requires 3 to 32 ASCII letters, digits, _, ., -')
    return value.lower()


def normalize_email(value):
    """校验独立邮箱标识并统一小写；不合并加号/点别名，不声称已验证邮箱所有权。

    当前版本支持 ASCII 邮箱（国际化域可用 punycode），总长最多 254、本地部分最多 64。
    大小写不区分是本服务的登录约定，不推断各邮件服务商的投递规则。
    """
    if not isinstance(value, str) or not value.isascii() or any(ord(char) < 32 for char in value):
        raise ValueError('请输入有效邮箱地址')
    normalized = value.strip().lower()
    if len(normalized) > 254 or normalized.count('@') != 1:
        raise ValueError('请输入有效邮箱地址')
    local, domain = normalized.split('@')
    if (not 1 <= len(local) <= 64 or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+", local)
            or '.' not in domain or domain.startswith('[')):
        raise ValueError('请输入有效邮箱地址')
    try:
        validate_email(normalized)
    except ValidationError:
        raise ValueError('请输入有效邮箱地址') from None
    return normalized


def email_registration_proof(password):
    """为邮箱注册幂等摘要生成服务端密钥保护的证明，避免数据库形成便宜的密码猜测校验器。

    密码真正存储仍为随机盐 scrypt；这里仅比较注册重试内容，原有账号/资产幂等记录不改写。
    部署必须保留 NZ_SECRET_KEY；更换密钥后，未决注册应通过账号状态确认而非重发创建。
    """
    return hmac.new(settings.SECRET_KEY.encode('utf-8'), b'nz-email-register-v1\0' + password.encode('utf-8'), hashlib.sha256).hexdigest()
