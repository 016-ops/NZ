"""仅配置仓库所属独立 MySQL 实例；生成凭据只保存私有环境文件，不能输出到日志。"""
import argparse
import os
import secrets
import time
from pathlib import Path
import MySQLdb
from dotenv import load_dotenv

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('action', choices=['init','wait','status','stop'])
parser.add_argument('--port', type=int, default=3307)
args = parser.parse_args()
load_dotenv(root / '.env.local')
if args.action != 'init' and not (root / '.env.local').is_file():
    raise SystemExit('Project local configuration is missing')
port = args.port if args.action == 'init' else int(os.environ.get('NZ_DB_PORT','3307'))
password = '' if args.action == 'init' else os.environ['NZ_MYSQL_ROOT_PASSWORD']
connection = None
for attempt in range(30 if args.action in ('init','wait') else 1):
    try:
        connection = MySQLdb.connect(host='127.0.0.1',port=port,user='root',passwd=password,charset='utf8mb4')
        break
    except MySQLdb.Error:
        time.sleep(1)
if connection is None:
    raise SystemExit('Cannot connect to the project MySQL instance')
with connection.cursor() as c:
    if args.action == 'init':
        owner_password, app_password = secrets.token_hex(32), secrets.token_hex(32)
        # Save recovery credentials before changing root; the file is git-ignored.
        with (root / '.env.local').open('x', encoding='utf-8') as output:
            output.write(f'NZ_DB_HOST=127.0.0.1\nNZ_DB_PORT={port}\nNZ_DB_USER=nz_local\nNZ_DB_PASSWORD={app_password}\nNZ_MYSQL_ROOT_PASSWORD={owner_password}\nNZ_SECRET_KEY={secrets.token_hex(32)}\nNZ_DB_NAME=nz_current\nNZ_SYNTHETIC_DB_NAME=nz_synthetic\nNZ_TEST_DB_NAME=nz_unittest\n')
        c.execute("ALTER USER 'root'@'localhost' IDENTIFIED BY %s", (owner_password,))
        c.execute("CREATE USER 'nz_local'@'127.0.0.1' IDENTIFIED BY %s", (app_password,))
        c.execute("GRANT ALL PRIVILEGES ON `nz\\_%`.* TO 'nz_local'@'127.0.0.1'")
        for name in ('nz_current','nz_synthetic'):
            c.execute(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin')
        print('Project MySQL initialized; credentials saved privately in .env.local.')
    elif args.action == 'stop':
        c.execute('SHUTDOWN')
        print('Project MySQL stopped.')
    else:
        c.execute('SELECT VERSION()')
        print('Project MySQL ready:', c.fetchone()[0])
connection.close()
