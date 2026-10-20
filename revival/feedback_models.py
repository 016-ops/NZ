"""意见反馈、消息、私有截图与公共活动封面；文件与业务账号数据分别管理。"""
from django.db import models
from django.db.models import Q


class FeedbackTicket(models.Model):
    """反馈归属玩家；revision同时保护回复与状态更新，关闭后不再接受新增内容。"""
    id = models.CharField(max_length=32, primary_key=True, db_collation='utf8mb4_0900_bin')
    account = models.ForeignKey('revival.Account', on_delete=models.DO_NOTHING)
    title = models.CharField(max_length=120)
    state = models.CharField(max_length=16, default='OPEN')
    revision = models.PositiveIntegerField(default=1)
    created_at = models.BigIntegerField()
    updated_at = models.BigIntegerField()

    class Meta:
        """数据库状态约束及本人/后台按更新时间查询索引。"""
        db_table = 'feedback_tickets'
        constraints = [models.CheckConstraint(condition=Q(state__in=['OPEN', 'IN_PROGRESS', 'RESOLVED', 'CLOSED']), name='ck_feedback_state')]
        indexes = [models.Index(fields=['account', 'updated_at'], name='ix_feedback_owner'),
                   models.Index(fields=['state', 'updated_at'], name='ix_feedback_state')]


class FeedbackMessage(models.Model):
    """只追加对话正文；作者身份来自认证会话，客户端不能伪造管理员回复。"""
    id = models.CharField(max_length=32, primary_key=True, db_collation='utf8mb4_0900_bin')
    ticket = models.ForeignKey('revival.FeedbackTicket', on_delete=models.DO_NOTHING)
    sequence = models.PositiveIntegerField()
    author_kind = models.CharField(max_length=8)
    author_id = models.CharField(max_length=32, db_collation='utf8mb4_0900_bin')
    body = models.TextField()
    created_at = models.BigIntegerField()

    class Meta:
        """消息按工单修订形成单调序列，同一秒的回复不会因随机UUID而倒序。"""
        db_table = 'feedback_messages'
        constraints = [models.UniqueConstraint(fields=['ticket', 'sequence'], name='uq_feedback_message_order')]
        indexes = [models.Index(fields=['ticket', 'created_at'], name='ix_feedback_messages')]


class FeedbackAttachment(models.Model):
    """截图仅经工单授权读取；原图不持久化，只保存去元数据的重编码图片。"""
    id = models.CharField(max_length=32, primary_key=True, db_collation='utf8mb4_0900_bin')
    ticket = models.ForeignKey('revival.FeedbackTicket', on_delete=models.DO_NOTHING)
    author_kind = models.CharField(max_length=8)
    author_id = models.CharField(max_length=32, db_collation='utf8mb4_0900_bin')
    upload_key = models.CharField(max_length=128, db_collation='utf8mb4_0900_bin')
    input_sha256 = models.CharField(max_length=64)
    sha256 = models.CharField(max_length=64)
    storage_key = models.CharField(max_length=80)
    mime = models.CharField(max_length=32)
    bytes = models.PositiveIntegerField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    created_at = models.BigIntegerField()

    class Meta:
        """工单、操作者、上传幂等键唯一；三张总量由同一部署事务锁保护。"""
        db_table = 'feedback_attachments'
        constraints = [models.UniqueConstraint(fields=['ticket', 'author_kind', 'author_id', 'upload_key'], name='uq_feedback_upload')]


class PublicMedia(models.Model):
    """管理员单独上传的公共封面；不能引用私有反馈截图作为公共素材。"""
    id = models.CharField(max_length=32, primary_key=True, db_collation='utf8mb4_0900_bin')
    author = models.ForeignKey('revival.Admin', on_delete=models.DO_NOTHING)
    upload_key = models.CharField(max_length=128, db_collation='utf8mb4_0900_bin')
    input_sha256 = models.CharField(max_length=64)
    sha256 = models.CharField(max_length=64)
    storage_key = models.CharField(max_length=80)
    mime = models.CharField(max_length=32)
    bytes = models.PositiveIntegerField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    created_at = models.BigIntegerField()

    class Meta:
        """公共图片上传由管理员及幂等键隔离，同键不能静默换图。"""
        db_table = 'public_media'
        constraints = [models.UniqueConstraint(fields=['author', 'upload_key'], name='uq_public_media_upload')]


class CampaignPresentation(models.Model):
    """活动独立页面展示数据；不覆盖发布后冻结的奖励规则或发奖资格。"""
    campaign = models.OneToOneField('revival.Campaign', on_delete=models.DO_NOTHING, primary_key=True)
    slug = models.CharField(max_length=96, unique=True, db_collation='utf8mb4_0900_bin')
    title = models.CharField(max_length=120)
    summary = models.CharField(max_length=500, default='')
    theme = models.CharField(max_length=24, default='default')
    cover = models.ForeignKey('revival.PublicMedia', on_delete=models.DO_NOTHING, null=True)
    sections = models.JSONField(default=list)
    revision = models.PositiveIntegerField(default=1)
    updated_at = models.BigIntegerField()

    class Meta:
        """slug唯一且发布后业务层锁定，展示修订独立于奖励草稿修订。"""
        db_table = 'campaign_presentations'


FEEDBACK_MODELS = {'feedback_tickets': FeedbackTicket, 'feedback_messages': FeedbackMessage,
                   'feedback_attachments': FeedbackAttachment, 'public_media': PublicMedia,
                   'campaign_presentations': CampaignPresentation}
