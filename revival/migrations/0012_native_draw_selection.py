"""新增自选奖池持久选择表；不修改已有库存、GID或历史抽奖结果。"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """仅向前新增表，旧规则和已提交订单继续有效。"""

    dependencies = [
        ('revival', '0011_native_sync_cursor'),
    ]

    operations = [
        migrations.CreateModel(
            name='NativeDrawSelection',
            fields=[
                ('account', models.OneToOneField(on_delete=django.db.models.deletion.DO_NOTHING, primary_key=True, serialize=False, to='revival.account')),
                ('selected', models.JSONField(default=list)),
                ('rule', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.nativerulerevision')),
            ],
            options={
                'db_table': 'native_draw_selections',
            },
        ),
    ]
