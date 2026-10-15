"""只把已检查的归档 Win32 入口/核心库恢复到登记工作副本；保存原字节与可验证恢复凭据。"""
import argparse
import json
from pathlib import Path
import uuid
from tools.client_workspace import checked_path, copy_verified, digest
from tools.native_image_audit import inspect

ALLOWED = {'Binaries/Win32/TGame.exe', 'Binaries/Win32/TGameBase.dll',
           'Binaries/Win32/GameLoader.exe', 'Binaries/Win32/WebBrowserControl.dll'}


def validate(root):
    """普通副本保留原登记规则；恢复副本必须逐文件匹配凭据、原件和备份摘要。"""
    root = root.resolve()
    metadata = json.loads((root.parent / (root.name + '.workspace.json')).read_text(encoding='utf-8'))
    if Path(metadata['workspace']) != root:
        raise ValueError('Workspace identity mismatch')
    if metadata['status'] == 'VERIFIED':
        return metadata, None
    if metadata['status'] == 'ARCHIVE_VERIFIED':
        archive, manifest = Path(metadata['archive']), Path(metadata['manifest'])
        if ((archive.stat().st_size, archive.stat().st_mtime_ns) != (metadata['archive_bytes'], metadata['archive_mtime_ns'])
                or digest(manifest) != metadata['manifest_sha256']):
            raise ValueError('Archive baseline identity changed')
        files = json.loads(manifest.read_text(encoding='utf-8'))
        return metadata, {'kind': 'ARCHIVE_BASELINE', 'files': [{'path': row['path'], 'after_sha256': row['sha256']} for row in files]}
    if metadata['status'] != 'RECOVERY_OVERLAY':
        raise ValueError('Workspace is not ready for a native probe')
    receipt_path = Path(metadata['recovery_receipt'])
    if digest(receipt_path) != metadata['recovery_receipt_sha256']:
        raise ValueError('Recovery receipt hash mismatch')
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    if receipt['state'] != 'APPLIED' or receipt['workspace'] != str(root) or receipt['original'] != metadata['source']:
        raise ValueError('Recovery receipt identity mismatch')
    if not receipt['files'] or len({row['path'] for row in receipt['files']}) != len(receipt['files']):
        raise ValueError('Empty or duplicate recovery records')
    for row in receipt['files']:
        if row['path'] not in ALLOWED:
            raise ValueError('Unexpected recovered path')
        original = checked_path(Path(metadata['source']), row['path'])
        target = checked_path(root, row['path'])
        if digest(original) != row['before_sha256'] or digest(target) != row['after_sha256'] or digest(Path(row['backup'])) != row['before_sha256']:
            raise ValueError('Recovered file, baseline or backup changed')
    return metadata, receipt


def apply(root, incoming, relative_paths, receipt_path):
    """先备份全部旧字节并持久记录，再替换已通过格式检查的原归档文件；原件无写操作。"""
    root, incoming, receipt_path = root.resolve(), incoming.resolve(), receipt_path.resolve()
    metadata, previous = validate(root)
    if metadata['status'] == 'ARCHIVE_VERIFIED':
        raise ValueError('Use the coherent archive baseline directly; do not mix an overlay into it')
    original = Path(metadata['source'])
    if (not relative_paths or len(set(relative_paths)) != len(relative_paths) or set(relative_paths) - ALLOWED
            or incoming.is_relative_to(root) or incoming.is_relative_to(original)
            or receipt_path.is_relative_to(root) or receipt_path.is_relative_to(original) or receipt_path.exists()):
        raise ValueError('Recovery requires allowed Win32 paths and independent new receipt')
    rows = {row['path']: row for row in previous['files']} if previous else {}
    copies = []
    backup_root = root.parent / 'native-recovery' / 'backups' / uuid.uuid4().hex
    for relative in relative_paths:
        source, target = checked_path(incoming, relative), checked_path(root, relative)
        restored = inspect(source)
        if restored.get('image_error') or restored['pe']['issues'] or not restored['windows_nonexecuting_mapping'].get('mapping_created', True):
            raise ValueError('Recovery source is not a valid PE image')
        baseline = checked_path(original, relative)
        before = digest(baseline)
        if relative not in rows:
            if digest(target) != before:
                raise ValueError('Unregistered target modifications')
            backup = backup_root / relative
            copy_verified(target, backup, {'path': relative, 'bytes': target.stat().st_size, 'sha256': before})
            rows[relative] = {'path': relative, 'before_sha256': before, 'before_bytes': target.stat().st_size,
                              'backup': str(backup)}
        rows[relative].update(after_sha256=restored['sha256'], after_bytes=restored['bytes'], recovery_source=str(source))
        copies.append((source, target, {'path': relative, 'bytes': restored['bytes'], 'sha256': restored['sha256']}))
    receipt = {'state': 'PREPARED', 'workspace': str(root), 'original': str(original),
               'files': list(rows.values()), 'original_write_operations': 0, 'binary_patches': False}
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    metadata_path = root.parent / (root.name + '.workspace.json')
    metadata.update(status='RECOVERY_INCOMPLETE', recovery_receipt=str(receipt_path), recovery_receipt_sha256=digest(receipt_path))
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    for source, target, expected in copies:
        copy_verified(source, target, expected)
    receipt['state'] = 'APPLIED'
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    metadata.update(status='RECOVERY_OVERLAY', recovery_receipt_sha256=digest(receipt_path))
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    validate(root)
    return receipt


def main():
    """默认只恢复截断核心库；明确开关才一并恢复归档中的匹配 EXE，结果不等于游戏就绪。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--restore-executable', action='store_true')
    parser.add_argument('--restore-launcher-dependencies', action='store_true')
    args = parser.parse_args()
    paths = ['Binaries/Win32/TGameBase.dll']
    if args.restore_executable:
        paths.append('Binaries/Win32/TGame.exe')
    if args.restore_launcher_dependencies:
        paths.extend(['Binaries/Win32/GameLoader.exe', 'Binaries/Win32/WebBrowserControl.dll'])
    receipt = apply(args.workspace, args.source, paths, args.receipt)
    print(json.dumps({'state': receipt['state'], 'recovered': paths, 'original_write_operations': 0, 'native_ready_verified': False}))


if __name__ == '__main__':
    main()
