"""将经过本机检查的控制面发布到用户指定 DEV；不上传游戏文件或私人目录 SQL。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import tarfile
from dotenv import dotenv_values
from tools.dev_remote import connect, execute


def package(root):
    """固定业务源码和构建产物白名单；包中不包含数据库、凭据、客户端及依赖缓存。"""
    files = []
    for name in ('revival', 'nzproject', 'adapters', 'config', 'admin/dist'):
        files.extend(path for path in (root / name).rglob('*') if path.is_file()
                     and '__pycache__' not in path.parts and path.suffix != '.pyc')
    files.extend(root / name for name in ('manage.py', 'requirements.txt', 'pyproject.toml'))
    if not (root / 'admin/dist/index.html').is_file():
        raise ValueError('Build the frontend before deployment')
    archive = root / 'runtime/dev-release.tar.gz'
    with tarfile.open(archive, 'w:gz') as bundle:
        for path in sorted(files):
            bundle.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
    with archive.open('rb') as stream:
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    return archive, sha, len(files)


def main():
    """上传固定发布包与受保护环境文件，远端安装日志仅保存在忽略目录，报告剔除连接信息。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--mail-env-file', type=Path, help='Optional private Aliyun SMTP settings; never included in release archive')
    parser.add_argument('--output', type=Path, default=Path('evidence/m0-m5-dev-deploy.json'), help='Separate report path preserves earlier deployment evidence')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    config = dict(dotenv_values(args.env_file))
    mail = dict(dotenv_values(args.mail_env_file)) if args.mail_env_file else None
    archive, sha, count = package(root)
    release = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + sha[:12]
    keys = ['NZ_DB_HOST', 'NZ_DB_PORT', 'NZ_DB_USER', 'NZ_DB_PASSWORD', 'NZ_DB_NAME',
            'NZ_SYNTHETIC_DB_NAME', 'NZ_PVE_DB_NAME', 'NZ_TEST_DB_NAME', 'NZ_SECRET_KEY']
    if any(not config.get(key) for key in keys):
        raise ValueError('A complete independent MySQL environment is required')
    # dotenv 单引号转义规则明确，禁止 shell source 解析；只由 python-dotenv 加载。
    environment = '\n'.join(key + "='" + config[key].replace('\\', '\\\\').replace("'", "\\'") + "'" for key in keys)
    environment += "\nNZ_MYSQL_BIN='/usr/bin'\n"
    client = connect(args.source, root / 'runtime/dev-known-hosts')
    try:
        with client.open_sftp() as sftp:
            sftp.put(str(archive), '/tmp/nz-revival-' + release + '.tar.gz')
            if mail is not None:
                # 独立文件不替换 MySQL 秘钥；只发送明确白名单，拒绝夹带本机路径或无关环境项。
                mail_keys = ['NZ_MAIL_PROVIDER', 'NZ_MAIL_HOST', 'NZ_MAIL_PORT', 'NZ_MAIL_FROM',
                             'NZ_MAIL_USERNAME', 'NZ_MAIL_PASSWORD', 'NZ_MAIL_SENDER_NAME', 'NZ_EMAIL_VERIFICATION_REQUIRED']
                if any(not mail.get(key) for key in mail_keys):
                    raise ValueError('Complete SMTP settings required')
                mail_environment = '\n'.join(key + "='" + mail[key].replace('\\', '\\\\').replace("'", "\\'") + "'" for key in mail_keys) + '\n'
                with sftp.open('/tmp/nz-revival-' + release + '.mail.env', 'wx') as stream:
                    sftp.chmod('/tmp/nz-revival-' + release + '.mail.env', 0o600)
                    stream.write(mail_environment)
            with sftp.open('/tmp/nz-revival-' + release + '.env', 'wx') as stream:
                # 先收紧空文件权限，再写入凭据，避免上传窗口中被其他系统用户读取。
                sftp.chmod('/tmp/nz-revival-' + release + '.env', 0o600)
                stream.write(environment)
            installer = '/tmp/nz-revival-' + release + '-install.sh'
            with sftp.open(installer, 'wx') as stream:
                stream.write((root / 'tools/dev_install.sh').read_text(encoding='utf-8').replace('\r\n', '\n'))
            sftp.chmod(installer, 0o700)
        code, output, error = execute(client, 'sudo -n bash ' + shlex.quote(installer) + ' ' + release + ' ' + sha, timeout=1800)
        (root / 'runtime/dev-deploy.log').write_text(output + '\n' + error, encoding='utf-8')
        report = {'kind': 'DEV_CONTROL_PLANE_DEPLOYMENT_NOT_GAME_NODE', 'release_id': release,
                  'archive_sha256': sha, 'archive_files': count, 'installer_exit_code': code,
                  'access': 'SSH_TUNNEL_LOOPBACK_ONLY', 'retail_verified': False}
        report_path = args.output if args.output.is_absolute() else root / args.output
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report))
        return 1 if code else 0
    finally:
        client.close()


if __name__ == '__main__':
    raise SystemExit(main())
