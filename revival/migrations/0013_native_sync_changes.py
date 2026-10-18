"""新增逐账号连续资产变更记录；历史库存和账本保持不变，旧版本间隙要求重新取快照。"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """只新增同步表和唯一约束，随资产事务共同提交及回滚。"""

    dependencies = [
        ('revival', '0012_native_draw_selection'),
    ]

    operations = [
        migrations.CreateModel(
            name='NativeSyncChange',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('revision', models.PositiveBigIntegerField()),
                ('instance_ids', models.JSONField(null=True)),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.account')),
            ],
            options={
                'db_table': 'native_sync_changes',
                'constraints': [models.UniqueConstraint(fields=('account', 'revision'), name='uq_native_sync_change')],
            },
        ),
    ]
