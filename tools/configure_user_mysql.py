"""从用户本机配置建立独立项目库；凭据仅保存在忽略的 runtime，不打印原始配置。"""
import argparse
import json
import re
import secrets
from pathlib import Path
import MySQLdb
from dotenv import dotenv_values, set_key


def read_credentials(path, default_port=None):
    """读取常见中英文键；密码只在内存使用，缺少字段时只报告字段名称。"""
    raw = path.read_bytes()
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('gb18030')
    aliases = {'外网地址': 'HOST', '地址': 'HOST', 'host': 'HOST', 'ip': 'HOST', '端口': 'PORT', 'port': 'PORT',
               '账号': 'USER', '用户名': 'USER', 'user': 'USER', 'username': 'USER', '密码': 'PASSWORD', 'password': 'PASSWORD'}
    values = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = re.split(r'[:=：]', line, maxsplit=1)
        if len(parts) != 2 or parts[0].strip().lower() not in aliases:
            raise ValueError('Unsupported connection configuration label')
        values[aliases[parts[0].strip().lower()]] = parts[1].strip()
    if default_port is not None:
        values.setdefault('PORT', default_port)
    if set(values) != {'HOST', 'PORT', 'USER', 'PASSWORD'}:
        raise ValueError('Configuration needs host, port, user and password')
    values['PORT'] = int(values['PORT'])
    if not 1 <= values['PORT'] <= 65535:
        raise ValueError('Invalid database port')
    return values


def main():
    """验证连接并创建随机命名的四个新库；已有私有配置用于同一次初始化的安全重试。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if not output.is_relative_to(root / 'runtime'):
        raise ValueError('Credentials output must be inside ignored project runtime')
    values = read_credentials(args.source)
    with MySQLdb.connect(host=values['HOST'], port=values['PORT'], user=values['USER'], passwd=values['PASSWORD'],
                         connect_timeout=8, charset='utf8mb4', ssl_mode='REQUIRED') as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT VERSION()')
            version = cursor.fetchone()[0]
            cursor.execute("SHOW STATUS LIKE 'Ssl_cipher'")
            encrypted = bool(cursor.fetchone()[1])
            if output.exists():
                config = dict(dotenv_values(output))
                if config.get('NZ_DATABASE_OWNER_MARKER') != 'USER_AUTHORIZED_PROJECT_ONLY':
                    raise ValueError('Refusing to adopt existing configuration')
            else:
                prefix = 'nz_revival_' + secrets.token_hex(4)
                config = {'NZ_DATABASE_OWNER_MARKER': 'USER_AUTHORIZED_PROJECT_ONLY',
                          'NZ_DB_HOST': values['HOST'], 'NZ_DB_PORT': str(values['PORT']), 'NZ_DB_USER': values['USER'],
                          'NZ_DB_PASSWORD': values['PASSWORD'], 'NZ_SECRET_KEY': secrets.token_hex(32),
                          'NZ_DB_NAME': prefix + '_current', 'NZ_SYNTHETIC_DB_NAME': prefix + '_synthetic',
                          'NZ_PVE_DB_NAME': prefix + '_pve', 'NZ_TEST_DB_NAME': prefix + '_unittest',
                          'NZ_CATALOG_DB_NAME': prefix + '_catalog',
                          'NZ_MYSQL_BIN': str((root / 'runtime/tools/mysql-8.4.6-winx64/bin').resolve()).replace('\\', '/')}
                output.parent.mkdir(parents=True, exist_ok=True)
                with output.open('x', encoding='utf-8'):
                    pass
                for key, value in config.items():
                    set_key(str(output), key, value)
            databases = [config[key] for key in ('NZ_DB_NAME', 'NZ_SYNTHETIC_DB_NAME', 'NZ_PVE_DB_NAME', 'NZ_CATALOG_DB_NAME')]
            for name in databases:
                if not re.fullmatch(r'nz_revival_[a-f0-9]{8}_(current|synthetic|pve|catalog)', name):
                    raise ValueError('Refusing an unexpected database namespace')
                cursor.execute(f'CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin')
            report = {'kind': 'USER_PROVIDED_MYSQL_CONNECTION', 'mysql_version': version,
                      'encrypted_transport': encrypted, 'new_project_databases': databases,
                      'unrelated_database_writes': 0, 'credentials_output': 'runtime/user-mysql.env'}
            (output.parent / 'user-mysql-connection.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            print(json.dumps(report))


if __name__ == '__main__':
    main()
