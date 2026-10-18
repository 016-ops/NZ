"""当前UI屏幕纹理路径的单变量诊断变体；不写原件、不改认证或就绪状态。"""
import hashlib
import re
import struct
import zlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from tools.client_catalog import MAGIC, RESOURCE_KEY, decode_resource

GAME_CONFIG_SHA256 = '1d970d31f82cd0d38c2386d9bda0e1928917141776fd0ff67b0342ea9525c34d'
UI_TEXTURE_PROFILE = 'cn2020-ui-screen-texture-disabled-diagnostic-v1'


def disable_current_screen_texture(original):
    """固定当前DefaultGame摘要后，只改TGUISceneClient的一处bUseScreenTexture为false。

    只返回加密变体，调用方须使用NativeConfigGuard安装与恢复。保留BOM、注释及其他原始字节，
    未知版本、重复/缺少字段均拒绝；这不是已证明可修复大厅的默认配置。
    """
    if hashlib.sha256(original).hexdigest() != GAME_CONFIG_SHA256:
        raise ValueError('CURRENT_UI_CONFIG_FINGERPRINT_MISMATCH')
    _, metadata = decode_resource(original, limit=1024*1024)
    if not metadata['encrypted']:
        raise ValueError('EXPECTED_ENCRYPTED_CURRENT_UI_CONFIG')
    decoder = Cipher(algorithms.AES(RESOURCE_KEY), modes.ECB()).decryptor()
    packed = decoder.update(original[len(MAGIC):]) + decoder.finalize()
    clear = zlib.decompress(packed[4:])
    section, changes, lines = None, 0, []
    for line in clear.splitlines(keepends=True):
        header = re.fullmatch(rb'\[([^\]]+)\]\r?\n?', line.removeprefix(b'\xef\xbb\xbf'))
        if header:
            section = header[1]
        if section == b'UTGame.TGUISceneClient' and line.startswith(b'bUseScreenTexture='):
            match = re.fullmatch(rb'bUseScreenTexture=true(\r?\n)?', line)
            if not match or changes:
                raise ValueError('UNEXPECTED_OR_DUPLICATE_UI_TEXTURE_FIELD')
            line = b'bUseScreenTexture=false' + (match[1] or b'')
            changes += 1
        lines.append(line)
    if changes != 1:
        raise ValueError('CURRENT_UI_TEXTURE_FIELD_MISSING')
    rewritten = b''.join(lines)
    packed = struct.pack('<I', len(rewritten)) + zlib.compress(rewritten)
    packed += bytes((-len(packed)) % 16)
    encoder = Cipher(algorithms.AES(RESOURCE_KEY), modes.ECB()).encryptor()
    result = MAGIC + encoder.update(packed) + encoder.finalize()
    text, _ = decode_resource(result, limit=1024*1024)
    if text != rewritten.decode(metadata['encoding']).rstrip('\0'):
        raise ValueError('UI_TEXTURE_VARIANT_ROUNDTRIP_MISMATCH')
    return result
