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


class PlayerEmail(models.Model):
    """玩家的独立邮箱标识；与旧账号一对一，保留既有 game_id、资产及历史登录名。"""
    account = models.OneToOneField(Account, on_delete=models.DO_NOTHING, primary_key=True)
    email = models.CharField(max_length=254, unique=True, db_collation='utf8mb4_0900_bin')
    created_at = models.BigIntegerField()
    verified_at = models.BigIntegerField(null=True)

    class Meta:
        """规范邮箱由业务层小写；唯一约束保护并发注册，不让两个账号共享邮箱。"""
        db_table = 'player_emails'


class LaunchTicket(models.Model):
    """独立启动协议的短期一次性凭据；绑定邮箱账号、原会话、客户端及启动器持有证明。"""
    digest = models.CharField(max_length=64, primary_key=True, db_collation='utf8mb4_0900_bin')
    id = models.CharField(max_length=32, unique=True, db_collation='utf8mb4_0900_bin')
    account = models.ForeignKey(Account, on_delete=models.DO_NOTHING)
    session = models.ForeignKey('Session', on_delete=models.DO_NOTHING)
    security_version = models.BigIntegerField()
    profile = models.CharField(max_length=100)
    client_sha256 = models.CharField(max_length=64)
    verifier_sha256 = models.CharField(max_length=64)
    created_at = models.BigIntegerField()
    expires_at = models.BigIntegerField()
    consumed_at = models.BigIntegerField(null=True)
    revoked = models.BooleanField(default=False)

    class Meta:
        """只保存票据/证明摘要，不把邮箱密码、玩家 Bearer 或可重复兑换结果写入票据。"""
        db_table = 'launch_tickets'


class GameSession(models.Model):
    """邮箱启动交接后的独立游戏会话；不能充当管理员、玩家 API 或节点凭据。"""
    digest = models.CharField(max_length=64, primary_key=True, db_collation='utf8mb4_0900_bin')
    account = models.ForeignKey(Account, on_delete=models.DO_NOTHING)
    session = models.ForeignKey('Session', on_delete=models.DO_NOTHING)
    launch = models.OneToOneField(LaunchTicket, on_delete=models.DO_NOTHING)
    security_version = models.BigIntegerField()
    profile = models.CharField(max_length=100)
    client_sha256 = models.CharField(max_length=64)
    created_at = models.BigIntegerField()
    expires_at = models.BigIntegerField()
    revoked = models.BooleanField(default=False)

    class Meta:
        """每张启动票据至多产生一个游戏会话；每次授权仍检查源会话和最新制裁。"""
        db_table = 'game_sessions'

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

class RoomSettings(models.Model):
    """房间控制面访问配置独立存表，保持旧 SQLite 导入字段兼容。"""
    room = models.OneToOneField(Room, primary_key=True, on_delete=models.DO_NOTHING)
    channel = models.CharField(max_length=64, default='local')
    difficulty = models.CharField(max_length=64, default='default')
    password_hash = models.CharField(max_length=256, default='')
    invite_only = models.BooleanField(default=False)
    class Meta:
        """密码仅存慢哈希，读取视图通过显式字段白名单去除它。"""
        db_table = 'room_settings'


class RoomInvitation(models.Model):
    """邀请只绑定指定房间和账号；过期或被替换后不能用于占席。"""
    digest = models.CharField(max_length=64, primary_key=True)
    room = models.ForeignKey(Room, on_delete=models.DO_NOTHING)
    account = models.ForeignKey(Account, on_delete=models.DO_NOTHING)
    expires_at = models.BigIntegerField()
    class Meta:
        """每个房间对同一账号只保留一个当前邀请。"""
        db_table = 'room_invitations'
        constraints = [models.UniqueConstraint(fields=['room', 'account'], name='uq_room_invitation')]


class AdminRecovery(models.Model):
    """高熵恢复码只保存摘要，一次使用后失效；重置 MFA 同事务撤销全部旧会话。"""
    digest = models.CharField(max_length=64, primary_key=True, db_collation='utf8mb4_0900_bin')
    admin = models.ForeignKey(Admin, on_delete=models.DO_NOTHING)
    used_at = models.BigIntegerField(null=True)
    class Meta:
        """恢复码按摘要唯一，禁止一个码被多个管理员复用。"""
        db_table = 'admin_recovery'


class GrantBatch(models.Model):
    """冻结发放请求和目标，子项分事务执行；进程重启可继续未执行目标。"""
    id = models.CharField(max_length=32, primary_key=True)
    actor = models.ForeignKey(Admin, on_delete=models.DO_NOTHING)
    catalog_version = models.CharField(max_length=128)
    payload = models.TextField()
    status = models.CharField(max_length=32, default='PENDING')
    created_at = models.BigIntegerField()
    parent = models.ForeignKey('self', null=True, on_delete=models.DO_NOTHING)
    class Meta:
        """批次 ID 是持久任务标识，重试通过新批次关联原批次。"""
        db_table = 'grant_batches'


class GrantTarget(models.Model):
    """每批每目标只执行一次；错误目标保留原 ID，不因用户筛选变化扩大范围。"""
    id = models.CharField(max_length=32, primary_key=True)
    batch = models.ForeignKey(GrantBatch, on_delete=models.DO_NOTHING)
    target = models.CharField(max_length=32)
    ordinal = models.PositiveIntegerField()
    status = models.CharField(max_length=32, default='PENDING')
    operation = models.ForeignKey(AssetOperation, null=True, on_delete=models.DO_NOTHING)
    error = models.CharField(max_length=64, default='')
    class Meta:
        """目标可以是不存在的账号并作为失败项返回，因此不使用账号外键。"""
        db_table = 'grant_targets'
        constraints = [models.UniqueConstraint(fields=['batch', 'target'], name='uq_grant_target')]


class ReviewDecision(models.Model):
    """人工复核只追加处置决定，不改写原始发放或待复核历史。"""
    id = models.CharField(max_length=32, primary_key=True)
    operation = models.OneToOneField(AssetOperation, on_delete=models.DO_NOTHING)
    actor = models.ForeignKey(Admin, on_delete=models.DO_NOTHING)
    resolution = models.CharField(max_length=32)
    reason = models.TextField()
    created_at = models.BigIntegerField()
    class Meta:
        """一个待复核操作只有一个最终决定，重复提交由幂等层返回原结果。"""
        db_table = 'review_decisions'


class NodeTicket(models.Model):
    """分配给单局实例的短时就绪凭据；绝不向玩家发放，也不等同真实实例通过。"""
    match = models.OneToOneField(Match, primary_key=True, on_delete=models.DO_NOTHING)
    token_hash = models.CharField(max_length=64)
    expires_at = models.BigIntegerField()
    evidence = models.CharField(max_length=64)
    ready_hash = models.CharField(max_length=64, default='')
    class Meta:
        """每局至多一个就绪票据，与 match 的实例、epoch 和租约联合核验。"""
        db_table = 'node_tickets'


class MatchEvent(models.Model):
    """受认证节点的有序事件和处理结果；同 ID 改内容冲突，断线重发返回已提交结果。"""
    internal_id = models.BigAutoField(primary_key=True)
    match = models.ForeignKey(Match, on_delete=models.DO_NOTHING)
    event_id = models.CharField(max_length=128)
    sequence = models.PositiveBigIntegerField()
    payload_hash = models.CharField(max_length=64)
    result = models.TextField()
    class Meta:
        """每局事件标识和序号均唯一，防止换 ID 重用旧序号推进两次。"""
        db_table = 'match_events'
        constraints = [models.UniqueConstraint(fields=['match', 'event_id'], name='uq_match_event_id'),
                       models.UniqueConstraint(fields=['match', 'sequence'], name='uq_match_event_seq')]


class PveRun(models.Model):
    """保存规则快照和权威 PvE 状态；与事件、资产事务共同提交，可重启恢复。"""
    match = models.OneToOneField(Match, primary_key=True, on_delete=models.DO_NOTHING)
    rules = models.TextField()
    state = models.TextField()
    class Meta:
        """一次原生实例分配对应一个 PvE 运行状态，尝试编号位于冻结状态中。"""
        db_table = 'pve_runs'


class NodeDelivery(models.Model):
    """每个事件每个目标实例独立确认，单个节点的确认不会掩盖另一个节点的失败。"""
    internal_id = models.BigAutoField(primary_key=True)
    event = models.ForeignKey(Outbox, on_delete=models.DO_NOTHING)
    match = models.ForeignKey(Match, on_delete=models.DO_NOTHING)
    status = models.CharField(max_length=32, default='PENDING')
    acknowledged_at = models.BigIntegerField(null=True)
    class Meta:
        """同一个 outbox 事件在同一局只发送一次逻辑通知，可以多次传输。"""
        db_table = 'node_deliveries'
        constraints = [models.UniqueConstraint(fields=['event', 'match'], name='uq_node_delivery')]


class EmailChallenge(models.Model):
    """验证码只存服务端 HMAC；记录投递与失败次数，重启后不能绕过一次性消费和发送额度。"""
    id = models.CharField(max_length=32, primary_key=True)
    email = models.CharField(max_length=254, db_collation='utf8mb4_0900_bin')
    purpose = models.CharField(max_length=16)
    digest = models.CharField(max_length=64)
    ip_digest = models.CharField(max_length=64)
    account = models.ForeignKey(Account, null=True, on_delete=models.DO_NOTHING)
    security_version = models.BigIntegerField(null=True)
    created_at = models.BigIntegerField()
    expires_at = models.BigIntegerField()
    status = models.CharField(max_length=16, default='PENDING')
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.BigIntegerField(null=True)
    error_code = models.CharField(max_length=32, default='')
    class Meta:
        """邮箱及来源窗口索引支持持久限流；不保存密码、验证码、SMTP 异常正文或邮件正文。"""
        db_table = 'email_challenges'
        indexes = [models.Index(fields=['email', 'created_at'], name='email_challenge_window'),
                   models.Index(fields=['ip_digest', 'created_at'], name='email_source_window')]


TABLE_MODELS = {
    'email_challenges': EmailChallenge,
    'player_emails': PlayerEmail,
    'launch_tickets': LaunchTicket,
    'game_sessions': GameSession,
    'node_deliveries': NodeDelivery,
    'room_settings': RoomSettings,
    'room_invitations': RoomInvitation,
    'admin_recovery': AdminRecovery,
    'grant_batches': GrantBatch,
    'grant_targets': GrantTarget,
    'review_decisions': ReviewDecision,
    'node_tickets': NodeTicket,
    'match_events': MatchEvent,
    'pve_runs': PveRun,
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
