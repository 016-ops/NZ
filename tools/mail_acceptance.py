"""人工收件的 DEV 邮箱验收；验证码仅通过标准输入读取，账号密码保存在忽略的本机私有状态。"""
import argparse
import json
import secrets
import sys
import time
from pathlib import Path
from tools.email_launcher import Client, LoginError, fingerprint
from revival.security import token_hash
from adapters.email_protocol import PROTOCOL
from tools.verify_email_dev import owner_login, command, metadata


def main():
    """分阶段实际发码、注册和恢复；不读取用户邮箱，不在控制台输出邮箱、密码、码或会话。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['register-send','register-finish','reset-send','reset-finish','status'])
    parser.add_argument('--email', help='Only for the explicitly authorized initial test recipient')
    parser.add_argument('--recover-interrupted', action='store_true', help='Repair only this owned test account after interrupted acceptance; at most three emails')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    path = root / 'runtime/mail-acceptance.json'
    state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    client = Client('http://127.0.0.1:18765')
    if args.phase == 'register-send':
        if state:
            raise RuntimeError('Existing acceptance state must be reviewed; no automatic resend')
        owner = json.loads((root/'runtime/dev-owner-current.json').read_text(encoding='utf-8'))
        admin = owner_login(client, owner)
        try:
            invite = command(client,admin,'invite.create',max_uses=1,expires_at=int(time.time())+3600)
            state = {'email':args.email, 'password':secrets.token_urlsafe(24), 'invitation':invite['invitation_token'],
                     'invitation_id':invite['invitation_id'], 'emails_requested':1}
            state['challenge'] = client.request('/api/player/email-code',dict(email=state['email'],purpose='register',invitation=state['invitation']))
        finally:
            client.request('/api/logout',{},admin)
    elif args.phase == 'register-finish':
        code = sys.stdin.readline().strip()
        payload = metadata(email=state['email'],password=state['password'],invitation=state['invitation'],
                           challenge_id=state['challenge']['challenge_id'],code=code)
        account = client.request('/api/player/register',payload)
        state['account_id'], state['game_id'] = account['account_id'], account['game_id']
        state['email_verified'] = account['email_verified']
        owner = json.loads((root/'runtime/dev-owner-current.json').read_text(encoding='utf-8'))
        admin = owner_login(client,owner)
        try:
            command(client,admin,'user.approve',target_account_id=state['account_id'])
            command(client,admin,'invite.revoke',invitation_id=state['invitation_id'])
        finally:
            client.request('/api/logout',{},admin)
        session = client.request('/api/player/login',dict(email=state['email'],password=state['password']))
        state['old_token'] = session['token']
        character = command(client,session['token'],'character.create',nickname='邮箱验收_'+str(state['game_id']))
        state['character_id'] = character['character_id']
        state['registered'] = True
    elif args.phase == 'reset-send':
        if not state.get('registered') or state.get('emails_requested',0)>=3:
            raise RuntimeError('Reset is allowed once after successful registration')
        if state.get('reset_challenge'):
            if not args.recover_interrupted or state.get('reset_verified'):
                raise RuntimeError('No automatic resend after an unknown reset outcome')
            # 此分支只修复本工具刚创建的验收账号。通过既有 Owner MFA 和审计命令准备已知临时密码，
            # 随后仍须真实邮件验证码才能完成被测的玩家自助恢复；不直接写数据库或解除制裁。
            owner=json.loads((root/'runtime/dev-owner-current.json').read_text(encoding='utf-8'))
            admin=owner_login(client,owner)
            state['password']=secrets.token_urlsafe(24)
            path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
            try:
                command(client,admin,'user.reset',target_account_id=state['account_id'],new_password=state['password'])
            finally:
                client.request('/api/logout',{},admin)
            state['old_token']=client.request('/api/player/login',dict(email=state['email'],password=state['password']))['token']
            state['recovery_preparation_admin_reset']=True
        state['reset_challenge'] = client.request('/api/player/email-code',dict(email=state['email'],purpose='reset'))
        state['emails_requested'] += 1
    elif args.phase == 'reset-finish':
        code = sys.stdin.readline().strip()
        password = secrets.token_urlsafe(24)
        before=client.request('/api/views/me',token=state['old_token'])
        client_sha,_=fingerprint(root/'runtime/native-recovery/coherent-2020/nz')
        verifier=secrets.token_urlsafe(32)
        ticket=client.request('/api/launcher/tickets',metadata(protocol=PROTOCOL,client_sha256=client_sha,
                              verifier_sha256=token_hash(verifier)),state['old_token'])
        game=client.request('/api/launcher/redeem',dict(protocol=PROTOCOL,client_sha256=client_sha,
                            ticket=ticket['ticket'],verifier=verifier))
        # 在有外部副作用前记录待用密码和前快照；HTTP 成功后立即检查点，避免进程中断丢失新凭据。
        state.update(pending_password=password,before_reset=before)
        path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        result = client.request('/api/player/password-reset',dict(email=state['email'],password=password,
                               challenge_id=state['reset_challenge']['challenge_id'],code=code))
        state['password'] = password
        state['reset_verified'] = result['password_reset']
        path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        try:
            client.request('/api/views/me',token=state.pop('old_token'))
        except LoginError as caught:
            if str(caught) != 'AUTH': raise
            state['old_session_revoked'] = True
        else:
            raise RuntimeError('OLD_SESSION_REMAINED_ACTIVE')
        try:
            client.request('/api/game/session',dict(protocol=PROTOCOL,client_sha256=client_sha),game['game_token'])
        except LoginError as caught:
            if str(caught)!='AUTH': raise
            state['old_game_session_revoked']=True
        else:
            raise RuntimeError('OLD_GAME_SESSION_REMAINED_ACTIVE')
        session = client.request('/api/player/login',dict(email=state['email'],password=password))
        me = client.request('/api/views/me',token=session['token'])
        state['same_character'] = me['character']['id'] == state['character_id']
        state['same_game_id'] = me['game_id'] == state['game_id']
        state['same_assets'] = all(me[field]==before[field] for field in ('wallets','inventory','equipment'))
        client.request('/api/logout',{},session['token'])
        state.pop('pending_password',None)
    elif args.phase != 'status':
        raise RuntimeError('Unknown phase')
    path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    report = {key:state.get(key,False) for key in ('email_verified','registered','reset_verified',
               'old_session_revoked','old_game_session_revoked','same_character','same_game_id','same_assets','recovery_preparation_admin_reset')}
    report.update(phase=args.phase,emails_requested=state.get('emails_requested',0),game_started=False,lobby_verified=False)
    (root/'evidence/aliyun-email-live.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
