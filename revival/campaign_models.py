"""开服活动、逐日资格与可信成绩模型；资产始终经既有不可改写账本发放。"""
from django.db import models
from django.db.models import Q


class Campaign(models.Model):
    """保存草稿及首次发布快照；发布后不覆盖奖励定义，暂停和结束只改变生命周期。"""
    id = models.CharField(max_length=32, primary_key=True, db_collation='utf8mb4_0900_bin')
    title = models.CharField(max_length=120)
    state = models.CharField(max_length=16, default='DRAFT')
    revision = models.PositiveIntegerField(default=1)
    definition = models.JSONField(default=dict)
    published_definition = models.JSONField(null=True)
    test_only = models.BooleanField(default=False)
    created_at = models.BigIntegerField()
    updated_at = models.BigIntegerField()
    ended_at = models.BigIntegerField(null=True)

    class Meta:
        """活动按显式主键定位，测试来源单独筛选，未发布活动不产生正式资格。"""
        db_table = 'campaigns'
        constraints = [models.CheckConstraint(condition=Q(state__in=['DRAFT', 'PUBLISHED', 'PAUSED', 'ENDED']), name='ck_campaign_state')]


class CampaignDay(models.Model):
    """一个账号在一个活动和北京时间自然日中最多签到或登记 Launcher 登录各一次。"""
    id = models.BigAutoField(primary_key=True)
    campaign = models.ForeignKey('revival.Campaign', on_delete=models.DO_NOTHING)
    account = models.ForeignKey('revival.Account', on_delete=models.DO_NOTHING)
    kind = models.CharField(max_length=24)
    local_day = models.CharField(max_length=10)
    created_at = models.BigIntegerField()

    class Meta:
        """跨月不清空活动累计；数据库唯一约束是并发点击和重登去重的最终边界。"""
        db_table = 'campaign_days'
        constraints = [models.UniqueConstraint(fields=['campaign', 'account', 'kind', 'local_day'], name='uq_campaign_daily')]


class CampaignClaim(models.Model):
    """奖励领取与资产操作同事务提交；榜奖使用统一 claim_key 保证每人每期只有一档。"""
    id = models.CharField(max_length=32, primary_key=True, db_collation='utf8mb4_0900_bin')
    campaign = models.ForeignKey('revival.Campaign', on_delete=models.DO_NOTHING)
    account = models.ForeignKey('revival.Account', on_delete=models.DO_NOTHING)
    claim_key = models.CharField(max_length=100, db_collation='utf8mb4_0900_bin')
    reward_key = models.CharField(max_length=64, db_collation='utf8mb4_0900_bin')
    operation = models.ForeignKey('revival.AssetOperation', on_delete=models.DO_NOTHING)
    created_at = models.BigIntegerField()

    class Meta:
        """失败事务不会留下领取记录；相同资格可安全重试，而成功后不能重复到账。"""
        db_table = 'campaign_claims'
        constraints = [models.UniqueConstraint(fields=['campaign', 'account', 'claim_key'], name='uq_campaign_claim')]


class CampaignResult(models.Model):
    """保存节点认证后的原始成绩摘要和冻结成员；合成成绩永远带 test_only 标记。"""
    id = models.CharField(max_length=32, primary_key=True, db_collation='utf8mb4_0900_bin')
    campaign = models.ForeignKey('revival.Campaign', on_delete=models.DO_NOTHING)
    match = models.ForeignKey('revival.Match', on_delete=models.DO_NOTHING)
    event_id = models.CharField(max_length=80, db_collation='utf8mb4_0900_bin')
    payload_hash = models.CharField(max_length=64, db_collation='utf8mb4_0900_bin')
    map_id = models.PositiveIntegerField()
    difficulty = models.PositiveIntegerField()
    player_count = models.PositiveSmallIntegerField()
    team_key = models.CharField(max_length=64, db_collation='utf8mb4_0900_bin')
    member_ids = models.JSONField(default=list)
    duration_ms = models.PositiveBigIntegerField()
    settled_at = models.BigIntegerField()
    outcome = models.CharField(max_length=16)
    boss_killed = models.BooleanField(default=False)
    valid = models.BooleanField(default=False)
    rank_eligible = models.BooleanField(default=False)
    rejection_code = models.CharField(max_length=40, default='')
    test_only = models.BooleanField(default=True)
    created_at = models.BigIntegerField()

    class Meta:
        """一局每期只写一个结果；同事件不同内容和同局换事件均不能追加第二份成绩。"""
        db_table = 'campaign_results'
        constraints = [
            models.UniqueConstraint(fields=['campaign', 'match'], name='uq_campaign_match'),
            models.UniqueConstraint(fields=['campaign', 'event_id'], name='uq_campaign_result_event'),
        ]
        indexes = [models.Index(fields=['campaign', 'map_id', 'difficulty', 'player_count'], name='ix_campaign_board')]


class CampaignBoardFreeze(models.Model):
    """封榜时冻结全部分榜及每个账号最高优先级奖励；后续查询和领取不再实时改榜。"""
    campaign = models.OneToOneField('revival.Campaign', on_delete=models.DO_NOTHING, primary_key=True)
    boards = models.JSONField(default=list)
    awards = models.JSONField(default=dict)
    created_at = models.BigIntegerField()

    class Meta:
        """每活动仅有一个终版快照；领奖失败可以重试，不需要重跑封榜或重新选择档位。"""
        db_table = 'campaign_board_freezes'


CAMPAIGN_MODELS = {
    'campaigns': Campaign,
    'campaign_days': CampaignDay,
    'campaign_claims': CampaignClaim,
    'campaign_results': CampaignResult,
    'campaign_board_freezes': CampaignBoardFreeze,
}
