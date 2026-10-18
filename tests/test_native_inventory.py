"""真实MySQL原生全目录权益、装备隔离及故障回滚；测试邮箱不发送实际邮件。"""
import json
from unittest.mock import patch
from django.db import DatabaseError, connections
from django.test.utils import CaptureQueriesContext
from django.db.models import Q
from django.test import Client, override_settings
from django.conf import settings
from revival import launcher, native_inventory
from revival.catalogs import NATIVE_INVENTORY_VERSION, with_native_inventory
from revival.security import new_token, token_hash
from revival.service import Fault
from .test_control_plane import Base
from .support import DatabasePool


class NativeInventoryTests(Base):
    """使用隔离测试库的真实事务，不把自动化库存成功当作原生武器可用。"""
    def setUp(self):
        """合成身份夹具仅为隔离测试添加兼容资产目录，生产配置仍独立。"""
        super().setUp()
        self.s.catalog = with_native_inventory(self.s.catalog)

    def player_game(self, suffix='one'):
        """创建无需发送邮件的测试身份并走真实票据兑换，返回本人游戏会话。"""
        invitation = self.invitation()
        email = f'inventory-{suffix}@example.test'
        account = self.s.register(self.meta(email=email, password=self.password, invitation=invitation['invitation_token']))
        self.command('user.approve', {'target_account_id': account['account_id']})
        player = self.s.login(email, self.password)['token']
        self.command('character.create', {'nickname': 'inventory-'+suffix}, player)
        verifier = new_token()
        data = dict(protocol=launcher.PROTOCOL, client_sha256='a'*64, rules_version=NATIVE_INVENTORY_VERSION)
        ticket = launcher.issue(self.s, player, self.meta(**data, verifier_sha256=token_hash(verifier)))
        game = launcher.redeem(self.s, dict(data, ticket=ticket['ticket'], verifier=verifier))['game_token']
        return account['account_id'], player, game, data

    def grant_all(self, account):
        """明确固定版本及审计理由，调用与管理命令相同的统一发放业务。"""
        return native_inventory.grant_full_inventory(self.s, account,
            rules_version=NATIVE_INVENTORY_VERSION, reason='automated native inventory test')

    def small_catalog(self):
        """故障和并发用含真实角色、背包、武器的子集，完整4323项另由全量用例覆盖。"""
        ids = {100013, 100014, 100016, 100026, 100037, 100063, 102545, 103696, 103691}
        for location in (0, 1, 2, 3, 64):
            ids.update(sorted(row['native']['item_id'] for row in self.s.catalog['items'].values()
                if row.get('native', {}).get('location') == location and row['native']['consume_type'] in (0, 2)
                and not row['native']['hidden'])[:2])
        # 角色已开始真实依赖隐藏默认身体资源。子集夹具也必须保留这些定义，
        # 不能用删掉依赖的测试目录要求产品继续返回不完整的成功换装。
        from revival.native_avatar_body import parts
        ids.update(part['item_id'] for role_id in list(ids) for part in parts(role_id))
        self.s.catalog['items'] = {k:v for k,v in self.s.catalog['items'].items() if k in {f'cn2020:{i}' for i in ids}}

    def counts(self):
        """读取会受发放影响的各表计数，用于验证失败后没有部分资产或孤立审计。"""
        with self.s.db.transaction() as c:
            return {t:c.count(t).fetchone()[0] for t in ('inventory', 'native_inventory_bindings',
                'native_super_accounts', 'equipment', 'asset_operations', 'asset_entries', 'audit_events', 'outbox')}

    def test_full_catalog_repeat_preserves_gids_and_user_unequip(self):
        """全4323件永久入库；重试不增加流水/GID，也不恢复玩家已经卸下的默认装备。"""
        account, _, game, data = self.player_game()
        with CaptureQueriesContext(connections['default']) as queries:
            self.assertEqual(self.grant_all(account)['items_granted'], 4323)
        # 远端逐件保存点曾使长事务阻塞服务；约束往返上限，而非依赖本机计时偶然通过。
        self.assertLess(len(queries), 4000)
        with self.s.db.transaction() as c:
            operations = c.select('asset_operations', Q(account_id=account, source='NATIVE_SUPER_ACCOUNT'))
            operation_ids = [x['id'] for x in operations]
            entries = c.select('asset_entries', Q(operation_id__in=operation_ids))
            details = c.select('asset_entry_details', Q(operation_id__in=operation_ids))
        self.assertEqual((len(operations), len(entries), len(details)), (217, 4323, 4323))
        self.assertTrue(all((x['before_value'], x['delta'], x['after_value']) == (0, 1, 1) for x in entries))
        self.assertTrue(all(json.loads(x['detail'])['native_rules_version'] == NATIVE_INVENTORY_VERSION for x in details))
        first = native_inventory.load(self.s, game, data)
        self.assertEqual(first['item_count'], 4323)
        from revival.catalogs import NATIVE_EQUIPMENT_PROJECTION
        with CaptureQueriesContext(connections['default']) as compact_queries:
            compact=native_inventory.load(self.s,game,dict(data,projection=NATIVE_EQUIPMENT_PROJECTION))
        self.assertLess(len(compact_queries),20)
        self.assertNotIn('items',compact)
        self.assertLess(len(json.dumps(compact)),2000)
        self.assertEqual(compact['equipment'],sorted(
            [dict(gid=x['gid'],equipment_slot=x['equipment_slot']) for x in first['items'] if x['equipment_slot']],
            key=lambda x:x['equipment_slot']))
        self.assertEqual(len({x['gid'] for x in first['items']}), 4323)
        self.assertEqual(sum(x['consume_type'] is None for x in first['items']), 296)
        role = next(x for x in first['items'] if x['equipment_slot'] == 'native:1:10')
        native_inventory.equip(self.s, game, dict(data, operation=1, subject=role['gid'], target=1, location=10))
        before = self.counts()
        with CaptureQueriesContext(connections['default']) as queries:
            self.assertEqual(self.grant_all(account)['status'], 'UNCHANGED')
        self.assertLess(len(queries), 50)
        self.assertEqual(self.counts(), before)
        after = native_inventory.load(self.s, game, data)
        self.assertEqual([x['gid'] for x in first['items']], [x['gid'] for x in after['items']])
        self.assertIsNone(next(x for x in after['items'] if x['gid'] == role['gid'])['equipment_slot'])

    def test_atomic_full_inventory_and_outbox_failure(self):
        """容量不足和最后审计/outbox故障都回滚整个发放，保留之前已有记录。"""
        self.small_catalog()
        account, _, _, _ = self.player_game()
        before = self.counts()
        self.s.catalog['inventory_limit'] = 2
        with self.assertRaises(Fault) as denied:
            self.grant_all(account)
        self.assertEqual(denied.exception.code, 'INVENTORY_FULL')
        self.assertEqual(self.counts(), before)
        self.s.catalog['inventory_limit'] = 10000
        original = self.s._outbox
        def fail_last(c, event_id, topic, aggregate, payload):
            """在全部库存已经写入后模拟最终事件持久化失败，验证外层事务原子性。"""
            if topic == 'native.inventory.changed':
                raise DatabaseError('injected final outbox failure')
            return original(c, event_id, topic, aggregate, payload)
        with patch.object(self.s, '_outbox', side_effect=fail_last):
            with self.assertRaises(DatabaseError):
                self.grant_all(account)
        self.assertEqual(self.counts(), before)

    def test_concurrent_grants_commit_once(self):
        """两连接并发全量补齐由部署事务锁串行，一次发放一次幂等返回。"""
        self.small_catalog()
        account, _, game, data = self.player_game()
        with DatabasePool(max_workers=2) as pool:
            values = list(pool.map(self.grant_all, [account, account]))
        self.assertEqual(sorted(v['status'] for v in values), ['GRANTED', 'UNCHANGED'])
        self.assertEqual(native_inventory.load(self.s, game, data)['item_count'], len(self.s.catalog['items']))

    def test_native_zero_target_unequip_owned_only_and_repeat(self):
        """实机Target0卸下仅操作本人实例；未装备重复无写入，穿戴或跨账号引用0仍拒绝。"""
        self.small_catalog()
        account, _, game, data = self.player_game()
        _, _, other_game, _ = self.player_game('other')
        self.grant_all(account)
        current = native_inventory.load(self.s, game, data)
        weapon = next(x for x in current['items'] if x['location'] == 0 and x['equipment_slot'])
        change = dict(data, operation=1, subject=weapon['gid'], target=0, location=0)
        with self.assertRaises(Fault):
            native_inventory.equip(self.s, other_game, change)
        with self.assertRaises(Fault):
            native_inventory.equip(self.s, game, dict(change, operation=0))
        self.assertEqual(native_inventory.equip(self.s, game, change)['status'], 'SAVED')
        before = self.counts()
        self.assertEqual(native_inventory.equip(self.s, game, change)['status'], 'UNCHANGED')
        self.assertEqual(self.counts(), before)
        after = native_inventory.load(self.s, game, data)
        self.assertIsNone(next(x for x in after['items'] if x['gid'] == weapon['gid'])['equipment_slot'])
        # boot67实际请求资源102545在目录中为槽65，但A008卸下填0；它本来未装备，须无副作用确认。
        accessory = next(x for x in after['items'] if x['item_id'] == 102545)
        self.assertEqual(native_inventory.equip(self.s, game, dict(change, subject=accessory['gid']))['status'], 'UNCHANGED')
        self.assertEqual(self.counts(), before)

    @override_settings(NZ_PROFILE='synthetic')
    def test_http_equipment_owner_version_revocation_and_atomicity(self):
        """HTTP只接受游戏Bearer；装备换位/重试持久，跨账号、版本变化、来源撤销及失败写入拒绝。"""
        self.small_catalog()
        account, player, game, data = self.player_game()
        other, _, game2, data2 = self.player_game('two')
        self.grant_all(account)
        self.grant_all(other)
        current = native_inventory.load(self.s, game, data)
        weapon = next(x for x in current['items'] if x['location'] == 0)
        bag = next(x for x in current['items'] if x['item_id'] == 100063)
        change = dict(data, operation=0, subject=weapon['gid'], target=bag['gid'], location=0)
        with self.assertRaises(Fault):
            native_inventory.equip(self.s, game2, dict(change, **data2))
        with self.assertRaises(Fault):
            native_inventory.load(self.s, game, dict(data, rules_version='future'))
        with patch.object(self.s, '_outbox', side_effect=DatabaseError('injected')):
            with self.assertRaises(DatabaseError):
                native_inventory.equip(self.s, game, change)
        self.assertEqual(native_inventory.load(self.s, game, data), current)
        client = Client(HTTP_HOST=f'127.0.0.1:{settings.NZ_PORT}', HTTP_X_NZ_REQUEST='1')
        with patch('revival.views.Service', return_value=self.s):
            response = client.post('/api/game/inventory/equip', json.dumps(change), content_type='application/json',
                                   HTTP_AUTHORIZATION='Bearer '+game)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['status'], 'SAVED')
            self.assertEqual(native_inventory.equip(self.s, game, change)['status'], 'UNCHANGED')
            denied = client.post('/api/game/inventory/get', json.dumps(data), content_type='application/json',
                                 HTTP_AUTHORIZATION='Bearer '+player)
            self.assertEqual(denied.status_code, 401)
        with self.s.db.transaction() as c:
            c.update('sessions', Q(token_hash=token_hash(player)), {'revoked': 1})
        with self.assertRaises(Fault):
            native_inventory.load(self.s, game, data)

    def test_hidden_default_knife_replaces_owned_melee_only(self):
        """实机卸下近战会穿回隐藏默认匕首；原武器释放且重试无重复审计，外部GID仍拒绝。"""
        self.small_catalog()
        account, _, game, data = self.player_game()
        _, _, other_game, _ = self.player_game('other')
        self.grant_all(account)
        snapshot = native_inventory.load(self.s, game, data)
        knife = next(x for x in snapshot['items'] if x['item_id']==100013)
        previous = next(x for x in snapshot['items'] if x['location']==2 and x['equipment_slot'])
        bag = next(x for x in snapshot['items'] if x['item_id']==100026)
        request = dict(data,operation=0,subject=knife['gid'],target=bag['gid'],location=2)
        with self.assertRaises(Fault):
            native_inventory.equip(self.s,other_game,request)
        self.assertEqual(native_inventory.equip(self.s,game,request)['status'],'SAVED')
        after = native_inventory.load(self.s,game,data)
        self.assertIsNone(next(x for x in after['items'] if x['gid']==previous['gid'])['equipment_slot'])
        self.assertEqual(next(x for x in after['items'] if x['gid']==knife['gid'])['equipment_slot'],f"native:{bag['gid']}:2")
        counts=self.counts()
        self.assertEqual(native_inventory.equip(self.s,game,request)['status'],'UNCHANGED')
        self.assertEqual(self.counts(),counts)

    def test_pve_role_switch_preserves_factions_and_rejects_other_owner(self):
        """猎场角色64在根1独立换装，旧PvE槽释放，两阵营不变；错背包/跨账号仍失败。"""
        self.small_catalog();account,_,game,data=self.player_game()
        _,_,other,_=self.player_game('other');self.grant_all(account)
        original=native_inventory.load(self.s,game,data)
        roles=[x for x in original['items'] if x['location']==64 and not x['hidden'] and x['consume_type']==0]
        self.assertGreaterEqual(len(roles),2)
        for role in roles[:2]:
            change=dict(data,operation=0,subject=role['gid'],target=1,location=64)
            self.assertEqual(native_inventory.equip(self.s,game,change)['status'],'SAVED')
            counts=self.counts()
            self.assertEqual(native_inventory.equip(self.s,game,change)['status'],'UNCHANGED')
            self.assertEqual(self.counts(),counts)
            for session,body in ((other,change),(game,dict(change,target=1001))):
                with self.assertRaises(Fault):
                    native_inventory.equip(self.s,session,body)
        after=native_inventory.load(self.s,game,data)
        self.assertEqual([x['gid'] for x in after['items'] if x['equipment_slot']=='native:1:64'],[roles[1]['gid']])
        self.assertEqual([x for x in original['items'] if x['location'] in (10,11)],
                         [x for x in after['items'] if x['location'] in (10,11)])

    def test_compact_commit_is_atomic_authorized_and_matches_full_snapshot(self):
        """轻量换装和回读同事务；回读故障回滚换装/审计，错投影与撤销凭据不能走快捷路径。"""
        from revival.catalogs import NATIVE_EQUIPMENT_PROJECTION
        self.small_catalog();account,player,game,data=self.player_game();self.grant_all(account)
        _,_,other,_=self.player_game('other')
        initial=native_inventory.load(self.s,game,data)
        role=next(x for x in initial['items'] if x['item_id']==103696)
        request=dict(data,operation=0,subject=role['gid'],target=1,location=64,projection=NATIVE_EQUIPMENT_PROJECTION)
        before=self.counts()
        with patch('revival.native_inventory.equipment_state',side_effect=DatabaseError('injected compact readback')):
            with self.assertRaises(DatabaseError):
                native_inventory.equip(self.s,game,request)
        self.assertEqual(self.counts(),before)
        self.assertEqual(native_inventory.load(self.s,game,data),initial)
        for token,body in ((other,request),(player,request),(game,dict(request,projection='future'))):
            with self.assertRaises(Fault):
                native_inventory.equip(self.s,token,body)
        compact_data=dict(data,projection=NATIVE_EQUIPMENT_PROJECTION)
        self.assertEqual(native_inventory.load(self.s,other,compact_data)['equipment'],[])
        client=Client(HTTP_HOST=f'127.0.0.1:{settings.NZ_PORT}',HTTP_X_NZ_REQUEST='1')
        with patch('revival.views.Service',return_value=self.s):
            response=client.post('/api/game/inventory/equip',json.dumps(request),content_type='application/json',
                                 HTTP_AUTHORIZATION='Bearer '+game)
        self.assertEqual(response.status_code,200)
        result=response.json();self.assertEqual(result['status'],'SAVED')
        self.assertEqual(result['equipment_state'],native_inventory.load(self.s,game,compact_data))
        full=native_inventory.load(self.s,game,data)
        self.assertEqual(result['equipment_state']['equipment'],sorted(
            [dict(gid=x['gid'],equipment_slot=x['equipment_slot']) for x in full['items'] if x['equipment_slot']],
            key=lambda x:x['equipment_slot']))
        before=self.counts()
        self.assertEqual(native_inventory.equip(self.s,game,request)['status'],'UNCHANGED')
        self.assertEqual(self.counts(),before)
        with self.s.db.transaction() as c:
            c.update('sessions',Q(token_hash=token_hash(player)),{'revoked':1})
        with self.assertRaises(Fault):
            native_inventory.load(self.s,game,compact_data)

    def test_backpack_root_preserves_all_other_bags_and_children(self):
        """boot78背包100037挂到根12；保持双默认包及其武器，重试幂等、回读故障回滚、越权拒绝。"""
        from revival.catalogs import NATIVE_EQUIPMENT_PROJECTION
        self.small_catalog();account,_,game,data=self.player_game();self.grant_all(account)
        _,_,other,_=self.player_game('other')
        initial=native_inventory.load(self.s,game,data)
        bag=next(x for x in initial['items'] if x['item_id']==100037)
        request=dict(data,operation=0,subject=bag['gid'],target=1,location=12,projection=NATIVE_EQUIPMENT_PROJECTION)
        before=self.counts()
        for token,body in ((other,request),(game,dict(request,target=bag['gid'])),(game,dict(request,location=0))):
            with self.assertRaises(Fault):
                native_inventory.equip(self.s,token,body)
        with patch('revival.native_inventory.equipment_state',side_effect=DatabaseError('injected backpack readback')):
            with self.assertRaises(DatabaseError):
                native_inventory.equip(self.s,game,request)
        self.assertEqual(self.counts(),before)
        self.assertEqual(native_inventory.load(self.s,game,data),initial)
        self.assertEqual(native_inventory.equip(self.s,game,request)['status'],'SAVED')
        after=native_inventory.load(self.s,game,data)
        self.assertEqual(next(x for x in after['items'] if x['gid']==bag['gid'])['equipment_slot'],'native:bag:100037')
        # 新要求启用背包必须具备默认刀；既有非刀实例及所有已装备武器仍逐字保留。
        unchanged=lambda x:x['gid']!=bag['gid'] and x['item_id']!=100013
        self.assertEqual([x for x in initial['items'] if unchanged(x)],[x for x in after['items'] if unchanged(x)])
        self.assertTrue({x['gid'] for x in initial['items']}<={x['gid'] for x in after['items']})
        for parent in [x for x in after['items'] if (x['equipment_slot'] or '').startswith('native:bag:')]:
            self.assertTrue(any(x['equipment_slot']==f"native:{parent['gid']}:2" for x in after['items']))
        counts=self.counts()
        self.assertEqual(native_inventory.equip(self.s,game,request)['status'],'UNCHANGED')
        self.assertEqual(self.counts(),counts)
        for item_id in (100026,100063):
            existing=next(x for x in after['items'] if x['item_id']==item_id)
            self.assertEqual(native_inventory.equip(self.s,game,dict(request,subject=existing['gid']))['status'],'UNCHANGED')

    def test_gm_wallet_maximize_atomic_repeat_and_isolation(self):
        """七币种补满共用账本，重复不写新版本或流水；非GM拒绝且其他账号保持零。"""
        from revival.native_wallet import maximize,WALLET_VERSION,WALLET_FIELDS
        self.small_catalog();account,_,game,data=self.player_game()
        other,_,other_game,_=self.player_game('other')
        args=dict(rules_version=WALLET_VERSION,reason='authorized GM test limits')
        with self.assertRaises(Fault):
            maximize(self.s,other,**args)
        self.grant_all(account)
        result=maximize(self.s,account,**args)
        self.assertEqual((result['status'],result['currencies_changed'],result['operation_count']),('GRANTED',7,1))
        self.assertEqual(result['balances'],{k:v['max'] for k,v in WALLET_FIELDS.items()})
        before=self.counts()
        self.assertEqual(maximize(self.s,account,**args)['status'],'UNCHANGED')
        self.assertEqual(self.counts(),before)
        self.assertEqual(native_inventory.load(self.s,game,data)['wallet']['balances'],result['balances'])
        self.assertEqual(set(native_inventory.load(self.s,other_game,data)['wallet']['balances'].values()),{0})
        with self.s.db.transaction() as c:
            op=c.select('asset_operations',Q(account_id=account,source='NATIVE_GM_WALLET_MAX')).fetchone()
            entries=c.select('asset_entries',Q(operation_id=op['id']))
        self.assertEqual(len(entries),7)
        self.assertTrue(all(x['before_value']==0 and x['after_value']==result['balances'][x['resource']] for x in entries))

    def test_gm_wallet_failure_rolls_back_and_concurrent_topup_commits_once(self):
        """最终outbox故障回滚钱包和流水；双连接重试仅一次补满，旧规则不允许写入。"""
        from revival.native_wallet import maximize,WALLET_VERSION
        self.small_catalog();account,_,game,data=self.player_game();self.grant_all(account)
        before=self.counts();original=self.s._outbox
        def fail_last(c,event_id,topic,aggregate,payload):
            """仅在补满最终事件处注入故障，验证已有钱包写入随整体事务回滚。"""
            if topic=='native.wallet.changed':
                raise DatabaseError('injected wallet failure')
            return original(c,event_id,topic,aggregate,payload)
        def topup(_):
            """供两个独立MySQL连接并发提交同一管理操作。"""
            return maximize(self.s,account,rules_version=WALLET_VERSION,reason='concurrency test')
        with patch.object(self.s,'_outbox',side_effect=fail_last):
            with self.assertRaises(DatabaseError):
                topup(None)
        self.assertEqual(self.counts(),before)
        self.assertEqual(set(native_inventory.load(self.s,game,data)['wallet']['balances'].values()),{0})
        with self.assertRaises(Fault):
            maximize(self.s,account,rules_version='future',reason='must reject stale rule')
        with DatabasePool(max_workers=2) as pool:
            results=list(pool.map(topup,[0,1]))
        self.assertEqual(sorted(x['status'] for x in results),['GRANTED','UNCHANGED'])
