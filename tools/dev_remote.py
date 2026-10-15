"""用户明确指定 DEV 的 SSH 连接和只读盘点；密码不出现在命令行或报告。"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import paramiko
from tools.configure_user_mysql import read_credentials


def connect(source, known_hosts):
    """首次记录目标主机公钥，后续严格拒绝变更；此 TOFU 记录不代表第三方指纹认证。"""
    config = read_credentials(source, default_port=22)
    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    client = paramiko.SSHClient()
    if known_hosts.exists():
        client.load_host_keys(str(known_hosts))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(config['HOST'], port=config['PORT'], username=config['USER'], password=config['PASSWORD'],
                       timeout=10, auth_timeout=15, banner_timeout=15, look_for_keys=False, allow_agent=False)
        client.get_transport().set_keepalive(30)
        if not known_hosts.exists():
            client.save_host_keys(str(known_hosts))
        return client
    except Exception:
        client.close()
        raise


def execute(client, command, timeout=30):
    """执行固定维护命令并读取输出；非零状态由调用方处理，不在异常中回显命令或密码。"""
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    stdin.close()
    output = stdout.read().decode('utf-8', errors='replace')
    error = stderr.read().decode('utf-8', errors='replace')
    return stdout.channel.recv_exit_status(), output, error


def main():
    """只读盘点系统和已有项目目录；输出不包含网络地址、账号及原始配置。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    client = connect(args.source, root / 'runtime/dev-known-hosts')
    try:
        key = client.get_transport().get_remote_server_key()
        code, output, error = execute(client, "python3 - <<'PY'\nimport json,os,shutil,platform,pathlib\nprint(json.dumps({'system':platform.system(),'release':platform.release(),'python':platform.python_version(),'uid':os.getuid(),'os_release':pathlib.Path('/etc/os-release').read_text(),'disk_free':shutil.disk_usage('/opt').free,'tools':{name:shutil.which(name) for name in ('systemctl','mysql','mysqldump','gcc','pkg-config','apt-get','dnf')},'project_exists':pathlib.Path('/opt/nz-revival').exists(),'units':sorted(p.name for p in pathlib.Path('/etc/systemd/system').glob('nz-*'))}))\nPY")
        report = {'kind': 'DEV_READ_ONLY_INSPECTION', 'ssh_host_key_sha256': base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('='),
                  'host_key_trust': 'FIRST_CONNECTION_RECORDED_TOFU', 'exit_code': code,
                  'inspection': json.loads(output) if code == 0 else None, 'error': error if code else None}
        (root / 'evidence/m0-m5-dev-inspection.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report))
    finally:
        client.close()


if __name__ == '__main__':
    main()
