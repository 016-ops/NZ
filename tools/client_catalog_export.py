"""JSON/Markdown/MySQL exports from one evidence-preserving catalogue model."""
from __future__ import annotations

import collections
import datetime
import hashlib
import json
import re
import struct
import zlib
from pathlib import Path

from tools.client_catalog import canonical, fields, identity, parse_ini, decode_resource, TEXT_EXTENSIONS


def load_capture(output, refresh_root=None):
    """Rebuild only verified captured metadata; never silently trust changed files."""
    report = json.loads((output / 'summary.json').read_text(encoding='utf-8'))
    if not report.get('client_files_unchanged') or report.get('verification_method') != 'TWO_FULL_SHA256_PASSES_AND_FILE_SET_CHECK':
        raise ValueError('Capture lacks full client verification')
    manifest = {entry['path']: entry for entry in json.loads((output / 'delivery-manifest.json').read_text(encoding='utf-8'))}
    for filename in ['summary.json', 'json/source_files.json', 'json/raw_records.json']:
        if filename not in manifest or hashlib.sha256((output / filename).read_bytes()).hexdigest() != manifest[filename]['sha256']:
            raise ValueError('Capture digest mismatch: ' + filename)
    files = json.loads((output / 'json/source_files.json').read_text(encoding='utf-8'))
    original = json.loads((output / 'json/raw_records.json').read_text(encoding='utf-8'))
    if len(files) != report['counts']['source_files'] or len(original) != report['counts']['raw_records']:
        raise ValueError('Capture count mismatch')
    paths = {f['path'] for f in files}
    if len(paths) != len(files) or any(r['source_path'] not in paths for r in original):
        raise ValueError('Capture contains duplicate paths or orphan records')
    digest = hashlib.sha256(canonical([(f['path'], f['bytes'], f['sha256']) for f in files]).encode('utf-8')).hexdigest()
    if digest != report['manifest_sha256']: raise ValueError('Client manifest digest mismatch')
    by_file = collections.defaultdict(list)
    for record in original: by_file[record['source_path']].append(record)
    records, refreshed = [], []
    refresh_root = refresh_root.resolve(strict=True) if refresh_root else None
    for file in files:
        members = sorted(by_file[file['path']], key=lambda r: r['line'])
        if refresh_root and file['status'] == 'DECODE_FAILED' and file['extension'] in TEXT_EXTENSIONS:
            path = (refresh_root / file['path']).resolve(strict=True)
            if refresh_root not in path.parents or path.is_symlink() or path.is_junction(): raise ValueError('Unsafe refresh path')
            if file['bytes'] <= 64 * 1024 * 1024:
                blob = path.read_bytes()
                if len(blob) != file['bytes'] or hashlib.sha256(blob).hexdigest() != file['sha256']:
                    raise ValueError('Client text changed since capture: ' + file['path'])
                try:
                    text, meta = decode_resource(blob)
                    if file['extension'] in ('.ini', '.cht', '.int', '.cfg'):
                        members = parse_ini(text, file['path']); parser = 'unreal-ini-v1'
                    else:
                        value = json.loads(text) if file['extension'] == '.json' else {'text': text}
                        members = [{'id': identity(file['path'], 1), 'source_path': file['path'], 'line': 1,
                                    'line_end': len(text.splitlines()), 'section': '', 'section_occurrence': 0,
                                    'kind': 'text_document', 'operator': '', 'key': None, 'occurrence': 1,
                                    'value': value, 'raw': text}]; parser = 'text-v1'
                    file.update(meta, status='PARSED_TEXT', error=None, parser=parser, record_count=len(members))
                    refreshed.append(file['path'])
                except (ValueError, UnicodeError, zlib.error, struct.error):
                    pass
        if file.get('parser') == 'unreal-ini-v1':
            # A final empty record represents a real trailing blank line. Keep
            # its terminator so repeated rebuilds cannot silently drop blanks.
            rebuilt = parse_ini('\n'.join(r['raw'] for r in members) + ('\n' if members else ''), file['path'])
            records.extend(rebuilt);file['record_count'] = len(rebuilt)
        else: records.extend(members)
    return files, records, {'source_snapshot_id': report['snapshot_id'],
                            'client_verified_at': (report.get('capture_metadata') or {}).get('client_verified_at') or report['generated_at'],
                            'refreshed_failed_text_files': refreshed,
                            'operation': 'REPARSE_CAPTURED_TEXT_CLIENT_NOT_READ_AGAIN'}


def add_external(catalog, document):
    catalog.external_sources = document['sources']
    catalog.external_annotations = []
    for annotation in document['annotations']:
        # Matching is restricted to client evidence and must never create external-only assets.
        expression = re.compile(annotation['client_match'], re.I)
        matches = [entity for entity in catalog.entities.values()
                   if entity['category'] in annotation['categories'] and (expression.search(entity['name']) or expression.search(entity['id']))]
        for entity in matches:
            row = {'id': identity(annotation['id'], entity['id']), 'entity_id': entity['id'],
                   'source_id': annotation['source_id'], 'display_name': annotation.get('display_name'),
                   'summary': annotation['summary'], 'status': 'EXTERNAL_SUPPLEMENT',
                   'match_method': 'REVIEWED_CLASS_PATTERN', 'client_match': annotation['client_match'],
                   'version_applicability': 'HISTORICAL_CONTEXT_NOT_CURRENT_BUILD_VALIDATION',
                   'conflict_policy': 'CLIENT_VALUES_PRESERVED'}
            catalog.external_annotations.append(row)
        if not matches:
            catalog.issue('EXTERNAL_ANNOTATION_NO_LOCAL_MATCH', detail={'annotation_id': annotation['id']})


def datasets(c):
    return {'source_files': c.files, 'raw_records': c.records, 'entities': sorted(c.entities.values(), key=lambda x: x['id']),
            'entity_evidence': c.evidence, 'items': c.items, 'weapons': c.weapons, 'modes': c.modes,
            'maps': c.maps, 'features': c.features, 'gameplay_rules': c.rules, 'currencies': c.currencies,
            'shop': c.shop, 'shop_options': c.options, 'shop_items': c.shop_items,
            'relations': sorted(c.relations.values(), key=lambda x: x['id']), 'issues': c.issues,
            'external_sources': getattr(c, 'external_sources', []),
            'external_annotations': getattr(c, 'external_annotations', [])}


# A shared schema registry drives both SQL generation and export verification.
# All internal identifiers/indexes use ASCII, within old InnoDB index limits.
TABLES = {
 'snapshots': [('snapshot_id', 'CHAR(64) CHARACTER SET ascii'), ('manifest_sha256', 'CHAR(64) CHARACTER SET ascii'),
               ('metadata', 'JSON')],
 'source_files': [('file_id', 'CHAR(64) CHARACTER SET ascii'), ('path', 'LONGTEXT'), ('bytes', 'BIGINT UNSIGNED'),
                  ('sha256', 'CHAR(64) CHARACTER SET ascii'), ('extension', 'VARCHAR(24)'), ('status', 'VARCHAR(48)'),
                  ('record_count', 'BIGINT UNSIGNED'), ('attributes', 'JSON')],
 'raw_records': [('record_id', 'CHAR(64) CHARACTER SET ascii'), ('file_id', 'CHAR(64) CHARACTER SET ascii'),
                 ('line_no', 'BIGINT UNSIGNED'), ('line_end', 'BIGINT UNSIGNED'), ('section_name', 'LONGTEXT'),
                 ('section_occurrence', 'BIGINT UNSIGNED'), ('inline_comment', 'LONGTEXT'),
                 ('kind', 'VARCHAR(48)'), ('operator', 'VARCHAR(1)'), ('key_name', 'LONGTEXT'),
                 ('occurrence', 'BIGINT UNSIGNED'), ('parsed_value', 'JSON'), ('raw_text', 'LONGTEXT')],
 'entities': [('entity_id', 'VARCHAR(180) CHARACTER SET ascii'), ('category', 'VARCHAR(48)'), ('native_id', 'LONGTEXT'),
              ('name', 'LONGTEXT'), ('description', 'LONGTEXT'), ('evidence_status', 'VARCHAR(48)'),
              ('runtime_status', 'VARCHAR(48)'), ('attributes', 'JSON')],
 'entity_evidence': [('evidence_id', 'CHAR(64) CHARACTER SET ascii'), ('entity_id', 'VARCHAR(180) CHARACTER SET ascii'),
                     ('record_id', 'CHAR(64) CHARACTER SET ascii'), ('file_id', 'CHAR(64) CHARACTER SET ascii'), ('status', 'VARCHAR(48)')],
 'items': [('definition_id', 'CHAR(64) CHARACTER SET ascii'), ('entity_id', 'VARCHAR(180) CHARACTER SET ascii'),
           ('record_id', 'CHAR(64) CHARACTER SET ascii'), ('internal_name', 'LONGTEXT'), ('classification', 'VARCHAR(48)'),
           ('location_raw', 'LONGTEXT'), ('consume_type_raw', 'LONGTEXT'), ('hidden', 'TINYINT'),
           ('stack_limit_raw', 'LONGTEXT'), ('icon_id_raw', 'LONGTEXT'), ('attributes', 'JSON')],
 'weapons': [('definition_id', 'CHAR(64) CHARACTER SET ascii'), ('entity_id', 'VARCHAR(180) CHARACTER SET ascii'),
             ('file_id', 'CHAR(64) CHARACTER SET ascii'), ('class_name', 'LONGTEXT'), ('attributes', 'JSON')],
 'modes': [('definition_id', 'CHAR(64) CHARACTER SET ascii'), ('entity_id', 'VARCHAR(180) CHARACTER SET ascii'),
           ('record_id', 'CHAR(64) CHARACTER SET ascii'), ('prefix', 'LONGTEXT'), ('class_name', 'LONGTEXT'),
           ('mode_id_raw', 'LONGTEXT'), ('player_limit_raw', 'LONGTEXT'), ('difficulty_raw', 'LONGTEXT'), ('attributes', 'JSON')],
 'maps': [('definition_id', 'CHAR(64) CHARACTER SET ascii'), ('entity_id', 'VARCHAR(180) CHARACTER SET ascii'),
          ('record_id', 'CHAR(64) CHARACTER SET ascii'), ('file_id', 'CHAR(64) CHARACTER SET ascii'),
          ('logical_map_id_raw', 'LONGTEXT'), ('player_limit_raw', 'LONGTEXT'), ('difficulty_raw', 'LONGTEXT'), ('attributes', 'JSON')],
 'features': [('definition_id', 'CHAR(64) CHARACTER SET ascii'), ('entity_id', 'VARCHAR(180) CHARACTER SET ascii'),
              ('file_id', 'CHAR(64) CHARACTER SET ascii'), ('class_name', 'LONGTEXT'), ('attributes', 'JSON')],
 'gameplay_rules': [('rule_id', 'CHAR(64) CHARACTER SET ascii'), ('entity_id', 'VARCHAR(180) CHARACTER SET ascii'),
                    ('record_id', 'CHAR(64) CHARACTER SET ascii'), ('key_name', 'LONGTEXT'),
                    ('operator', 'VARCHAR(1)'), ('parsed_value', 'JSON')],
 'currencies': [('currency_id', 'VARCHAR(180) CHARACTER SET ascii'), ('code', 'LONGTEXT'),
                ('display_name', 'LONGTEXT'), ('exchange_rate_raw', 'LONGTEXT'), ('attributes', 'JSON')],
 'shop': [('definition_id', 'CHAR(64) CHARACTER SET ascii'), ('entity_id', 'VARCHAR(180) CHARACTER SET ascii'),
          ('record_id', 'CHAR(64) CHARACTER SET ascii'), ('currency_id', 'VARCHAR(180) CHARACTER SET ascii'),
          ('is_package', 'TINYINT'), ('show_commodity', 'TINYINT'), ('sale_tag_raw', 'LONGTEXT'),
          ('sale_date_raw', 'LONGTEXT'), ('recall_date_raw', 'LONGTEXT'), ('buy_grade_raw', 'LONGTEXT'), ('attributes', 'JSON')],
 'shop_options': [('option_id', 'CHAR(64) CHARACTER SET ascii'), ('product_id', 'VARCHAR(180) CHARACTER SET ascii'),
                  ('record_id', 'CHAR(64) CHARACTER SET ascii'), ('option_index', 'BIGINT UNSIGNED'),
                  ('price_raw', 'JSON'), ('period_raw', 'JSON'), ('suite_period_raw', 'JSON'), ('period_unit', 'LONGTEXT')],
 'shop_items': [('component_id', 'CHAR(64) CHARACTER SET ascii'), ('product_id', 'VARCHAR(180) CHARACTER SET ascii'),
                ('record_id', 'CHAR(64) CHARACTER SET ascii'), ('item_index', 'BIGINT UNSIGNED'),
                ('item_id', 'VARCHAR(180) CHARACTER SET ascii'), ('native_item_id', 'LONGTEXT'), ('quantity', 'BIGINT UNSIGNED')],
 'relations': [('relation_id', 'CHAR(64) CHARACTER SET ascii'), ('source_id', 'VARCHAR(180) CHARACTER SET ascii'),
               ('target_id', 'VARCHAR(180) CHARACTER SET ascii'), ('relation_type', 'LONGTEXT'),
               ('record_id', 'CHAR(64) CHARACTER SET ascii'), ('raw_target', 'LONGTEXT'), ('status', 'VARCHAR(48)')],
 'issues': [('issue_id', 'CHAR(64) CHARACTER SET ascii'), ('code', 'VARCHAR(64)'), ('file_id', 'CHAR(64) CHARACTER SET ascii'),
            ('record_id', 'CHAR(64) CHARACTER SET ascii'), ('detail', 'JSON')],
 'external_sources': [('source_id', 'VARCHAR(180) CHARACTER SET ascii'), ('url', 'LONGTEXT'), ('title', 'LONGTEXT'),
                      ('published_date', 'VARCHAR(24)'), ('accessed_date', 'VARCHAR(24)'), ('attributes', 'JSON')],
 'external_annotations': [('annotation_id', 'CHAR(64) CHARACTER SET ascii'), ('entity_id', 'VARCHAR(180) CHARACTER SET ascii'),
                          ('source_id', 'VARCHAR(180) CHARACTER SET ascii'), ('display_name', 'LONGTEXT'),
                          ('summary', 'LONGTEXT'), ('attributes', 'JSON')],
}

TABLES = {name: [(column, sqltype + (' COLLATE ascii_bin' if 'CHARACTER SET ascii' in sqltype else ''))
                 for column, sqltype in columns] for name, columns in TABLES.items()}

FOREIGN = {
 'raw_records': [('file_id', 'source_files', 'file_id')],
 'entity_evidence': [('entity_id', 'entities', 'entity_id'), ('record_id', 'raw_records', 'record_id'), ('file_id', 'source_files', 'file_id')],
 'items': [('entity_id', 'entities', 'entity_id'), ('record_id', 'raw_records', 'record_id')],
 'weapons': [('entity_id', 'entities', 'entity_id'), ('file_id', 'source_files', 'file_id')],
 'modes': [('entity_id', 'entities', 'entity_id'), ('record_id', 'raw_records', 'record_id')],
 'maps': [('entity_id', 'entities', 'entity_id'), ('file_id', 'source_files', 'file_id'), ('record_id', 'raw_records', 'record_id')],
 'features': [('entity_id', 'entities', 'entity_id'), ('file_id', 'source_files', 'file_id')],
 'gameplay_rules': [('entity_id', 'entities', 'entity_id'), ('record_id', 'raw_records', 'record_id')],
 'currencies': [('currency_id', 'entities', 'entity_id')],
 'shop': [('entity_id', 'entities', 'entity_id'), ('currency_id', 'entities', 'entity_id'), ('record_id', 'raw_records', 'record_id')],
 'shop_options': [('product_id', 'entities', 'entity_id'), ('record_id', 'raw_records', 'record_id')],
 'shop_items': [('product_id', 'entities', 'entity_id'), ('item_id', 'entities', 'entity_id'), ('record_id', 'raw_records', 'record_id')],
 'relations': [('source_id', 'entities', 'entity_id'), ('target_id', 'entities', 'entity_id'), ('record_id', 'raw_records', 'record_id')],
 'issues': [('file_id', 'source_files', 'file_id'), ('record_id', 'raw_records', 'record_id')],
 'external_annotations': [('entity_id', 'entities', 'entity_id'), ('source_id', 'external_sources', 'source_id')],
}


def sql_rows(data, snapshot, report):
    rows = {'snapshots': [{'snapshot_id': snapshot, 'manifest_sha256': report['manifest_sha256'], 'metadata': report}]}
    for category, members in data.items():
        result = []
        for r in members:
            # Name conversions are explicit; JSON-valued columns are encoded only when writing SQL.
            transformed = dict(r)
            remap = {
             'source_files': {'file_id': 'id', 'attributes': None},
             'entities': {'entity_id': 'id', 'attributes': None},
             'raw_records': {'record_id': 'id', 'line_no': 'line', 'section_name': 'section', 'key_name': 'key', 'parsed_value': 'value', 'raw_text': 'raw'},
             'entity_evidence': {'evidence_id': 'id'},
             'items': {'definition_id': 'id', 'location_raw': 'location', 'consume_type_raw': 'consume_type', 'stack_limit_raw': 'stack_limit', 'icon_id_raw': 'icon_id'},
             'weapons': {'definition_id': 'id'}, 'features': {'definition_id': 'id'},
             'modes': {'definition_id': 'id', 'mode_id_raw': 'mode_id', 'player_limit_raw': 'player_limit', 'difficulty_raw': 'difficulty'},
             'maps': {'definition_id': None, 'logical_map_id_raw': 'logical_map_id', 'player_limit_raw': 'player_limit', 'difficulty_raw': 'difficulty'},
             'gameplay_rules': {'rule_id': 'id', 'key_name': 'key', 'parsed_value': 'value'},
             'currencies': {'currency_id': 'entity_id', 'exchange_rate_raw': 'exchange_rate'},
             'shop': {'definition_id': 'id', 'sale_tag_raw': 'sale_tag', 'buy_grade_raw': 'buy_grade'},
             'shop_options': {'option_id': 'id'}, 'shop_items': {'component_id': 'id'},
             'relations': {'relation_id': 'id'}, 'issues': {'issue_id': 'id'},
             'external_sources': {'source_id': 'id', 'attributes': None},
             'external_annotations': {'annotation_id': 'id', 'attributes': None},
            }.get(category, {})
            for target, source in remap.items():
                transformed[target] = r if source is None else r.get(source)
            if category == 'maps': transformed['definition_id'] = identity(r['entity_id'], r.get('record_id'), r['source_path'])
            if 'file_id' in dict(TABLES[category]):
                p = r.get('source_path') or (r.get('path') if category == 'source_files' else None)
                if category != 'source_files': transformed['file_id'] = identity(p) if p else None
            for column, sqltype in TABLES[category]:
                if sqltype == 'LONGTEXT' and transformed.get(column) is not None and not isinstance(transformed[column], str):
                    transformed[column] = canonical(transformed[column])
            transformed = {column: transformed.get(column) for column, _ in TABLES[category]}
            transformed['snapshot_id'] = snapshot
            result.append(transformed)
        rows[category] = result
    return rows


def sql_literal(value, json_type=False):
    if value is None: return 'NULL'
    if json_type: value = canonical(value)
    if isinstance(value, bool): return '1' if value else '0'
    if isinstance(value, (int, float)): return str(value)
    # Hex encoding avoids sql_mode/backslash/quote dependence, including Unicode and NULs.
    return "CONVERT(X'" + value.encode('utf-8').hex() + "' USING utf8mb4)"


def export_sql(rows, snapshot, output):
    schema = ['-- MySQL 5.7.8+ / 8.x. Select a dedicated database before running.',
              'SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci;']
    for name, columns in TABLES.items():
        statements = []
        if name != 'snapshots': statements.append('  snapshot_id CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL')
        for index, (col, typ) in enumerate(columns):
            statements.append(f'  `{col}` {typ}' + (' NOT NULL' if index == 0 else ' NULL'))
        pk = columns[0][0]
        statements.append('  PRIMARY KEY (' + ('' if name == 'snapshots' else 'snapshot_id,') + f'`{pk}`)')
        if name != 'snapshots':
            statements.append('  FOREIGN KEY (snapshot_id) REFERENCES nzcat_snapshots(snapshot_id)')
        for col, target, target_col in FOREIGN.get(name, []):
            statements.append(f'  FOREIGN KEY (snapshot_id,`{col}`) REFERENCES nzcat_{target}(snapshot_id,`{target_col}`)')
        if name == 'entities': statements.append('  KEY category_index (snapshot_id,category)')
        if name == 'issues': statements.append('  KEY issue_code_index (snapshot_id,code)')
        if name == 'source_files': statements.append('  KEY parse_status_index (snapshot_id,status)')
        schema.append(f'CREATE TABLE IF NOT EXISTS nzcat_{name} (\n' + ',\n'.join(statements) + '\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;')
    (output / '001_schema.sql').write_text('\n\n'.join(schema) + '\n', encoding='utf-8')
    # One row per statement provides exact auditability; bounded statements avoid packet limits.
    with (output / '002_seed.sql').open('w', encoding='utf-8', newline='\n') as stream:
        stream.write('-- Immutable snapshot data. Replay is idempotent; other snapshots are preserved.\nSET NAMES utf8mb4;\nSTART TRANSACTION;\n')
        for name, members in rows.items():
            types = dict(TABLES[name]); columns = (['snapshot_id'] if name != 'snapshots' else []) + list(types)
            stream.write(f'-- TABLE nzcat_{name}: {len(members)} rows\n')
            for r in members:
                values = ','.join(sql_literal(r.get(col), types.get(col) == 'JSON') for col in columns)
                pk = TABLES[name][0][0]
                statement = f'INSERT INTO nzcat_{name} (' + ','.join('`'+col+'`' for col in columns) + ') VALUES (' + values + f') ON DUPLICATE KEY UPDATE `{pk}`=nzcat_{name}.`{pk}`;\n'
                if len(statement.encode('utf-8')) >= 4 * 1024 * 1024:
                    raise ValueError('SQL statement exceeds 4 MiB packet budget: ' + name + '/' + str(r.get(pk)))
                stream.write(statement)
        stream.write('COMMIT;\n')
    checks = ['-- Run after schema/seed import. Every count_matches must equal 1.',
              'SET @snapshot_id=' + sql_literal(snapshot) + ';']
    for name, members in rows.items():
        checks.append(f"SELECT '{name}' AS dataset, COUNT(*) AS actual_count, {len(members)} AS expected_count, COUNT(*)={len(members)} AS count_matches FROM nzcat_{name} WHERE snapshot_id=@snapshot_id;")
        for col, target, target_col in FOREIGN.get(name, []):
            checks.append(f"SELECT '{name}.{col}' AS reference_check, COUNT(*) AS orphan_count FROM nzcat_{name} a LEFT JOIN nzcat_{target} b ON b.snapshot_id=a.snapshot_id AND b.`{target_col}`=a.`{col}` WHERE a.snapshot_id=@snapshot_id AND a.`{col}` IS NOT NULL AND b.`{target_col}` IS NULL;")
    checks += ["SELECT category,native_id,COUNT(*) AS duplicate_count FROM nzcat_entities WHERE snapshot_id=@snapshot_id AND native_id IS NOT NULL GROUP BY category,native_id HAVING COUNT(*)>1;",
               "SELECT code,COUNT(*) AS issue_count FROM nzcat_issues WHERE snapshot_id=@snapshot_id GROUP BY code;",
               "SELECT 'database_import_status' AS item,'This query must be run on your MySQL; static generation is not an import test' AS note;"]
    (output / '003_verify.sql').write_text('\n\n'.join(checks) + '\n', encoding='utf-8')


def md_text(value):
    return str(value if value is not None else '未确认').replace('|', '\\|').replace('\n', '<br>').replace('\r', '')


def write_markdown(data, report, output):
    names = {r['id']: r for r in data['entities']}
    annotations = collections.defaultdict(list)
    for row in data['external_annotations']: annotations[row['entity_id']].append(row)
    titles = {'items': '物品与道具', 'weapons': '武器类与属性', 'modes': '玩法与游戏模式', 'maps': '地图与地图资源',
              'features': '功能与配置系统', 'gameplay_rules': '玩法与功能配置明细', 'currencies': '货币代码',
              'shop': '商城商品', 'shop_options': '商城价格与期限', 'shop_items': '商品与礼包组成',
              'issues': '缺口与解析问题', 'external_sources': '外部资料来源',
              'external_annotations': '外部资料补充', 'relations': '内容关联',
              'source_files': '完整文件覆盖台账', 'raw_records': '原始配置记录', 'entity_evidence': '条目证据定位', 'entities': '全部内容条目'}
    index = ['# 当前客户端资产目录', '', f"快照：`{report['snapshot_id']}`", '',
             '客户端配置、资源线索和外部补充分别记录。配置存在不代表可用、在售、真实掉率或已实机验证。', '',
             '**实际 MySQL 数据库导入尚未验证。**', '',
             '[玩法与模式总表](GAMEPLAY_GUIDE.md) · [功能系统总表](FUNCTIONS_GUIDE.md) · [物品总表](ITEM_INDEX.md) · [商城总表](SHOP_INDEX.md) · [地图总表](MAP_INDEX.md)', '',
             '## 分类清单', '', '| 分类 | 记录数 | 文档 | JSON |', '|---|---:|---|---|']
    for category, rows in data.items():
        # Split large text documents into deterministic pages of 250 records.
        pages = [rows[start:start+250] for start in range(0, len(rows), 250)] or [[]]
        page_paths = []
        for number, page in enumerate(pages, 1):
            filename = f'{category}-{number:04d}.md'; page_paths.append(filename)
            with (output / filename).open('w', encoding='utf-8', newline='\n') as stream:
                stream.write(f'# {titles[category]} — {number}/{len(pages)}\n\n每条记录与 JSON 使用相同标识；空值表示未确认或不适用。\n\n')
                for row in page:
                    identifier = row.get('id', row.get('entity_id'))
                    entity = names.get(row.get('entity_id', row.get('product_id')), {})
                    stream.write(f"## {md_text(entity.get('name', row.get('name', row.get('path', row.get('code', identifier)))))}\n\n标识：`{identifier}`\n\n")
                    if entity:
                        stream.write(f"证据：{entity['evidence_status']}；实机：{entity['runtime_status']}。\n\n")
                        if entity.get('description'): stream.write(md_text(entity['description']) + '\n\n')
                    for annotation in annotations.get(row.get('entity_id'), []):
                        stream.write(f"外部补充：{md_text(annotation['display_name'])} — {annotation['summary']}（来源 `{annotation['source_id']}`；当前版本未核验）。\n\n")
                    stream.write('```json\n' + json.dumps(row, ensure_ascii=False, indent=2, allow_nan=False) + '\n```\n\n')
        (output / f'{category}.md').write_text(f'# {titles[category]}\n\n总记录数：{len(rows)}。\n\n' + '\n'.join(f'- [第 {i+1} 页]({p})' for i, p in enumerate(page_paths)) + '\n', encoding='utf-8')
        index.append(f"| {titles[category]} | {len(rows)} | [{category}.md]({category}.md) | [{category}.json](../json/{category}.json) |")
    index += ['', '## 覆盖与限制', '', '```json', json.dumps(report, ensure_ascii=False, indent=2), '```', '',
              '[字段字典](FIELD_DICTIONARY.md) · [数据库导入说明](../IMPORT_MYSQL.md)', '']
    (output / 'README.md').write_text('\n'.join(index), encoding='utf-8')
    dictionary = ['# 字段字典与解释边界', '',
                  '- `id/entity_id`：目录内部标识，不能当作客户端协议 ID；`native_id` 才是源配置中的 ID。',
                  '- `record_id/source_path/line/section/occurrence`：定位原始定义和重复记录。',
                  '- `operator`：保留 Unreal 的 `+ . - !` 操作；不将移除/清空指令解释为生效内容。',
                  '- `value/attributes`：结构化原值；`fields` 列表保留字段顺序及重复字段，数组保留顺序。',
                  '- `features.attributes` 为配置字段索引，完整值通过 record_id 查询 raw_records；避免把整个物品库或商城库重复嵌入功能条目。',
                  '- `raw`：完整原始行；未知语法仍保留，不能静默丢弃。',
                  '- `classification=WEAPON_CLASS_MATCH`：物品内部名称与本地武器类名匹配；不是官方分类枚举解释。',
                  '- `evidence_status`：CLIENT_DEFINITION 为客户端配置定义；RESOURCE_CLUE 为文件资源线索。',
                  '- `runtime_status=NOT_VALIDATED`：没有实机验证。外部证据始终另存，不修改本地值。',
                  '- `price_raw/period_raw/suite_period_raw`：按数组索引配对的客户端数值；期限单位及负值含义未推断。',
                  '- 空 PeriodArrayForSuite 是可选套装数组缺省；非空长度差异另记 SHOP_SUITE_ARRAY_LENGTH，不能当成价格/期限错误。',
                  '- `sale_date_raw/recall_date_raw`：源配置日期数值，不表示当前在售。',
                  '- `quantity=null`：源条目未明确数量；不会自行假定礼包每项数量为 1。',
                  '- `map_resource` 包括主图、子关卡及其他资源，不等同于可选地图数量。',
                  '- `feature` 为配置节；引擎支持类与玩家功能需结合类名和配置辨别，不宣称每个节都是玩家菜单。',
                  '- `issues` 记录缺损文件、解析问题、重复 ID 和未解析引用；未解析引用的目标为空。',
                  '', '## SQL 表与列', '']
    for name, columns in TABLES.items():
        dictionary += [f'### nzcat_{name}', '', '| 列 | 类型 |', '|---|---|']
        if name != 'snapshots': dictionary.append('| snapshot_id | CHAR(64), ASCII |')
        dictionary += [f'| {col} | {typ} |' for col, typ in columns] + ['']
    (output / 'FIELD_DICTIONARY.md').write_text('\n'.join(dictionary), encoding='utf-8')
    write_guides(data, names, annotations, output)


def write_guides(data, names, annotations, output):
    def table(path, title, intro, columns, rows):
        lines = ['# ' + title, '', intro, '', '| ' + ' | '.join(columns) + ' |',
                 '|' + '|'.join('---' for _ in columns) + '|']
        lines.extend('| ' + ' | '.join(md_text(cell) for cell in row) + ' |' for row in rows)
        (output / path).write_text('\n'.join(lines) + '\n', encoding='utf-8')
    modes = collections.defaultdict(list)
    for r in data['modes']: modes[r['entity_id']].append(r)
    def local_names(eid):
        return ' / '.join(dict.fromkeys(r['name'] for r in names[eid].get('localized_names', []))) or None
    table('GAMEPLAY_GUIDE.md', '玩法与模式总表',
          '每行是客户端明确引用的模式类；多个模式 ID、前缀或变体保留。历史补充列仅为类名对应推断，不代表当前规则已实机验证。完整配置见 modes.md 与 gameplay_rules.md。',
          ['内部标识 / 模式类', '本地显示名称', '客户端模式 ID', '地图前缀', '历史补充', '规则记录数'],
          [(eid, local_names(eid), ', '.join(sorted({str(r['mode_id']) for r in rows if r['mode_id'] is not None})) or None,
            ', '.join(sorted({r['prefix'] for r in rows if r['prefix']})) or None,
            ' / '.join(a['display_name'] + '：' + a['summary'] for a in annotations[eid]) or None,
            len({rid for r in rows for rid in r.get('rule_record_ids', [])})) for eid, rows in sorted(modes.items())])
    table('ITEM_INDEX.md', '物品、道具与武器总表',
          '所有源物品定义，包括隐藏条目。官方分类枚举与期限语义未确认；武器类匹配可在 weapons.md 和 relations.md 中追踪。完整描述、属性与字段见 items.md。',
          ['内部标识', '客户端 ID', '中文名称', '内部名称', '分类证据', '装备位置原值', '消耗类型原值', '隐藏', '图标 ID'],
          [(r['entity_id'], names[r['entity_id']]['native_id'], names[r['entity_id']]['name'], r['internal_name'],
            r['classification'], r['location'], r['consume_type'], r['hidden'], r['icon_id']) for r in data['items']])
    options = collections.defaultdict(list); parts = collections.defaultdict(list)
    for r in data['shop_options']: options[r['record_id']].append(r)
    for r in data['shop_items']: parts[r['record_id']].append(r)
    table('SHOP_INDEX.md', '商城商品总表',
          '价格与期限按数组位置展示原值，单位未确认。上下架日期与显示标志只表示配置，不能视为当前可购买。源配置未明确数量时礼包数量为空。完整字段见 shop.md。',
          ['内部标识', '商品 ID', '名称', '货币代码', '价格 / 期限 / 套装期限原值', '物品组成', '礼包标记', '显示标记', '上架 / 召回日期原值'],
          [(r['entity_id'], names[r['entity_id']]['native_id'], names[r['entity_id']]['name'], r['currency_id'],
            ' / '.join(f"[{o['option_index']}] {o['price_raw']} / {o['period_raw']} / {o['suite_period_raw']}" for o in options[r['record_id']]),
            ' / '.join(p['native_item_id'] + ' ' + names.get(p['item_id'], {}).get('name', '未解析引用') for p in parts[r['record_id']]),
            r['is_package'], r['show_commodity'], str(r['sale_date_raw']) + ' / ' + str(r['recall_date_raw'])) for r in data['shop']])
    table('MAP_INDEX.md', '逻辑地图总表',
          '此表仅列 GameMapSettings 定义的逻辑地图，物理地图资源和子关卡另见 maps.md。模式关联按配置前缀记录为线索。',
          ['内部标识', '客户端地图 ID', '本地名称', '地图资源名称', '人数原值', '难度原值'],
          [(r['entity_id'], r['logical_map_id'], names[r['entity_id']]['name'], fields(r['attributes']).get('MapName'),
            r['player_limit'], r['difficulty']) for r in data['maps'] if r.get('logical_map_id') is not None])
    # Group by class-name evidence, while exposing the rule used for classification.
    feature_patterns = [
        ('仓库与装备', r'Inventory|InvManager|Bag|Equip|ItemLibrary|Avatar'),
        ('商城与消费', r'Shop|Commodity|Purchase|Cart|Trading|Exchange'),
        ('任务成长成就', r'Task|Mission|Achievement|Rank|Level|Season|BattlePass'),
        ('社交与战队', r'Friend|Chat|Team|Family|Guild|Mail|QTalk'),
        ('福利活动抽奖', r'Lottery|Activity|Award|Reward|Gift|Chest|Luck'),
        ('合成升级强化', r'Synthe|Upgrade|Enhance|Decompose|ReBorn|Epic|Gem|Dye'),
        ('宠物技能与战斗', r'Pet|Skill|Trap|GameInfo|Match|PlayerController|Pawn'),
    ]
    rows = []
    for r in data['features']:
        group = next((label for label, pattern in feature_patterns if re.search(pattern, r['class_name'], re.I)), '其他配置或界面支持')
        rows.append((group, r['entity_id'], local_names(r['entity_id']), r['source_path'], len(r['record_ids'])))
    table('FUNCTIONS_GUIDE.md', '功能与配置系统总表',
          '按类名关键词分组，属于检索分类，不是菜单完整性或功能可用性结论。包含游戏功能、界面文案及底层支持类；所有字段见 features.md。',
          ['检索分类', '内部标识 / 类名', '本地标题', '来源', '字段记录数'], sorted(rows, key=lambda r: (r[0], r[1], r[3])))


def export_catalogue(catalog, snapshot, output):
    data = datasets(catalog)
    manifest_hash = hashlib.sha256(canonical([(r['path'], r['bytes'], r['sha256']) for r in data['source_files']]).encode('utf-8')).hexdigest()
    report = {'schema_version': 1, 'snapshot_id': snapshot,
              'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'source': 'USER_PROVIDED_LOCAL_DIRECTORY', 'retail_version': None,
              'manifest_sha256': manifest_hash, 'counts': {k: len(v) for k, v in data.items()},
              'file_status_counts': dict(collections.Counter(r['status'] for r in data['source_files'])),
              'issue_counts': dict(collections.Counter(r['code'] for r in data['issues'])),
              'entity_categories': dict(collections.Counter(r['category'] for r in data['entities'])),
              'client_files_unchanged': True, 'verification_method': 'TWO_FULL_SHA256_PASSES_AND_FILE_SET_CHECK',
              'database_import_status': 'NOT_VERIFIED_NO_MYSQL_CONNECTION',
              'completeness': 'ALL_FILES_INDEXED_RECOVERABLE_TEXT_EXTRACTED_GAPS_EXPLICIT',
              'runtime_validation': 'NOT_PERFORMED'}
    report['catalogue_definition_hash'] = getattr(catalog, 'definition_hash', None)
    report['capture_metadata'] = getattr(catalog, 'capture_metadata', None)
    for sub in ['json', 'md', 'sql']: (output / sub).mkdir(parents=True, exist_ok=True)
    for category, rows in data.items():
        (output / 'json' / (category + '.json')).write_text(json.dumps(rows, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    (output / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    rows = sql_rows(data, snapshot, report)
    export_sql(rows, snapshot, output / 'sql')
    write_markdown(data, report, output / 'md')
    (output / 'IMPORT_MYSQL.md').write_text('''# MySQL 导入与复验

目标语法：MySQL 5.7.8+ / 8.x，InnoDB、utf8mb4、JSON。实际数据库导入尚未验证。

使用专用空目录库，先备份要复用的现有数据库。脚本使用 `nzcat_` 前缀，不迁移现有业务表。
不要向已存在且结构不兼容的同名表导入；建表脚本不负责迁移旧结构。

在 MySQL 交互客户端执行，密码使用 `-p` 交互输入，不写入文件或命令行：

```text
mysql --default-character-set=utf8mb4 -u YOUR_USER -p
CREATE DATABASE IF NOT EXISTS nz_client_catalog CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE nz_client_catalog;
source C:/absolute/path/client-catalog/sql/001_schema.sql;
source C:/absolute/path/client-catalog/sql/002_seed.sql;
source C:/absolute/path/client-catalog/sql/003_verify.sql;
```

替换 source 路径为本交付目录的实际绝对路径。运行 SQL 时遇到任何错误应停止，不使用忽略错误模式。
导入数据使用事务；建表 DDL 单独执行，因为 MySQL DDL 会隐式提交。
数据脚本可能较大，逐条 INSERT 不要求整体文件装入客户端内存。

验收：所有 `count_matches` 为 1，所有 `orphan_count` 为 0。
问题分组结果中的未解析引用是已登记的客户端缺口，不应通过伪造物品解决。
重复执行 002_seed.sql 后再执行 003_verify.sql，计数必须不变。
不同客户端快照保留独立 snapshot_id，查询时始终限定 snapshot_id。
本快照重复导入为无变化操作，不会覆盖既有记录；不要在已导入快照内人工修改元数据。

检查语句示例：

```sql
SELECT snapshot_id, metadata FROM nzcat_snapshots;
SELECT entity_id, native_id, name FROM nzcat_entities
WHERE snapshot_id='YOUR_SNAPSHOT_ID' AND category='item';
SELECT p.entity_id, e.name, o.option_index, o.price_raw, o.period_raw
FROM nzcat_shop p JOIN nzcat_entities e
 ON e.snapshot_id=p.snapshot_id AND e.entity_id=p.entity_id
JOIN nzcat_shop_options o ON o.snapshot_id=p.snapshot_id AND o.record_id=p.record_id
WHERE p.snapshot_id='YOUR_SNAPSHOT_ID';
```

## 本地静态复验

运行 `python -m tools.client_catalog_verify --output <交付目录>`，核对文件摘要、JSON/MD/SQL 行数与字段、外键引用。
静态验证不能代替真实 MySQL 导入；SQL 中保留 NOT_VERIFIED 状态。
''', encoding='utf-8')
    manifest = [{'path': p.relative_to(output).as_posix(), 'bytes': p.stat().st_size,
                 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                for p in sorted(output.rglob('*')) if p.is_file()]
    (output / 'delivery-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def verify_output(output):
    report = json.loads((output / 'summary.json').read_text(encoding='utf-8'))
    data = {name: json.loads((output / 'json' / (name + '.json')).read_text(encoding='utf-8')) for name in report['counts']}
    expected = sql_rows(data, report['snapshot_id'], report)
    problems, seen_counts = [], collections.Counter()
    # Verify each complete INSERT against exactly the JSON-derived expected values.
    with (output / 'sql/002_seed.sql').open(encoding='utf-8') as stream:
        for line in stream:
            if not line.startswith('INSERT INTO '): continue
            if len(line.encode('utf-8')) >= 4 * 1024 * 1024: problems.append('SQL packet budget exceeded')
            match = re.fullmatch(r'INSERT INTO nzcat_(\w+) \(([^\n]+)\) VALUES \(([^\n]+)\) ON DUPLICATE KEY UPDATE `([^`]+)`=nzcat_\1\.`\4`;\n', line)
            if not match:
                problems.append('Invalid INSERT syntax'); continue
            table, column_text, values, _ = match.groups()
            index = seen_counts[table]; seen_counts[table] += 1
            if table not in expected or index >= len(expected[table]):
                problems.append('Unexpected SQL row: ' + table); continue
            columns = [c.strip('`') for c in column_text.split(',')]
            types = dict(TABLES[table])
            row = expected[table][index]
            expected_columns = (['snapshot_id'] if table != 'snapshots' else []) + list(types)
            if columns != expected_columns: problems.append(f'SQL column mismatch: {table}/{index}')
            wanted = ','.join(sql_literal(row.get(col), types.get(col) == 'JSON') for col in columns)
            if values != wanted: problems.append(f'SQL/JSON field mismatch: {table}/{index}')
    for table, rows in expected.items():
        if seen_counts[table] != len(rows): problems.append('SQL count mismatch: ' + table)
        primary = TABLES[table][0][0]
        if len({r[primary] for r in rows}) != len(rows): problems.append('Duplicate primary key: ' + table)
        for col, target, target_col in FOREIGN.get(table, []):
            valid = {r[target_col] for r in expected[target]}
            if any(r.get(col) is not None and r[col] not in valid for r in rows): problems.append('Orphan reference: ' + table + '.' + col)
    for category, rows in data.items():
        if len(rows) != report['counts'][category]: problems.append('JSON count mismatch: ' + category)
        md_rows = 0
        for page in sorted((output / 'md').glob(category + '-*.md')):
            text = page.read_text(encoding='utf-8')
            for block in re.findall(r'```json\n(.*?)\n```', text, flags=re.S):
                if md_rows >= len(rows) or json.loads(block) != rows[md_rows]:
                    problems.append(f'MD/JSON field mismatch: {category}/{md_rows}')
                md_rows += 1
        if md_rows != len(rows): problems.append('MD count mismatch: ' + category)
    for entry in json.loads((output / 'delivery-manifest.json').read_text(encoding='utf-8')):
        path = (output / entry['path']).resolve()
        if output.resolve() not in path.parents:
            problems.append('Unsafe manifest path'); continue
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            problems.append('Delivery digest mismatch: ' + entry['path'])
    result = {'passed': not problems, 'checks': ['SQL_JSON_ALL_FIELDS', 'MD_JSON_ALL_FIELDS', 'COUNTS', 'PRIMARY_KEYS', 'FOREIGN_REFERENCES', 'DELIVERY_SHA256'],
              'problems': problems, 'mysql_import': 'NOT_VERIFIED'}
    (output / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result
