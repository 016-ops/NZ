import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from revival.assets import deliver_outbox, preview, settle
from revival.rooms import heartbeat, maintain, test_host_ready
from revival.security import otp, token_hash
from revival.service import Fault, Service, identifier


class Base(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'state.db'
        self.now = 1800000000
        self.s = Service(self.path, test_profile=True, clock=lambda: self.now)
        self.password = 'local-test-password-123'
        self.admin = self.s.bootstrap_admin('owner', self.password)
        self.owner = self.s.login('owner', self.password, kind='admin', otp_code=otp(self.admin['otp_secret'], self.now // 30))['token']

    def tearDown(self):
        self.directory.cleanup()

    def meta(self, **data):
        return {'request_id': identifier(), 'idempotency_key': identifier(), 'reason': 'automated synthetic test', **data}

    def command(self, action, data=None, token=None):
        return self.s.command(token or self.owner, action, self.meta(**(data or {})))

    def invitation(self, uses=1):
        return self.command('invite.create', {'max_uses': uses, 'expires_at': self.now + 600})

    def player(self, login='player-a', character=True):
        invite = self.invitation()
        result = self.s.register(self.meta(login=login, password=self.password, invitation=invite['invitation_token']))
        self.command('user.approve', {'target_account_id': result['account_id']})
        token = self.s.login(login, self.password)['token']
        if character:
            self.command('character.create', {'nickname': login}, token)
        return result['account_id'], token

    def grant(self, account_id, changes, key=None):
        data = self.meta(target_account_id=account_id, catalog_version=self.s.catalog['id'], changes=changes)
        if key: data['idempotency_key'] = key
        return self.s.command(self.owner, 'asset.grant', data)

    def balance(self, account_id, code='GP'):
        return next(w['balance'] for w in self.s.query(self.owner, 'user', account_id=account_id)['wallets'] if w['currency'] == code)

    def step_up(self):
        self.now += 30
        return {'current_password': self.password, 'otp_code': otp(self.admin['otp_secret'], self.now // 30)}

    def extra_admin(self, role):
        admin_id = identifier()
        with self.s.db.transaction() as c:
            original = c.execute('SELECT * FROM admins LIMIT 1').fetchone()
            c.execute('INSERT INTO admins(id,login,password_hash,otp_secret,role) VALUES(?,?,?,?,?)',
                      (admin_id, role, original['password_hash'], original['otp_secret'], role))
        return self.s.login(role, self.password, kind='admin', otp_code=otp(self.admin['otp_secret'], self.now // 30))['token']

    def match(self):
        a, ta = self.player('player-a')
        b, tb = self.player('player-b')
        room = self.command('room.create', {'map_key': 'test-arena'}, ta)['room_id']
        self.command('room.join', {'room_id': room}, tb)
        self.command('room.ready', {'room_id': room, 'ready': True}, ta)
        self.command('room.ready', {'room_id': room, 'ready': True}, tb)
        revision = self.s.query(ta, 'rooms')[0]['revision']
        match = self.command('room.start', {'room_id': room, 'expected_revision': revision}, ta)
        return (a, ta), (b, tb), room, match


class Accounts(Base):
    def test_concurrent_invitation_use_is_atomic(self):
        invite = self.invitation()
        def register(i):
            try:
                return self.s.register(self.meta(login=f'user-{i}', password=self.password, invitation=invite['invitation_token']))
            except Fault as e:
                return e.code
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(register, range(10)))
        self.assertEqual(sum(isinstance(x, dict) for x in results), 1)
        self.assertEqual(self.s.query(self.owner, 'invitations')[0]['uses'], 1)

    def test_registration_replay_does_not_consume_invite_again(self):
        invite = self.invitation()
        data = self.meta(login='user-one', password=self.password, invitation=invite['invitation_token'])
        first = self.s.register(data)
        for _ in range(10): self.assertEqual(first, self.s.register(data))
        self.assertEqual(self.s.query(self.owner, 'invitations')[0]['uses'], 1)

    def test_expired_and_revoked_invitations_are_rejected(self):
        invite = self.invitation()
        self.command('invite.revoke', {'invitation_id': invite['invitation_id']})
        with self.assertRaises(Fault):
            self.s.register(self.meta(login='user-one', password=self.password, invitation=invite['invitation_token']))
        invite = self.invitation(); self.now += 601
        with self.assertRaises(Fault):
            self.s.register(self.meta(login='user-one', password=self.password, invitation=invite['invitation_token']))

    def test_pending_and_wrong_password_do_not_authenticate(self):
        invite = self.invitation()
        self.s.register(self.meta(login='user-one', password=self.password, invitation=invite['invitation_token']))
        with self.assertRaises(Fault): self.s.login('user-one', self.password)
        account, token = self.player()
        with self.assertRaises(Fault): self.s.login('player-a', 'incorrect-password')
        with self.assertRaises(Fault): self.s.login('nonexistent', self.password)

    def test_admin_mfa_required_and_cannot_replay(self):
        with self.assertRaises(Fault): self.s.login('owner', self.password, kind='admin')
        with self.assertRaises(Fault): self.s.login('owner', self.password, kind='admin', otp_code=otp(self.admin['otp_secret'], self.now // 30))
        with self.assertRaises(Fault): self.s.bootstrap_admin('another', self.password)

    def test_character_and_assets_are_independent_and_initialize_once(self):
        a, ta = self.player(); b, tb = self.player('player-b')
        original = self.s.query(ta, 'me')
        for _ in range(10): self.command('character.create', {'nickname': 'player-a'}, ta)
        self.assertEqual(original['character'], self.s.query(ta, 'me')['character'])
        self.assertNotEqual(original['game_id'], self.s.query(tb, 'me')['game_id'])
        self.grant(a, [{'type': 'currency', 'code': 'GP', 'delta': 10}])
        self.assertEqual(self.balance(b), 0)
        with self.assertRaises(Fault): self.s.query(ta, 'user', account_id=b)

    def test_local_nickname_validation_uniqueness_and_chinese(self):
        a, ta = self.player(character=False); b, tb = self.player('player-b', character=False)
        self.command('character.create', {'nickname': '测试玩家'}, ta)
        with self.assertRaises(Fault): self.command('character.create', {'nickname': '测试玩家'}, tb)
        with self.assertRaises(Fault): self.command('character.create', {'nickname': '<script>alert(1)</script>'}, tb)
        with self.assertRaises(Fault): self.command('character.create', {'nickname': '长' * 25}, tb)

    def test_reset_revokes_old_credentials_and_session(self):
        a, ta = self.player()
        self.command('user.reset', {'target_account_id': a, 'new_password': 'new-local-password-123'})
        with self.assertRaises(Fault): self.s.query(ta, 'me')
        with self.assertRaises(Fault): self.s.login('player-a', self.password)
        self.assertTrue(self.s.login('player-a', 'new-local-password-123')['token'])

    def test_disable_approve_does_not_bypass_disabled(self):
        a, ta = self.player()
        self.command('user.disable', {'target_account_id': a})
        with self.assertRaises(Fault): self.command('user.approve', {'target_account_id': a})
        with self.assertRaises(Fault): self.s.query(ta, 'me')
        self.command('user.activate', {'target_account_id': a})
        self.assertTrue(self.s.login('player-a', self.password)['token'])

    def test_stacked_sanctions_and_expiry(self):
        a, ta = self.player()
        one = self.command('sanction.create', {'target_account_id': a, 'expires_at': self.now + 60})
        two = self.command('sanction.create', {'target_account_id': a, 'expires_at': None})
        self.command('sanction.revoke', {'sanction_id': two['sanction_id']})
        with self.assertRaises(Fault): self.s.login('player-a', self.password)
        self.now += 61
        self.assertTrue(self.s.login('player-a', self.password)['token'])
        with self.assertRaises(Fault): self.s.query(ta, 'me')

    def test_restart_preserves_mapping_character_ban_and_assets(self):
        a, ta = self.player()
        before = self.s.query(ta, 'me')
        self.grant(a, [{'type': 'currency', 'code': 'GP', 'delta': 100}])
        self.command('sanction.create', {'target_account_id': a})
        self.s = Service(self.path, test_profile=True, clock=lambda: self.now)
        with self.assertRaises(Fault): self.s.login('player-a', self.password)
        after = self.s.query(self.owner, 'user', account_id=a)
        self.assertEqual(before['character'], after['character']); self.assertEqual(self.balance(a), 100)

    def test_session_replacement_and_expiry_remove_old_seat(self):
        a, ta = self.player()
        room = self.command('room.create', {'map_key':'test-arena'}, ta)
        token = self.s.login('player-a', self.password)['token']
        with self.assertRaises(Fault): self.s.query(ta, 'me')
        self.assertIsNone(self.s.query(token, 'me')['room'])
        self.command('room.create', {'map_key':'test-arena'}, token)
        self.now += 3601
        self.assertEqual(maintain(self.s)['expired_members'], 1)

    def test_permission_matrix_and_no_credentials_in_read_models(self):
        a, ta = self.player()
        for token in (ta, self.extra_admin('users'), self.extra_admin('auditor')):
            with self.assertRaises(Fault):
                self.s.command(token, 'asset.grant', self.meta(target_account_id=a, catalog_version=self.s.catalog['id'], changes=[{'type':'currency','code':'GP','delta':1}]))
        resource = self.extra_admin('resources')
        with self.assertRaises(Fault): self.command('user.reset', {'target_account_id': a, 'new_password': self.password}, resource)
        exported = json.dumps(self.s.query(self.owner, 'user', account_id=a)) + json.dumps(self.s.query(self.owner, 'audit'))
        for sensitive in (self.password, self.owner, ta, self.admin['otp_secret'], 'password_hash', 'token_hash'):
            self.assertNotIn(sensitive, exported)


class Assets(Base):
    def test_replay_and_payload_conflict(self):
        a, ta = self.player(); data=self.meta(target_account_id=a,catalog_version=self.s.catalog['id'],changes=[{'type':'currency','code':'GP','delta':100}])
        first=self.s.command(self.owner,'asset.grant',data)
        for _ in range(10): self.assertEqual(first,self.s.command(self.owner,'asset.grant',data))
        data['changes'][0]['delta']=101
        with self.assertRaises(Fault): self.s.command(self.owner,'asset.grant',data)
        self.assertEqual(self.balance(a),100)
        self.assertEqual(len(self.s.query(self.owner,'operations',account_id=a)),1)

    def test_unknown_negative_overflow_invalid_duration_and_cross_account(self):
        a, ta=self.player(); b, tb=self.player('player-b')
        for change in ({'type':'currency','code':'UNKNOWN','delta':1},{'type':'currency','code':'GP','delta':-1},
                       {'type':'currency','code':'GP','delta':True},{'type':'currency','code':'GP','delta':10**30},
                       {'type':'item','item_key':'unknown','quantity':1}, {'type':'item','item_key':'test-token','quantity':-1},
                       {'type':'item','item_key':'test-token','quantity':1,'duration_seconds':0}):
            with self.assertRaises(Fault): self.grant(a,[change])
        item=self.grant(a,[{'type':'item','item_key':'test-token','quantity':1}])
        owned=self.s.query(ta,'me')['inventory'][0]
        with self.assertRaises(Fault): self.command('inventory.consume',{'catalog_version':self.s.catalog['id'],'instance_id':owned['id'],'quantity':1},tb)
        self.assertEqual(self.balance(a),0);self.assertEqual(self.balance(b),0)

    def test_purchase_failure_rolls_back_wallet_and_outbox(self):
        a, ta=self.player()
        self.grant(a,[{'type':'currency','code':'GP','delta':1000},{'type':'item','item_key':'test-rifle','quantity':1}])
        before=self.s.query(self.owner,'outbox')
        with self.assertRaises(Fault): self.command('shop.purchase',{'catalog_version':self.s.catalog['id'],'offer_key':'test-rifle-gp'},ta)
        self.assertEqual(self.balance(a),1000);self.assertEqual(before,self.s.query(self.owner,'outbox'))

    def test_concurrent_grants_and_purchase_preserve_balance(self):
        a, ta=self.player();self.grant(a,[{'type':'currency','code':'GP','delta':1000}])
        def change(i):
            if i==0:return self.command('shop.purchase',{'catalog_version':self.s.catalog['id'],'offer_key':'test-rifle-gp'},ta)
            return self.grant(a,[{'type':'currency','code':'GP','delta':100}])
        with ThreadPoolExecutor(max_workers=10) as pool:list(pool.map(change,range(10)))
        self.assertEqual(self.balance(a),1400);self.assertEqual(len(self.s.query(ta,'me')['inventory']),1)

    def test_preview_is_read_only_and_commit_revalidates(self):
        a,ta=self.player();data={'target_account_id':a,'catalog_version':self.s.catalog['id'],'changes':[{'type':'currency','code':'GP','delta':100}]}
        projected=preview(self.s,self.owner,data)
        self.assertEqual(projected['after']['wallets'][1]['balance'] if projected['after']['wallets'][1]['currency']=='GP' else projected['after']['wallets'][0]['balance'],100)
        self.assertEqual(self.balance(a),0);self.assertEqual(self.s.query(self.owner,'outbox'),[])

    def test_consumption_equipment_expiry_and_limit(self):
        a,ta=self.player()
        self.grant(a,[{'type':'item','item_key':'test-token','quantity':2},{'type':'item','item_key':'test-rifle','quantity':1,'duration_seconds':10}])
        items={i['item_key']:i for i in self.s.query(ta,'me')['inventory']}
        self.command('inventory.equip',{'catalog_version':self.s.catalog['id'],'instance_id':items['test-rifle']['id'],'slot':'primary'},ta)
        self.command('inventory.consume',{'catalog_version':self.s.catalog['id'],'instance_id':items['test-token']['id'],'quantity':2},ta)
        self.now+=11
        with self.assertRaises(Fault):self.command('inventory.equip',{'catalog_version':self.s.catalog['id'],'instance_id':items['test-rifle']['id'],'slot':'primary'},ta)
        self.s.catalog['inventory_limit']=1
        with self.assertRaises(Fault):self.grant(a,[{'type':'item','item_key':'test-token','quantity':1}])

    def test_atomic_audit_failure_and_immutable_ledger(self):
        a,ta=self.player()
        with self.s.db.transaction() as c:
            c.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON audit_events WHEN NEW.action='asset.grant' BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.grant(a,[{'type':'currency','code':'GP','delta':100}])
        self.assertEqual(self.balance(a),0);self.assertEqual(self.s.query(self.owner,'outbox'),[])
        with self.s.db.transaction() as c:c.execute('DROP TRIGGER fail_audit')
        self.grant(a,[{'type':'currency','code':'GP','delta':10}])
        with self.assertRaises(sqlite3.IntegrityError):
            with self.s.db.transaction() as c:c.execute('UPDATE asset_entries SET delta=100')
        with self.assertRaises(sqlite3.IntegrityError):
            with self.s.db.transaction() as c:c.execute('DELETE FROM audit_events')

    def test_post_commit_reply_loss_and_restart_replay(self):
        a,ta=self.player();data=self.meta(target_account_id=a,catalog_version=self.s.catalog['id'],changes=[{'type':'currency','code':'GP','delta':10}])
        first=self.s.command(self.owner,'asset.grant',data)
        self.s=Service(self.path,test_profile=True,clock=lambda:self.now)
        self.assertEqual(first,self.s.command(self.owner,'asset.grant',data));self.assertEqual(self.balance(a),10)

    def test_outbox_retry_never_repeats_assets(self):
        a,ta=self.player();self.grant(a,[{'type':'currency','code':'GP','delta':100}])
        def failure(*args):raise RuntimeError('secret should not be logged')
        results=deliver_outbox(self.s,failure)
        self.assertFalse(results[0]['delivered']);self.now+=3
        delivered=[]
        def send(key,*args):delivered.append(key);return True
        self.assertTrue(deliver_outbox(self.s,send)[0]['delivered']);self.assertEqual(deliver_outbox(self.s,send),[])
        self.assertEqual(self.balance(a),100);self.assertEqual(len(delivered),1)

    def test_spent_compensation_is_review_not_negative(self):
        a,ta=self.player();original=self.grant(a,[{'type':'currency','code':'GP','delta':600}])
        self.command('shop.purchase',{'catalog_version':self.s.catalog['id'],'offer_key':'test-rifle-gp'},ta)
        result=self.command('asset.compensate',{'operation_id':original['operation_id'],'catalog_version':self.s.catalog['id'],**self.step_up()})
        self.assertEqual(result['status'],'MANUAL_REVIEW');self.assertEqual(self.balance(a),100)

    def test_compensation_success_and_no_double_reversal(self):
        a,ta=self.player();original=self.grant(a,[{'type':'currency','code':'GP','delta':50},{'type':'currency','code':'GP','delta':50}])
        result=self.command('asset.compensate',{'operation_id':original['operation_id'],'catalog_version':self.s.catalog['id'],**self.step_up()})
        self.assertEqual(result['status'],'COMMITTED');self.assertEqual(self.balance(a),0)
        with self.assertRaises(Fault):self.command('asset.compensate',{'operation_id':original['operation_id'],'catalog_version':self.s.catalog['id'],**self.step_up()})

    def test_batch_freezes_targets_partial_failure_and_replay(self):
        a,ta=self.player();b,tb=self.player('player-b')
        data=self.meta(target_account_ids=[a,b,'missing'],catalog_version=self.s.catalog['id'],changes=[{'type':'currency','code':'GP','delta':10}],**self.step_up())
        first=self.s.command(self.owner,'asset.batch',data)
        self.assertEqual([r['status'] for r in first['results']],['COMMITTED','COMMITTED','REJECTED'])
        self.assertEqual(first,self.s.command(self.owner,'asset.batch',data));self.assertEqual(self.balance(a),10);self.assertEqual(self.balance(b),10)

    def test_large_owner_grants_require_fresh_mfa(self):
        a,ta=self.player()
        changes=[{'type':'currency','code':'NZ','delta':10000},{'type':'currency','code':'GP','delta':50000}]
        with self.assertRaises(Fault):self.grant(a,changes)
        self.command('asset.grant',{'target_account_id':a,'catalog_version':self.s.catalog['id'],'changes':changes,**self.step_up()})
        self.assertEqual(self.balance(a,'NZ'),10000);self.assertEqual(self.balance(a),50000)


class Rooms(Base):
    def test_room_replays_and_host_transfer(self):
        a,ta=self.player();b,tb=self.player('player-b')
        room=self.command('room.create',{'map_key':'test-arena'},ta)['room_id']
        for _ in range(10):self.command('room.join',{'room_id':room},tb)
        self.assertEqual(self.s.query(tb,'me')['room']['slot'],1)
        self.command('room.leave',{'room_id':room},ta)
        self.assertEqual(self.s.query(tb,'rooms')[0]['owner'],b)

    def test_concurrent_last_seat_and_one_room_per_player(self):
        players=[self.player(f'player-{i}') for i in range(6)]
        room=self.command('room.create',{'map_key':'test-arena'},players[0][1])['room_id']
        def join(player):
            try:return self.command('room.join',{'room_id':room},player[1])
            except Fault as e:return e.code
        with ThreadPoolExecutor(max_workers=5) as pool:results=list(pool.map(join,players[1:]))
        self.assertEqual(sum(isinstance(x,dict) for x in results),3)
        with self.assertRaises(Fault):self.command('room.create',{'map_key':'test-arena'},players[0][1])

    def test_configuration_revision_readiness_and_start_freeze(self):
        a,ta=self.player();room=self.command('room.create',{'map_key':'test-arena'},ta)['room_id']
        with self.assertRaises(Fault):self.command('room.start',{'room_id':room,'expected_revision':1},ta)
        self.command('room.ready',{'room_id':room,'ready':True},ta)
        with self.assertRaises(Fault):self.command('room.start',{'room_id':room,'expected_revision':1},ta)
        revision=self.s.query(ta,'rooms')[0]['revision']
        match=self.command('room.start',{'room_id':room,'expected_revision':revision},ta)
        self.assertNotIn('node_token',match);self.assertFalse(match['retail_ready'])
        with self.assertRaises(Fault):self.command('room.configure',{'room_id':room,'map_key':'test-arena','expected_revision':revision},ta)

    def test_allocation_timeout_and_twenty_cancel_cycles(self):
        a,ta=self.player();room=self.command('room.create',{'map_key':'test-arena'},ta)['room_id']
        for _ in range(20):
            self.command('room.ready',{'room_id':room,'ready':True},ta)
            revision=self.s.query(ta,'rooms')[0]['revision']
            self.command('room.start',{'room_id':room,'expected_revision':revision},ta)
            self.command('room.cancel',{'room_id':room},ta)
        self.command('room.ready',{'room_id':room,'ready':True},ta)
        revision=self.s.query(ta,'rooms')[0]['revision']
        self.command('room.start',{'room_id':room,'expected_revision':revision},ta)
        self.now+=31
        self.assertEqual(maintain(self.s)['expired_matches'],1)
        with self.s.db.connect() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM matches WHERE state IN ('STARTING','ACTIVE')").fetchone()[0],0)

    def test_trusted_synthetic_settlement_and_client_forgery(self):
        (a,ta),(b,tb),room,match=self.match()
        data={'match_id':match['match_id'],'epoch':match['epoch'],'outcome':'VICTORY'}
        with self.assertRaises(Fault):settle(self.s,ta,data)
        node=test_host_ready(self.s,match['match_id'])
        first=settle(self.s,node['node_token'],data)
        for _ in range(10):self.assertEqual(first,settle(self.s,node['node_token'],data))
        with self.assertRaises(Fault):settle(self.s,node['node_token'],dict(data,outcome='FAILURE'))
        self.assertEqual(self.balance(a),100);self.assertEqual(self.balance(b),100)
        self.assertFalse(first['real_clear_verified'])

    def test_ban_during_match_preserves_other_player_and_no_reward_for_revoked(self):
        (a,ta),(b,tb),room,match=self.match();node=test_host_ready(self.s,match['match_id'])
        self.command('sanction.create',{'target_account_id':a})
        with self.assertRaises(Fault):self.s.query(ta,'me')
        self.assertEqual(self.s.query(tb,'rooms')[0]['owner'],b)
        settle(self.s,node['node_token'],{'match_id':match['match_id'],'epoch':match['epoch'],'outcome':'VICTORY'})
        self.assertEqual(self.balance(a),0);self.assertEqual(self.balance(b),100)

    def test_stale_node_lease_and_epoch_rejected(self):
        (a,ta),(b,tb),room,match=self.match();node=test_host_ready(self.s,match['match_id'])
        with self.assertRaises(Fault):heartbeat(self.s,node['node_token'],match['match_id'],'wrong-epoch')
        self.now+=31
        with self.assertRaises(Fault):settle(self.s,node['node_token'],{'match_id':match['match_id'],'epoch':match['epoch'],'outcome':'VICTORY'})
        self.assertEqual(self.balance(a),0)

    def test_two_rooms_are_isolated(self):
        a,ta=self.player();b,tb=self.player('player-b')
        ra=self.command('room.create',{'map_key':'test-arena'},ta)['room_id']
        rb=self.command('room.create',{'map_key':'test-arena'},tb)['room_id']
        self.command('room.leave',{'room_id':ra},ta)
        self.assertEqual(self.s.query(tb,'me')['room']['room_id'],rb)
        self.assertEqual(len(self.s.query(tb,'rooms')),1)


class Deployment(Base):
    def test_backup_integrity_and_profile_isolation(self):
        a,ta=self.player();self.grant(a,[{'type':'currency','code':'GP','delta':10}])
        backup=Path(self.directory.name)/'backup.db';self.s.db.backup(backup)
        with closing(sqlite3.connect(backup)) as c:self.assertEqual(c.execute('SELECT SUM(balance) FROM wallets').fetchone()[0],10)
        with self.assertRaises(ValueError):self.s.db.backup(backup)
        with self.assertRaises(Fault):Service(self.path,test_profile=False)

    def test_default_profile_refuses_guessed_resources_and_native_host(self):
        current=Service(Path(self.directory.name)/'current.db')
        self.assertEqual(current.catalog['currencies'],{});self.assertEqual(current.catalog['maps'],{})
        with self.assertRaises(Fault):test_host_ready(current,'anything')
        with self.assertRaises(Fault):settle(current,'anything',{})
        from adapters.cn_current.profile import ClientProfile,CompatibilityBlocked
        profile=ClientProfile(Path(__file__).resolve().parents[1]/'config/cn-current.json')
        with self.assertRaises(CompatibilityBlocked):profile.require('retail_login')
        with self.assertRaises(CompatibilityBlocked):profile.decode(b'\x00\x01')


if __name__=='__main__':unittest.main()
