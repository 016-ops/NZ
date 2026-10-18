"""向前新增原生操作状态、规则、回执、签到和社交表，保留已有库存、GID及历史流水。"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """仅添加新表和唯一约束，不回填或重置玩家资产；部署前在独立MySQL验证。"""

    dependencies = [
        ('revival', '0008_native_inventory_entitlements'),
    ]

    operations = [
        migrations.CreateModel(
            name='NativeClan',
            fields=[
                ('id', models.CharField(max_length=32, primary_key=True, serialize=False)),
                ('name', models.CharField(db_collation='utf8mb4_0900_bin', max_length=64, unique=True)),
                ('announcement', models.CharField(default='', max_length=1000)),
                ('capacity', models.PositiveIntegerField(default=100)),
                ('badge', models.PositiveIntegerField(default=0)),
                ('active', models.BooleanField(default=True)),
                ('created_at', models.BigIntegerField()),
            ],
            options={
                'db_table': 'native_clans',
            },
        ),
        migrations.CreateModel(
            name='NativeItemState',
            fields=[
                ('inventory', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True, serialize=False, to='revival.inventory')),
                ('state', models.JSONField(default=dict)),
                ('revision', models.PositiveBigIntegerField(default=1)),
            ],
            options={
                'db_table': 'native_item_states',
            },
        ),
        migrations.CreateModel(
            name='NativeClanMember',
            fields=[
                ('account', models.OneToOneField(on_delete=django.db.models.deletion.DO_NOTHING, primary_key=True, serialize=False, to='revival.account')),
                ('role', models.CharField(max_length=16)),
                ('joined_at', models.BigIntegerField()),
                ('clan', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.nativeclan')),
            ],
            options={
                'db_table': 'native_clan_members',
            },
        ),
        migrations.CreateModel(
            name='NativeRuleRevision',
            fields=[
                ('id', models.CharField(max_length=32, primary_key=True, serialize=False)),
                ('namespace', models.CharField(db_collation='utf8mb4_0900_bin', max_length=100)),
                ('revision', models.PositiveBigIntegerField()),
                ('definition', models.JSONField()),
                ('enabled', models.BooleanField(default=False)),
                ('created_at', models.BigIntegerField()),
                ('actor', models.CharField(max_length=160)),
            ],
            options={
                'db_table': 'native_rule_revisions',
                'constraints': [models.UniqueConstraint(fields=('namespace', 'revision'), name='uq_native_rule_revision')],
            },
        ),
        migrations.CreateModel(
            name='NativeClanApplication',
            fields=[
                ('id', models.CharField(max_length=32, primary_key=True, serialize=False)),
                ('invited', models.BooleanField(default=False)),
                ('status', models.CharField(default='PENDING', max_length=16)),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.account')),
                ('clan', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.nativeclan')),
            ],
            options={
                'db_table': 'native_clan_applications',
                'constraints': [models.UniqueConstraint(fields=('clan', 'account', 'invited'), name='uq_native_clan_application')],
            },
        ),
        migrations.CreateModel(
            name='NativeFriend',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('status', models.CharField(max_length=16)),
                ('remark', models.CharField(default='', max_length=64)),
                ('group', models.CharField(default='', max_length=64)),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='revival.account')),
                ('peer', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='revival.account')),
            ],
            options={
                'db_table': 'native_friends',
                'constraints': [models.UniqueConstraint(fields=('account', 'peer'), name='uq_native_friend_pair'), models.CheckConstraint(condition=models.Q(('account', models.F('peer')), _negated=True), name='ck_native_friend_self')],
            },
        ),
        migrations.CreateModel(
            name='NativePlayerEvent',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('kind', models.CharField(max_length=64)),
                ('payload', models.JSONField()),
                ('created_at', models.BigIntegerField()),
                ('acknowledged', models.BooleanField(default=False)),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.account')),
            ],
            options={
                'db_table': 'native_player_events',
                'indexes': [models.Index(fields=['account', 'acknowledged', 'id'], name='native_events_pending')],
            },
        ),
        migrations.CreateModel(
            name='NativeReceipt',
            fields=[
                ('id', models.CharField(max_length=32, primary_key=True, serialize=False)),
                ('kind', models.CharField(max_length=32)),
                ('result', models.JSONField()),
                ('created_at', models.BigIntegerField()),
                ('confirmed', models.BooleanField(default=False)),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.account')),
            ],
            options={
                'db_table': 'native_receipts',
                'indexes': [models.Index(fields=['account', 'created_at'], name='native_receipt_history')],
            },
        ),
        migrations.CreateModel(
            name='NativeCheckin',
            fields=[
                ('id', models.CharField(max_length=32, primary_key=True, serialize=False)),
                ('campaign', models.CharField(max_length=100)),
                ('day', models.DateField()),
                ('result', models.JSONField()),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.account')),
                ('rule', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.nativerulerevision')),
            ],
            options={
                'db_table': 'native_checkins',
                'constraints': [models.UniqueConstraint(fields=('account', 'campaign', 'day'), name='uq_native_checkin_day')],
            },
        ),
    ]
