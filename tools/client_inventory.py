"""Read-only client fingerprints and candidate content, without executing binaries."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import struct
import tempfile
from collections import Counter
from pathlib import Path


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def pe_architecture(path: Path) -> str:
    with path.open('rb') as source:
        if source.read(2) != b'MZ':
            return 'NOT_PE'
        source.seek(0x3C)
        data = source.read(4)
        if len(data) != 4:
            return 'INVALID_PE'
        offset, = struct.unpack('<I', data)
        if offset > path.stat().st_size - 6:
            return 'INVALID_PE'
        source.seek(offset)
        if source.read(4) != b'PE\x00\x00':
            return 'INVALID_PE'
        machine, = struct.unpack('<H', source.read(2))
        return {0x14C: 'x86', 0x8664: 'x64', 0xAA64: 'arm64'}.get(machine, f'UNKNOWN_{machine:04x}')


def inventory(root: Path, output: Path) -> dict:
    root, output = root.resolve(strict=True), output.resolve()
    if not root.is_dir() or root == Path(root.anchor):
        raise ValueError('Client root must be a non-root directory')
    if output == root or root in output.parents:
        raise ValueError('Output must be outside the client directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    counts, maps, definitions, binaries = Counter(), [], [], []
    total, size = 0, 0
    # Write atomically; partial manifests must never look complete.
    handle, temporary = tempfile.mkstemp(prefix='manifest-', dir=output.parent)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8', newline='') as target:
            writer = csv.DictWriter(target, fieldnames=['path', 'bytes', 'sha256', 'type', 'architecture'])
            writer.writeheader()
            for base, dirs, files in os.walk(root, followlinks=False):
                dirs.sort()
                for directory in dirs:
                    if (Path(base) / directory).is_symlink() or (Path(base) / directory).is_junction():
                        raise ValueError('Reparse-point directory is not allowed')
                for name in sorted(files):
                    path = Path(base) / name
                    if path.is_symlink():
                        raise ValueError('Symlink file is not allowed')
                    before = path.stat()
                    relative = path.relative_to(root).as_posix()
                    suffix = path.suffix.lower()
                    fingerprint = digest(path)
                    after = path.stat()
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ValueError(f'File changed while scanning: {relative}')
                    architecture = pe_architecture(path) if suffix in {'.exe', '.dll'} else ''
                    row = dict(path=relative, bytes=before.st_size, sha256=fingerprint,
                               type=suffix or 'NO_EXTENSION', architecture=architecture)
                    writer.writerow(row)
                    counts[suffix] += 1
                    total += 1
                    size += before.st_size
                    if suffix in {'.udk', '.umap'}:
                        maps.append(dict(row, status='CANDIDATE', mode_id=None, player_limit=None))
                    if (suffix == '.bin' and '/Data/' in '/' + relative) or suffix == '.tdr':
                        definitions.append(dict(row, status='UNDECODED'))
                    if suffix == '.exe' and relative.startswith('Binaries/'):
                        binaries.append(row)
                    if total % 5000 == 0:
                        print(f'Scanned {total} files, {size / (1024 ** 3):.2f} GiB', flush=True)
        if not total:
            raise ValueError('Empty client directory')
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    summary = dict(files=total, bytes=size, extensions=dict(counts),
                   manifest_sha256=digest(output), authenticity='UNVERIFIED',
                   source='USER_PROVIDED_LOCAL_DIRECTORY', baseline='cn-current',
                   version_evidence='Separate version-evidence.json; hashes do not prove originality',
                   entries=binaries)
    output.with_suffix('.summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    output.with_suffix('.maps.json').write_text(json.dumps(maps, ensure_ascii=False, indent=2), encoding='utf-8')
    output.with_suffix('.definitions.json').write_text(json.dumps(definitions, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary


def compare(left: Path, right: Path) -> dict:
    def load(path):
        with path.open(encoding='utf-8-sig', newline='') as source:
            rows = list(csv.DictReader(source))
        index = {row['path']: row for row in rows}
        if len(index) != len(rows):
            raise ValueError('Duplicate manifest paths')
        return index
    old, new = load(left), load(right)
    return dict(added=sorted(new.keys() - old.keys()), removed=sorted(old.keys() - new.keys()),
                changed=sorted(key for key in old.keys() & new.keys()
                               if (old[key]['bytes'], old[key]['sha256']) != (new[key]['bytes'], new[key]['sha256'])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    scan = commands.add_parser('scan')
    scan.add_argument('root', type=Path)
    scan.add_argument('output', type=Path)
    diff = commands.add_parser('compare')
    diff.add_argument('left', type=Path)
    diff.add_argument('right', type=Path)
    args = parser.parse_args()
    result = inventory(args.root, args.output) if args.command == 'scan' else compare(args.left, args.right)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == 'compare' and any(result.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
