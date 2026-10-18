"""向前新增按账号/客户端/TDR隔离的原生设置表；不改写既有账号、资产或历史审计。"""

import django.db.models.deletion
from django.db import migrations, models
from django.db.models.functions import Length
from django.db.models.lookups import Exact


class Migration(migrations.Migration):
    """只增加新表及唯一性、字节长度、版本约束；应用升级前保留现有数据库新写入。"""

    dependencies = [
        ('revival', '0006_email_verification'),
    ]

    operations = [
        migrations.CreateModel(
            name='NativePlayerSettings',
            fields=[
                ('id', models.CharField(db_collation='utf8mb4_0900_bin', max_length=32, primary_key=True, serialize=False)),
                ('client_sha256', models.CharField(db_collation='utf8mb4_0900_bin', max_length=64)),
                ('metadata_sha256', models.CharField(db_collation='utf8mb4_0900_bin', max_length=64)),
                ('payload', models.BinaryField(max_length=3200)),
                ('payload_sha256', models.CharField(db_collation='utf8mb4_0900_bin', max_length=64)),
                ('byte_count', models.PositiveSmallIntegerField()),
                ('revision', models.PositiveBigIntegerField()),
                ('updated_at', models.BigIntegerField()),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.account')),
            ],
            options={
                'db_table': 'native_player_settings',
                'constraints': [models.UniqueConstraint(fields=('account', 'client_sha256', 'metadata_sha256'), name='uq_native_settings_scope'), models.CheckConstraint(condition=models.Q(('byte_count__lte', 3200), ('revision__gte', 1)), name='ck_native_settings_bounds'), models.CheckConstraint(condition=Exact(Length('payload'), models.F('byte_count')), name='ck_native_settings_length')],
            },
        ),
    ]
