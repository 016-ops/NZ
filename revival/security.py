"""本地凭据摘要、scrypt 密码和 TOTP 校验；保留旧格式与时间步防重放。"""
import base64
import hashlib
import hmac
import re
import secrets
import struct


def token_hash(value):
    """返回令牌的 SHA-256 摘要；持久化及查找使用摘要，不保存明文 Bearer。"""
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def new_token():
    """生成不可预测的 URL 安全随机凭据，供会话及邀请使用。"""
    return secrets.token_urlsafe(32)


def password_hash(password, *, allow_test_short=False):
    """保留既有 scrypt 格式；只有本地合成账号创建命令可显式允许六位测试密码。

    注册、重置与普通 Owner 初始化不传此参数，继续要求 12–128 字符；HTTP 请求不能控制该开关。
    """
    minimum = 6 if allow_test_short else 12
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


def otp(secret, step):
    """根据 Base32 密钥及时间步计算六位 TOTP；不自行更新持久化防重放状态。"""
    key = base64.b32decode(secret)
    digest = hmac.new(key, struct.pack('>Q', step), hashlib.sha1).digest()
    offset = digest[-1] & 15
    number = (struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7fffffff) % 1000000
    return f'{number:06d}'


def verify_otp(secret, code, timestamp, last_step=-1):
    """在当前及相邻时间步验证验证码，只接受大于已用时间步的结果；失败返回 None。"""
    if not isinstance(code, str) or not re.fullmatch(r'\d{6}', code):
        return None
    current = timestamp // 30
    for step in (current, current - 1, current + 1):
        if step > last_step and hmac.compare_digest(otp(secret, step), code):
            return step
    return None
