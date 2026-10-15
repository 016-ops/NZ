"""当前构建的认证、研究换钥及上下行业务封装边界；不提供通用登录或具体业务规则。"""
import hmac
import re
import secrets
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

TRANSPORT_PROFILE = 'nz-current-auth-challenge-research-v2'
APPLICATION_PROFILE = 'nz-current-keychange-application-research-v1'
DOWNLINK_PROFILE = 'nz-current-application-downlink-research-v1'
MAX_FRAME_BYTES = 64 * 1024
_HEADER = struct.Struct('>BBBBII')
_MAGIC = 0x55
_VERSION = 0x0E
_AUTH, _SYN, _SYNACK = 3, 8, 9
_IV = bytes(range(16))
_FOOTER = b'tsf4g\x10'


class TransportError(ValueError):
    """当前研究合同之外的输入；异常消息仅含固定错误码，不回显令牌、密文或明文。"""


def _header(raw, max_bytes=MAX_FRAME_BYTES):
    """核对固定头和总长度；额外正文只开放已证 SYNACK 的 4 字节加密序号形式。"""
    if type(max_bytes) is not int or not _HEADER.size <= max_bytes <= MAX_FRAME_BYTES:
        raise TransportError('INVALID_FRAME_LIMIT')
    if len(raw) != _HEADER.size:
        raise TransportError('INVALID_HEADER_LENGTH')
    magic, version, command, encrypted_header_length, head_length, body_length = _HEADER.unpack(raw)
    if magic != _MAGIC or version != _VERSION:
        raise TransportError('UNSUPPORTED_HEADER')
    if command not in (_AUTH, _SYN, _SYNACK):
        raise TransportError('UNSUPPORTED_COMMAND')
    if head_length < _HEADER.size or head_length + body_length > max_bytes:
        raise TransportError('INVALID_FRAME_LENGTH')
    # 当前异步 SYNACK 经通用发送路径附加 BE32 序号，其 AES3 正文恰为一块 16 字节。
    # 其他头/正文组合仍不接受，不能因为发现该形式就放开任意业务正文或加密头。
    if encrypted_header_length != 0 or body_length != 0:
        if (command, encrypted_header_length, head_length, body_length) != (_SYNACK, 4, 45, 16):
            raise TransportError('UNSUPPORTED_ENCRYPTED_BODY')
    return command, head_length, body_length


def _exact(stream, length):
    """只读取当前帧所需字节，容忍 TCP 分片；提前 EOF 拒绝，调用方负责设置连接超时。"""
    content = bytearray()
    while len(content) < length:
        fragment = stream.recv(length - len(content))
        if not fragment:
            raise TransportError('TRUNCATED_FRAME')
        content.extend(fragment)
    return bytes(content)


def receive_frame(stream, *, max_bytes=MAX_FRAME_BYTES):
    """接收最多 64KiB 的完整头与正文，不超读；正文只允许已证异步 SYNACK 的一块密文。"""
    # 先校验调用方限制，避免不合法限制仍造成网络读取；头校验后才分配或读取剩余内容。
    if type(max_bytes) is not int or not _HEADER.size <= max_bytes <= MAX_FRAME_BYTES:
        raise TransportError('INVALID_FRAME_LIMIT')
    raw = _exact(stream, _HEADER.size)
    _, head_length, body_length = _header(raw, max_bytes)
    return raw + _exact(stream, head_length + body_length - _HEADER.size)


def _frame(frame, command):
    """验证完整单帧及指定命令，拒绝截断和尾随字节，分别返回头扩展和有界正文。"""
    if not isinstance(frame, bytes) or len(frame) < _HEADER.size:
        raise TransportError('INVALID_FRAME')
    actual_command, head_length, body_length = _header(frame[:_HEADER.size])
    if command != actual_command:
        raise TransportError('UNEXPECTED_COMMAND')
    if len(frame) != head_length + body_length:
        raise TransportError('FRAME_LENGTH_MISMATCH')
    return frame[_HEADER.size:head_length], frame[head_length:]


def validate_auth(frame, expected_id, expected_token):
    """只接受本次预先确定的独立账号与令牌；返回结构摘要，不创建会话或输出凭据材料。"""
    if type(expected_id) is not int or not 1 <= expected_id <= 2147483647:
        raise TransportError('INVALID_EXPECTED_ID')
    if not isinstance(expected_token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', expected_token):
        raise TransportError('INVALID_EXPECTED_TOKEN')
    payload, _ = _frame(frame, _AUTH)
    if len(payload) < 17:
        raise TransportError('INVALID_AUTH_LENGTH')
    encryption_method, service_id, auth_type, game_id = struct.unpack_from('>IIII', payload)
    token_length = payload[16]
    if not 1 <= token_length <= 128 or len(payload) != 17 + token_length:
        raise TransportError('INVALID_AUTH_LENGTH')
    # AUTH 的 ServiceID=0 是当前包的已证字段，不能与 IPC 的研究 ServiceId=1 混为同一业务值。
    if (encryption_method, service_id, auth_type) != (3, 0, 3):
        raise TransportError('UNSUPPORTED_AUTH_FIELDS')
    token_matches = hmac.compare_digest(payload[17:], expected_token.encode('ascii'))
    if game_id != expected_id or not token_matches:
        raise TransportError('AUTH_IDENTITY_MISMATCH')
    return {'transport_profile': TRANSPORT_PROFILE, 'command': _AUTH, 'head_length': len(frame),
            'body_length': 0, 'encryption_method': encryption_method, 'service_id': service_id,
            'auth_type': auth_type, 'game_id': game_id, 'token_length': token_length,
            'expected_identity_verified': True}


def _crypto_inputs(key, challenge):
    """只接受当前已证 16 字节 bootstrap AES key 与 challenge，不隐式截断或推导未知格式。"""
    if not isinstance(key, bytes) or len(key) != 16:
        raise TransportError('INVALID_BOOTSTRAP_KEY')
    if not isinstance(challenge, bytes) or len(challenge) != 16:
        raise TransportError('INVALID_CHALLENGE')


def make_challenge(key, challenge):
    """构造当前 SYN；调用方每连接传入新的随机挑战，使用已证固定 IV、随机填充和尾标。"""
    _crypto_inputs(key, challenge)
    plaintext = challenge + secrets.token_bytes(10) + _FOOTER
    encoder = Cipher(algorithms.AES(key), modes.CBC(_IV)).encryptor()
    ciphertext = encoder.update(plaintext) + encoder.finalize()
    # compact 帧把 32 字节密文及其单字节长度放在 HeadLen 内，BodyLen 保持 0。
    return _HEADER.pack(_MAGIC, _VERSION, _SYN, 0, _HEADER.size + 1 + len(ciphertext), 0) + bytes([len(ciphertext)]) + ciphertext


def verify_challenge_reply(frame, key, challenge):
    """验证当前 SYNACK；格式异常拒绝，合法密文的尾标/挑战不符返回 False，不进入业务状态。"""
    _crypto_inputs(key, challenge)
    payload, body = _frame(frame, _SYNACK)
    if len(payload) != 33 or payload[0] != 32:
        raise TransportError('INVALID_CHALLENGE_CIPHERTEXT_LENGTH')
    decoder = Cipher(algorithms.AES(key), modes.CBC(_IV)).decryptor()
    plaintext = decoder.update(payload[1:]) + decoder.finalize()
    # 随机填充没有固定内容；尾标和末尾填充长度必须都成立，挑战用常量时间比较防止前缀泄露。
    footer_matches = hmac.compare_digest(plaintext[26:], _FOOTER)
    challenge_matches = hmac.compare_digest(plaintext[:16], challenge)
    sequence_body_matches = True
    if body:
        # 换钥前该发送路径仍用 bootstrap key 和同一 IV；解密后必须恰好保留 4 字节序号。
        # 序号首值为客户端随机初始化，后续递增；这里只核验形状，不猜 0/1 或公开实际序号。
        body_decoder = Cipher(algorithms.AES(key), modes.CBC(_IV)).decryptor()
        sequence_plaintext = body_decoder.update(body) + body_decoder.finalize()
        sequence_body_matches = hmac.compare_digest(sequence_plaintext[10:], b'tsf4g\x0c')
    return footer_matches and challenge_matches and sequence_body_matches


def make_key_change(old_key, new_key):
    """显式生成已证 CHGSKEY 研究帧；调用方须先完成 AUTH/challenge，并提供新随机 16 字节 key。"""
    if not isinstance(old_key, bytes) or len(old_key) != 16:
        raise TransportError('INVALID_BOOTSTRAP_KEY')
    if not isinstance(new_key, bytes) or len(new_key) != 16:
        raise TransportError('INVALID_NEW_SESSION_KEY')
    plaintext = new_key + secrets.token_bytes(10) + _FOOTER
    encoder = Cipher(algorithms.AES(old_key), modes.CBC(_IV)).encryptor()
    ciphertext = encoder.update(plaintext) + encoder.finalize()
    # Type0 只是当前解析器忽略字段的显式研究选择；不可宣称它是已验证的生产业务规则。
    extension = struct.pack('>HH', 0, len(ciphertext)) + ciphertext
    return _HEADER.pack(_MAGIC, _VERSION, 1, 0, _HEADER.size + len(extension), 0) + extension


def _application_header(raw, max_bytes=MAX_FRAME_BYTES):
    """业务取证独立限制为当前 cmd0/12 字节头/4 字节加密序号，不放宽认证帧接收器。"""
    if type(max_bytes) is not int or not _HEADER.size <= max_bytes <= MAX_FRAME_BYTES:
        raise TransportError('INVALID_FRAME_LIMIT')
    if len(raw) != _HEADER.size:
        raise TransportError('INVALID_HEADER_LENGTH')
    magic, version, command, encrypted_header_length, head_length, body_length = _HEADER.unpack(raw)
    if (magic, version, command, encrypted_header_length, head_length) != (_MAGIC, _VERSION, 0, 4, 12):
        raise TransportError('UNSUPPORTED_APPLICATION_HEADER')
    if body_length < 16 or body_length % 16 or head_length + body_length > max_bytes:
        raise TransportError('INVALID_APPLICATION_LENGTH')
    return head_length, body_length


def receive_application_frame(stream, *, max_bytes=MAX_FRAME_BYTES):
    """接收一个有界业务取证帧，兼容 TCP 分片且不超读；调用方负责超时及仅在换钥后调用。"""
    if type(max_bytes) is not int or not _HEADER.size <= max_bytes <= MAX_FRAME_BYTES:
        raise TransportError('INVALID_FRAME_LIMIT')
    raw = _exact(stream, _HEADER.size)
    _, body_length = _application_header(raw, max_bytes)
    return raw + _exact(stream, body_length)


def decode_application(frame, new_key):
    """用换钥后的 key 解密一帧，严格去除当前填充和 4 字节序号，仅返回供私人存证的业务字节。"""
    if not isinstance(new_key, bytes) or len(new_key) != 16:
        raise TransportError('INVALID_NEW_SESSION_KEY')
    if not isinstance(frame, bytes) or len(frame) < _HEADER.size:
        raise TransportError('INVALID_FRAME')
    head_length, body_length = _application_header(frame[:_HEADER.size])
    if len(frame) != head_length + body_length:
        raise TransportError('FRAME_LENGTH_MISMATCH')
    decoder = Cipher(algorithms.AES(new_key), modes.CBC(_IV)).decryptor()
    plaintext = decoder.update(frame[head_length:]) + decoder.finalize()
    padding_length = plaintext[-1]
    footer_matches = hmac.compare_digest(plaintext[-6:-1], b'tsf4g')
    # 当前 AES3 填充通式为 (-(明文长度+6))%16+6，即 6..21 字节；其中至少包含尾标与长度字节。
    # 除验证 footer 外还保守检查剩余长度至少 4 字节，避免原生宽松解析被研究工具照搬为越界。
    unpadded_length = len(plaintext) - padding_length
    canonical_padding = (-(unpadded_length + 6)) % 16 + 6
    if (not footer_matches or not 6 <= padding_length <= 21 or unpadded_length < 4
            or padding_length != canonical_padding):
        raise TransportError('INVALID_APPLICATION_PADDING')
    # 序号随机初始化后递增；仅剥离已证前缀，不把它输出为身份或据此猜测业务消息。
    return plaintext[4:-padding_length]


def make_application_response(application, key):
    """按当前接收路径封装非空下行业务；调用方提供已核验内容和 key，本函数不构造或解释消息。"""
    if not isinstance(key, bytes) or len(key) != 16:
        raise TransportError('INVALID_DOWNLINK_KEY')
    if not isinstance(application, bytes) or not application:
        raise TransportError('INVALID_APPLICATION_PAYLOAD')
    padding_length = (-(len(application) + 6)) % 16 + 6
    body_length = len(application) + padding_length
    if _HEADER.size + body_length > MAX_FRAME_BYTES:
        raise TransportError('INVALID_APPLICATION_LENGTH')
    # 当前活动接收路径将解密 body 原样入队，不读取或剥离序号；下行不可照搬上行的 4 字节前缀。
    plaintext = application + secrets.token_bytes(padding_length - 6) + b'tsf4g' + bytes([padding_length])
    encoder = Cipher(algorithms.AES(key), modes.CBC(_IV)).encryptor()
    ciphertext = encoder.update(plaintext) + encoder.finalize()
    # EncHeadLen0 是无加密序号头的显式研究选择，独立 profile 保留与上行 EncHeadLen4 的差异。
    return _HEADER.pack(_MAGIC, _VERSION, 0, 0, _HEADER.size, body_length) + ciphertext
