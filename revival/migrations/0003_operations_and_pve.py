"""新增管理员恢复、持久批次、人工复核和节点/PvE 状态表；不修改已有资产流水。"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """增量建表，旧业务 ID 与字段保持兼容；运行数据保留，由迁移图控制顺序。"""

    dependencies = [
        ('revival', '0002_invariants'),
    ]

    operations = [
        migrations.CreateModel(
            name='NodeTicket',
            fields=[
                ('match', models.OneToOneField(on_delete=django.db.models.deletion.DO_NOTHING, primary_key=True, serialize=False, to='revival.match')),
                ('token_hash', models.CharField(max_length=64)),
                ('expires_at', models.BigIntegerField()),
                ('evidence', models.CharField(max_length=64)),
                ('ready_hash', models.CharField(default='', max_length=64)),
            ],
            options={
                'db_table': 'node_tickets',
            },
        ),
        migrations.CreateModel(
            name='PveRun',
            fields=[
                ('match', models.OneToOneField(on_delete=django.db.models.deletion.DO_NOTHING, primary_key=True, serialize=False, to='revival.match')),
                ('rules', models.TextField()),
                ('state', models.TextField()),
            ],
            options={
                'db_table': 'pve_runs',
            },
        ),
        migrations.CreateModel(
            name='AdminRecovery',
            fields=[
                ('digest', models.CharField(db_collation='utf8mb4_0900_bin', max_length=64, primary_key=True, serialize=False)),
                ('used_at', models.BigIntegerField(null=True)),
                ('admin', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.admin')),
            ],
            options={
                'db_table': 'admin_recovery',
            },
        ),
        migrations.CreateModel(
            name='GrantBatch',
            fields=[
                ('id', models.CharField(max_length=32, primary_key=True, serialize=False)),
                ('catalog_version', models.CharField(max_length=128)),
                ('payload', models.TextField()),
                ('status', models.CharField(default='PENDING', max_length=32)),
                ('created_at', models.BigIntegerField()),
                ('actor', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.admin')),
                ('parent', models.ForeignKey(null=True, on_delete=django.db.models.deletion.DO_NOTHING, to='revival.grantbatch')),
            ],
            options={
                'db_table': 'grant_batches',
            },
        ),
        migrations.CreateModel(
            name='ReviewDecision',
            fields=[
                ('id', models.CharField(max_length=32, primary_key=True, serialize=False)),
                ('resolution', models.CharField(max_length=32)),
                ('reason', models.TextField()),
                ('created_at', models.BigIntegerField()),
                ('actor', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.admin')),
                ('operation', models.OneToOneField(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.assetoperation')),
            ],
            options={
                'db_table': 'review_decisions',
            },
        ),
        migrations.CreateModel(
            name='GrantTarget',
            fields=[
                ('id', models.CharField(max_length=32, primary_key=True, serialize=False)),
                ('target', models.CharField(max_length=32)),
                ('ordinal', models.PositiveIntegerField()),
                ('status', models.CharField(default='PENDING', max_length=32)),
                ('error', models.CharField(default='', max_length=64)),
                ('batch', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.grantbatch')),
                ('operation', models.ForeignKey(null=True, on_delete=django.db.models.deletion.DO_NOTHING, to='revival.assetoperation')),
            ],
            options={
                'db_table': 'grant_targets',
                'constraints': [models.UniqueConstraint(fields=('batch', 'target'), name='uq_grant_target')],
            },
        ),
        migrations.CreateModel(
            name='MatchEvent',
            fields=[
                ('internal_id', models.BigAutoField(primary_key=True, serialize=False)),
                ('event_id', models.CharField(max_length=128)),
                ('sequence', models.PositiveBigIntegerField()),
                ('payload_hash', models.CharField(max_length=64)),
                ('result', models.TextField()),
                ('match', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.match')),
            ],
            options={
                'db_table': 'match_events',
                'constraints': [models.UniqueConstraint(fields=('match', 'event_id'), name='uq_match_event_id'), models.UniqueConstraint(fields=('match', 'sequence'), name='uq_match_event_seq')],
            },
        ),
    ]
