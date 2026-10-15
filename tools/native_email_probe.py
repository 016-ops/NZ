"""在独立 32 位进程自测当前 TenProxy 的本地身份读取，不启动游戏或改变原客户端。"""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
from adapters.cn_current.email_ipc import IPC_PROFILE, LOOPBACK_IPC_PROFILE, PROXY_SHA256, local_session_id, research_mapping
from tools.client_workspace import digest
from tools.native_job import JobGroup
from tools.native_recovery import validate


def probe(workspace, game_id, game_token, *, prefix='TCLS_SHAREDMEMEMORY', private_error_path=None,
          loopback_port=None, client_path=''):
    """校验当前 DLL 后运行自测；实际游戏令牌只经 stdin 传输，结果不含令牌或原始登录结构。"""
    root = workspace.resolve(strict=True)
    validate(root)
    dll = root / 'Binaries/Win32/TenProxy.dll'
    if digest(dll) != PROXY_SHA256:
        raise ValueError('Current proxy fingerprint differs; IPC research offsets are not applicable')
    if prefix not in ('TCLS_SHAREDMEMEMORY', 'TCLS_SHAREDMEMEMORY+'):
        raise ValueError('Only current-process research mapping names are allowed')
    powershell = Path(os.environ['SystemRoot']) / 'SysWOW64/WindowsPowerShell/v1.0/powershell.exe'
    script = Path(__file__).with_name('native_email_proxy.ps1')
    payload = json.dumps({'mapping': base64.b64encode(research_mapping(game_id, game_token, loopback_port=loopback_port,client_path=client_path)).decode(),
                          'session_id': base64.b64encode(local_session_id(game_token)).decode(),
                          'game_id': game_id, 'game_token': game_token, 'prefix': prefix, 'loopback_port':loopback_port or 0})
    environment = os.environ.copy()
    # 从 PowerShell 7 启动 Windows PowerShell 5.1 时，继承的模块搜索路径可能缺少系统 32 位模块。
    # 仅修正本次子进程，保留哈希与签名门禁，不降低系统执行策略或写入用户配置。
    environment['PSModulePath'] = str(powershell.parent / 'Modules')
    with JobGroup() as group:
        process = subprocess.Popen([str(powershell), '-NoProfile', '-NonInteractive', '-File', str(script), '-DllPath', str(dll)],
                                   cwd=dll.parent, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=subprocess.CREATE_NO_WINDOW, env=environment)
        group.attach(process)
        try:
            output, error = process.communicate(payload.encode('ascii'), timeout=30)
        except subprocess.TimeoutExpired:
            group.terminate()
            process.communicate(timeout=5)
            return {'kind': 'NATIVE_EMAIL_IPC_SELF_TEST', 'timed_out': True, 'ipc_read_verified': False, 'native_launch_ready': False}
        group.terminate()
        if process.returncode != 0:
            # 非托管失败可能带输入材料，禁止将 stderr 直接变成公开报告或 traceback。
            if private_error_path is not None:
                private_root = Path(__file__).resolve().parents[1] / 'evidence/private'
                if not private_error_path.resolve().is_relative_to(private_root) or private_error_path.exists():
                    raise ValueError('Error diagnostics require a new private evidence path')
                private_error_path.parent.mkdir(parents=True, exist_ok=True)
                private_error_path.write_bytes(error)
            return {'kind': 'NATIVE_EMAIL_IPC_SELF_TEST', 'exit_code': process.returncode,
                    'probe_error': 'SELF_TEST_PROCESS_FAILED', 'ipc_read_verified': False, 'native_launch_ready': False}
        lines = output.decode('utf-8', errors='replace').splitlines()
        records = []
        for line in lines:
            try:
                value = json.loads(line)
                if isinstance(value, dict) and 'FactoryCreated' in value:
                    records.append(value)
            except ValueError:
                continue
        cleanup = not group.pids()
        verified = len(records) == 1 and cleanup and all(records[0].get(key) is True for key in (
            'FactoryCreated', 'LoginInfoAccepted', 'LocalTokenMatches', 'LocalSessionIdMatches', 'OutputGuardIntact', 'ServerRecordVerified'))
        return {'kind': 'NATIVE_EMAIL_IPC_SELF_TEST', 'proxy_sha256': PROXY_SHA256,
                'ipc_profile': LOOPBACK_IPC_PROFILE if loopback_port is not None else IPC_PROFILE, 'exit_code': process.returncode,
                'observations': records, 'prefix_candidate': prefix, 'process_tree_cleanup': not group.pids(),
                'ipc_read_verified': verified, 'native_launch_ready': False, 'game_process_started': False, 'qq_credentials_used': False}


def main():
    """使用明确的本地研究身份运行自测，输出新证据文件，拒绝写入原件或工作客户端。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--prefix', choices=['TCLS_SHAREDMEMEMORY', 'TCLS_SHAREDMEMEMORY+'], default='TCLS_SHAREDMEMEMORY')
    args = parser.parse_args()
    root = args.workspace.resolve(strict=True)
    metadata, _ = validate(root)
    if args.output.exists() or any(args.output.resolve().is_relative_to(path) for path in (root, Path(metadata['source']))):
        raise ValueError('Output must be new and outside both client directories')
    private_error = args.output.with_suffix('.stderr.txt') if args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1] / 'evidence/private') else None
    result = probe(root, 10001, 'NZ_LOCAL_RESEARCH_ONLY_NO_QQ_CREDENTIAL', prefix=args.prefix, private_error_path=private_error)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result))
    raise SystemExit(0 if result.get('ipc_read_verified') else 1)


if __name__ == '__main__':
    main()
