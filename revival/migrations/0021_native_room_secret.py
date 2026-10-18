"""向前新增房间口令密文，供原生房主移交通知；账号密码、旧房间设置及资产不改变。"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """新增字段默认空；当前尚未发布20，正式部署会一并建立加密房间合同。"""

    dependencies = [
        ('revival', '0020_native_rooms'),
    ]

    operations = [
        migrations.AddField(
            model_name='nativeroombinding',
            name='sealed_password',
            field=models.TextField(default=''),
        ),
    ]
