"""注册控制面 Django 应用和模型默认主键规则。"""
from django.apps import AppConfig


class RevivalConfig(AppConfig):
    """注册控制面 Django 应用；模型和迁移由 Django 管理，不启用默认认证表替代业务账号。"""
    name = 'revival'
    default_auto_field = 'django.db.models.BigAutoField'
