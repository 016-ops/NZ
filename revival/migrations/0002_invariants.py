"""安装 schema/部署锁、流水复合关联和禁止改写历史的触发器；非原子 DDL 可安全重试。"""
from django.db import migrations


# 通过 migration 初始化唯一部署锁和 schema 版本；业务服务不会在请求中自动创建或修改数据库结构。
def seed(apps, schema_editor):
    """通过 migration 初始化唯一部署锁和 schema 版本；业务服务不会在请求中自动创建或修改数据库结构。"""
    alias = schema_editor.connection.alias
    apps.get_model('revival','DeploymentLock').objects.using(alias).get_or_create(pk=1)
    apps.get_model('revival','SchemaVersion').objects.using(alias).get_or_create(version=1)


# MySQL DDL 不支持事务回滚，此步骤显式 atomic=False；检查已有约束以支持部分迁移失败后的安全重试。
def add_detail_link(apps, schema_editor):
    """MySQL DDL 不支持事务回滚，此步骤显式 atomic=False；检查已有约束以支持部分迁移失败后的安全重试。"""
    with schema_editor.connection.cursor() as cursor:
        constraints = schema_editor.connection.introspection.get_constraints(cursor, 'asset_entry_details')
    if 'fk_entry_detail' not in constraints:
        schema_editor.execute('ALTER TABLE asset_entry_details ADD CONSTRAINT fk_entry_detail FOREIGN KEY (operation_id, entry_no) REFERENCES asset_entries(operation_id, entry_no)')


# 迁移回退仅移除本步骤创建的复合外键，保留已导入或已提交的业务数据。
def remove_detail_link(apps, schema_editor):
    """迁移回退仅移除本步骤创建的复合外键，保留已导入或已提交的业务数据。"""
    schema_editor.execute('ALTER TABLE asset_entry_details DROP FOREIGN KEY fk_entry_detail')


operations = [migrations.RunPython(seed, migrations.RunPython.noop),
              migrations.RunPython(add_detail_link, remove_detail_link, atomic=False)]
for table in ('audit_events', 'asset_entries', 'asset_entry_details'):
    for operation in ('UPDATE','DELETE'):
        name = f'immutable_{table}_{operation.lower()}'
        operations.append(migrations.RunSQL(
            f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {operation} ON {table} FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Immutable history'",
            f'DROP TRIGGER {name}'))


class Migration(migrations.Migration):
    """声明依赖和数据库不变量迁移；执行顺序由 Django 迁移图管理。"""
    dependencies = [('revival','0001_initial')]
    operations = operations
