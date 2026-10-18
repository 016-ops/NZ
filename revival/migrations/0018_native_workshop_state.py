"""向前新增工坊每日免费状态，保留所有既有资产、抽奖、签到和规则版本。"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """仅建账号状态表；免费时间由第一笔真实开奖写入，不预先赠送或扣除道具。"""
    dependencies=[('revival','0017_native_draw_round')]
    operations=[migrations.CreateModel(name='NativeWorkshopState',fields=[
        ('account',models.OneToOneField(on_delete=django.db.models.deletion.DO_NOTHING,primary_key=True,serialize=False,to='revival.account')),
        ('last_free_at',models.PositiveBigIntegerField(default=0)),
    ],options={'db_table':'native_workshop_states'})]
