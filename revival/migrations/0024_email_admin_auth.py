"""新增管理员邮件挑战并撤销旧认证；不改变任何既有玩家状态或资产。"""
from django.db import migrations, models
import django.db.models.deletion
from django.db.models import F, Q


def activate_email_auth(apps, schema_editor):
    """旧会话不能跨认证策略切换；清除不再使用的TOTP/恢复码，保留账号与所有业务资产。"""
    alias = schema_editor.connection.alias
    apps.get_model('revival', 'Admin').objects.using(alias).update(
        otp_secret='', last_otp_step=-1, security_version=F('security_version')+1)
    apps.get_model('revival', 'Session').objects.using(alias).filter(kind='admin').update(revoked=1)
    apps.get_model('revival', 'AdminRecovery').objects.using(alias).all().delete()
    # 自助注册策略只应用于明确的用户请求；上线迁移不得批量激活历史待审、停用或封禁玩家。


class Migration(migrations.Migration):
    """结构增加与一次认证策略切换；反向迁移不能复活已撤销会话或旧秘密。"""
    dependencies = [('revival', '0023_server_monitoring')]
    operations = [
        migrations.CreateModel(
            name='AdminAuthChallenge',
            fields=[
                ('id', models.CharField(max_length=32, primary_key=True, serialize=False)),
                ('subject_digest', models.CharField(max_length=64)),
                ('purpose', models.CharField(max_length=16)),
                ('session_hash', models.CharField(default='', max_length=64)),
                ('operation_hash', models.CharField(default='', max_length=64)),
                ('security_version', models.BigIntegerField(null=True)),
                ('digest', models.CharField(default='', max_length=64)),
                ('ip_digest', models.CharField(max_length=64)),
                ('created_at', models.BigIntegerField()),
                ('expires_at', models.BigIntegerField()),
                ('status', models.CharField(default='PENDING', max_length=16)),
                ('attempts', models.PositiveSmallIntegerField(default=0)),
                ('consumed_at', models.BigIntegerField(null=True)),
                ('error_code', models.CharField(default='', max_length=32)),
                ('admin', models.ForeignKey(null=True, on_delete=django.db.models.deletion.DO_NOTHING, to='revival.admin')),
            ],
            options={
                'db_table': 'admin_auth_challenges',
                'indexes': [models.Index(fields=['ip_digest', 'created_at'], name='ix_admin_auth_source'),
                            models.Index(fields=['subject_digest', 'purpose', 'created_at'], name='ix_admin_auth_subject')],
                'constraints': [models.CheckConstraint(condition=Q(purpose__in=['LOGIN', 'STEP_UP']) &
                    Q(status__in=['PENDING', 'SENT', 'FAILED', 'SUPERSEDED', 'REJECTED']), name='ck_admin_auth_challenge')],
            }),
        migrations.RunPython(activate_email_auth, migrations.RunPython.noop),
    ]
