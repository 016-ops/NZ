"""Django ORM 逻辑列仓储与部署级串行事务入口；运行时不接受业务 SQL 或 SQLite 文件。"""
from contextlib import contextmanager
from django.db import connections, transaction
from django.db.models import Max, Q, Subquery
from .models import DeploymentLock, TABLE_MODELS


# 旧格式逻辑列字典；整数下标仅兼容单值聚合，序列化仍输出原业务键。
class Row(dict):
    """旧格式逻辑列字典；整数下标仅兼容单值聚合，序列化仍输出原业务键。"""
    def __getitem__(self, key):
        """按列名读取字段；整数下标兼容聚合结果，非法键继续抛出标准查找异常。"""
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


# 物化的业务结果集合，保留既有 fetchone/rowcount 调用语义，但所有数据库访问已由 ORM 执行。
class Rows(list):
    """物化的业务结果集合，保留既有 fetchone/rowcount 调用语义，但所有数据库访问已由 ORM 执行。"""
    def __init__(self, values=(), rowcount=None):
        """初始化当前对象依赖和状态；参数仅保存在对象中，具体持久化由显式事务入口处理。"""
        super().__init__(Row(v) for v in values)
        self.rowcount = len(self) if rowcount is None else rowcount

    def fetchone(self):
        """返回第一行业务字典；空结果返回 None，不触发新的数据库查询。"""
        return self[0] if self else None

    def fetchall(self):
        """返回已物化结果的列表副本，不触发新的数据库查询。"""
        return list(self)


# 将数据库旧列名映射到 Django 字段 attname；排除内部新增自增键，因此关系字段与原 HTTP/导入格式保持一致。
def column_map(model):
    """将数据库旧列名映射到 Django 字段 attname；排除内部新增自增键，因此关系字段与原 HTTP/导入格式保持一致。"""
    return {f.column: f.attname for f in model._meta.concrete_fields if f.name != 'internal_id'}


# 围绕 Django ORM 的逻辑列仓储；不接受或解析 SQL，业务模块调用明确的查询与写入操作。
class Repository:
    """围绕 Django ORM 的逻辑列仓储；不接受或解析 SQL，业务模块调用明确的查询与写入操作。"""
    def __init__(self, alias='default'):
        """初始化当前对象依赖和状态；参数仅保存在对象中，具体持久化由显式事务入口处理。"""
        self.alias = alias
        self.savepoints = {}

    def table(self, name):
        """将固定逻辑表名转换为指定数据库别名上的 QuerySet；不接收任意 SQL。"""
        return TABLE_MODELS[name].objects.using(self.alias)

    # 通过 ORM 条件、排序、分页读取明确逻辑列，立即物化成旧格式字典；事务结束后不会再延迟执行数据库查询。
    def select(self, name, condition=Q(), *, columns=None, order=(), limit=None, offset=0):
        """通过 ORM 条件、排序、分页读取明确逻辑列，立即物化成旧格式字典；事务结束后不会再延迟执行数据库查询。"""
        mapping = column_map(TABLE_MODELS[name])
        columns = columns or list(mapping)
        query = self.table(name).filter(condition).order_by(*order)
        query = query[offset:offset + limit] if limit is not None else query[offset:]
        return Rows({col: value[mapping[col]] for col in columns}
                    for value in query.values(*(mapping[col] for col in columns)))

    # 根据逻辑列映射构建模型，在嵌套 atomic 中执行；唯一冲突不会污染父事务，批量目标保存点仍能正常回滚。
    def insert(self, name, values, *, ignore=False):
        """根据逻辑列映射构建模型，在嵌套 atomic 中执行；唯一冲突不会污染父事务，批量目标保存点仍能正常回滚。"""
        mapping = column_map(TABLE_MODELS[name])
        values = {mapping[k]: v for k, v in values.items()}
        with transaction.atomic(using=self.alias):
            if ignore:
                self.table(name).get_or_create(**values)
            else:
                self.table(name).create(**values)
        return Rows(rowcount=1)

    def insert_many(self, name, values):
        """在已有事务中批量插入逻辑列，保留模型默认值和数据库约束，不忽略冲突。

        只供已完成整批业务校验的资产路径使用；不创建独立提交，也不允许事务外部分落库。
        """
        if not connections[self.alias].in_atomic_block:
            raise RuntimeError('BULK_INSERT_REQUIRES_TRANSACTION')
        model = TABLE_MODELS[name]
        mapping = column_map(model)
        rows = [model(**{mapping[k]: v for k, v in value.items()}) for value in values]
        model.objects.using(self.alias).bulk_create(rows, batch_size=1000)
        return Rows(rowcount=len(rows))

    # 只将允许的逻辑列传给 QuerySet.update；版本递增采用 F 表达式，避免读出后再写回旧版本。
    def update(self, name, condition, values):
        """只将允许的逻辑列传给 QuerySet.update；版本递增采用 F 表达式，避免读出后再写回旧版本。"""
        mapping = column_map(TABLE_MODELS[name])
        count = self.table(name).filter(condition).update(**{mapping[k]: v for k, v in values.items()})
        return Rows(rowcount=count)

    # 通过 ORM 删除明确筛选结果；历史流水和审计另外由数据库触发器拒绝删除，不能绕过持久化约束。
    def delete(self, name, condition):
        """通过 ORM 删除明确筛选结果；历史流水和审计另外由数据库触发器拒绝删除，不能绕过持久化约束。"""
        count, _ = self.table(name).filter(condition).delete()
        return Rows(rowcount=count)

    # 由 MySQL 执行 COUNT，返回兼容现有业务读取的单行结果；不把整个表加载进内存计数。
    def count(self, name, condition=Q()):
        """由 MySQL 执行 COUNT，返回兼容现有业务读取的单行结果；不把整个表加载进内存计数。"""
        return Rows([{'count': self.table(name).filter(condition).count()}])

    # 使用 ORM 聚合并显式处理空集合，保持旧 game_id 起始值及最大席位检查语义。
    def maximum(self, name, field, condition=Q(), *, fallback=None, increment=0):
        """使用 ORM 聚合并显式处理空集合，保持旧 game_id 起始值及最大席位检查语义。"""
        result = self.table(name).filter(condition).aggregate(value=Max(field))['value']
        value = fallback if result is None else result
        return Rows([{'value': value + increment if value is not None else None}])

    # 按账号和槽位唯一键原子创建或替换装备引用；外层已经验证归属、目录槽位与过期时间。
    def equip(self, account_id, slot, inventory_id):
        """按账号和槽位唯一键原子创建或替换装备引用；外层已经验证归属、目录槽位与过期时间。"""
        self.table('equipment').update_or_create(account_id=account_id, slot=slot, defaults={'inventory_id': inventory_id})

    # 按流水序号组合不可改写条目和详情，内部主键不外露；复合外键在 migration 中由 MySQL 强制保护。
    def entries(self, operation_id):
        """按流水序号组合不可改写条目和详情，内部主键不外露；复合外键在 migration 中由 MySQL 强制保护。"""
        rows = self.select('asset_entries', Q(operation_id=operation_id), order=('entry_no',))
        details = {r['entry_no']: r['detail'] for r in self.select('asset_entry_details', Q(operation_id=operation_id))}
        return Rows(dict(row, detail=details.get(row['entry_no'])) for row in rows)

    # 只读取账号当前未到期装备，按旧字段构造对局冻结快照；开始后的换装只影响下一局。
    def equipment_snapshot(self, account_id, now):
        """只读取账号当前未到期装备，按旧字段构造对局冻结快照；开始后的换装只影响下一局。"""
        query = self.table('equipment').filter(Q(account_id=account_id) &
                (Q(inventory__expires_at__isnull=True) | Q(inventory__expires_at__gt=now)))
        return Rows({'slot': r['slot'], 'id': r['inventory_id'], 'item_key': r['inventory__item_key'],
                     'quantity': r['inventory__quantity'], 'expires_at': r['inventory__expires_at']}
                    for r in query.values('slot','inventory_id','inventory__item_key','inventory__quantity','inventory__expires_at'))

    # 用 ORM 子查询找出没有任何未撤销且未过期玩家会话的席位；由维护事务统一释放。
    def expired_members(self, now):
        """用 ORM 子查询找出没有任何未撤销且未过期玩家会话的席位；由维护事务统一释放。"""
        active = self.table('sessions').filter(kind='player', revoked=0, expires_at__gt=now).values('subject_id')
        return Rows(self.table('room_members').exclude(account_id__in=Subquery(active)).values('account_id'))

    # 在当前 atomic 内建立命名保存点；仅供预览和批量子项使用，不在业务层拼接 SQL。
    def savepoint(self, name):
        """在当前 atomic 内建立命名保存点；仅供预览和批量子项使用，不在业务层拼接 SQL。"""
        self.savepoints[name] = transaction.savepoint(using=self.alias)

    # 回滚指定保存点后的全部写入，父事务和其他批量目标仍然保留。
    def rollback(self, name):
        """回滚指定保存点后的全部写入，父事务和其他批量目标仍然保留。"""
        transaction.savepoint_rollback(self.savepoints[name], using=self.alias)

    # 提交并移除已完成保存点，防止后续子项错误复用旧保存点标识。
    def release(self, name):
        """提交并移除已完成保存点，防止后续子项错误复用旧保存点标识。"""
        transaction.savepoint_commit(self.savepoints.pop(name), using=self.alias)


# 配置的 MySQL 别名与全局事务入口；禁止传 SQLite 路径，迁移结构与业务请求完全分离。
class Database:
    """配置的 MySQL 别名与全局事务入口；禁止传 SQLite 路径，迁移结构与业务请求完全分离。"""
    def __init__(self, alias='default'):
        """初始化当前对象依赖和状态；参数仅保存在对象中，具体持久化由显式事务入口处理。"""
        if alias not in connections:
            raise ValueError('Use a configured Django MySQL database alias, not a SQLite path')
        self.alias = alias

    @contextmanager
    def transaction(self):
        """锁住固定部署记录直到提交/回滚，模拟旧单写者边界；读视图也取得一致业务状态。"""
        if connections[self.alias].vendor != 'mysql':
            raise RuntimeError('The control plane requires MySQL')
        with transaction.atomic(using=self.alias):
            DeploymentLock.objects.using(self.alias).select_for_update().get(pk=1)
            yield Repository(self.alias)

    connect = transaction

    def backup(self, destination):
        """显式备份入口；恢复验收通过之后才返回成功，不支持覆盖已有文件。"""
        from .backup import backup
        return backup(self, destination)
