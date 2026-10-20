"""纯本机Launcher的PyInstaller Django hook：禁止自动发现项目设置、迁移和数据库。

上游通用hook会扫描manage.py和项目settings，并收集同级数据库。此服务仅通过
settings.configure使用INSTALLED_APPS=[]，不运行Django业务ORM；只需要WSGI、HTTP、
URL解析、JSON编码及默认异常处理。实际缺少依赖由Windows冻结进程smoke负责暴露。
"""
from PyInstaller.utils.hooks import collect_data_files


# 仅收Django自身公共数据，不读取nzproject、runtime、环境文件或用户数据库。
datas = collect_data_files('django', includes=['conf/locale/en/**', 'conf/locale/zh_Hans/**',
                                             'views/templates/**'])
hiddenimports = [
    'django.conf.global_settings', 'django.core.handlers.wsgi', 'django.core.handlers.exception',
    'django.http.request', 'django.http.response', 'django.core.serializers.json',
    'django.urls', 'django.urls.converters', 'django.urls.resolvers',
    'django.views.defaults', 'django.views.debug', 'django.utils.log',
    'django.template.backends.django', 'django.template.defaultfilters',
    'django.template.defaulttags', 'django.template.loader_tags',
]
