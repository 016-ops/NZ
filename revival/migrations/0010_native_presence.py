"""新增游戏在线心跳，不从已有票据推定玩家在线，不修改账号和库存。"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """仅向前增加独立表，升级保留所有既有游戏凭据和业务流水。"""

    dependencies = [
        ('revival', '0009_native_feature_transactions'),
    ]

    operations = [
        migrations.CreateModel(
            name='NativePresence',
            fields=[
                ('account', models.OneToOneField(on_delete=django.db.models.deletion.DO_NOTHING, primary_key=True, serialize=False, to='revival.account')),
                ('last_seen', models.BigIntegerField()),
                ('game', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.gamesession')),
            ],
            options={
                'db_table': 'native_presence',
            },
        ),
    ]
