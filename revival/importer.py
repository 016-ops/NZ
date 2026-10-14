"""SQLite v1 到 MySQL 的显式迁移；源文件只读，目标数据全部在一个事务中写入。"""
import json
import sqlite3
from django.db import transaction
from django.db.models import Q
from contextlib import closing
from pathlib import Path

from .db import Database, column_map
from .models import TABLE_MODELS

# 顺序体现外键依赖；关联补偿操作还需按 linked_operation 拓扑排序。
IMPORT_ORDER = ['admins','accounts','invitations','characters','sessions','sanctions',
    'audit_events','requests','wallets','inventory','equipment','asset_operations',
    'asset_entries','outbox','asset_entry_details','rooms','room_members','matches','match_players','settlements']


def normalized(rows):
    """比较逻辑业务列，排除新 ORM 自增键；不将敏感行内容写到输出或错误中。"""
    return sorted(json.dumps(dict(row), sort_keys=True, ensure_ascii=False, separators=(',',':')) for row in rows)


def import_sqlite(source, *, dry_run=False, test_profile=False):
    """验证源版本、外键、profile 和目标空库，导入后逐行对账；任一失败整体回滚。"""
    path = Path(source).resolve(strict=True)
    expected_profile = 'test-only-v1' if test_profile else 'cn-current-unverified'
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as legacy:
        legacy.row_factory = sqlite3.Row
        legacy.execute('BEGIN')  # 冻结读取快照，避免多张表读取到不同时间的数据。
        if legacy.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Source integrity check failed')
        if legacy.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Source foreign keys are invalid')
        if [r[0] for r in legacy.execute('SELECT version FROM schema_version')] != [1]:
            raise ValueError('Only legacy schema version 1 is supported')
        metadata = dict(legacy.execute('SELECT key,value FROM deployment_metadata').fetchall())
        if metadata.get('profile') != expected_profile:
            raise ValueError('Source profile does not match the target profile')
        data = {}
        for name in IMPORT_ORDER:
            # 表名来自固定白名单，源用户不能控制 SQL 标识符。
            rows = [dict(r) for r in legacy.execute(f'SELECT * FROM {name}')]
            source_columns = {r[1] for r in legacy.execute(f'PRAGMA table_info({name})')}
            if source_columns != set(column_map(TABLE_MODELS[name])):
                raise ValueError(f'Unsupported source columns in {name}')
            data[name] = rows
    report = {'dry_run': dry_run, 'profile': expected_profile,
              'tables': {k: len(v) for k,v in data.items()},
              'wallet_balance': sum(r['balance'] for r in data['wallets']),
              'inventory_quantity': sum(r['quantity'] for r in data['inventory'])}
    database = Database()
    with database.transaction() as repository:
        if any(repository.table(name).exists() for name in IMPORT_ORDER):
            raise ValueError('Target business database must be empty')
        previous = repository.select('deployment_metadata', Q(key='profile')).fetchone()
        if previous and previous['value'] != expected_profile:
            raise ValueError('Target profile conflicts with source')
        for name in IMPORT_ORDER:
            pending = list(data[name])
            while pending:
                progressed = False
                for row in pending[:]:
                    if name=='asset_operations' and row['linked_operation'] and not repository.table(name).filter(pk=row['linked_operation']).exists():
                        continue
                    repository.insert(name, row)
                    pending.remove(row)
                    progressed = True
                if not progressed:
                    raise ValueError('Source contains cyclic or missing operation links')
        for key,value in metadata.items():
            repository.table('deployment_metadata').update_or_create(key=key, defaults={'value':value})
        for name in IMPORT_ORDER:
            if normalized(repository.select(name)) != normalized(data[name]):
                raise ValueError(f'Imported data verification failed: {name}')
        report['verified'] = True
        if dry_run:
            # dry-run 也真正走过 MySQL 约束和逐行核对，不能仅检查源格式后宣称可导入。
            # 标记整个外层事务回滚，账号、账本、审计和 profile 元数据均不留在目标库。
            transaction.set_rollback(True, using=database.alias)
    return report
