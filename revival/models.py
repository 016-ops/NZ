"""控制面持久化模型；保持旧业务列名并以 MySQL 约束保护身份、资产和房间关系。"""
from django.db import models
from django.db.models import Q, F

# 固定主键为 1 的单行锁；用 SELECT FOR UPDATE 保留本地控制面原有串行事务语义。
class DeploymentLock(models.Model):
    """固定主键为 1 的单行锁；用 SELECT FOR UPDATE 保留本地控制面原有串行事务语义。"""
    id = models.PositiveSmallIntegerField(primary_key=True)
    class Meta:
        """指定稳定业务表名和持久化约束；Django 依照这些元数据生成迁移。"""
        db_table = 'deployment_lock'

# 独立管理员与 MFA 防重放状态；不映射为 Django 默认密码体系，确保旧凭据迁移后仍可验证。
class Admin(models.Model):
    """独立管理员与 MFA 防重放状态；不映射为 Django 默认密码体系，确保旧凭据迁移后仍可验证。"""
    id = models.CharField(max_length=32, primary_key=True, db_collation='utf8mb4_0900_bin')
    login = models.CharField(max_length=32, db_collation='utf8mb4_0900_bin')
    password_hash = models.CharField(max_length=256, db_collation='utf8mb4_0900_bin')
    otp_secret = models.CharField(max_length=64, db_collation='utf8mb4_0900_bin')
    last_otp_step = models.BigIntegerField(default=-1)
    role = models.CharField(max_length=32, db_collation='utf8mb4_0900_bin')
    active = models.BigIntegerField(default=1)
    security_version = models.BigIntegerField(default=1)
    class Meta:
        """指定稳定业务表名和持久化约束；Django 依照这些元数据生成迁移。"""
        db_table = 'admins'
        constraints = [models.UniqueConstraint(fields=['login'], name='uq_admins_0'), models.CheckConstraint(condition=Q(role__in=['owner','users','resources','auditor']) & Q(active__in=[0,1]), name='ck_admins')]

# 玩家业务账号；game_id 保留本地整数映射，安全版本决定旧会话与冻结对局资格是否有效。
class Account(models.Model):
    """玩家业务账号；game_id 保留本地整数映射，安全版本决定旧会话与冻结对局资格是否有效。"""
    id = models.CharField(max_length=32, primary_key=True, db_collation='utf8mb4_0900_bin')
    game_id = models.BigIntegerField()
    login = models.CharField(max_length=32, db_collation='utf8mb4_0900_bin')
    password_hash = models.CharField(max_length=256, db_collation='utf8mb4_0900_bin')
    status = models.CharField(max_length=32, db_collation='utf8mb4_0900_bin')
    security_version = models.BigIntegerField(default=1)
    created_at = models.BigIntegerField()
    last_login = models.BigIntegerField(null=True)
    class Meta:
        """指定稳定业务表名和持久化约束；Django 依照这些元数据生成迁移。"""
        db_table = 'accounts'
        constraints = [models.UniqueConstraint(fields=['login'], name='uq_accounts_0'), models.UniqueConstraint(fields=['game_id'], name='uq_accounts_1'), models.CheckConstraint(condition=Q(status__in=['PENDING_REVIEW','ACTIVE','DISABLED']), name='ck_accounts')]

# 邀请只存 token 摘要；次数、期限和撤销状态在同一部署锁内检查并消费。
class Invitation(models.Model):
    """邀请只存 token 摘要；次数、期限和撤销状态在同一部署锁内检查并消费。"""
    id = models.CharField(max_length=32, primary_key=True, db_collation='utf8mb4_0900_bin')
    token_hash = models.CharField(max_length=64, db_collation='utf8mb4_0900_bin')
    max_uses = models.BigIntegerField()
    uses = models.BigIntegerField(default=0)
    expires_at = models.BigIntegerField()
    revoked = models.BigIntegerField(default=0)
    class Meta:
        """指定稳定业务表名和持久化约束；Django 依照这些元数据生成迁移。"""
        db_table = 'invitations'
        constraints = [models.UniqueConstraint(fields=['token_hash'], name='uq_invitations_0'), models.CheckConstraint(condition=Q(max_uses__gt=0) & Q(uses__gte=0) & Q(uses__lte=F('max_uses')), name='ck_invitations')]

# 账号至多一个角色；昵称使用二进制排序与唯一约束，保留 Unicode 和大小写区分行为。
class Character(models.Model):
    """账号至多一个角色；昵称使用二进制排序与唯一约束，保留 Unicode 和大小写区分行为。"""
    id = models.CharField(max_length=32, primary_key=True, db_collation='utf8mb4_0900_bin')
    account = models.OneToOneField('Account', on_delete=models.DO_NOTHING, db_column='account_id')
    nickname = models.CharField(max_length=32, db_collation='utf8mb4_0900_bin')
    init_state = models.CharField(max_length=32, db_collation='utf8mb4_0900_bin')
    profile = models.CharField(max_length=100, db_collation='utf8mb4_0900_bin')
    class Meta:
        """指定稳定业务表名和持久化约束；Django 依照这些元数据生成迁移。"""
        db_table = 'characters'
        constraints = [models.UniqueConstraint(fields=['nickname'], name='uq_characters_0'), models.UniqueConstraint(fields=['account'], name='uq_characters_1'), models.CheckConstraint(condition=Q(init_state='READY'), name='ck_characters')]

# 玩家/管理员共用摘要会话表；subject_id 为多态引用，服务每次请求检查身份、安全版本和有效期限。
class Session(models.Model):
    """玩家/管理员共用摘要会话表；subject_id 为多态引用，服务每次请求检查身份、安全版本和有效期限。"""
    token_hash = models.CharField(max_length=64, primary_key=True, db_collation='utf8mb4_0900_bin')
    subject_id = models.CharField(max_length=32, db_collation='utf8mb4_0900_bin')
    kind = models.CharField(max_length=32, db_collation='utf8mb4_0900_bin')
    security_version = models.BigIntegerField()
    expires_at = models.BigIntegerField()
    revoked = models.BigIntegerField(default=0)
    class Meta:
        """指定稳定业务表名和持久化约束；Django 依照这些元数据生成迁移。"""
        db_table = 'sessions'
        constraints = [models.CheckConstraint(condition=Q(kind__in=['player','admin']), name='ck_sessions')]

# 多个制裁可叠加；永久制裁期限为 NULL，撤销一条后仍检查其他有效制裁。
class Sanction(models.Model):
    """多个制裁可叠加；永久制裁期限为 NULL，撤销一条后仍检查其他有效制裁。"""
    id = models.CharField(max_length=32, primary_key=True, db_collation='utf8mb4_0900_bin')
    account = models.ForeignKey('Account', on_delete=models.DO_NOTHING, db_column='account_id')
    reason = models.TextField(db_collation='utf8mb4_0900_bin')
    starts_at = models.BigIntegerField()
    expires_at = models.BigIntegerField(null=True)
    revoked = models.BigIntegerField(default=0)
    class Meta:
        """指定稳定业务表名和持久化约束；Django 依照这些元数据生成迁移。"""
        db_table = 'sanctions'

# 只追加审计历史，detail 只记录安全业务摘要；禁止 UPDATE/DELETE 的保护位于 MySQL 触发器。
class AuditEvent(models.Model):
    """只追加审计历史，detail 只记录安全业务摘要；禁止 UPDATE/DELETE 的保护位于 MySQL 触发器。"""
    id = models.BigAutoField(primary_key=True)
    actor = models.CharField(max_length=160, db_collation='utf8mb4_0900_bin')
    action = models.CharField(max_length=100, db_collation='utf8mb4_0900_bin')
    target = models.CharField(max_length=160, null=True, db_collation='utf8mb4_0900_bin')
    request_id = models.CharField(max_length=128, db_collation='utf8mb4_0900_bin')
    time = models.BigIntegerField()
    result = models.TextField(db_collation='utf8mb4_0900_bin')
    reason = models.TextField(db_collation='utf8mb4_0900_bin')
    detail = models.TextField(db_collation='utf8mb4_0900_bin')
    class Meta:
        """指定稳定业务表名和持久化约束；Django 依照这些元数据生成迁移。"""
        db_table = 'audit_events'

# 操作者 scope 与 key 形成业务唯一键；新增 internal_id 仅为 ORM 使用，不进入接口或旧库逐行对账。
class Request(models.Model):
    """操作者 scope 与 key 形成业务唯一键；新增 internal_id 仅为 ORM 使用，不进入接口或旧库逐行对账。"""
    internal_id = models.BigAutoField(primary_key=True)
    scope = models.CharField(max_length=160, db_collation='utf8mb4_0900_bin')
    key = models.CharField(max_length=128, db_collation='utf8mb4_0900_bin')
    payload_hash = models.CharField(max_length=64, db_collation='utf8mb4_0900_bin')
    result = models.TextField(db_collation='utf8mb4_0900_bin')
    class Meta:
        """指定稳定业务表名和持久化约束；Django 依照这些元数据生成迁移。"""
        db_table = 'requests'
        constraints = [models.UniqueConstraint(fields=['scope', 'key'], name='uq_requests_key')]

# 每账号每币种唯一，余额非负；版本递增与资产流水、审计和 outbox 原子提交。
class Wallet(models.Model):
    """每账号每币种唯一，余额非负；版本递增与资产流水、审计和 outbox 原子提交。"""
    internal_id = models.BigAutoField(primary_key=True)
    account = models.ForeignKey('Account', on_delete=models.DO_NOTHING, db_column='account_id')
    currency = models.CharField(max_length=32, db_collation='utf8mb4_0900_bin')
    balance = models.BigIntegerField()
    version = models.BigIntegerField(default=0)
    class Meta:
        """指定稳定业务表名和持久化约束；Django 依照这些元数据生成迁移。"""
        db_table = 'wallets'
        constraints = [models.UniqueConstraint(fields=['account', 'currency'], name='uq_wallets_key'), models.CheckConstraint(condition=Q(balance__gte=0), name='ck_wallets')]

# 库存实例独立记录数量、期限、绑定及版本；业务层执行目录容量与道具类型限制。
class Inventory(models.Model):
    """库存实例独立记录数量、期限、绑定及版本；业务层执行目录容量与道具类型限制。"""
    id = models.CharField(max_length=32, primary_key=True, db_collation='utf8mb4_0900_bin')
    account = models.ForeignKey('Account', on_delete=models.DO_NOTHING, db_column='account_id')
    item_key = models.CharField(max_length=100, db_collation='utf8mb4_0900_bin')
    quantity = models.BigIntegerField()
    expires_at = models.BigIntegerField(null=True)
    bound = models.BigIntegerField(default=1)
    version = models.BigIntegerField(default=1)
    class Meta:
        """指定稳定业务表名和持久化约束；Django 依照这些元数据生成迁移。"""
        db_table = 'inventory'
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name='ck_inventory')]

# 账号与槽位唯一，引用已验证归属的库存实例；开始对局时复制到冻结快照。
class Equipment(models.Model):
    """账号与槽位唯一，引用已验证归属的库存实例；开始对局时复制到冻结快照。"""
    internal_id = models.BigAutoField(primary_key=True)
    account = models.ForeignKey('Account', on_delete=models.DO_NOTHING, db_column='account_id')
    slot = models.CharField(max_length=32, db_collation='utf8mb4_0900_bin')
    inventory = models.ForeignKey('Inventory', on_delete=models.DO_NOTHING, db_column='inventory_id')
    class Meta:
        """指定稳定业务表名和持久化约束；Django 依照这些元数据生成迁移。"""
        db_table = 'equipment'
        constraints = [models.UniqueConstraint(fields=['account', 'slot'], name='uq_equipment_key')]

# 资产操作头和关联补偿历史；MANUAL_REVIEW 表示不能自动追回，不能删除原发放。
class AssetOperation(models.Model):
    """资产操作头和关联补偿历史；MANUAL_REVIEW 表示不能自动追回，不能删除原发放。"""
    id = models.CharField(max_length=32, primary_key=True, db_collation='utf8mb4_0900_bin')
    account = models.ForeignKey('Account', on_delete=models.DO_NOTHING, db_column='account_id')
    actor = models.CharField(max_length=160, db_collation='utf8mb4_0900_bin')
    source = models.CharField(max_length=100, db_collation='utf8mb4_0900_bin')
    reason = models.TextField(db_collation='utf8mb4_0900_bin')
    created_at = models.BigIntegerField()
    status = models.CharField(max_length=32, db_collation='utf8mb4_0900_bin')
    linked_operation = models.ForeignKey('AssetOperation', on_delete=models.DO_NOTHING, db_column='linked_operation', null=True)
    class Meta:
        """指定稳定业务表名和持久化约束；Django 依照这些元数据生成迁移。"""
        db_table = 'asset_operations'
        constraints = [models.CheckConstraint(condition=Q(status__in=['COMMITTED','MANUAL_REVIEW']), name='ck_asset_operations')]

# 每操作内 entry_no 唯一，保存前值、增量、后值及实例线索；MySQL 禁止改写或删除。
class AssetEntry(models.Model):
    """每操作内 entry_no 唯一，保存前值、增量、后值及实例线索；MySQL 禁止改写或删除。"""
    internal_id = models.BigAutoField(primary_key=True)
    operation = models.ForeignKey('AssetOperation', on_delete=models.DO_NOTHING, db_column='operation_id')
    entry_no = models.BigIntegerField()
    kind = models.CharField(max_length=32, db_collation='utf8mb4_0900_bin')
    resource = models.CharField(max_length=100, db_collation='utf8mb4_0900_bin')
    instance_id = models.CharField(max_length=32, null=True, db_collation='utf8mb4_0900_bin')
    delta = models.BigIntegerField()
    before_value = models.BigIntegerField()
    after_value = models.BigIntegerField()
    class Meta:
        """指定稳定业务表名和持久化约束；Django 依照这些元数据生成迁移。"""
        db_table = 'asset_entries'
        constraints = [models.UniqueConstraint(fields=['operation', 'entry_no'], name='uq_asset_entries_key')]

# 业务提交后的待确认事件；event_key 去重，尝试次数和下次重试仅影响通知，不重做资产。
class Outbox(models.Model):
    """业务提交后的待确认事件；event_key 去重，尝试次数和下次重试仅影响通知，不重做资产。"""
    id = models.BigAutoField(primary_key=True)
    event_key = models.CharField(max_length=200, db_collation='utf8mb4_0900_bin')
    type = models.CharField(max_length=100, db_collation='utf8mb4_0900_bin')
    account_id = models.TextField(null=True, db_collation='utf8mb4_0900_bin')
    payload = models.TextField(db_collation='utf8mb4_0900_bin')
    status = models.CharField(max_length=32, default='PENDING', db_collation='utf8mb4_0900_bin')
    attempts = models.BigIntegerField(default=0)
    next_retry = models.BigIntegerField(default=0)
    delivered_at = models.BigIntegerField(null=True)
    last_error = models.TextField(null=True, db_collation='utf8mb4_0900_bin')
    class Meta:
        """指定稳定业务表名和持久化约束；Django 依照这些元数据生成迁移。"""
        db_table = 'outbox'
        constraints = [models.UniqueConstraint(fields=['event_key'], name='uq_outbox_0')]

# 保留旧 operation_id/entry_no 列，并由 migration 建立到条目的复合外键；禁止改写详情。
class AssetEntryDetail(models.Model):
    """保留旧 operation_id/entry_no 列，并由 migration 建立到条目的复合外键；禁止改写详情。"""
    internal_id = models.BigAutoField(primary_key=True)
    operation_id = models.CharField(max_length=32, db_collation='utf8mb4_0900_bin')
    entry_no = models.BigIntegerField()
    detail = models.TextField(db_collation='utf8mb4_0900_bin')
    class Meta:
        """指定稳定业务表名和持久化约束；Django 依照这些元数据生成迁移。"""
        db_table = 'asset_entry_details'
        constraints = [models.UniqueConstraint(fields=['operation_id', 'entry_no'], name='uq_asset_entry_details_key')]

# 房间状态与配置 revision；match_id 保留旧弱引用以避免房间与对局创建形成循环外键。
class Room(models.Model):
    """房间状态与配置 revision；match_id 保留旧弱引用以避免房间与对局创建形成循环外键。"""
    id = models.CharField(max_length=32, primary_key=True, db_collation='utf8mb4_0900_bin')
    owner = models.ForeignKey('Account', on_delete=models.DO_NOTHING, db_column='owner')
    map_key = models.CharField(max_length=100, db_collation='utf8mb4_0900_bin')
    capacity = models.BigIntegerField()
    state = models.CharField(max_length=32, db_collation='utf8mb4_0900_bin')
    revision = models.BigIntegerField(default=1)
    match_id = models.CharField(max_length=32, null=True, db_collation='utf8mb4_0900_bin')
    class Meta:
        """指定稳定业务表名和持久化约束；Django 依照这些元数据生成迁移。"""
        db_table = 'rooms'
        constraints = [models.CheckConstraint(condition=Q(capacity__gt=0), name='ck_rooms')]

# 账号作为主键保证一个玩家只占一个房间；room/slot 唯一约束保护最后席位并发。
class RoomMember(models.Model):
    """账号作为主键保证一个玩家只占一个房间；room/slot 唯一约束保护最后席位并发。"""
    account = models.OneToOneField('Account', on_delete=models.DO_NOTHING, db_column='account_id', primary_key=True)
    room = models.ForeignKey('Room', on_delete=models.DO_NOTHING, db_column='room_id')
    slot = models.BigIntegerField()
    ready = models.BigIntegerField(default=0)
    joined_at = models.BigIntegerField()
    class Meta:
        """指定稳定业务表名和持久化约束；Django 依照这些元数据生成迁移。"""
        db_table = 'room_members'
        constraints = [models.UniqueConstraint(fields=['room', 'slot'], name='uq_room_members_0')]

# 冻结的实例、epoch、目录/玩家快照和租约；节点令牌独立于玩家与管理员会话。
class Match(models.Model):
    """冻结的实例、epoch、目录/玩家快照和租约；节点令牌独立于玩家与管理员会话。"""
    id = models.CharField(max_length=32, primary_key=True, db_collation='utf8mb4_0900_bin')
    room = models.ForeignKey('Room', on_delete=models.DO_NOTHING, db_column='room_id')
    instance_id = models.CharField(max_length=32, db_collation='utf8mb4_0900_bin')
    epoch = models.CharField(max_length=32, db_collation='utf8mb4_0900_bin')
    state = models.CharField(max_length=32, db_collation='utf8mb4_0900_bin')
    snapshot = models.TextField(db_collation='utf8mb4_0900_bin')
    node_token_hash = models.CharField(max_length=64, db_collation='utf8mb4_0900_bin')
    created_at = models.BigIntegerField()
    lease_until = models.BigIntegerField()
    class Meta:
        """指定稳定业务表名和持久化约束；Django 依照这些元数据生成迁移。"""
        db_table = 'matches'
        constraints = [models.UniqueConstraint(fields=['instance_id'], name='uq_matches_0')]

# 每局每玩家唯一；eligible 与 security_version 在结算时共同决定奖励资格。
class MatchPlayer(models.Model):
    """每局每玩家唯一；eligible 与 security_version 在结算时共同决定奖励资格。"""
    internal_id = models.BigAutoField(primary_key=True)
    match = models.ForeignKey('Match', on_delete=models.DO_NOTHING, db_column='match_id')
    account = models.ForeignKey('Account', on_delete=models.DO_NOTHING, db_column='account_id')
    security_version = models.BigIntegerField()
    eligible = models.BigIntegerField(default=1)
    class Meta:
        """指定稳定业务表名和持久化约束；Django 依照这些元数据生成迁移。"""
        db_table = 'match_players'
        constraints = [models.UniqueConstraint(fields=['match', 'account'], name='uq_match_players_key')]

# 每局只保存一个结算摘要和结果；同局同内容重试不会产生重复奖励。
class Settlement(models.Model):
    """每局只保存一个结算摘要和结果；同局同内容重试不会产生重复奖励。"""
    match = models.OneToOneField('Match', on_delete=models.DO_NOTHING, db_column='match_id', primary_key=True)
    payload_hash = models.CharField(max_length=64, db_collation='utf8mb4_0900_bin')
    result = models.TextField(db_collation='utf8mb4_0900_bin')
    class Meta:
        """指定稳定业务表名和持久化约束；Django 依照这些元数据生成迁移。"""
        db_table = 'settlements'

# 保留旧 schema v1 标记用于源格式兼容；真正的结构升级由 django_migrations 管理。
class SchemaVersion(models.Model):
    """保留旧 schema v1 标记用于源格式兼容；真正的结构升级由 django_migrations 管理。"""
    version = models.BigIntegerField(primary_key=True)
    class Meta:
        """指定稳定业务表名和持久化约束；Django 依照这些元数据生成迁移。"""
        db_table = 'schema_version'
        constraints = [models.CheckConstraint(condition=Q(version=1), name='ck_schema_version')]

# 记住数据库 profile，拒绝当前客户端与合成规则混用；旧库其他元数据也完整导入。
class DeploymentMetadata(models.Model):
    """记住数据库 profile，拒绝当前客户端与合成规则混用；旧库其他元数据也完整导入。"""
    key = models.CharField(max_length=128, primary_key=True, db_collation='utf8mb4_0900_bin')
    value = models.CharField(max_length=200, db_collation='utf8mb4_0900_bin')
    class Meta:
        """指定稳定业务表名和持久化约束；Django 依照这些元数据生成迁移。"""
        db_table = 'deployment_metadata'

TABLE_MODELS = {
    'admins': Admin,
    'accounts': Account,
    'invitations': Invitation,
    'characters': Character,
    'sessions': Session,
    'sanctions': Sanction,
    'audit_events': AuditEvent,
    'requests': Request,
    'wallets': Wallet,
    'inventory': Inventory,
    'equipment': Equipment,
    'asset_operations': AssetOperation,
    'asset_entries': AssetEntry,
    'outbox': Outbox,
    'asset_entry_details': AssetEntryDetail,
    'rooms': Room,
    'room_members': RoomMember,
    'matches': Match,
    'match_players': MatchPlayer,
    'settlements': Settlement,
    'schema_version': SchemaVersion,
    'deployment_metadata': DeploymentMetadata,
}
