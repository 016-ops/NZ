"""用户明确授权的本地测试账号配置；所有放宽仅绑定合成库内的唯一账号 ID。"""
import base64
import json
import os
import secrets
from django.conf import settings
from django.core.management.base import CommandError
from django.db import connections
from dotenv import dotenv_values
from .security import normalize_login, password_hash, token_hash
from .service import Service, identifier


def create_local_test_admin(login, *, password=None, password_only=False):
    """在部署锁内创建账号和审计，安装私有配置；文件或提交失败时恢复原配置。

    固定码账号与免码账号各自独立配置，不能覆盖已有账号或凭据；此函数不提供 HTTP 入口。
    密码仅以 scrypt 哈希入库，明文凭据只存忽略的 runtime 文件，供请求它的本地用户读取。
    """
    if settings.NZ_PROFILE!='synthetic':
        raise CommandError('Test authentication exceptions require TEST_PROFILE')
    login=normalize_login(login)
    config=settings.BASE_DIR/'.env.local'
    original=config.read_text(encoding='utf-8') if config.is_file() else None
    previous=dict(dotenv_values(config)) if config.is_file() else {}
    prefix='NZ_PASSWORD_ONLY_TEST_ADMIN' if password_only else 'NZ_FIXED_TEST_ADMIN'
    if previous.get(prefix+'_ID') or os.environ.get(prefix+'_ID'):
        raise CommandError('This test account mode is already configured; refusing to overwrite it')
    directory=settings.BASE_DIR/'runtime';directory.mkdir(parents=True,exist_ok=True)
    target=directory/('demo-admin.json' if password_only else 'test-admin.json')
    temporary=settings.BASE_DIR/'.env.local.test-admin-tmp'
    if target.exists() or temporary.exists():
        raise CommandError('Private output already exists; refusing to overwrite it')
    password=password if password is not None else secrets.token_urlsafe(24)
    hashed=password_hash(password,allow_test_short=password_only)
    code=str(secrets.randbelow(900000)+100000)
    admin_id=identifier()
    secret=base64.b32encode(secrets.token_bytes(20)).decode('ascii')
    database=connections['default'].settings_dict['NAME']
    previous[prefix+'_ID']=admin_id
    previous[prefix+'_DATABASE']=database
    if not password_only: previous[prefix+'_OTP_HASH']=token_hash(code)
    installed=False;created_credentials=False;created_temporary=False
    service=Service(test_profile=True)
    try:
        with service.db.transaction() as repository:
            if repository.table('admins').filter(login=login).exists():
                raise CommandError('Administrator login already exists')
            repository.insert('admins',{'id':admin_id,'login':login,'password_hash':hashed,'otp_secret':secret,'role':'owner'})
            service._audit(repository,'local-test-setup','admin.test-bootstrap',admin_id,identifier(),
                          'User-requested synthetic test administrator',{'profile':'TEST_PROFILE','login_mfa':not password_only})
            with target.open('x',encoding='utf-8') as output:
                created_credentials=True
                credentials={'login':login,'password':password,'profile':'TEST_PROFILE','database':database,'role':'owner','session_seconds':900}
                if password_only:
                    # 登录免码不影响高风险再认证；可选真实 MFA 绑定信息仅留在私有文件。
                    credentials['login_mfa_required']=False
                    credentials['high_risk_mfa_secret']=secret
                else: credentials['fixed_test_otp']=code
                json.dump(credentials,output,ensure_ascii=False,indent=2)
            with temporary.open('x',encoding='utf-8') as output:
                created_temporary=True
                for key,value in previous.items():
                    if value is not None:
                        escaped=str(value).replace('\\','\\\\').replace("'","\\'")
                        output.write(f"{key}='{escaped}'\n")
            temporary.replace(config);installed=True
    except Exception:
        if created_credentials: target.unlink(missing_ok=True)
        if created_temporary: temporary.unlink(missing_ok=True)
        if installed:
            if original is None: config.unlink(missing_ok=True)
            else:
                temporary.write_text(original,encoding='utf-8');temporary.replace(config)
        raise
    return target
