"""向前新增工程配方熟练度，保留既有账号、库存和资产流水。"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """只建立独立进度表；首次实际制造提交时才产生本人的记录。"""
    dependencies=[('revival','0018_native_workshop_state')]
    operations=[migrations.CreateModel(name='NativeEngineeringProgress',fields=[
        ('id',models.CharField(max_length=32,primary_key=True,serialize=False)),
        ('recipe_id',models.PositiveIntegerField()),
        ('proficiency',models.PositiveSmallIntegerField(default=0)),
        ('attempts',models.PositiveBigIntegerField(default=0)),
        ('account',models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING,to='revival.account')),
    ],options={'db_table':'native_engineering_progress','constraints':[
        models.UniqueConstraint(fields=('account','recipe_id'),name='uq_native_engineering_recipe')]})]
