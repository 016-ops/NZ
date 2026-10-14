"""MySQL 一致性备份：新文件写入、临时库恢复、逐表对账后才报告成功。"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
import MySQLdb
from MySQLdb.cursors import DictCursor

from django.db import connections
from .db import column_map
from .models import TABLE_MODELS


def snapshot(repository):
    """将稳定业务列规范化后计算摘要；报告仅含计数与摘要，不输出凭据。"""
    result = {}
    for name in TABLE_MODELS:
        rows = [json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(',',':'))
                for row in repository.select(name)]
        digest = hashlib.sha256('\n'.join(sorted(rows)).encode()).hexdigest()
        result[name] = {'count': len(rows), 'sha256': digest}
    return result


def mysql_binary(name):
    """优先采用显式配置或 PATH；Windows 回退到已确认的本机安装目录。"""
    configured = os.environ.get('NZ_MYSQL_BIN')
    if configured:
        return str(Path(configured) / (name+'.exe' if os.name=='nt' else name))
    found = shutil.which(name)
    if found:
        return found
    candidate = Path('C:/Program Files/MySQL/MySQL Server 9.0/bin') / (name+'.exe')
    if candidate.is_file():
        return str(candidate)
    raise RuntimeError('Set NZ_MYSQL_BIN to the MySQL client tools directory')


def backup(database, destination):
    """锁定业务写入后导出完整 SQL，在随机临时库恢复验证，最后清理该临时库。"""
    target = Path(destination).resolve()
    if target.exists():
        raise ValueError('Backup requires a new destination')
    target.parent.mkdir(parents=True, exist_ok=True)
    config = connections[database.alias].settings_dict.copy()
    if not re.fullmatch(r'[A-Za-z0-9_]+', config['NAME']):
        raise ValueError('Unsupported database name')
    temporary = 'nz_backup_' + uuid.uuid4().hex
    env = os.environ.copy()
    env['MYSQL_PWD'] = config['PASSWORD']
    args = ['--protocol=TCP', '--host='+config['HOST'], '--port='+str(config['PORT']), '--user='+config['USER']]
    created = False
    try:
        with database.transaction() as repository:
            before = snapshot(repository)
            with target.open('xb') as output:
                process = subprocess.run([mysql_binary('mysqldump'), *args, '--single-transaction',
                    '--skip-add-locks', '--no-tablespaces', '--set-gtid-purged=OFF', '--column-statistics=0',
                    config['NAME']], env=env, stdout=output, stderr=subprocess.PIPE, timeout=120)
            if process.returncode:
                raise RuntimeError('MySQL dump failed; credential-bearing stderr is withheld')
            with connections[database.alias].cursor() as cursor:
                cursor.execute(f'CREATE DATABASE `{temporary}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin')
                created = True
            with target.open('rb') as source:
                process = subprocess.run([mysql_binary('mysql'), *args, temporary], env=env, stdin=source,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
            if process.returncode:
                raise RuntimeError('Backup restore verification failed; stderr is withheld')
            # 恢复验收使用独立原生连接，不注册新的应用数据库别名。
            # 固定模型白名单的 SQL 仅用于备份逐表对账，业务请求仍全部通过 ORM。
            restored = {}
            with MySQLdb.connect(host=config['HOST'],port=int(config['PORT']),user=config['USER'],
                    passwd=config['PASSWORD'],db=temporary,charset='utf8mb4',cursorclass=DictCursor) as verified_connection:
                with verified_connection.cursor() as cursor:
                    for name, model in TABLE_MODELS.items():
                        columns = ','.join(f'`{column}`' for column in column_map(model))
                        cursor.execute(f'SELECT {columns} FROM `{name}`')
                        rows = sorted(json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':')) for row in cursor.fetchall())
                        restored[name] = {'count':len(rows),'sha256':hashlib.sha256('\n'.join(rows).encode()).hexdigest()}
            if restored != before:
                raise RuntimeError('Restored backup differs from the source snapshot')
        return {'verified': True, 'tables': {k:v['count'] for k,v in before.items()}}
    finally:
        if created:
            with connections[database.alias].cursor() as cursor:
                cursor.execute(f'DROP DATABASE `{temporary}`')
