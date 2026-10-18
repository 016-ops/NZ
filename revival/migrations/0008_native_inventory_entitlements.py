"""向前新增原生库存稳定GID及超级账号权益；不重建或回退已有账号、库存与资产历史。"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """只建两张关联表；权益必须由显式管理命令经统一资产账本发放。"""

    dependencies = [
        ('revival', '0007_native_player_settings'),
    ]

    operations = [
        migrations.CreateModel(
            name='NativeSuperAccount',
            fields=[
                ('account', models.OneToOneField(on_delete=django.db.models.deletion.DO_NOTHING, primary_key=True, serialize=False, to='revival.account')),
                ('rules_version', models.CharField(max_length=100)),
                ('source_sha256', models.CharField(max_length=64)),
                ('granted_at', models.BigIntegerField()),
                ('enabled', models.BooleanField(default=True)),
            ],
            options={
                'db_table': 'native_super_accounts',
            },
        ),
        migrations.CreateModel(
            name='NativeInventoryBinding',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('rules_version', models.CharField(max_length=100)),
                ('inventory', models.OneToOneField(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.inventory')),
            ],
            options={
                'db_table': 'native_inventory_bindings',
            },
        ),
    ]
