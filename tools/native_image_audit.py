"""只读检查 PE 磁盘范围，并用 Windows 非执行映像映射验证格式；不调用 DLL 初始化。"""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import struct


def layout(data, file_size=None):
    """解析有限 PE 头并检查节原始数据范围；BSS 的虚拟补零不能用于补造缺失磁盘内容。"""
    size = len(data) if file_size is None else file_size
    if len(data) < 64 or data[:2] != b'MZ':
        raise ValueError('NOT_PE')
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    if pe < 64 or pe + 24 > len(data) or data[pe:pe + 4] != b'PE\0\0':
        raise ValueError('INVALID_PE_HEADER')
    machine, count = struct.unpack_from('<HH', data, pe + 4)
    optional_size = struct.unpack_from('<H', data, pe + 20)[0]
    optional = pe + 24
    if not 1 <= count <= 96 or optional_size < 64 or optional + optional_size + count * 40 > len(data):
        raise ValueError('INVALID_SECTION_TABLE')
    magic = struct.unpack_from('<H', data, optional)[0]
    if magic not in (0x10b, 0x20b):
        raise ValueError('INVALID_OPTIONAL_HEADER')
    rows, issues = [], []
    if (machine, magic) not in ((0x14c, 0x10b), (0x8664, 0x20b), (0xaa64, 0x20b)):
        issues.append('MACHINE_OPTIONAL_HEADER_MISMATCH')
    for index in range(count):
        start = optional + optional_size + index * 40
        name = data[start:start + 8].split(b'\0')[0].decode('ascii', errors='replace')
        virtual_size, rva, raw_size, raw = struct.unpack_from('<IIII', data, start + 8)
        beyond = max(0, raw + raw_size - size) if raw_size else 0
        if beyond:
            issues.append('SECTION_RAW_DATA_BEYOND_EOF:' + name)
        rows.append({'name': name, 'rva': rva, 'virtual_size': virtual_size, 'raw_offset': raw,
                     'raw_size': raw_size, 'bytes_beyond_eof': beyond})
    return {'machine': hex(machine), 'optional_magic': hex(magic), 'sections': rows, 'issues': issues,
            'required_raw_end': max(row['raw_offset'] + row['raw_size'] for row in rows if row['raw_size']) if any(row['raw_size'] for row in rows) else 0}


def image_mapping(path):
    """CreateFileMapping SEC_IMAGE_NO_EXECUTE 只创建非执行映像对象；关闭句柄，不运行入口或 DllMain。"""
    if os.name != 'nt':
        return {'executed': False, 'status': 'NOT_WINDOWS_NOT_RUN'}
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CreateFileMappingW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR]
    kernel.CreateFileMappingW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path.resolve()), 0x80000000, 7, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        return {'executed': False, 'opened': False, 'winerror': ctypes.get_last_error()}
    mapping = None
    try:
        mapping = kernel.CreateFileMappingW(handle, None, 0x11000002, 0, 0, None)
        return {'executed': False, 'opened': True, 'mapping_created': bool(mapping),
                'winerror': 0 if mapping else ctypes.get_last_error()}
    finally:
        if mapping:
            kernel.CloseHandle(mapping)
        kernel.CloseHandle(handle)


def inspect(path):
    """摘要覆盖完整文件，布局只读取有限头；全零判断覆盖整个文件且只读。"""
    with path.open('rb') as stream:
        digest, zero = hashlib.sha256(), True
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
            if zero and any(chunk):
                zero = False
    with path.open('rb') as stream:
        head = stream.read(1024 * 1024)
    result = {'bytes': path.stat().st_size, 'sha256': digest.hexdigest(), 'entirely_zero_filled': zero,
              'windows_nonexecuting_mapping': image_mapping(path)}
    try:
        result['pe'] = layout(head, path.stat().st_size)
    except ValueError as error:
        result['image_error'] = str(error)
    return result


def main():
    """逐文件输出无绝对路径报告；必须使用新输出文件，避免覆盖先前失败证据。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs='+', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--client-root', type=Path, default=Path(__file__).resolve().parents[2] / 'nz')
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.client_root.resolve()):
        raise ValueError('Audit output must be outside the original client')
    report = {'kind': 'READ_ONLY_NATIVE_IMAGE_AUDIT', 'files': [dict(name=path.name, **inspect(path)) for path in args.files]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(report))


if __name__ == '__main__':
    main()
