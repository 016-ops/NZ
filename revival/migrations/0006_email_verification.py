"""新增持久邮箱验证码，不改写既有玩家、审计、制裁或资产；旧邮箱可通过验证码补验。"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """仅追加独立表和发送额度索引；降级应用时保留此表，不回滚玩家密码或已消费验证码。"""

    dependencies = [
        ('revival', '0005_independent_email_login'),
    ]

    operations = [
        migrations.CreateModel(
            name='EmailChallenge',
            fields=[
                ('id', models.CharField(max_length=32, primary_key=True, serialize=False)),
                ('email', models.CharField(db_collation='utf8mb4_0900_bin', max_length=254)),
                ('purpose', models.CharField(max_length=16)),
                ('digest', models.CharField(max_length=64)),
                ('ip_digest', models.CharField(max_length=64)),
                ('security_version', models.BigIntegerField(null=True)),
                ('created_at', models.BigIntegerField()),
                ('expires_at', models.BigIntegerField()),
                ('status', models.CharField(default='PENDING', max_length=16)),
                ('attempts', models.PositiveSmallIntegerField(default=0)),
                ('consumed_at', models.BigIntegerField(null=True)),
                ('error_code', models.CharField(default='', max_length=32)),
                ('account', models.ForeignKey(null=True, on_delete=django.db.models.deletion.DO_NOTHING, to='revival.account')),
            ],
            options={
                'db_table': 'email_challenges',
                'indexes': [models.Index(fields=['email', 'created_at'], name='email_challenge_window'), models.Index(fields=['ip_digest', 'created_at'], name='email_source_window')],
            },
        ),
    ]
