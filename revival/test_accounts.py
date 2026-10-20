"""旧固定码和免码账号工具的退役入口；所有管理员统一使用固定邮箱邮件认证。"""
from django.core.management.base import CommandError


def create_local_test_admin(login, *, password=None, password_only=False):
    """保留导入兼容但立即拒绝，不读取私有配置、不创建账号、不写入任何凭据文件。"""
    raise CommandError('AUTH_METHOD_RETIRED: use bootstrap_admin and fixed-mailbox email authentication')
