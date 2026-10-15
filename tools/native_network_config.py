"""当前恢复样本的原生 Geo/Zone 配置研究变体：只改固定文件中的四个网络值，保留其他字节语义。"""
import hashlib
import re
import struct
import zlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from tools.client_catalog import MAGIC, RESOURCE_KEY, decode_resource

CONFIG_SHA256 = 'cd9db2c5ac64273ec2ebba1eeab4d7a7eca28be46fb9c04e576e07b005db0373'
PROFILE = 'nz-current-geozone-loopback-research-v1'


def redirect_current_network(original, port):
    """验证固定配置指纹后，返回 Geo/Zone 的回环变体；不写文件、不更改认证类型、密钥或 TDR 路径。

    这只用于观察当前样本是否消费该配置，不能宣称客户端已经接入独立服务端。
    未知版本、重复字段、非普通端口均拒绝；重封装后通过现有解码器逐字核验。
    """
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Research endpoint requires a non-privileged loopback port')
    if hashlib.sha256(original).hexdigest() != CONFIG_SHA256:
        raise ValueError('Current network configuration fingerprint mismatch')
    before, metadata = decode_resource(original, limit=65536)
    if not metadata['encrypted']:
        raise ValueError('Expected current encrypted configuration')
    decoder = Cipher(algorithms.AES(RESOURCE_KEY), modes.ECB()).decryptor()
    packed = decoder.update(original[len(MAGIC):]) + decoder.finalize()
    # 上述固定哈希与有界解码已校验声明长度和 zlib EOF。保留 BOM、换行与非目标配置的原始字节。
    clear = zlib.decompress(packed[4:])
    expected = {
        b'UTGame.TGClientGeoNetHandler': {b'Host': b'172.25.34.161', b'Port': b'65006'},
        b'UTGame.TGClientZoneNetHandler': {b'Host': b'10.123.102.101', b'Port': b'65000'},
    }
    section = None
    changed = []
    lines = []
    for line in clear.splitlines(keepends=True):
        header = re.fullmatch(rb'\[([^\]]+)\]\r?\n?', line.removeprefix(b'\xef\xbb\xbf'))
        if header:
            section = header[1]
        match = re.fullmatch(rb'(Host|Port)=([^\r\n]*)(\r?\n)?', line)
        if section in expected and match:
            key = match[1]
            if match[2] != expected[section][key] or (section, key) in changed:
                raise ValueError('Unexpected or repeated current network field')
            replacement = b'127.0.0.1' if key == b'Host' else str(port).encode('ascii')
            line = key + b'=' + replacement + (match[3] or b'')
            changed.append((section, key))
        lines.append(line)
    if len(changed) != 4:
        raise ValueError('Current network fields incomplete')
    rewritten = b''.join(lines)
    packed = struct.pack('<I', len(rewritten)) + zlib.compress(rewritten)
    # 原格式以 zlib EOF 确定内容尾部，AES 对齐填充不参与配置语义；不改项目只读提取器。
    packed += bytes((-len(packed)) % 16)
    encoder = Cipher(algorithms.AES(RESOURCE_KEY), modes.ECB()).encryptor()
    result = MAGIC + encoder.update(packed) + encoder.finalize()
    after, _ = decode_resource(result, limit=65536)
    if after != rewritten.decode(metadata['encoding']).rstrip('\0') or after == before:
        raise ValueError('Network variant round-trip mismatch')
    return result
