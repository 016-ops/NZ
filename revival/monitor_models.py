"""主机监控独立于游戏节点授权；采样凭据只存摘要，原始及分钟数据有独立保留期。"""
from django.db import models


class MonitorNode(models.Model):
    """登记一个允许上报的主机；管理读模型永不返回凭据摘要。"""
    id = models.CharField(max_length=32, primary_key=True)
    name = models.CharField(max_length=80)
    platform = models.CharField(max_length=16)
    token_digest = models.CharField(max_length=64, unique=True)
    enabled = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)
    thresholds = models.JSONField(default=dict)
    created_at = models.BigIntegerField()

    class Meta:
        """节点长期保留，停用只撤销采样资格，不删除历史。"""
        db_table = 'monitor_nodes'


class MonitorSample(models.Model):
    """单次受认证的测量；启动标识与序号唯一防止网络重试重复采样。"""
    id = models.BigAutoField(primary_key=True)
    node = models.ForeignKey(MonitorNode, on_delete=models.DO_NOTHING)
    boot_id = models.CharField(max_length=64)
    sequence = models.PositiveBigIntegerField()
    measured_at = models.BigIntegerField()
    received_at = models.BigIntegerField()
    payload_hash = models.CharField(max_length=64)
    metrics = models.JSONField()

    class Meta:
        """按节点与采样时间读取趋势；重复序号必须对应同一请求内容。"""
        db_table = 'monitor_samples'
        constraints = [models.UniqueConstraint(fields=['node', 'boot_id', 'sequence'], name='uq_monitor_sample_sequence')]
        indexes = [models.Index(fields=['node', 'received_at'], name='monitor_sample_recent')]


class MonitorMinute(models.Model):
    """分钟聚合延长趋势窗口；网络缺测不以零值混入平均数。"""
    node = models.ForeignKey(MonitorNode, on_delete=models.DO_NOTHING)
    minute = models.BigIntegerField()
    count = models.PositiveIntegerField(default=0)
    cpu_sum = models.FloatField(default=0)
    memory_sum = models.FloatField(default=0)
    rx_sum = models.FloatField(default=0)
    tx_sum = models.FloatField(default=0)
    network_count = models.PositiveIntegerField(default=0)

    class Meta:
        """每节点每分钟只有一个聚合桶，采样与聚合在同一事务提交。"""
        db_table = 'monitor_minutes'
        constraints = [models.UniqueConstraint(fields=['node', 'minute'], name='uq_monitor_minute')]


MONITOR_MODELS = {'monitor_nodes': MonitorNode, 'monitor_samples': MonitorSample, 'monitor_minutes': MonitorMinute}
