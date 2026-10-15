"""在已验证工作副本上记录原生启动探针；进程存活或端口存在不算地图/战斗就绪。"""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import time
from tools.native_recovery import validate
from tools.native_job import JobGroup


def loaded_modules(pid):
    """只列举本次进程已装入模块的文件名，不读取玩家内存、窗口或其他进程。"""
    class Module(ctypes.Structure):
        """Windows MODULEENTRY32W 布局，同时容纳 x86 和 x64 模块枚举结果。"""
        _fields_ = [('dwSize', wintypes.DWORD), ('th32ModuleID', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
                    ('GlblcntUsage', wintypes.DWORD), ('ProccntUsage', wintypes.DWORD), ('modBaseAddr', ctypes.c_void_p),
                    ('modBaseSize', wintypes.DWORD), ('hModule', wintypes.HMODULE), ('szModule', wintypes.WCHAR * 256),
                    ('szExePath', wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Module32FirstW.argtypes = kernel.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Module)]
    kernel.Module32FirstW.restype = kernel.Module32NextW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(0x18, pid)
    if snapshot == ctypes.c_void_p(-1).value:
        return {'winerror': ctypes.get_last_error()}
    names = []
    try:
        entry = Module()
        entry.dwSize = ctypes.sizeof(entry)
        valid = kernel.Module32FirstW(snapshot, ctypes.byref(entry))
        while valid and len(names) < 1024:
            names.append(entry.szModule)
            valid = kernel.Module32NextW(snapshot, ctypes.byref(entry))
        return {'names': names}
    finally:
        kernel.CloseHandle(snapshot)


def pe_imports(path):
    """只读解析 PE 的机器类型和直接导入 DLL，不加载游戏模块或执行补丁。"""
    data = path.read_bytes()
    if data[:2] != b'MZ':
        raise ValueError('Not a PE image')
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    if data[pe:pe + 4] != b'PE\0\0':
        raise ValueError('Invalid PE signature')
    machine, sections = struct.unpack_from('<HH', data, pe + 4)
    optional_size = struct.unpack_from('<H', data, pe + 20)[0]
    optional = pe + 24
    magic = struct.unpack_from('<H', data, optional)[0]
    directory = optional + (96 if magic == 0x10b else 112 if magic == 0x20b else 0)
    if directory == optional:
        raise ValueError('Unknown PE optional header')
    import_rva = struct.unpack_from('<I', data, directory + 8)[0]
    ranges = []
    for index in range(sections):
        section = optional + optional_size + index * 40
        virtual_size, rva, raw_size, raw = struct.unpack_from('<IIII', data, section + 8)
        ranges.append((rva, max(virtual_size, raw_size), raw))

    def offset(rva):
        """把导入表 RVA 映射到磁盘偏移，越界或无法映射时停止解析。"""
        for start, length, raw in ranges:
            if start <= rva < start + length and raw + rva - start < len(data):
                return raw + rva - start
        raise ValueError('Unmapped PE import RVA')

    imports = []
    if import_rva:
        position = offset(import_rva)
        for _ in range(512):
            descriptor = struct.unpack_from('<IIIII', data, position)
            if not any(descriptor):
                break
            name = offset(descriptor[3])
            end = data.index(b'\0', name, min(len(data), name + 512))
            imports.append(data[name:end].decode('ascii'))
            position += 20
        else:
            raise ValueError('Too many import descriptors')
    return {'architecture': 'x86' if machine == 0x14c else 'x64' if machine == 0x8664 else hex(machine), 'imports': imports}


def main():
    """核对工作副本身份及入口哈希，限时启动客户端或地图探针并保存子进程退出码和新增日志。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--architecture', choices=['Win32', 'Win64'], required=True)
    parser.add_argument('--map', default='SV-Maya_3_Main')
    parser.add_argument('--seconds', type=int, default=25)
    parser.add_argument('--as-invoker', action='store_true', help='Probe without elevation; no system setting is changed')
    parser.add_argument('--visible', action='store_true', help='Show this explicitly requested diagnostic run')
    parser.add_argument('--mode', choices=['server', 'client'], default='server')
    parser.add_argument('--legacy-argv0', action='store_true', help='Reproduce the historical bare TGame argv0 probe')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.workspace.resolve(strict=True)
    metadata, recovery = validate(root)
    if args.output.resolve().is_relative_to(Path(metadata['source'])):
        raise ValueError('Output cannot be inside original client')
    if not args.map.replace('_', '').replace('-', '').isalnum() or not 1 <= args.seconds <= 300:
        raise ValueError('Invalid map or duration')
    binary = root / 'Binaries' / args.architecture / 'TGame.exe'
    original = Path(metadata['source']) / binary.relative_to(root)
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    if recovery:
        expected = next((row['after_sha256'] for row in recovery['files'] if row['path'] == binary.relative_to(root).as_posix()), None)
        if expected is None:
            expected = sha(original)
    else:
        expected = sha(original)
    if sha(binary) != expected:
        raise ValueError('Probe refuses an altered executable')
    before = {str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns) for path in root.rglob('*') if path.is_file()}
    args.output.mkdir(parents=True, exist_ok=False)
    command = ['TGame' if args.legacy_argv0 else str(binary)]
    if args.mode == 'server':
        command.extend(['server', args.map + '?game=PVEGame.TGSVGame', '-unattended', '-nosplash', '-nomoviestartup', '-log', '-FORCELOGFLUSH'])
    report = {'kind': 'CURRENT_CLIENT_NATIVE_PROBE', 'entry_sha256': sha(binary), 'command': [binary.name, *command[1:]],
              'argv0': 'LEGACY_BARE_TGAME' if args.legacy_argv0 else 'ABSOLUTE_EXECUTABLE_PATH', 'mode': args.mode,
              'execution_context': 'RUN_AS_INVOKER' if args.as_invoker else 'DEFAULT',
              'map_loaded_verified': False, 'spawn_verified': False,
              'network_ready_verified': False, 'retail_ready': False, 'original_client_writes': 0}
    if recovery and recovery.get('kind') == 'ARCHIVE_BASELINE':
        report['archive_baseline'] = {'archive_sha256': metadata['archive_sha256'], 'manifest_sha256': metadata['manifest_sha256'],
                                      'crc_verified_files': metadata['files']}
    elif recovery:
        report['recovery'] = {'receipt_sha256': metadata['recovery_receipt_sha256'],
                              'files': [{'path': row['path'], 'sha256': row['after_sha256']} for row in recovery['files']],
                              'source': 'USER_PROVIDED_ARCHIVE_RECOVERY_NOT_BINARY_PATCH'}
    started = time.monotonic()
    try:
        report['pe'] = pe_imports(binary)
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 5 if args.visible else 0
        with (args.output / 'stdout.bin').open('wb') as output, (args.output / 'stderr.bin').open('wb') as error:
            environment = os.environ.copy()
            if args.as_invoker:
                # 兼容标记只传给本次子进程：探测普通用户失败路径，不请求提权、不写系统配置。
                environment['__COMPAT_LAYER'] = 'RunAsInvoker'
            with JobGroup() as group:
                process = subprocess.Popen(command, executable=str(binary), cwd=binary.parent, stdin=subprocess.DEVNULL,
                                           stdout=output, stderr=error, startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW, env=environment)
                group.attach(process)
                report['process_id'] = process.pid
                report['containment'] = 'WINDOWS_JOB_KILL_ON_CLOSE_NO_UI_OR_RESOURCE_LIMITS'
                # 启动后立即保存 PID；父入口退出 0 不表示 GameLoader 派生的游戏已经结束或就绪。
                (args.output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
                deadline, observed = time.monotonic() + args.seconds, {process.pid}
                active = group.pids()
                while active and time.monotonic() < deadline:
                    observed.update(active)
                    time.sleep(0.1)
                    active = group.pids()
                report['root_exit_before_cleanup'] = process.poll()
                report['observed_process_ids'] = sorted(observed)
                report['timed_out'] = bool(active)
                if active:
                    report['loaded_modules_at_timeout'] = {str(pid): loaded_modules(pid) for pid in active}
                    group.terminate()
                process.wait(timeout=5)
                cleanup_deadline = time.monotonic() + 5
                while group.pids() and time.monotonic() < cleanup_deadline:
                    time.sleep(0.1)
                report['exit_code'] = process.returncode
                report['cleanup_verified'] = not group.pids()
                report['observed_exit_codes'] = {str(pid): {'decimal': code, 'hex': f'0x{code:08X}'}
                                                 for pid, code in group.collect_exits(timeout_ms=1000).items()}
                report['exit_query_errors'] = group.query_errors
    except OSError as error:
        report['start_error'] = {'winerror': error.winerror, 'errno': error.errno, 'message': str(error)}
    except ValueError as error:
        report['image_error'] = str(error)
    report['seconds'] = round(time.monotonic() - started, 3)
    report['changed_files'] = []
    for path in root.rglob('*'):
        if path.is_file():
            relative = str(path.relative_to(root))
            if before.get(relative) != (path.stat().st_size, path.stat().st_mtime_ns):
                report['changed_files'].append(relative)
                if path.suffix.lower() in ('.log', '.txt', '.dmp') and path.stat().st_size <= 10 * 1024 * 1024:
                    target = args.output / 'client-output' / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(path.read_bytes())
    report['conclusion'] = 'NATIVE_RUNTIME_NOT_VERIFIED'
    (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True))


if __name__ == '__main__':
    main()
