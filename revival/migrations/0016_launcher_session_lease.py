"""仅新增可空的Launcher租约上限，保留旧会话、账号及全部资产。"""
from django.db import migrations, models


class Migration(migrations.Migration):
    """历史游戏会话保持NULL；新客户端显式选择租约模式后才使用续期合同。"""
    dependencies = [('revival', '0015_native_clan_bindings')]
    operations = [migrations.AddField(model_name='gamesession', name='lease_max_expires_at',
                                     field=models.BigIntegerField(null=True))]
