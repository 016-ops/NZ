"""本地 Django 配置：读取环境中的 MySQL 与 profile 配置，凭据不写入源码。"""
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
environment_file = Path(os.environ.get('NZ_ENV_FILE', '.env.local'))
if not environment_file.is_absolute():
    environment_file = BASE_DIR / environment_file
if os.environ.get('NZ_ENV_FILE') and not environment_file.is_file():
    raise ValueError('The explicitly selected NZ_ENV_FILE does not exist')
load_dotenv(environment_file, override=False)
# 发信凭据单独保存，服务器部署时同样位于只允许服务组读取的 shared 中。
mail_file = Path(os.environ.get('NZ_MAIL_ENV_FILE', BASE_DIR / 'runtime/aliyun-mail.env'))
load_dotenv(mail_file, override=False)
NZ_EMAIL_VERIFICATION_REQUIRED = os.environ.get('NZ_EMAIL_VERIFICATION_REQUIRED', '1') == '1'
NZ_MAIL_PROVIDER = os.environ.get('NZ_MAIL_PROVIDER', '')
NZ_MAIL_HOST = os.environ.get('NZ_MAIL_HOST', '')
NZ_MAIL_PORT = int(os.environ.get('NZ_MAIL_PORT', '465'))
NZ_MAIL_FROM = os.environ.get('NZ_MAIL_FROM', '')
NZ_MAIL_USERNAME = os.environ.get('NZ_MAIL_USERNAME', '')
NZ_MAIL_PASSWORD = os.environ.get('NZ_MAIL_PASSWORD', '')
NZ_MAIL_SENDER_NAME = os.environ.get('NZ_MAIL_SENDER_NAME', 'NZ Revival')
SECRET_KEY = os.environ.get('NZ_SECRET_KEY', 'local-control-plane-no-cookie-sessions')
DEBUG = False
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', '[::1]', 'testserver']
# 公网 Web 只接受部署者明确设置的单一来源；默认仍保持原 SSH 回环部署边界。
NZ_PUBLIC_ORIGIN = os.environ.get('NZ_PUBLIC_ORIGIN', '').rstrip('/')
NZ_PUBLIC_HOST = ''
# 仅显式信任本机HTTPS反代的X-Real-IP；默认忽略，避免公网请求自行伪造限流身份。
NZ_TRUST_LOOPBACK_PROXY = os.environ.get('NZ_TRUST_LOOPBACK_PROXY', '0') == '1'
if NZ_PUBLIC_ORIGIN:
    # 房间口令密文使用此密钥派生；公网部署必须提供私有强度足够的值，不能沿用本机默认。
    if SECRET_KEY == 'local-control-plane-no-cookie-sessions' or len(SECRET_KEY) < 32:
        raise ValueError('Public Web deployment requires a private NZ_SECRET_KEY of at least 32 characters')
    public = urlsplit(NZ_PUBLIC_ORIGIN)
    if (not public.hostname or public.username or public.password or public.path or public.query or public.fragment
            or public.scheme not in ('http', 'https')
            or public.scheme == 'http' and public.hostname not in ('127.0.0.1', 'localhost', '::1')):
        raise ValueError('NZ_PUBLIC_ORIGIN must be one HTTPS origin or a loopback HTTP origin')
    NZ_PUBLIC_HOST = public.netloc
    ALLOWED_HOSTS.append(public.hostname)
ROOT_URLCONF = 'nzproject.urls'
WSGI_APPLICATION = 'nzproject.wsgi.application'
INSTALLED_APPS = ['revival.apps.RevivalConfig']
MIDDLEWARE = ['revival.middleware.ControlPlaneMiddleware']
NZ_PROFILE = os.environ.get('NZ_PROFILE', 'current')
if NZ_PROFILE not in ('current', 'synthetic'):
    raise ValueError('NZ_PROFILE must be current or synthetic')
current_name = os.environ.get('NZ_DB_NAME', 'nz_current')
synthetic_name = os.environ.get('NZ_SYNTHETIC_DB_NAME', 'nz_synthetic')
NZ_PVE_TEST_PROFILE = os.environ.get('NZ_PVE_TEST_PROFILE') == '1'
pve_name = os.environ.get('NZ_PVE_DB_NAME', 'nz_pve_synthetic')
if NZ_PVE_TEST_PROFILE and NZ_PROFILE != 'synthetic':
    raise ValueError('PvE test rules require synthetic profile')
test_name = os.environ.get('NZ_TEST_DB_NAME', 'nz_unittest')
if len({current_name, synthetic_name, pve_name, test_name}) != 4:
    raise ValueError('Current, synthetic and test databases must be distinct')
DATABASES = {'default': {
    'ENGINE': 'django.db.backends.mysql',
    'NAME': (pve_name if NZ_PVE_TEST_PROFILE else synthetic_name) if NZ_PROFILE == 'synthetic' else current_name,
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
