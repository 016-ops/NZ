"""本地 Django 配置：读取环境中的 MySQL 与 profile 配置，凭据不写入源码。"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / '.env.local', override=False)
SECRET_KEY = os.environ.get('NZ_SECRET_KEY', 'local-control-plane-no-cookie-sessions')
DEBUG = False
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', '[::1]', 'testserver']
ROOT_URLCONF = 'nzproject.urls'
WSGI_APPLICATION = 'nzproject.wsgi.application'
INSTALLED_APPS = ['revival.apps.RevivalConfig']
MIDDLEWARE = ['revival.middleware.ControlPlaneMiddleware']
NZ_PROFILE = os.environ.get('NZ_PROFILE', 'current')
if NZ_PROFILE not in ('current', 'synthetic'):
    raise ValueError('NZ_PROFILE must be current or synthetic')
current_name = os.environ.get('NZ_DB_NAME', 'nz_current')
synthetic_name = os.environ.get('NZ_SYNTHETIC_DB_NAME', 'nz_synthetic')
test_name = os.environ.get('NZ_TEST_DB_NAME', 'nz_unittest')
if len({current_name, synthetic_name, test_name}) != 3:
    raise ValueError('Current, synthetic and test databases must be distinct')
DATABASES = {'default': {
    'ENGINE': 'django.db.backends.mysql',
    'NAME': synthetic_name if NZ_PROFILE == 'synthetic' else current_name,
    'HOST': os.environ.get('NZ_DB_HOST', '127.0.0.1'),
    'PORT': os.environ.get('NZ_DB_PORT', '3307'),
    'USER': os.environ.get('NZ_DB_USER', 'nz_local'),
    'PASSWORD': os.environ.get('NZ_DB_PASSWORD', ''),
    'CONN_MAX_AGE': 0,
    'OPTIONS': {'charset': 'utf8mb4', 'isolation_level': 'read committed',
                'init_command': "SET sql_mode='STRICT_TRANS_TABLES', default_storage_engine=INNODB"},
    'TEST': {'NAME': test_name, 'CHARSET': 'utf8mb4', 'COLLATION': 'utf8mb4_0900_bin'},
}}
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_TZ = True
TIME_ZONE = 'UTC'
DATA_UPLOAD_MAX_MEMORY_SIZE = 65536
NZ_PORT = int(os.environ.get('NZ_PORT', '8765'))
