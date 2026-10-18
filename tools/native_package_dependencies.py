"""只读核验当前764/2资源包的名称和导入表；仅发布依赖元数据，不导出地图、贴图或脚本正文。

当前恢复副本的3E27A8FF/3E27A900包首部分别使用当前原生分支中的两种资源AES常量。
这里只解析完整AES块覆盖的已核对字段和LZO虚拟区间；首部尾部、导出对象及动态加载仍未验证。
格式参考UEViewer的UnPackage.cpp/UnPackage3.cpp，当前字段由实际偏移/名称表结束位置互证。
"""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
from pathlib import Path
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from tools.client_catalog import RESOURCE_KEY

PROFILE = 'cn2020-package-import-prefix-research-v1'
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TABLE_BYTES = 16 * 1024 * 1024
MAX_CHUNK_BYTES = 32 * 1024 * 1024
# boot55当前B8B60..B8BD4分支：FF用1F，00用55作为资源密钥的第六字节。
# 此常量只用于本地资源解码，绝不是账号、AUTH或网络会话凭据。
SCRIPT_RESOURCE_KEY = RESOURCE_KEY[:5] + b'\x55' + RESOURCE_KEY[6:]


class PackageInspectionError(ValueError):
    """以固定错误码保留未知、损坏或越界包；不猜字段，不回显包正文。"""


def exact_read(stream, offset, count, file_size):
    """在已知文件大小内精确读取有界区间，防止错误偏移和截断后静默解析。"""
    if offset < 0 or count < 0 or offset + count > file_size or count > MAX_CHUNK_BYTES:
        raise PackageInspectionError('PACKAGE_READ_OUT_OF_BOUNDS')
    stream.seek(offset)
    data = stream.read(count)
    if len(data) != count:
        raise PackageInspectionError('PACKAGE_TRUNCATED_DURING_READ')
    return data


def decompress_chunk(stream, offset, compressed_bytes, expected_bytes, file_size):
    """验证UE3块总量/子块边界后限长LZO解压；禁止按攻击性长度分配无限缓冲区。"""
    import lzokay
    data = exact_read(stream, offset, compressed_bytes, file_size)
    if len(data) < 16:
        raise PackageInspectionError('TRUNCATED_LZO_CHUNK_HEADER')
    magic, block_size, compressed, unpacked = struct.unpack_from('<IIII', data)
    if (magic != 0x9e2a83c1 or not 1 <= block_size <= 131072
            or unpacked != expected_bytes or not 1 <= unpacked <= MAX_CHUNK_BYTES):
        raise PackageInspectionError('UNSUPPORTED_LZO_CHUNK_HEADER')
    count = (unpacked + block_size - 1) // block_size
    position = 16 + count * 8
    if position > len(data) or compressed + position != len(data):
        raise PackageInspectionError('LZO_CHUNK_LENGTH_MISMATCH')
    result, total = [], 0
    for index in range(count):
        stored, clear = struct.unpack_from('<II', data, 16 + index * 8)
        # 除最后一块外必须满块；累计长度/压缩区间一致才能交给原生解压库。
        if (not 1 <= stored <= MAX_CHUNK_BYTES or clear != min(block_size, unpacked - total)
                or position + stored > len(data)):
            raise PackageInspectionError('LZO_SUBBLOCK_LENGTH_MISMATCH')
        try:
            block = lzokay.decompress(data[position:position + stored], clear)
        except Exception as error:
            raise PackageInspectionError('LZO_SUBBLOCK_DECODE_FAILED') from error
        if len(block) != clear:
            raise PackageInspectionError('LZO_SUBBLOCK_OUTPUT_MISMATCH')
        result.append(block)
        total += clear
        position += stored
    if position != len(data) or total != unpacked:
        raise PackageInspectionError('LZO_CHUNK_TOTAL_MISMATCH')
    return b''.join(result)


def import_table(path):
    """返回当前包内部导入根及对象引用；固定764/2布局，未知脚本变体直接保留错误。

    文件前后大小/时间及流式摘要用于冻结本次读取。只解压覆盖名称/导入表的块，不保存解压正文。
    未解出的首部尾部明确保留，不把导入表读取成功升级成完整依赖闭包或原生可加载证明。
    """
    path = Path(path)
    before = path.stat()
    if not 118 <= before.st_size <= MAX_FILE_BYTES:
        raise PackageInspectionError('UNSUPPORTED_PACKAGE_FILE_SIZE')
    with path.open('rb') as stream:
        tag, header_end = struct.unpack('<II', exact_read(stream, 0, 8, before.st_size))
        if tag not in (0x3e27a8ff, 0x3e27a900) or not 118 <= header_end <= 65535:
            raise PackageInspectionError('UNSUPPORTED_ENCRYPTED_PACKAGE_HEADER')
        cipher_count = (header_end - 8) // 16 * 16
        key = RESOURCE_KEY if tag == 0x3e27a8ff else SCRIPT_RESOURCE_KEY
        decoder = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        header = decoder.update(exact_read(stream, 8, cipher_count, before.st_size)) + decoder.finalize()
        if len(header) < 16 or struct.unpack_from('<I', header)[0] != 0x000202fc:
            raise PackageInspectionError('UNSUPPORTED_DECODED_PACKAGE_SUMMARY')
        group_length = struct.unpack_from('<i', header, 8)[0]
        delta = group_length - 5
        if (not 1 <= group_length <= 1024 or len(header) < 109 + delta
                or header[12 + group_length - 1] != 0
                or b'\0' in header[12:12 + group_length - 1]
                or struct.unpack_from('<I', header, 81 + delta)[0] != 1):
            raise PackageInspectionError('UNSUPPORTED_DECODED_PACKAGE_SUMMARY')
        header_size = struct.unpack_from('<I', header, 4)[0]
        name_count, name_offset, export_count, export_offset, count, offset = struct.unpack_from('<IIIIII', header, 21 + delta)
        table_end = offset + count * 28
        if (not 1 <= name_count <= 200000 or count > 200000 or export_count > 200000
                or not 118 <= name_offset <= offset <= table_end <= export_offset <= header_size
                or export_offset - name_offset > MAX_TABLE_BYTES):
            raise PackageInspectionError('PACKAGE_TABLE_BOUNDS_INVALID')
        compression = struct.unpack_from('<I', header, 105 + delta)[0]
        if compression == 0:
            tables = exact_read(stream, name_offset, export_offset - name_offset, before.st_size)
        elif compression == 2:
            if len(header) < 113 + delta:
                raise PackageInspectionError('COMPRESSED_SUMMARY_COUNT_UNAVAILABLE')
            chunks = struct.unpack_from('<I', header, 109 + delta)[0]
            if not 1 <= chunks <= 4096:
                raise PackageInspectionError('COMPRESSED_SUMMARY_COUNT_INVALID')
            ranges = []
            for index in range(chunks):
                start = 113 + delta + index * 16
                # AES首部末尾不完整的字段不能靠补零读取。只取覆盖所需表区的完整描述符。
                if start + 12 > len(header):
                    break
                logical, unpacked, physical = struct.unpack_from('<III', header, start)
                block_header = exact_read(stream, physical, 16, before.st_size)
                magic, block_size, compressed, chunk_unpacked = struct.unpack('<IIII', block_header)
                if (magic != 0x9e2a83c1 or not 1 <= block_size <= 131072
                        or chunk_unpacked != unpacked or not 1 <= unpacked <= MAX_CHUNK_BYTES):
                    raise PackageInspectionError('COMPRESSED_SUMMARY_CHUNK_INVALID')
                stored = 16 + 8 * ((unpacked + block_size - 1) // block_size) + compressed
                # 最后描述符的大小可能只有低三字节落在完整AES块内。用独立块头计算总长，
                # 必须与所有可见字节一致；不能补零或信任未知密文尾部。
                available = min(4, len(header) - start - 12)
                if header[start + 12:start + 12 + available] != struct.pack('<I', stored)[:available]:
                    raise PackageInspectionError('COMPRESSED_SUMMARY_STORED_SIZE_MISMATCH')
                if (not 1 <= unpacked <= MAX_CHUNK_BYTES or physical < header_end
                        or physical + stored > before.st_size or stored < 16):
                    raise PackageInspectionError('COMPRESSED_SUMMARY_CHUNK_INVALID')
                if logical < export_offset and logical + unpacked > name_offset:
                    clear = decompress_chunk(stream, physical, stored, unpacked, before.st_size)
                    begin, end = max(name_offset, logical), min(export_offset, logical + unpacked)
                    ranges.append((begin, end, clear[begin - logical:end - logical]))
            position, pieces = name_offset, []
            for begin, end, data in sorted(ranges):
                if begin != position:
                    raise PackageInspectionError('COMPRESSED_TABLE_GAP_OR_OVERLAP')
                pieces.append(data)
                position = end
            if position != export_offset:
                raise PackageInspectionError('COMPRESSED_TABLE_RANGE_UNAVAILABLE')
            tables = b''.join(pieces)
        else:
            raise PackageInspectionError('PACKAGE_COMPRESSION_UNVERIFIED')
        names, position = [], 0
        for _ in range(name_count):
            if position + 4 > offset - name_offset:
                raise PackageInspectionError('PACKAGE_NAME_COUNT_OUT_OF_RANGE')
            length = struct.unpack_from('<i', tables, position)[0]
            position += 4
            width = 2 if length < 0 else 1
            end = position + abs(length) * width
            if not 1 <= abs(length) <= 1024 or end + 8 > offset - name_offset:
                raise PackageInspectionError('PACKAGE_NAME_LENGTH_INVALID')
            try:
                text = tables[position:end].decode('utf-16-le' if width == 2 else 'utf-8', errors='strict')
            except UnicodeError as error:
                raise PackageInspectionError('PACKAGE_NAME_ENCODING_INVALID') from error
            if not text.endswith('\0') or '\0' in text[:-1]:
                raise PackageInspectionError('PACKAGE_NAME_TERMINATOR_INVALID')
            names.append(text[:-1])
            position = end + 8
        if position != offset - name_offset:
            raise PackageInspectionError('PACKAGE_NAME_TABLE_END_MISMATCH')
        imports, roots = [], []
        for index in range(count):
            cp, cpn, cl, cln, outer, ob, obn = struct.unpack_from('<iiiiiii', tables, offset - name_offset + index * 28)
            if (any(not 0 <= value < len(names) for value in (cp, cl, ob))
                    or min(cpn, cln, obn) < 0 or not -count <= outer <= export_count):
                raise PackageInspectionError('PACKAGE_IMPORT_REFERENCE_INVALID')
            entry = dict(class_package=names[cp], class_name=names[cl], outer=outer,
                         object_name=names[ob], object_number=obn)
            imports.append(entry)
            if outer == 0 and names[cp] == 'Core' and names[cl] == 'Package' and obn == 0:
                roots.append(names[ob])
        stream.seek(0)
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise PackageInspectionError('PACKAGE_CHANGED_DURING_READ')
    return dict(profile=PROFILE, status='IMPORT_TABLE_PREFIX_VERIFIED', sha256=sha, bytes=after.st_size,
                version=764, licensee_version=2, compression=compression, names=name_count,
                imports_count=count, exports_count=export_count, root_packages=sorted(set(roots)),
                imports=imports, summary_tail_verified=False, export_objects_verified=False,
                dynamic_dependencies_verified=False, native_loaded=False)


def inventory(root, seeds, max_packages=1024):
    """按真实导入表递归盘点已提供种子；缺失、重名和未知格式均保留，结果始终是研究清单。"""
    root = Path(root).resolve(strict=True)
    if type(max_packages) is not int or not 1 <= max_packages <= 4096:
        raise PackageInspectionError('INVALID_PACKAGE_TRAVERSAL_LIMIT')
    cooked = (root / 'TGame/CookedPC').resolve(strict=True)
    lookup = {}
    for path in cooked.rglob('*'):
        if path.suffix.lower() in ('.u', '.udk', '.upk') and path.is_file():
            lookup.setdefault(path.stem.casefold(), []).append(path)
    pending = deque(seeds)
    seen, rows = set(), []
    while pending:
        name = pending.popleft()
        if not isinstance(name, str) or not name or any(char in name for char in '/\\:'):
            raise PackageInspectionError('INVALID_PACKAGE_SEED_OR_REFERENCE')
        if name.casefold() in seen:
            continue
        if len(seen) >= max_packages:
            pending.appendleft(name)
            break
        seen.add(name.casefold())
        matches = lookup.get(name.casefold(), [])
        row = dict(package=name)
        if not matches:
            row['status'] = 'MISSING'
        elif len(matches) != 1:
            row.update(status='AMBIGUOUS', paths=sorted(path.relative_to(root).as_posix() for path in matches))
        else:
            path = matches[0]
            if not path.resolve().is_relative_to(cooked):
                raise PackageInspectionError('PACKAGE_PATH_OUTSIDE_COOKED_ROOT')
            row['path'] = path.relative_to(root).as_posix()
            try:
                result = import_table(path)
                pending.extend(result['root_packages'])
                result.pop('imports')  # 公共清单只需包名，不收集完整对象表。
                row.update(result)
            except PackageInspectionError as error:
                row.update(status=str(error), bytes=path.stat().st_size)
        rows.append(row)
    remaining = sorted({name for name in pending if name.casefold() not in seen}, key=str.casefold)
    return dict(kind='CURRENT_PACKAGE_IMPORT_RESEARCH', profile=PROFILE, seeds=list(seeds), packages=rows,
                traversal_complete=not remaining, unexpanded_packages=remaining,
                import_roots_resolved=not remaining and all(row['status'] == 'IMPORT_TABLE_PREFIX_VERIFIED' for row in rows),
                dependency_closure_verified=False, native_loaded=False,
                limits=['Summary tail, export objects and dynamic loads remain unverified'])


def main():
    """读取恢复副本并新建JSON清单，拒绝覆盖旧证据或在客户端内部输出。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--packages', nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-packages', type=int, default=1024)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.is_relative_to(args.workspace.resolve()) or output.exists():
        raise ValueError('PACKAGE_OUTPUT_MUST_BE_NEW_AND_OUTSIDE_CLIENT')
    result = inventory(args.workspace, args.packages, args.max_packages)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(packages=len(result['packages']), dependency_closure_verified=False, native_loaded=False)))


if __name__ == '__main__':
    main()
