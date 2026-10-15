"""建立可校验、可恢复的客户端工作副本；原目录始终只读，禁止硬链接或目录联接。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import time


def digest(path):
    """流式计算单文件 SHA256，不把大型资源包整体装入内存。"""
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def checked_path(root, relative):
    """拒绝绝对路径、目录穿越和重解析点，确保复制始终局限于指定根目录。"""
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Unsafe manifest path')
    result = root / relative
    for parent in [result, *result.parents]:
        if parent == root.parent:
            break
        if parent.exists() and (parent.is_symlink() or getattr(parent.stat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError('Reparse points are not allowed')
        if parent.is_file() and parent.stat().st_nlink > 1:
            raise ValueError('Hard-linked files are not independent copies')
    if not result.resolve().is_relative_to(root):
        raise ValueError('Path escapes workspace')
    return result


def copy_verified(source, target, expected):
    """复制同时核对原文件摘要，再独立读回目标；不匹配时保留失败，不覆盖已验收文件。"""
    if source.stat().st_size != expected['bytes']:
        raise ValueError('Source size differs from captured baseline: ' + expected['path'])
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.nz-copy-part')
    if temporary.exists():
        raise ValueError('Incomplete copy exists: ' + str(temporary))
    before = source.stat()
    sha = hashlib.sha256()
    try:
        with source.open('rb') as reader, temporary.open('xb') as writer:
            while chunk := reader.read(4 * 1024 * 1024):
                writer.write(chunk)
                sha.update(chunk)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or sha.hexdigest() != expected['sha256']:
            raise ValueError('Source changed or hash mismatch: ' + expected['path'])
        if digest(temporary) != expected['sha256']:
            raise ValueError('Work copy read-back failed: ' + expected['path'])
        shutil.copystat(source, temporary)
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()


def main():
    """创建副本或校验/恢复已登记副本；恢复只替换基线文件，额外日志保留供追溯。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['create', 'verify', 'restore'])
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    source, workspace = args.source.resolve(), args.workspace.resolve()
    if source == workspace or workspace.is_relative_to(source) or source.is_relative_to(workspace):
        raise ValueError('Source and work copy must be independent trees')
    records = json.loads(args.manifest.read_text(encoding='utf-8'))
    paths = [record['path'] for record in records]
    if not records or len({path.casefold() for path in paths}) != len(paths):
        raise ValueError('Empty or duplicate manifest')
    metadata = workspace.parent / (workspace.name + '.workspace.json')
    identity = {'source': str(source), 'workspace': str(workspace), 'manifest_sha256': digest(args.manifest)}
    if metadata.exists():
        previous = json.loads(metadata.read_text(encoding='utf-8'))
        if any(previous.get(key) != value for key, value in identity.items()):
            raise ValueError('Workspace identity mismatch')
    elif args.action != 'create' or (workspace.exists() and any(workspace.iterdir())):
        raise ValueError('Refusing to adopt an unregistered nonempty workspace')
    workspace.mkdir(parents=True, exist_ok=True)
    if not metadata.exists():
        metadata.write_text(json.dumps(dict(identity, status='INCOMPLETE'), ensure_ascii=False, indent=2), encoding='utf-8')
    actual = {path.relative_to(source).as_posix() for path in source.rglob('*') if path.is_file()}
    if actual != set(paths):
        raise ValueError('Source file set differs from baseline')
    started = time.monotonic()
    changed, copied = [], 0
    for index, record in enumerate(records, 1):
        original = checked_path(source, record['path'])
        target = checked_path(workspace, record['path'])
        correct = target.is_file() and target.stat().st_size == record['bytes'] and digest(target) == record['sha256']
        # 已存在的正确副本也不能掩盖原件变化；verify 同时核对原件和副本。
        if correct and (original.stat().st_size != record['bytes'] or digest(original) != record['sha256']):
            raise ValueError('Original source differs from baseline: ' + record['path'])
        if not correct:
            changed.append(record['path'])
            if args.action != 'verify':
                copy_verified(original, target, record)
                copied += 1
        if index % 250 == 0:
            print(json.dumps({'checked': index, 'total': len(records), 'copied': copied}), flush=True)
    final_files = {path.relative_to(source).as_posix() for path in source.rglob('*') if path.is_file()}
    if final_files != set(paths):
        raise ValueError('Source file set changed during operation')
    report = dict(identity, status='VERIFIED' if not changed or args.action != 'verify' else 'MODIFIED',
                  checked=len(records), copied=copied, changed=changed, seconds=round(time.monotonic() - started, 3),
                  source_write_operations=0, hardlinks=False, recovery_policy='RESTORE_BASELINE_FILES_KEEP_EXTRA_LOGS')
    report_path = metadata if args.action != 'verify' else workspace.parent / (workspace.name + '.verification.json')
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key not in ('changed', 'source', 'workspace')}, ensure_ascii=True))
    return 1 if args.action == 'verify' and changed else 0


if __name__ == '__main__':
    raise SystemExit(main())
