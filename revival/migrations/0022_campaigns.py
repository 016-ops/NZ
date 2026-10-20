"""向前新增开服活动、逐日资格、一次领取、合成受信成绩与封榜快照，保留全部原生资产。"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """仅创建本批五张独立表及约束，不改写既有账号、库存、流水或历史活动规则。"""

    dependencies = [
        ('revival', '0021_native_room_secret'),
    ]

    operations = [
        migrations.CreateModel(
            name='Campaign',
            fields=[
                ('id', models.CharField(db_collation='utf8mb4_0900_bin', max_length=32, primary_key=True, serialize=False)),
                ('title', models.CharField(max_length=120)),
                ('state', models.CharField(default='DRAFT', max_length=16)),
                ('revision', models.PositiveIntegerField(default=1)),
                ('definition', models.JSONField(default=dict)),
                ('published_definition', models.JSONField(null=True)),
                ('test_only', models.BooleanField(default=False)),
                ('created_at', models.BigIntegerField()),
                ('updated_at', models.BigIntegerField()),
                ('ended_at', models.BigIntegerField(null=True)),
            ],
            options={
                'db_table': 'campaigns',
            },
        ),
        migrations.CreateModel(
            name='CampaignClaim',
            fields=[
                ('id', models.CharField(db_collation='utf8mb4_0900_bin', max_length=32, primary_key=True, serialize=False)),
                ('claim_key', models.CharField(db_collation='utf8mb4_0900_bin', max_length=100)),
                ('reward_key', models.CharField(db_collation='utf8mb4_0900_bin', max_length=64)),
                ('created_at', models.BigIntegerField()),
            ],
            options={
                'db_table': 'campaign_claims',
            },
        ),
        migrations.CreateModel(
            name='CampaignDay',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('kind', models.CharField(max_length=24)),
                ('local_day', models.CharField(max_length=10)),
                ('created_at', models.BigIntegerField()),
            ],
            options={
                'db_table': 'campaign_days',
            },
        ),
        migrations.CreateModel(
            name='CampaignResult',
            fields=[
                ('id', models.CharField(db_collation='utf8mb4_0900_bin', max_length=32, primary_key=True, serialize=False)),
                ('event_id', models.CharField(db_collation='utf8mb4_0900_bin', max_length=80)),
                ('payload_hash', models.CharField(db_collation='utf8mb4_0900_bin', max_length=64)),
                ('map_id', models.PositiveIntegerField()),
                ('difficulty', models.PositiveIntegerField()),
                ('player_count', models.PositiveSmallIntegerField()),
                ('team_key', models.CharField(db_collation='utf8mb4_0900_bin', max_length=64)),
                ('member_ids', models.JSONField(default=list)),
                ('duration_ms', models.PositiveBigIntegerField()),
                ('settled_at', models.BigIntegerField()),
                ('outcome', models.CharField(max_length=16)),
                ('boss_killed', models.BooleanField(default=False)),
                ('valid', models.BooleanField(default=False)),
                ('rank_eligible', models.BooleanField(default=False)),
                ('rejection_code', models.CharField(default='', max_length=40)),
                ('test_only', models.BooleanField(default=True)),
                ('created_at', models.BigIntegerField()),
            ],
            options={
                'db_table': 'campaign_results',
            },
        ),
        migrations.CreateModel(
            name='CampaignBoardFreeze',
            fields=[
                ('campaign', models.OneToOneField(on_delete=django.db.models.deletion.DO_NOTHING, primary_key=True, serialize=False, to='revival.campaign')),
                ('boards', models.JSONField(default=list)),
                ('awards', models.JSONField(default=dict)),
                ('created_at', models.BigIntegerField()),
            ],
            options={
                'db_table': 'campaign_board_freezes',
            },
        ),
        migrations.AddConstraint(
            model_name='campaign',
            constraint=models.CheckConstraint(condition=models.Q(('state__in', ['DRAFT', 'PUBLISHED', 'PAUSED', 'ENDED'])), name='ck_campaign_state'),
        ),
        migrations.AddField(
            model_name='campaignclaim',
            name='account',
            field=models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.account'),
        ),
        migrations.AddField(
            model_name='campaignclaim',
            name='campaign',
            field=models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.campaign'),
        ),
        migrations.AddField(
            model_name='campaignclaim',
            name='operation',
            field=models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.assetoperation'),
        ),
        migrations.AddField(
            model_name='campaignday',
            name='account',
            field=models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.account'),
        ),
        migrations.AddField(
            model_name='campaignday',
            name='campaign',
            field=models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.campaign'),
        ),
        migrations.AddField(
            model_name='campaignresult',
            name='campaign',
            field=models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.campaign'),
        ),
        migrations.AddField(
            model_name='campaignresult',
            name='match',
            field=models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.match'),
        ),
        migrations.AddConstraint(
            model_name='campaignclaim',
            constraint=models.UniqueConstraint(fields=('campaign', 'account', 'claim_key'), name='uq_campaign_claim'),
        ),
        migrations.AddConstraint(
            model_name='campaignday',
            constraint=models.UniqueConstraint(fields=('campaign', 'account', 'kind', 'local_day'), name='uq_campaign_daily'),
        ),
        migrations.AddIndex(
            model_name='campaignresult',
            index=models.Index(fields=['campaign', 'map_id', 'difficulty', 'player_count'], name='ix_campaign_board'),
        ),
        migrations.AddConstraint(
            model_name='campaignresult',
            constraint=models.UniqueConstraint(fields=('campaign', 'match'), name='uq_campaign_match'),
        ),
        migrations.AddConstraint(
            model_name='campaignresult',
            constraint=models.UniqueConstraint(fields=('campaign', 'event_id'), name='uq_campaign_result_event'),
        ),
    ]
