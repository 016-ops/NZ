"""兼容 Git LF 与历史 Windows CRLF 的冻结 JSON，任何非换行内容变化仍需原固定摘要批准。"""
import hashlib


def frozen_bytes(path, expected_sha256):
    """返回与固定摘要一致的字节表示；找不到等价表示时原样返回，由调用方保留既有拒绝码。

    不重新计算并接受新指纹，也不改写磁盘文件。仅把实际 CRLF 折为 LF 后分别尝试
    LF/CRLF 两种表示；JSON 字符串中的转义字符不会被改动，单独 CR 也不被吞掉。
    """
    raw = path.read_bytes()
    lf = raw.replace(b'\r\n', b'\n')
    for candidate in (raw, lf, lf.replace(b'\n', b'\r\n')):
        if hashlib.sha256(candidate).hexdigest() == expected_sha256:
            return candidate
    return raw
