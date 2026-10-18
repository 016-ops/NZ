"""新增原生战队/申请数字身份，并为已有记录建立稳定绑定，不改变成员和审批状态。"""

import django.db.models.deletion
from django.db import migrations, models


def bind_existing(apps, schema_editor):
    """迁移只追加映射，按历史主键稳定排序；不重建或删除既有战队。"""
    for source,binding,field in [('NativeClan','NativeClanBinding','clan_id'),
                                 ('NativeClanApplication','NativeClanApplicationBinding','application_id')]:
        model=apps.get_model('revival',binding)
        for key in apps.get_model('revival',source).objects.order_by('pk').values_list('pk',flat=True).iterator():
            model.objects.get_or_create(**{field:key})


class Migration(migrations.Migration):
    """绑定迁移采用历史模型；回退不删除已有业务战队和申请。"""

    dependencies = [
        ('revival', '0014_native_friend_groups'),
    ]

    operations = [
        migrations.CreateModel(
            name='NativeClanApplicationBinding',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('application', models.OneToOneField(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.nativeclanapplication')),
            ],
            options={
                'db_table': 'native_clan_application_bindings',
            },
        ),
        migrations.CreateModel(
            name='NativeClanBinding',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('clan', models.OneToOneField(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.nativeclan')),
            ],
            options={
                'db_table': 'native_clan_bindings',
            },
        ),
        migrations.RunPython(bind_existing,migrations.RunPython.noop),
    ]
