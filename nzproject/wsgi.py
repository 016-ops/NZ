"""Waitress 使用的 Django WSGI 应用；配置来自项目环境，不使用旧 HTTP 服务。"""
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nzproject.settings')
application = get_wsgi_application()
