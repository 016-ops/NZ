"""区分原生客户端本轮新增日志与历史内容，避免将上轮浏览器错误误判为本轮失败。"""
import hashlib
from pathlib import Path

from tools.client_workspace import checked_path

MAX_LOG_BYTES = 10 * 1024 * 1024


def snapshot_logs(root, files):
    """启动前记录有限日志的长度、摘要；不保留正文，不读取根外链接，不写客户端文件。"""
    result = {}
    for path in files:
        if path.suffix.lower() not in ('.log', '.txt'):
            continue
        relative = path.relative_to(root).as_posix()
        checked_path(root, relative)
        if path.stat().st_size >= MAX_LOG_BYTES:
            continue
        content = path.read_bytes()
        if len(content) < MAX_LOG_BYTES:
            result[relative] = {'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
    return result


def capture_logs(root, output, baseline):
    """清理自有进程后保存私人完整日志和本轮片段，返回无正文的来源记录。

    追加日志必须逐字节摘要匹配旧前缀；轮转、截断或旧内容改写时保留完整当前文件，
    显式标记无法用偏移区分历史，不能把它当作全部发生在本轮的错误证据。
    """
    records = []
    for path in root.rglob('*'):
        if not path.is_file() or path.suffix.lower() not in ('.log', '.txt'):
            continue
        relative = path.relative_to(root).as_posix()
        checked_path(root, relative)
        if path.stat().st_size >= MAX_LOG_BYTES:
            continue
        content = path.read_bytes()
        if len(content) >= MAX_LOG_BYTES:
            continue
        previous = baseline.get(relative)
        if previous and hashlib.sha256(content).hexdigest() == previous['sha256']:
            continue
        offset = 0
        kind = 'NEW_FILE'
        if previous:
            offset = previous['bytes']
            if len(content) >= offset and hashlib.sha256(content[:offset]).hexdigest() == previous['sha256']:
                kind = 'VERIFIED_APPEND'
            else:
                kind, offset = 'REPLACED_OR_REWRITTEN_UNSEPARATED', 0
        # 两份输出都在调用方已创建的私人目录，正文不进入公开report或控制台。
        target = output / 'client-output' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        current = output / 'client-output-current' / relative
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(content[offset:])
        records.append({'path': relative, 'classification': kind, 'start_offset': offset,
                        'bytes': len(content) - offset, 'sha256': hashlib.sha256(content[offset:]).hexdigest()})
    return records
