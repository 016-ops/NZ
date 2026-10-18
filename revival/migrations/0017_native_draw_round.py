"""只为已有自选记录新增轮次与完整奖池，不重置库存、规则和历史抽奖回执。"""
from django.db import migrations, models


class Migration(migrations.Migration):
    """历史选择保留，空轮次兼容旧规则；新轮次在明确选池时创建。"""
    dependencies=[('revival','0016_launcher_session_lease')]
    operations=[
        migrations.AddField(model_name='nativedrawselection',name='pool_keys',field=models.JSONField(default=list)),
        migrations.AddField(model_name='nativedrawselection',name='round_id',field=models.CharField(max_length=32,default='')),
    ]
