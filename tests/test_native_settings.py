"""真实MySQL原生设置回归：版本隔离、CAS重试、并发、撤销及审计/outbox原子性，不发送邮件。"""
import base64
import json
from unittest.mock import patch

from django.db import DatabaseError, IntegrityError
from django.db.models import Q
from django.conf import settings
from django.test import Client, override_settings
from adapters.cn_current.zone import ZONE_METADATA_SHA256
from revival import launcher, native_settings
from revival.security import new_token, token_hash
from revival.service import Fault, Service, identifier
from .test_control_plane import Base
from .support import DatabasePool


class NativeSettingsTests(Base):
    """各例仅使用Django独立测试库和关闭验证邮件的既有Base夹具，不接触真实玩家库或SMTP。"""
    def email_game(self, suffix='one', client_sha='a'*64):
        """建立独立测试邮箱账号及一次性游戏交接；返回账号、来源会话、游戏凭据和绑定字段。"""
        invite=self.invitation()
        email=f'settings-{suffix}@example.test'
        account=self.s.register(self.meta(email=email,password=self.password,invitation=invite['invitation_token']))
        self.command('user.approve',{'target_account_id':account['account_id']})
        player=self.s.login(email,self.password)['token']
        game,data=self.game_for(player,client_sha)
        return account['account_id'],player,game,data

    def game_for(self, player, client_sha):
        """为已登录玩家按指定客户端指纹兑换游戏会话；不假造原生认证或跳过现有票据检查。"""
        verifier=new_token()
        ticket=launcher.issue(self.s,player,self.meta(protocol=launcher.PROTOCOL,client_sha256=client_sha,
                                                    verifier_sha256=token_hash(verifier)))
        data=dict(protocol=launcher.PROTOCOL,client_sha256=client_sha,metadata_sha256=ZONE_METADATA_SHA256)
        game=launcher.redeem(self.s,dict(data,ticket=ticket['ticket'],verifier=verifier))['game_token']
        return game,data

    def write(self, game, data, payload, revision=0):
        """经真实领域接口提交完整不透明字节与CAS版本，编码只用于HTTP传输。"""
        return native_settings.save(self.s,game,dict(data,payload_base64=base64.b64encode(payload).decode('ascii'),
                                                   expected_revision=revision))

    def test_binary_roundtrip_restart_and_client_version_isolation(self):
        """包含NUL/非UTF8的3200B设置原样重登；另一客户端指纹与未知TDR不能覆盖原版本。"""
        _,player,game,data=self.email_game()
        payload=(bytes(range(256))*13)[:3200]
        saved=self.write(game,data,payload)
        self.assertEqual(saved['revision'],1)
        self.assertEqual(saved['bytes'],3200)
        restored=Service(test_profile=True,clock=lambda:self.now)
        self.assertEqual(native_settings.load(restored,game,data)['payload_base64'],saved['payload_base64'])
        game_b,data_b=self.game_for(player,'b'*64)
        self.assertEqual(native_settings.load(self.s,game_b,data_b)['revision'],0)
        game_a,data_a=self.game_for(player,'a'*64)
        self.assertEqual(base64.b64decode(native_settings.load(self.s,game_a,data_a)['payload_base64']),payload)
        with self.assertRaisesRegex(Fault,'Unsupported native settings metadata'):
            native_settings.load(self.s,game_a,dict(data_a,metadata_sha256='c'*64))

    def test_concurrent_writes_and_lost_ack_retry_do_not_lose_newer_settings(self):
        """同一revision并发不同正文只有一次提交；相同正文重试不增版，旧不同内容不能覆盖新内容。"""
        _,_,game,data=self.email_game()
        def write(payload):
            """各线程使用独立MySQL连接执行相同CAS，预期一份正文胜出。"""
            try:return self.write(game,data,payload)
            except Fault as error:return error.code
        with DatabasePool(max_workers=2) as pool:
            results=list(pool.map(write,[b'first',b'second']))
        self.assertEqual(sum(isinstance(r,dict) for r in results),1)
        self.assertIn('SETTINGS_REVISION_CONFLICT',results)
        current=native_settings.load(self.s,game,data)
        body=base64.b64decode(current['payload_base64'])
        self.assertEqual(self.write(game,data,body)['status'],'UNCHANGED')
        self.assertEqual(self.write(game,data,b'newer',1)['revision'],2)
        with self.assertRaises(Fault) as conflict:self.write(game,data,body,1)
        self.assertEqual(conflict.exception.code,'SETTINGS_REVISION_CONFLICT')
        with self.assertRaises(Fault):self.write(game,data,b'newer',99)
        with self.s.db.transaction() as c:
            self.assertEqual(c.count('native_player_settings')[0][0],1)
            self.assertEqual(c.count('outbox',Q(type='native.settings.changed'))[0][0],2)

    def test_cross_account_and_non_game_credentials_are_isolated(self):
        """其他账号只能写自己的设置；玩家/管理员凭据、错误客户端指纹均不能调用游戏设置。"""
        first,player,game,data=self.email_game()
        self.write(game,data,b'account one')
        second,_,other,other_data=self.email_game('two')
        self.assertEqual(native_settings.load(self.s,other,other_data)['revision'],0)
        self.write(other,dict(other_data,target_account_id=first),b'account two')
        self.assertEqual(base64.b64decode(native_settings.load(self.s,game,data)['payload_base64']),b'account one')
        for credential,changed in ((player,data),(self.owner,data),(game,dict(data,client_sha256='b'*64))):
            with self.assertRaises(Fault):native_settings.load(self.s,credential,changed)
        with self.s.db.transaction() as c:
            self.assertEqual({r['account_id'] for r in c.select('native_player_settings')},{first,second})

    def test_source_session_revocation_expiry_and_security_change_reject_writes(self):
        """每次操作检查来源会话/到期/安全版本，不允许缓存已撤销的游戏授权。"""
        for index,kind in enumerate(('logout','expired','security')):
            account,player,game,data=self.email_game(str(index))
            self.write(game,data,b'preserved')
            with self.s.db.transaction() as c:
                if kind=='logout':c.update('sessions',Q(token_hash=token_hash(player)),{'revoked':1})
                elif kind=='expired':c.update('game_sessions',Q(digest=token_hash(game)),{'expires_at':self.now})
                else:c.update('accounts',Q(id=account),{'security_version':2})
            with self.assertRaises(Fault):native_settings.load(self.s,game,data)
            with self.assertRaises(Fault):self.write(game,data,b'forbidden',1)
            with self.s.db.transaction() as c:
                row=c.select('native_player_settings',Q(account_id=account)).fetchone()
                self.assertEqual(bytes(row['payload']),b'preserved')
                self.assertEqual(row['revision'],1)

    def test_invalid_base64_boundaries_and_revision_fail_before_changes(self):
        """超限、非规范base64、布尔/负版本拒绝；空设置是可持久化的显式客户端选择。"""
        _,_,game,data=self.email_game()
        for payload,revision in (('AB==',0),('not base64',0),(base64.b64encode(b'x'*3201).decode(),0),('',True),('',-1)):
            with self.subTest(revision=revision), self.assertRaises(Fault):
                native_settings.save(self.s,game,dict(data,payload_base64=payload,expected_revision=revision))
        self.assertEqual(self.write(game,data,b'')['revision'],1)
        self.assertEqual(native_settings.load(self.s,game,data)['bytes'],0)

    def test_audit_and_outbox_failures_roll_back_settings_update(self):
        """审计或outbox故障必须与设置更新一起回滚，不能回复一次并未持久化的保存成功。"""
        _,_,game,data=self.email_game()
        self.write(game,data,b'old')
        for method in ('_audit','_outbox'):
            with patch.object(self.s,method,side_effect=DatabaseError('injected failure')):
                with self.assertRaises(DatabaseError):self.write(game,data,b'new',1)
            result=native_settings.load(self.s,game,data)
            self.assertEqual(result['revision'],1)
            self.assertEqual(base64.b64decode(result['payload_base64']),b'old')

    def test_database_constraints_reject_wrong_lengths_zero_revision_and_duplicate_scope(self):
        """直接仓储写也受真实MySQL长度/版本/复合唯一键保护，不仅依赖HTTP校验。"""
        _,_,game,data=self.email_game()
        self.write(game,data,b'one')
        with self.s.db.transaction() as c:
            original=dict(c.select('native_player_settings').fetchone())
        for change in ({'id':identifier()},{'id':identifier(),'client_sha256':'b'*64,'byte_count':0},
                       {'id':identifier(),'client_sha256':'b'*64,'revision':0},
                       {'id':identifier(),'client_sha256':'b'*64,'payload':b'x'*3201,'byte_count':3201}):
            with self.s.db.transaction() as c:
                with self.assertRaises(IntegrityError):c.insert('native_player_settings',dict(original,**change))

    @override_settings(NZ_PROFILE='synthetic')
    def test_http_routes_require_game_bearer_and_commit_before_acknowledging(self):
        """真实Django路由/中间件接受游戏凭据；玩家Bearer拒绝，成功响应后的正文已可从MySQL重读。"""
        _,player,game,data=self.email_game()
        client=Client(HTTP_HOST=f'127.0.0.1:{settings.NZ_PORT}',HTTP_X_NZ_REQUEST='1')
        with patch('revival.views.Service',return_value=self.s):
            denied=client.post('/api/game/settings/get',json.dumps(data),content_type='application/json',
                               HTTP_AUTHORIZATION='Bearer '+player)
            self.assertEqual(denied.status_code,401)
            saved=client.post('/api/game/settings/set',json.dumps(dict(data,payload_base64='AAEC/w==',expected_revision=0)),
                              content_type='application/json',HTTP_AUTHORIZATION='Bearer '+game)
            self.assertEqual(saved.status_code,200)
            self.assertEqual(saved.json()['revision'],1)
            loaded=client.post('/api/game/settings/get',json.dumps(data),content_type='application/json',
                               HTTP_AUTHORIZATION='Bearer '+game)
            self.assertEqual(loaded.status_code,200)
            self.assertEqual(loaded.json()['payload_base64'],'AAEC/w==')

    def test_opaque_payload_stays_out_of_audit_and_outbox(self):
        """只有受授权的设置读取返回正文；审计/outbox仅保留摘要、长度和版本。"""
        _,_,game,data=self.email_game()
        payload=b'private-preference-marker'
        self.write(game,data,payload)
        with self.s.db.transaction() as c:
            audit=c.select('audit_events',Q(action='native.settings.save')).fetchone()
            event=c.select('outbox',Q(type='native.settings.changed')).fetchone()
        for encoded in (audit['detail'],event['payload']):
            self.assertNotIn(payload.decode(),encoded)
            self.assertNotIn(base64.b64encode(payload).decode(),encoded)
            summary=json.loads(encoded)
            self.assertEqual(summary['bytes'],len(payload))
            self.assertNotIn('payload_base64',summary)

    def test_binary_settings_survive_verified_mysql_backup_restore(self):
        """真正导出/恢复到随机临时MySQL库并逐表对账，覆盖BLOB所有字节，正式业务库不回退。"""
        from pathlib import Path
        from revival.backup import backup
        _,_,game,data=self.email_game()
        payload=bytes(range(256))*4
        self.write(game,data,payload)
        report=backup(self.s.db,Path(self.directory.name)/'native-settings.sql')
        self.assertTrue(report['verified'])
        self.assertEqual(report['tables']['native_player_settings'],1)
        self.assertEqual(base64.b64decode(native_settings.load(self.s,game,data)['payload_base64']),payload)
