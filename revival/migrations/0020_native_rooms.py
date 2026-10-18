"""向前新增原生房号与游戏会话席位绑定；不修改旧房间、账号、库存或历史流水。"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """仅建两张独立表；空表不会启用节点、创建房间或重置当前业务数据。"""

    dependencies = [
        ('revival', '0019_native_engineering_progress'),
    ]

    operations = [
        migrations.CreateModel(
            name='NativeRoomBinding',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('display_id', models.PositiveSmallIntegerField()),
                ('main_channel', models.PositiveIntegerField(default=1)),
                ('sub_channel', models.PositiveIntegerField(default=1)),
                ('name', models.CharField(max_length=31)),
                ('fighter_capacity', models.PositiveSmallIntegerField()),
                ('observer_capacity', models.PositiveSmallIntegerField(default=0)),
                ('settings', models.JSONField()),
                ('rule_version', models.CharField(max_length=64)),
                ('created_at', models.BigIntegerField()),
                ('room', models.OneToOneField(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.room')),
            ],
            options={
                'db_table': 'native_room_bindings',
            },
        ),
        migrations.CreateModel(
            name='NativeRoomPresence',
            fields=[
                ('account', models.OneToOneField(on_delete=django.db.models.deletion.DO_NOTHING, primary_key=True, serialize=False, to='revival.account')),
                ('observer', models.BooleanField(default=False)),
                ('in_match', models.BooleanField(default=False)),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.gamesession')),
            ],
            options={
                'db_table': 'native_room_presence',
            },
        ),
    ]
