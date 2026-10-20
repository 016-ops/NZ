"""MySQL 一致性备份：新文件写入、临时库恢复、逐表对账后才报告成功。"""
import hashlib
import base64
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
from django.core.serializers.json import DjangoJSONEncoder
from .db import column_map
from .models import TABLE_MODELS


def canonical_row(row):
    """为对账确定性编码二进制列，保留其类型；设置BLOB不能按UTF8解码或直接交给JSON。

    ORM和恢复验证的原生MySQL游标共用此函数；正文只参与内存摘要，不进入备份结果报告。
    """
    values = {key: {'binary_base64': base64.b64encode(bytes(value)).decode('ascii')}
              if isinstance(value, (bytes, bytearray, memoryview)) else value for key, value in row.items()}
    # DateField 等由ORM和原生驱动都返回日期对象；按Django相同的无损ISO表示参与摘要。
    return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(',', ':'), cls=DjangoJSONEncoder)


def normalize_restored_row(model, row):
    """将原生MySQL恢复行转换为与ORM相同的布尔/JSON类型，再逐列做严格内容对账。

    MySQLdb默认把JSON列返回文本，ORM返回dict/list/标量；仅修正表示差异，不丢字段、
    不跳过JSON内容。JSON格式损坏仍抛错，不能被当作备份验证通过。
    """
    result = dict(row)
    for field in model._meta.concrete_fields:
        column = field.column
        if column not in result or result[column] is None:
            continue
        kind = field.get_internal_type()
        if kind == 'BooleanField':
            result[column] = bool(result[column])
        elif kind == 'JSONField' and isinstance(result[column], (str, bytes, bytearray)):
            # 使用字段登记的decoder；不沿用from_db_value遇损坏JSON后退回原文本的宽松行为。
            result[column] = json.loads(result[column], cls=field.decoder)
    return result


def snapshot(repository):
    """将稳定业务列规范化后计算摘要；报告仅含计数与摘要，不输出凭据。"""
    result = {}
    for name in TABLE_MODELS:
        rows = [canonical_row(row) for row in repository.select(name)]
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
                    '--skip-add-locks', '--no-tablespaces', '--set-gtid-purged=OFF', '--column-statistics=0', '--hex-blob',
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
                        # 对账必须使用相同字段类型：包括JSON内容，而不只是tinyint布尔表示。
                        values = [normalize_restored_row(model, row) for row in cursor.fetchall()]
                        rows = sorted(canonical_row(row) for row in values)
                        restored[name] = {'count':len(rows),'sha256':hashlib.sha256('\n'.join(rows).encode()).hexdigest()}
            if restored != before:
                raise RuntimeError('Restored backup differs from the source snapshot')
        return {'verified': True, 'tables': {k:v['count'] for k,v in before.items()}}
    finally:
        if created:
            with connections[database.alias].cursor() as cursor:
                cursor.execute(f'DROP DATABASE `{temporary}`')
