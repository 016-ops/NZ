"""增加资产同步游标；保留现有4323项权益、GID、装备关系和不可改写流水。"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """向前新增同步表，不重置业务数据库。"""

    dependencies = [
        ('revival', '0010_native_presence'),
    ]

    operations = [
        migrations.CreateModel(
            name='NativeSyncCursor',
            fields=[
                ('account', models.OneToOneField(on_delete=django.db.models.deletion.DO_NOTHING, primary_key=True, serialize=False, to='revival.account')),
                ('revision', models.PositiveBigIntegerField(default=0)),
            ],
            options={
                'db_table': 'native_sync_cursors',
            },
        ),
    ]
