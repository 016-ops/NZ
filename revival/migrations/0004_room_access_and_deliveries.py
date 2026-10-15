"""增量添加房间访问规则、定向邀请及逐实例同步确认，不改旧房间或账本字段。"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """按依赖创建访问/确认表，旧 v1 数据可通过默认公开策略继续读取。"""

    dependencies = [
        ('revival', '0003_operations_and_pve'),
    ]

    operations = [
        migrations.CreateModel(
            name='RoomSettings',
            fields=[
                ('room', models.OneToOneField(on_delete=django.db.models.deletion.DO_NOTHING, primary_key=True, serialize=False, to='revival.room')),
                ('channel', models.CharField(default='local', max_length=64)),
                ('difficulty', models.CharField(default='default', max_length=64)),
                ('password_hash', models.CharField(default='', max_length=256)),
                ('invite_only', models.BooleanField(default=False)),
            ],
            options={
                'db_table': 'room_settings',
            },
        ),
        migrations.CreateModel(
            name='NodeDelivery',
            fields=[
                ('internal_id', models.BigAutoField(primary_key=True, serialize=False)),
                ('status', models.CharField(default='PENDING', max_length=32)),
                ('acknowledged_at', models.BigIntegerField(null=True)),
                ('event', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.outbox')),
                ('match', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.match')),
            ],
            options={
                'db_table': 'node_deliveries',
                'constraints': [models.UniqueConstraint(fields=('event', 'match'), name='uq_node_delivery')],
            },
        ),
        migrations.CreateModel(
            name='RoomInvitation',
            fields=[
                ('digest', models.CharField(max_length=64, primary_key=True, serialize=False)),
                ('expires_at', models.BigIntegerField()),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.account')),
                ('room', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.room')),
            ],
            options={
                'db_table': 'room_invitations',
                'constraints': [models.UniqueConstraint(fields=('room', 'account'), name='uq_room_invitation')],
            },
        ),
    ]
