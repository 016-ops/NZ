"""Django 管理入口；复用项目配置启动迁移、业务命令和自动化测试。"""
import os
import sys
from django.core.management import execute_from_command_line

if __name__ == '__main__':
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nzproject.settings')
    execute_from_command_line(sys.argv)
