"""从当前 Django 模型、路由和已固定原生协议提取设计书来源，不连接数据库或读取凭据。"""
import ast
import inspect
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'outputs/01a15734-c809-7ff2-8313-f617805a4441/design-sources.json'


def source_position(model, field=None):
    """按模型声明定位相应字段；继承/自动主键回退到类定义行，不伪造不存在的声明。"""
    path = Path(inspect.getsourcefile(model))
    tree = ast.parse(path.read_text(encoding='utf-8'))
    line = 1
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == model.__name__:
            line = node.lineno
            for statement in node.body:
                if isinstance(statement, ast.Assign) and any(isinstance(target, ast.Name) and target.id == field for target in statement.targets):
                    line = statement.lineno
    return f'{path.relative_to(ROOT).as_posix()}:{line}'


def find_line(path, text):
    """返回来源文件中首个完整匹配片段的真实行号；找不到时只保留文件路径。"""
    for line, value in enumerate((ROOT / path).read_text(encoding='utf-8').splitlines(), 1):
        if text in value:
            return f'{path}:{line}'
    return path


def sql_type(field):
    """依据 Django MySQL 映射展示字段类型；不打开连接，也不宣称回读了运行中数据库。"""
    if field.is_relation:
        return sql_type(field.target_field)
    kind = field.get_internal_type()
    types = {'BigAutoField': 'bigint AUTO_INCREMENT', 'AutoField': 'int AUTO_INCREMENT',
             'BigIntegerField': 'bigint', 'IntegerField': 'int', 'SmallIntegerField': 'smallint',
             'PositiveIntegerField': 'int unsigned', 'PositiveSmallIntegerField': 'smallint unsigned',
             'PositiveBigIntegerField': 'bigint unsigned', 'TextField': 'longtext', 'JSONField': 'json',
             'BooleanField': 'tinyint(1)', 'FloatField': 'double', 'DateTimeField': 'datetime(6)', 'DateField': 'date'}
    return f'varchar({field.max_length})' if kind == 'CharField' else types.get(kind, kind)


def main():
    """初始化应用注册表后导出完整逻辑结构、源行号和协议目录；不写入业务数据库。"""
    from django.conf import settings
    # 设计书仅需要模型注册表；独立最小设置不导入运行时settings，因而不读取.env或真实连接配置。
    settings.configure(INSTALLED_APPS=['revival.apps.RevivalConfig'],
                       DATABASES={'default': {'ENGINE': 'django.db.backends.mysql', 'NAME': 'design_metadata_only'}},
                       DEFAULT_AUTO_FIELD='django.db.models.BigAutoField', USE_TZ=True,
                       SECRET_KEY='design-metadata-only-not-a-runtime-secret')
    import django
    django.setup()
    from django.apps import apps
    from django.db.models import NOT_PROVIDED
    from revival.service import COMMANDS, PERMISSIONS
    from nzproject.urls import urlpatterns
    schema, fields, constraints, relations = [], [], [], []
    for model in sorted(apps.get_app_config('revival').get_models(), key=lambda value: value._meta.db_table):
        meta = model._meta
        schema.append({'table': meta.db_table, 'model': model.__name__, 'description': inspect.getdoc(model) or '',
                       'field_count': len(meta.concrete_fields), 'constraint_count': len(meta.constraints),
                       'index_count': len(meta.indexes), 'source': source_position(model)})
        for field in meta.concrete_fields:
            default = field.default
            default_text = '无默认值' if default is NOT_PROVIDED else (
                f'工厂 {default.__name__}' if callable(default) else json.dumps(default, ensure_ascii=False))
            fields.append({'table': meta.db_table, 'column': field.column, 'field': field.name,
                           'sql_type': sql_type(field), 'nullable': bool(field.null), 'primary_key': bool(field.primary_key),
                           'unique': bool(field.unique), 'default': default_text, 'collation': getattr(field, 'db_collation', None) or '继承数据库排序规则',
                           'source': source_position(model, field.name)})
            if field.is_relation:
                relations.append({'table': meta.db_table, 'column': field.column,
                                  'target_table': field.related_model._meta.db_table, 'target_column': field.target_field.column,
                                  'on_delete': field.remote_field.on_delete.__name__, 'nullable': bool(field.null),
                                  'source': source_position(model, field.name)})
        for constraint in meta.constraints:
            constraints.append({'table': meta.db_table, 'kind': type(constraint).__name__, 'name': constraint.name,
                                'definition': repr(constraint), 'source': source_position(model)})
        for index in meta.indexes:
            constraints.append({'table': meta.db_table, 'kind': 'Index', 'name': index.name,
                                'definition': ', '.join(index.fields) or repr(index), 'source': source_position(model)})
    routes = [{'path': '/' + str(route.pattern), 'source': find_line('nzproject/urls.py', "path('" + str(route.pattern) + "'")}
              for route in urlpatterns]
    commands = [{'action': key, 'permission': permission, 'source': find_line('revival/service.py', "'" + key + "'")}
                for key, permission in sorted(COMMANDS.items())]
    native_path = ROOT / 'config/cn2020-feature-protocol-v13.json'
    native = json.loads(native_path.read_text(encoding='utf-8'))
    protocol = [{'command': f'0x{int(key):04X}', 'schema': value,
                 'status': '已固定结构；业务/实机以交接记录分项确认', 'source': 'config/cn2020-feature-protocol-v13.json:1'}
                for key, value in sorted(native['commands'].items(), key=lambda item: int(item[0]))]
    protocol_fields = []
    for name, definition in sorted(native['schemas'].items()):
        for item in definition['fields']:
            protocol_fields.append({'schema': name, 'name': item['name'], 'tdr_type': item['type'],
                                    'host_offset': item['offset'], 'host_size': item['size'], 'capacity': item['capacity'],
                                    'nested': item.get('nested') or '', 'reference': item.get('reference') or '',
                                    'source': 'config/cn2020-feature-protocol-v13.json:1'})
    api_contracts, section = [], '既有API合同'
    documentation = (ROOT / 'docs/API.md').read_text(encoding='utf-8').splitlines()
    for index, line in enumerate(documentation):
        if line.startswith('#'):
            section = line.lstrip('#').strip()
        if not line.startswith('|') or re.fullmatch(r'[|:\- ]+', line):
            continue
        if index + 1 < len(documentation) and re.fullmatch(r'[|:\- ]+', documentation[index + 1]):
            continue
        # 仅拆分反引号代码之外的列边界，保留 VICTORY|FAILURE 等真实联合类型。
        cells = [value.strip().replace('`', '') for value in re.split(r'\|(?=(?:[^`]*`[^`]*`)*[^`]*$)', line.strip('|'))]
        if cells:
            api_contracts.append({'section': section, 'entry': cells[0], 'contract': cells[1] if len(cells) > 1 else '',
                                  'permission': ' / '.join(cells[2:]), 'source': f'docs/API.md:{index + 1}'})
    try:
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = '无法读取'
    result = {'baseline_commit': commit, 'as_of': '2026-10-19 America/Los_Angeles',
              'tables': schema, 'fields': fields, 'constraints': constraints, 'relations': relations,
              'routes': routes, 'commands': commands, 'permissions': {key: sorted(value) for key, value in PERMISSIONS.items()},
              'native_commands': protocol, 'native_fields': protocol_fields, 'api_contracts': api_contracts,
              'migrations': sorted(path.name for path in (ROOT / 'revival/migrations').glob('[0-9]*.py'))}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: len(result[key]) for key in ('tables', 'fields', 'constraints', 'relations', 'routes', 'commands', 'native_commands', 'native_fields')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
