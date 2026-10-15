"""将已校验目录 SQL 导入专用项目库并重复导入核对；不接触玩家业务库。"""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import re
import time
import MySQLdb
import sqlparse
from dotenv import dotenv_values
from tools.client_catalog_export import TABLES

INSERT = re.compile(r'INSERT INTO nzcat_(\w+) \(([^\n]+)\) VALUES \(([^\n]+)\) ON DUPLICATE KEY UPDATE `([^`]+)`=nzcat_\1\.`\4`;\n?')


def seed_groups(path, packet_budget):
    """只合并生成器固定格式的相邻 INSERT，保持列、值和无覆盖 upsert 完全一致。"""
    key, values, size = None, [], 0
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if not line.strip() or line.startswith('--') or line.strip() in ('SET NAMES utf8mb4;', 'START TRANSACTION;', 'COMMIT;'):
                continue
            match = INSERT.fullmatch(line)
            if not match or match[1] not in TABLES:
                raise ValueError('Seed contains an unsupported statement')
            table, columns, value, primary = match.groups()
            current = (table, f'INSERT INTO nzcat_{table} ({columns}) VALUES ',
                       f' ON DUPLICATE KEY UPDATE `{primary}`=nzcat_{table}.`{primary}`')
            row = '(' + value + ')'
            length = len(row.encode('utf-8'))
            if length + len(current[1]) + len(current[2]) >= packet_budget:
                raise ValueError('A source row exceeds the server packet budget')
            if values and (key != current or size + length + 1 >= packet_budget or len(values) >= 1000):
                yield key[0], key[1] + ','.join(values) + key[2], len(values)
                values, size = [], 0
            key = current
            values.append(row)
            size += length + 1
    if values:
        yield key[0], key[1] + ','.join(values) + key[2], len(values)


def statements(path):
    """移除生成器整行注释，由 SQL 分词器保留字符串内分号，不能直接按字符分割。"""
    body = '\n'.join(line for line in path.read_text(encoding='utf-8').splitlines() if not line.startswith('--'))
    return [statement.strip() for statement in sqlparse.split(body) if statement.strip()]


def verification(cursor, path):
    """真正执行生成的校验 SQL；逐表数量必须一致，外键孤立引用必须为零。"""
    counts, references, duplicates = {}, {}, 0
    for statement in statements(path):
        if not statement.startswith(('SELECT ', 'SET @snapshot_id=')):
            raise ValueError('Unsupported verification SQL')
        cursor.execute(statement)
        if cursor.description is None:
            continue
        names = [column[0] for column in cursor.description]
        rows = cursor.fetchall()
        if 'count_matches' in names:
            for row in rows:
                if row[names.index('count_matches')] != 1:
                    raise ValueError('Imported dataset count mismatch')
                counts[row[0]] = row[1]
        elif 'orphan_count' in names:
            for row in rows:
                if row[1] != 0:
                    raise ValueError('Imported catalog has orphan references')
                references[row[0]] = row[1]
        elif 'duplicate_count' in names:
            duplicates += len(rows)
    if len(counts) != len(TABLES):
        raise ValueError('Verification did not cover every catalog table')
    return {'table_counts': counts, 'reference_checks': references, 'native_id_duplicate_groups': duplicates}


def main():
    """核对 SQL 摘要后导入两次并验证稳定行数；错误只报告阶段及错误码，不回显配置或查询。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    config = dict(dotenv_values(args.env_file))
    database = config.get('NZ_CATALOG_DB_NAME', '')
    if not re.fullmatch(r'nz_revival_[0-9a-f]{8}_catalog', database):
        raise ValueError('Only a dedicated generated catalog database is allowed')
    if database in {config.get(key) for key in ('NZ_DB_NAME', 'NZ_SYNTHETIC_DB_NAME', 'NZ_PVE_DB_NAME', 'NZ_TEST_DB_NAME')}:
        raise ValueError('Catalog and business databases must be separate')
    manifests = {entry['path']: entry['sha256'] for entry in json.loads((args.catalog / 'delivery-manifest.json').read_text(encoding='utf-8'))}
    for name in ('001_schema.sql', '002_seed.sql', '003_verify.sql'):
        with (args.catalog / 'sql' / name).open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != manifests['sql/' + name]:
                raise ValueError('Catalog SQL hash differs from delivery manifest')
    started, phase = time.monotonic(), 'CONNECT'
    try:
        with MySQLdb.connect(host=config['NZ_DB_HOST'], port=int(config['NZ_DB_PORT']), user=config['NZ_DB_USER'],
                             passwd=config['NZ_DB_PASSWORD'], db=database, charset='utf8mb4', connect_timeout=8, compress=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT VERSION(),@@max_allowed_packet')
                version, maximum = cursor.fetchone()
                budget = min(3 * 1024 * 1024, maximum - 65536)
                cursor.execute('SHOW TABLES')
                if any(row[0] not in {'nzcat_' + name for name in TABLES} for row in cursor.fetchall()):
                    raise ValueError('Refusing a database with unrelated tables')
                phase = 'SCHEMA'
                for statement in statements(args.catalog / 'sql/001_schema.sql'):
                    if not statement.startswith(('SET NAMES ', 'CREATE TABLE IF NOT EXISTS nzcat_')):
                        raise ValueError('Unsupported schema SQL')
                    cursor.execute(statement)
                passes = []
                for attempt in (1, 2):
                    phase = 'IMPORT_' + str(attempt)
                    rows, batches = collections.Counter(), 0
                    connection.begin()
                    for table, sql, count in seed_groups(args.catalog / 'sql/002_seed.sql', budget):
                        cursor.execute(sql)
                        rows[table] += count
                        batches += 1
                        if batches % 100 == 0:
                            print(json.dumps({'pass': attempt, 'batches': batches, 'rows': sum(rows.values())}), flush=True)
                    connection.commit()
                    phase = 'VERIFY_' + str(attempt)
                    checked = verification(cursor, args.catalog / 'sql/003_verify.sql')
                    if dict(rows) != checked['table_counts']:
                        raise ValueError('Seed rows and actual snapshot counts differ')
                    passes.append(dict(checked, sql_batches=batches))
                if passes[0]['table_counts'] != passes[1]['table_counts']:
                    raise ValueError('Repeated import changed row counts')
                report = {'kind': 'ACTUAL_USER_MYSQL_CATALOG_IMPORT', 'mysql_version': version,
                          'snapshot_id': json.loads((args.catalog / 'summary.json').read_text(encoding='utf-8'))['snapshot_id'],
                          'passes': passes, 'replay_row_counts_unchanged': True, 'business_database_writes': 0,
                          'runtime_game_verified': False, 'seconds': round(time.monotonic() - started, 3)}
    except (MySQLdb.Error, ValueError) as error:
        report = {'kind': 'ACTUAL_USER_MYSQL_CATALOG_IMPORT', 'phase': phase, 'passed': False,
                  'error_type': type(error).__name__, 'error_code': error.args[0] if isinstance(error, MySQLdb.Error) else 'VALIDATION_FAILED'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'passes'}))
    return 1 if report.get('passed') is False else 0


if __name__ == '__main__':
    raise SystemExit(main())
