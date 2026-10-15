"""增量添加独立邮箱及一次性交接表，保留旧玩家 ID、账号、账本和制裁数据。"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """只创建新表和约束，不从旧用户名猜测邮箱，也不自动迁移为已验证身份。"""

    dependencies = [
        ('revival', '0004_room_access_and_deliveries'),
    ]

    operations = [
        migrations.CreateModel(
            name='PlayerEmail',
            fields=[
                ('account', models.OneToOneField(on_delete=django.db.models.deletion.DO_NOTHING, primary_key=True, serialize=False, to='revival.account')),
                ('email', models.CharField(db_collation='utf8mb4_0900_bin', max_length=254, unique=True)),
                ('created_at', models.BigIntegerField()),
                ('verified_at', models.BigIntegerField(null=True)),
            ],
            options={
                'db_table': 'player_emails',
            },
        ),
        migrations.CreateModel(
            name='LaunchTicket',
            fields=[
                ('digest', models.CharField(db_collation='utf8mb4_0900_bin', max_length=64, primary_key=True, serialize=False)),
                ('id', models.CharField(db_collation='utf8mb4_0900_bin', max_length=32, unique=True)),
                ('security_version', models.BigIntegerField()),
                ('profile', models.CharField(max_length=100)),
                ('client_sha256', models.CharField(max_length=64)),
                ('verifier_sha256', models.CharField(max_length=64)),
                ('created_at', models.BigIntegerField()),
                ('expires_at', models.BigIntegerField()),
                ('consumed_at', models.BigIntegerField(null=True)),
                ('revoked', models.BooleanField(default=False)),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.account')),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.session')),
            ],
            options={
                'db_table': 'launch_tickets',
            },
        ),
        migrations.CreateModel(
            name='GameSession',
            fields=[
                ('digest', models.CharField(db_collation='utf8mb4_0900_bin', max_length=64, primary_key=True, serialize=False)),
                ('security_version', models.BigIntegerField()),
                ('profile', models.CharField(max_length=100)),
                ('client_sha256', models.CharField(max_length=64)),
                ('created_at', models.BigIntegerField()),
                ('expires_at', models.BigIntegerField()),
                ('revoked', models.BooleanField(default=False)),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.account')),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.session')),
                ('launch', models.OneToOneField(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.launchticket')),
            ],
            options={
                'db_table': 'game_sessions',
            },
        ),
    ]
