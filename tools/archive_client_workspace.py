"""把用户原 RAR 完整还原到新的独立目录，按每个成员 CRC 与 SHA256 建立可复查基线。"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import time
import zlib
import rarfile
from tools.client_workspace import digest


def checked_members(infos):
    """只允许 nz 根内普通文件/目录；拒绝链接、路径穿越、ADS 和 Windows 不区分大小写的重复。"""
    records, seen = [], set()
    for info in infos:
        name = info.filename.replace('\\', '/').rstrip('/')
        parts = name.split('/')
        devices = {'con', 'prn', 'aux', 'nul', 'conin$', 'conout$', *('com' + str(n) for n in range(10)), *('lpt' + str(n) for n in range(10))}
        if (not parts or parts[0] != 'nz' or any(part in ('', '.', '..') or ':' in part or part.rstrip(' .') != part
                or any(ord(char) < 32 for char in part) or part.split('.', 1)[0].casefold() in devices for part in parts)
                or PurePosixPath(name).is_absolute() or info.is_symlink() or getattr(info, 'file_redir', None)):
            raise ValueError('Unsafe archive member')
        key = name.casefold()
        if key in seen:
            raise ValueError('Duplicate archive member')
        seen.add(key)
        if not info.is_dir():
            if len(parts) < 2:
                raise ValueError('Archive root must be a directory')
            records.append({'path': '/'.join(parts[1:]), 'bytes': info.file_size, 'crc32': info.CRC})
    if not records:
        raise ValueError('Archive has no client files')
    return records


def verify_file(path, expected):
    """逐文件检查真实大小及完整 CRC32，并同时计算 SHA256；不相信解包返回码就默认成功。"""
    if path.is_symlink() or not path.is_file() or path.stat().st_size != expected['bytes']:
        raise ValueError('Extracted member size or type mismatch: ' + expected['path'])
    crc, sha = 0, hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            crc = zlib.crc32(chunk, crc)
            sha.update(chunk)
    if crc & 0xffffffff != expected['crc32']:
        raise ValueError('Extracted member CRC mismatch: ' + expected['path'])
    return dict(expected, sha256=sha.hexdigest())


def main():
    """新目录解包、监测剩余空间，全部校验后登记；失败保留证据且不接纳为可启动基线。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--original', type=Path, required=True)
    args = parser.parse_args()
    archive, destination, original = args.archive.resolve(), args.destination.resolve(), args.original.resolve()
    if destination.exists() or destination.is_relative_to(original) or original.is_relative_to(destination):
        raise ValueError('A new independent destination is required')
    with rarfile.RarFile(archive) as container:
        records = checked_members(container.infolist())
    total = sum(row['bytes'] for row in records)
    reserve = 2 * 1024 ** 3
    destination.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(destination.parent).free < total + reserve:
        raise ValueError('Insufficient disk space including a 2 GiB reserve')
    destination.mkdir()
    started = time.monotonic()
    stat = archive.stat()
    archive_sha = digest(archive)
    with (destination / 'extraction.log').open('wb') as log:
        process = subprocess.Popen(['tar.exe', '-xf', str(archive), '-C', str(destination)],
                                   stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        while process.poll() is None:
            if shutil.disk_usage(destination).free < reserve:
                process.terminate()
                process.wait(timeout=10)
                raise RuntimeError('Extraction stopped to preserve disk reserve')
            time.sleep(1)
        if process.returncode:
            raise RuntimeError('Archive extraction failed; inspect private extraction.log')
    root = destination / 'nz'
    actual = {path.relative_to(root).as_posix() for path in root.rglob('*') if path.is_file()}
    if actual != {row['path'] for row in records}:
        raise ValueError('Extracted file set differs from archive')
    verified = []
    for index, row in enumerate(records, 1):
        verified.append(verify_file(root / row['path'], row))
        if index % 500 == 0:
            print(json.dumps({'crc_and_sha_verified': index, 'total': len(records)}), flush=True)
    if (archive.stat().st_size, archive.stat().st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
        raise ValueError('Source archive changed during extraction')
    manifest = destination / 'archive-manifest.json'
    manifest.write_text(json.dumps(verified, ensure_ascii=False, indent=2), encoding='utf-8')
    metadata = {'status': 'ARCHIVE_VERIFIED', 'workspace': str(root), 'source': str(original),
                'archive': str(archive), 'archive_sha256': archive_sha, 'archive_bytes': stat.st_size,
                'archive_mtime_ns': stat.st_mtime_ns, 'manifest': str(manifest), 'manifest_sha256': digest(manifest),
                'files': len(records), 'bytes': total, 'original_write_operations': 0,
                'seconds': round(time.monotonic() - started, 3)}
    (destination / 'nz.workspace.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in metadata.items() if key not in ('workspace', 'source', 'archive', 'manifest')}))


if __name__ == '__main__':
    main()
