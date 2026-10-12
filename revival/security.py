import base64
import hashlib
import hmac
import re
import secrets
import struct


def token_hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def new_token():
    return secrets.token_urlsafe(32)


def password_hash(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        raise ValueError('Password must contain 12 to 128 characters')
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=32768, r=8, p=3, maxmem=128 * 1024 * 1024)
    return 'scrypt$32768$8$3$' + salt.hex() + '$' + digest.hex()


def verify_password(password, encoded):
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
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{3,32}', value):
        raise ValueError('Local web login requires 3 to 32 ASCII letters, digits, _, ., -')
    return value.lower()


def otp(secret, step):
    key = base64.b32decode(secret)
    digest = hmac.new(key, struct.pack('>Q', step), hashlib.sha1).digest()
    offset = digest[-1] & 15
    number = (struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7fffffff) % 1000000
    return f'{number:06d}'


def verify_otp(secret, code, timestamp, last_step=-1):
    if not isinstance(code, str) or not re.fullmatch(r'\d{6}', code):
        return None
    current = timestamp // 30
    for step in (current, current - 1, current + 1):
        if step > last_step and hmac.compare_digest(otp(secret, step), code):
            return step
    return None
