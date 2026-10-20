"""在Windows构建便携DEV Launcher：Tauri窗口、独立Python本机服务、React和公开目录。

默认产物留在忽略的launcher/bin；没有客户端原件、SSH配置、数据库或开发机凭据。
本脚本只构建本地包，不上传、不签名、不部署。Mac/Linux仅可使用--plan核对构建合同。
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
import zipfile

from tools.launcher_runtime import PACKAGE_VERSION, validate_public_config


ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    """流式计算实际交付文件摘要，不记录文件内容或开发机秘密。"""
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def public_config(api_base_url, loopback_host_port=None):
    """只生成受限公开连接参数；发行包无法接收SSH密码、令牌或客户端路径。"""
    config = {'schema_version': PACKAGE_VERSION, 'api_base_url': api_base_url}
    if loopback_host_port is not None:
        config['loopback_host_port'] = loopback_host_port
    return validate_public_config(config)


def package_plan(config):
    """提供可审查的固定打包输入和能力边界，--plan不会创建目录或运行编译。"""
    validate_public_config(config)
    return {'schema_version': PACKAGE_VERSION, 'target': 'windows-x86_64',
            'inputs': ['launcher/target/release/nz-launcher.exe', 'tools/launcher_runtime.py',
                       'admin/dist', 'config/<git-tracked-json>', 'launcher/licenses'],
            'layout': ['NZLauncher.exe', 'launcher-config.json', 'service/NZLauncherService.exe',
                       'service/_internal/admin/dist', 'service/_internal/config', 'package-manifest.json'],
            'api_base_url': config['api_base_url'], 'state_directory': '%LOCALAPPDATA%/NZRevivalLauncher',
            'native_start_available': False, 'native_blocker': 'PACKAGE_NATIVE_CLIENT_UNAVAILABLE',
            'excluded': ['SSH credentials', '.env*', 'runtime', 'evidence/private', 'raw client',
                         'databases', '.venv', 'node_modules', 'Git metadata'],
            'verification': 'WINDOWS_BUILD_AND_CLEAN_MACHINE_ACCEPTANCE_REQUIRED'}


def verify_payload(path):
    """拒绝符号链接和常见私人材料路径；清单只覆盖本脚本显式组装的发行文件。"""
    forbidden_parts = {'.git', '.venv', 'node_modules', 'runtime', 'private', '.ssh', '.aws'}
    files = []
    for item in sorted(path.rglob('*')):
        relative = item.relative_to(path)
        if item.is_symlink() or any(part.lower() in forbidden_parts for part in relative.parts):
            raise ValueError('PRIVATE_OR_LINKED_PACKAGE_INPUT')
        if not item.is_file():
            continue
        name = item.name.lower()
        if (name.startswith('.env') or item.suffix.lower() in {'.pem', '.key', '.db', '.sqlite', '.sqlite3'}
                or name in {'dev环境服务器.txt', '房间服务器配置.txt', 'user-mysql.env', 'aliyun-mail.env'}
                or 'known-hosts' in name):
            raise ValueError('PRIVATE_PACKAGE_INPUT')
        files.append({'path': relative.as_posix(), 'bytes': item.stat().st_size, 'sha256': digest(item)})
    return files


def smoke_service(output):
    """Windows实际启动冻结服务并发送close；不登录或连接API，只确认资源/依赖/管道可用。

    临时AppData只属于该验收进程，stdout中的随机本机能力仅在内存解析，不写清单。
    失败使用固定错误码；仅输出退出码、解析状态、安全日志码和缺失模块名，
    不能把原始管道能力、完整 stderr 或用户目录回显到构建产物。
    """
    import os
    with tempfile.TemporaryDirectory(prefix='nz-launcher-smoke-') as directory:
        environment = dict(os.environ, LOCALAPPDATA=directory)
        started = time.monotonic()
        result, bootstrap_parsed = None, False
        try:
            result = subprocess.run([str(output / 'service/NZLauncherService.exe'), '--desktop'],
                cwd=output, input=b'close\n', stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=45, check=False, env=environment)
            message = json.loads(result.stdout.decode('utf-8-sig').splitlines()[0])
            bootstrap_parsed = isinstance(message, dict)
            address = urlsplit(message['url'])
            access = address.fragment.removeprefix('access=')
            if (result.returncode != 0 or message.get('version') != 'cn2020-launcher-v1'
                    or address.scheme != 'http' or address.hostname != '127.0.0.1' or address.port != 18769
                    or address.path != '/launcher' or address.query or address.username or address.password
                    or not address.fragment.startswith('access=') or len(access) != 43
                    or any(not (value.isascii() and (value.isalnum() or value in '_-')) for value in access)):
                raise ValueError('Invalid bootstrap')
        except (OSError, ValueError, TypeError, KeyError, IndexError, subprocess.TimeoutExpired):
            # 诊断只从严格白名单字段构造；保留随机能力的 stdout 绝不输出。
            print(json.dumps({'frozen_service_smoke': smoke_failure_details(
                Path(directory), result, bootstrap_parsed)}, ensure_ascii=True), file=sys.stderr)
            raise RuntimeError('FROZEN_SERVICE_BOOTSTRAP_SMOKE_FAILED') from None
    return {'bootstrap_close_passed': True, 'api_login_executed': False,
            'seconds': round(time.monotonic() - started, 3)}


def smoke_failure_details(directory, process, bootstrap_parsed):
    """从进程与私有临时日志提取固定诊断；拒绝路径、URL、异常全文和过大日志。"""
    details = {'exit_code': None, 'bootstrap_parsed': bool(bootstrap_parsed),
               'startup_error_code': None, 'stderr_missing_module': None}
    if process is not None:
        if type(process.returncode) is int:
            details['exit_code'] = process.returncode
        raw = process.stderr
        if isinstance(raw, bytes):
            raw = raw.decode('utf-8', errors='replace')
        if isinstance(raw, str):
            # 只匹配标准异常末行的单一模块标识符，不能回显别的 stderr 内容。
            missing = re.search(r"(?m)^ModuleNotFoundError: No module named '"
                                r"([A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*)'\r?$", raw)
            if missing and len(missing.group(1)) <= 192:
                details['stderr_missing_module'] = missing.group(1)
    path = directory / 'NZRevivalLauncher/runtime/native-launcher-error.log'
    if path.is_file() and path.stat().st_size <= 256:
        try:
            code = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            code = ''
        if re.fullmatch(r'(?:[A-Z][A-Z_0-9]{0,63}|IMPORT_MISSING_'
                        r'[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*)', code):
            details['startup_error_code'] = code
    return details


def build(output, config, *, skip_frontend=False):
    """执行锁定前端/Rust与PyInstaller onedir构建，组装新目录并逐文件生成摘要后压缩。"""
    if sys.platform != 'win32':
        raise RuntimeError('WINDOWS_BUILD_HOST_REQUIRED')
    output = output.resolve()
    archive = output.with_suffix('.zip')
    if output.exists() or archive.exists():
        raise FileExistsError('Use a new package output directory')
    if output == ROOT or output in ROOT.parents:
        raise ValueError('PACKAGE_OUTPUT_MUST_NOT_REPLACE_REPOSITORY')
    build_root = ROOT / 'runtime/launcher-package'
    build_root.mkdir(parents=True, exist_ok=True)
    if not skip_frontend:
        subprocess.run(['npm.cmd', '--prefix', 'admin', 'ci'], cwd=ROOT, check=True)
        subprocess.run(['npm.cmd', '--prefix', 'admin', 'run', 'build'], cwd=ROOT, check=True)
    if not (ROOT / 'admin/dist/index.html').is_file():
        raise RuntimeError('FRONTEND_BUILD_REQUIRED')
    subprocess.run(['cargo', 'test', '--manifest-path', 'launcher/Cargo.toml', '--release', '--locked'],
                   cwd=ROOT, check=True)
    subprocess.run(['cargo', 'build', '--manifest-path', 'launcher/Cargo.toml', '--release', '--locked'],
                   cwd=ROOT, check=True)
    freeze = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--console',
              '--name', 'NZLauncherService', '--distpath', str(build_root / 'dist'),
              '--workpath', str(build_root / 'work'), '--specpath', str(build_root), '--paths', str(ROOT),
              '--add-data', str(ROOT / 'admin/dist') + ';admin/dist',
              '--collect-submodules', 'adapters', '--hidden-import', 'tools.native_launcher',
              '--hidden-import', 'tools.native_launcher_window', '--hidden-import', 'django.urls',
              '--hidden-import', 'django.core.wsgi', '--exclude-module', 'paramiko',
              '--exclude-module', 'tools.dev_remote', '--exclude-module', 'tools.dev_tunnel',
              '--exclude-module', 'MySQLdb', '--exclude-module', 'nzproject',
              '--additional-hooks-dir', str(ROOT / 'tools/launcher_hooks')]
    # 只添加版本控制登记的公开JSON，不能把操作者临时放入config的私人文件带入发行包。
    tracked = subprocess.run(['git', 'ls-files', '--', 'config/*.json'], cwd=ROOT, check=True,
                             capture_output=True, text=True).stdout.splitlines()
    if not tracked:
        raise RuntimeError('PUBLIC_CONFIG_RESOURCES_MISSING')
    for relative in tracked:
        resource = ROOT / relative
        if resource.is_symlink() or resource.suffix != '.json' or resource.parent != ROOT / 'config':
            raise ValueError('UNEXPECTED_CONFIG_RESOURCE')
        freeze.extend(['--add-data', str(resource) + ';config'])
    freeze.append(str(ROOT / 'tools/launcher_runtime.py'))
    subprocess.run(freeze, cwd=ROOT, check=True)
    service = build_root / 'dist/NZLauncherService'
    if not (service / 'NZLauncherService.exe').is_file():
        raise RuntimeError('FROZEN_SERVICE_MISSING')
    output.mkdir(parents=True)
    shutil.copyfile(ROOT / 'launcher/target/release/nz-launcher.exe', output / 'NZLauncher.exe')
    shutil.copytree(service, output / 'service')
    if (ROOT / 'launcher/licenses').is_dir():
        shutil.copytree(ROOT / 'launcher/licenses', output / 'licenses')
    (output / 'launcher-config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    instructions = ('逆战 Launcher DEV 便携包\n\n双击 NZLauncher.exe，使用自己的邮箱登录。\n'
                    '此包无需开发仓库或系统 Python。Windows 需安装 Microsoft Edge WebView2 Runtime。\n'
                    'launcher-config.json 仅存公开 HTTPS 地址或操作者预建的回环隧道；不包含 SSH 凭据。\n'
                    '当前包用于平台登录、仓库、商城与活动；完整游戏客户端和原生实玩另行验收，启动会明确阻塞。\n'
                    '运行记录位于 %LOCALAPPDATA%\\NZRevivalLauncher\\runtime。\n'
                    '此包未签名；真正 Windows 构建与干净机器验收以交付记录为准。\n')
    (output / '使用说明.txt').write_text(instructions, encoding='utf-8')
    smoke = smoke_service(output)
    manifest = dict(package_plan(config), service_smoke=smoke, files=verify_payload(output))
    (output / 'package-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
        for item in sorted(output.rglob('*')):
            if item.is_file():
                bundle.write(item, arcname=output.name + '/' + item.relative_to(output).as_posix())
    return {'package': str(output), 'archive': str(archive), 'archive_sha256': digest(archive),
            'native_start_available': False, 'windows_clean_machine_verified': False}


def main():
    """解析操作者的公开网关配置；计划只输出，实际构建必须在Windows明确执行。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-base-url', required=True)
    parser.add_argument('--loopback-host-port', type=int)
    parser.add_argument('--output', type=Path, default=ROOT / 'launcher/bin/NZLauncher-DEV')
    parser.add_argument('--skip-frontend', action='store_true')
    parser.add_argument('--plan', action='store_true')
    args = parser.parse_args()
    config = public_config(args.api_base_url, args.loopback_host_port)
    result = package_plan(config) if args.plan else build(args.output, config, skip_frontend=args.skip_frontend)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
