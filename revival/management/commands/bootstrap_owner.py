"""bootstrap_owner 命令：复用统一生命周期实现，避免两个入口行为不一致。"""
from revival.management.local_commands import LocalCommand


class Command(LocalCommand):
    """Django 管理命令入口；复用本模块业务处理，参数校验失败以 CommandError 终止。"""
    operation = 'bootstrap_owner'
