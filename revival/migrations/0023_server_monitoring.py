"""新增独立节点、原始采样和分钟趋势；不更改现有玩家资产、会话或游戏节点权限。"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """顺接活动迁移，唯一键保证采样重试与分钟聚合不重复。"""

    dependencies = [
        ('revival', '0022_campaigns'),
    ]

    operations = [
        migrations.CreateModel(
            name='MonitorNode',
            fields=[
                ('id', models.CharField(max_length=32, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=80)),
                ('platform', models.CharField(max_length=16)),
                ('token_digest', models.CharField(max_length=64, unique=True)),
                ('enabled', models.BooleanField(default=True)),
                ('revision', models.PositiveIntegerField(default=1)),
                ('thresholds', models.JSONField(default=dict)),
                ('created_at', models.BigIntegerField()),
            ],
            options={
                'db_table': 'monitor_nodes',
            },
        ),
        migrations.CreateModel(
            name='MonitorMinute',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('minute', models.BigIntegerField()),
                ('count', models.PositiveIntegerField(default=0)),
                ('cpu_sum', models.FloatField(default=0)),
                ('memory_sum', models.FloatField(default=0)),
                ('rx_sum', models.FloatField(default=0)),
                ('tx_sum', models.FloatField(default=0)),
                ('network_count', models.PositiveIntegerField(default=0)),
                ('node', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.monitornode')),
            ],
            options={
                'db_table': 'monitor_minutes',
                'constraints': [models.UniqueConstraint(fields=('node', 'minute'), name='uq_monitor_minute')],
            },
        ),
        migrations.CreateModel(
            name='MonitorSample',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('boot_id', models.CharField(max_length=64)),
                ('sequence', models.PositiveBigIntegerField()),
                ('measured_at', models.BigIntegerField()),
                ('received_at', models.BigIntegerField()),
                ('payload_hash', models.CharField(max_length=64)),
                ('metrics', models.JSONField()),
                ('node', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.monitornode')),
            ],
            options={
                'db_table': 'monitor_samples',
                'indexes': [models.Index(fields=['node', 'received_at'], name='monitor_sample_recent')],
                'constraints': [models.UniqueConstraint(fields=('node', 'boot_id', 'sequence'), name='uq_monitor_sample_sequence')],
            },
        ),
    ]
