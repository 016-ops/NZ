"""为独立 DEV 业务库创建新 Owner；空库初始化，非空库必须由已有 Owner 正常再认证。"""
import argparse
import getpass
import json
import os
from pathlib import Path
import secrets
import time
from urllib.parse import quote


def main():
    """随机密码和首次 MFA/恢复码仅写本机忽略文件，不重置已有账号或放宽认证规则。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pve', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--operator-json', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if not output.is_relative_to(root / 'runtime') or output.exists():
        raise ValueError('A new private runtime credentials file is required')
    os.environ['DJANGO_SETTINGS_MODULE'] = 'nzproject.settings'
    os.environ['NZ_PROFILE'] = 'synthetic' if args.pve else 'current'
    os.environ['NZ_PVE_TEST_PROFILE'] = '1' if args.pve else '0'
    import django
    django.setup()
    from revival.service import Service, identifier
    from revival.security import otp
    service = Service(test_profile=args.pve)
    password = secrets.token_urlsafe(24)
    with service.db.transaction():
        if args.operator_json:
            operator = json.loads(args.operator_json.read_text(encoding='utf-8'))
            operator_password = getpass.getpass('Existing Owner password: ')
            step = int(time.time()) // 30
            session = service.login(operator['login'], operator_password, kind='admin', otp_code=otp(operator['otp_secret'], step))
            # 正常 TOTP 校验支持相邻时间步；再认证必须使用尚未消费的新码。
            result = service.command(session['token'], 'admin.create', {
                'login': 'dev-owner', 'new_password': password, 'role': 'owner',
                'current_password': operator_password, 'otp_code': otp(operator['otp_secret'], step + 1),
                'request_id': identifier(), 'idempotency_key': identifier(), 'reason': 'Initialize private DEV Owner',
            })
        else:
            result = service.bootstrap_admin('dev-owner', password)
        record = dict(result, login='dev-owner', password=password, profile=service.catalog['id'])
        record['mfa_uri'] = 'otpauth://totp/NZRevival:dev-owner?secret=' + quote(result['otp_secret']) + '&issuer=NZRevival'
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x', encoding='utf-8') as stream:
            json.dump(record, stream, indent=2)
    print(json.dumps({'profile': service.catalog['id'], 'owner_created': True, 'private_credentials_file': output.name}))


if __name__ == '__main__':
    main()
