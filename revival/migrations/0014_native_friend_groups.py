"""新增账号范围内的好友分组；不更改已有好友关系或备注。"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """仅新增分组表和账号范围内唯一约束。"""

    dependencies = [
        ('revival', '0013_native_sync_changes'),
    ]

    operations = [
        migrations.CreateModel(
            name='NativeFriendGroup',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('group_id', models.PositiveSmallIntegerField()),
                ('name', models.CharField(max_length=32)),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.account')),
            ],
            options={
                'db_table': 'native_friend_groups',
                'constraints': [models.UniqueConstraint(fields=('account', 'group_id'), name='uq_native_friend_group')],
            },
        ),
    ]
