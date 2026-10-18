"""真实MySQL验证验收配装、百宝箱成长/领取与功能卡隔离；不替代原生战斗效果验收。"""
import json
from pathlib import Path
import struct
from unittest.mock import patch
from django.db import DatabaseError
from django.db.models import Q

from .test_control_plane import Base
from . import test_native_features
from .support import DatabasePool
from revival.catalogs import with_native_inventory,NATIVE_INVENTORY_VERSION
from revival.native_acceptance_loadout import prepare,VERSION,WEAPONS,CARDS
from revival.native_actions import apply,item_rewards
from revival.native_features import command
from revival.native_features_catalog import FEATURE_VERSION
from revival.native_inventory import equip,grant_full_inventory
from revival.native_pve_benefits import vip_info,level
from revival.service import Fault
from adapters.cn_current.features import packet,parse,metadata


class NativePveBenefitTests(Base):
    """使用正式票据和资产服务创建隔离账号；当前库、GM或正式签到不作为测试夹具。"""
    player_game=test_native_features.NativeFeatureTests.player_game
    view=test_native_features.NativeFeatureTests.view
    call=test_native_features.NativeFeatureTests.call
    bridge=test_native_features.NativeFeatureTests.bridge

    def setUp(self):
        """每例独立MySQL账号、冻结目录及游戏凭据，不依赖开发服务器已有测试身份。"""
        super().setUp();self.s.catalog=with_native_inventory(self.s.catalog)
        self.account,self.player_token,self.game,self.base=self.player_game()
        self.base['feature_version']=FEATURE_VERSION
        with self.s.db.transaction() as c:
            self.game_id=c.select('accounts',Q(id=self.account)).fetchone()['game_id']

    def prepare(self,key='pve-acceptance-test',ids=None):
        """正式受控管理入口配置当前账号；传入相同请求ID可核对未知提交重试。"""
        return prepare(self.s,ids or [self.game_id],request_id=key,reason='隔离MySQL验收配装回归')

    def counts(self):
        """比较资产、审计、同步及回执表，检验外层事务没有留下半套装备。"""
        with self.s.db.transaction() as c:
            return {name:c.count(name).fetchone()[0] for name in ('inventory','equipment','native_inventory_bindings',
                'native_item_states','asset_operations','asset_entries','native_receipts','native_sync_changes','audit_events','native_player_events')}

    def test_prepare_two_accounts_exact_equipment_growth_and_unchanged_wallet(self):
        """两个账号得到真实首包四件套、三张根功能卡和10500点，原武器与钱包保留。"""
        other,_,_,_=self.player_game('other')
        with self.s.db.transaction() as c:
            other_id=c.select('accounts',Q(id=other)).fetchone()['game_id']
            apply(self.s,c,self.account,item_rewards(self.s,[dict(item_id=100292)]),'TEST_FIXTURE')
            apply(self.s,c,self.account,[dict(type='currency',code='cn2020:GP',delta=12345)],'TEST_FIXTURE')
        self.prepare(ids=[other_id,self.game_id])
        rows=self.view('inventory')['items'];items={row['item_id']:row for row in rows}
        self.assertEqual(len(rows),10)
        self.assertTrue(all(row['expires_at'] is None and row['quantity']==1 for row in rows))
        bag=items[100026]['gid']
        for item_id,slot in WEAPONS+CARDS:
            self.assertEqual(items[item_id]['equipment_slot'],f"native:{bag if slot<4 else 1}:{slot}")
        benefit=self.view('pve.benefits')
        self.assertEqual((benefit['level'],benefit['active'],benefit['vip_info']['VipPVEPoint']),(3,True,10500))
        self.assertEqual(len(benefit['functional_items']),3)
        with self.s.db.transaction() as c:
            self.assertEqual(c.select('wallets',Q(account_id=self.account,currency='cn2020:GP')).fetchone()['balance'],12345)
            self.assertEqual(c.count('inventory',Q(account_id=other)).fetchone()[0],9)

    def test_replay_is_unchanged_and_new_request_reuses_assets(self):
        """未知提交重试不增发和重复审计；新请求重装复用同一GID，也保留已经领取标记。"""
        first=self.prepare();before=self.counts();rows=self.view('inventory')['items']
        self.assertEqual(self.prepare(),first);self.assertEqual(self.counts(),before)
        self.call('pve.store.claim',benefit_id=10014,prop_ids=[100482])
        second=self.prepare('new-preset-request')
        current=self.view('inventory')['items']
        self.assertEqual({r['item_id']:r['gid'] for r in rows},{r['item_id']:r['gid'] for r in current if r['item_id']!=100482})
        self.assertEqual(second['accounts'][0]['operations'],[])
        with self.assertRaises(Fault) as error:self.call('pve.store.claim',benefit_id=10014,prop_ids=[100482])
        self.assertEqual(error.exception.code,'PVE_STORE_TAKEN')

    def test_gm_existing_4323_entitlements_and_gids_survive_preset(self):
        """完整GM目录只改装备和百宝箱状态，永久物品和稳定GID总数仍4323。"""
        grant_full_inventory(self.s,self.account,rules_version=NATIVE_INVENTORY_VERSION,reason='隔离GM验收夹具')
        with self.s.db.transaction() as c:
            before=set(c.table('native_inventory_bindings').filter(inventory__account_id=self.account).values_list('inventory_id','id'))
        self.prepare()
        with self.s.db.transaction() as c:
            after=set(c.table('native_inventory_bindings').filter(inventory__account_id=self.account).values_list('inventory_id','id'))
            self.assertEqual(c.count('inventory',Q(account_id=self.account)).fetchone()[0],4323)
        self.assertEqual(len(after),4323);self.assertEqual(before,after)
        self.assertEqual(self.view('pve.benefits')['level'],3)

    def test_second_account_tail_failure_rolls_back_entire_batch(self):
        """第二个账号最终事件失败，首账号也不能留下已提交的武器、状态或流水。"""
        other,_,_,_=self.player_game('other')
        with self.s.db.transaction() as c:other_id=c.select('accounts',Q(id=other)).fetchone()['game_id']
        before=self.counts()
        with patch('revival.native_acceptance_loadout.event',side_effect=[None,DatabaseError('injected tail')]):
            with self.assertRaises(DatabaseError):self.prepare(ids=[self.game_id,other_id])
        self.assertEqual(self.counts(),before)

    def test_gm_additional_bags_keep_all_entitlements_and_get_independent_knives(self):
        """已启用六个背包的GM保留4323项权益；额外空刀槽通过新独立默认刀补齐，不复用同一GID。"""
        from revival.native_starter import BAGS
        grant_full_inventory(self.s,self.account,rules_version=NATIVE_INVENTORY_VERSION,reason='隔离多背包GM夹具')
        with self.s.db.transaction() as c:
            before=set(c.table('inventory').filter(account_id=self.account).values_list('id','item_key'))
            for item_id in BAGS:
                bag=c.select('inventory',Q(account_id=self.account,item_key=f'cn2020:{item_id}')).fetchone()
                c.equip(self.account,f'native:bag:{item_id}',bag['id'])
        self.prepare()
        with self.s.db.transaction() as c:
            after=set(c.table('inventory').filter(account_id=self.account).values_list('id','item_key'))
            self.assertTrue(before.issubset(after));self.assertEqual({key for _,key in before},{key for _,key in after})
            self.assertEqual(len(after),4327)
            self.assertEqual({key for _,key in after-before},{'cn2020:100013'})
            knives=[]
            for item_id in BAGS[1:]:
                bag=c.select('inventory',Q(account_id=self.account,item_key=f'cn2020:{item_id}')).fetchone()
                gid=c.select('native_inventory_bindings',Q(inventory_id=bag['id'])).fetchone()['id']+1000
                equipped=c.select('equipment',Q(account_id=self.account,slot=f'native:{gid}:2')).fetchone()
                knife=c.select('inventory',Q(id=equipped['inventory_id'])).fetchone()
                self.assertEqual(knife['item_key'],'cn2020:100013');knives.append(knife['id'])
            self.assertEqual(len(set(knives)),5)

    def test_unknown_or_disabled_target_and_duplicate_ids_do_not_modify_assets(self):
        """全批先核对账号资格；停用账号、未知账号和重复目标均不能造成部分配装。"""
        before=self.counts()
        for ids in ([self.game_id,2147483647],[self.game_id,self.game_id],[True]):
            with self.assertRaises(Fault):self.prepare(ids=ids)
            self.assertEqual(self.counts(),before)
        with self.s.db.transaction() as c:c.update('accounts',Q(id=self.account),dict(status='DISABLED'))
        with self.assertRaises(Fault):self.prepare()
        self.assertEqual(self.counts(),before)

    def test_functional_card_root_slot_cross_account_and_native_roundtrip(self):
        """卡片自动槽落根1，不能挂在背包或引用他人GID；拒绝后合法装备继续成功。"""
        self.prepare();bridge=self.bridge();inventory=bridge.inventory
        card=next(row for row in inventory.items if row['item_id']==101210)
        bag=next(row for row in inventory.items if row['item_id']==100026)
        with self.assertRaises(Fault):equip(self.s,self.game,dict(self.base,operation=0,subject=card['gid'],target=bag['gid'],location=36))
        other,_,_,_=self.player_game('other')
        with self.s.db.transaction() as c:
            apply(self.s,c,other,item_rewards(self.s,[dict(item_id=101210)]),'TEST_FIXTURE')
            foreign=c.select('native_inventory_bindings',Q(inventory_id=c.select('inventory',Q(account_id=other,item_key='cn2020:101210')).fetchone()['id'])).fetchone()['id']+1000
        with self.assertRaises(Fault):equip(self.s,self.game,dict(self.base,operation=0,subject=foreign,target=1,location=36))
        result=inventory.equip(struct.pack('>HHHHhQQb',0x3243,0xa008,8,19,0,card['gid'],1,-1))
        self.assertEqual(struct.unpack('>HhQQb',result[8:]),(0x8100,0,card['gid'],1,36))
        codec=struct.Struct('>QQiQiqiiiQbiiB')
        props=[codec.unpack_from(response,13+70*i) for response in inventory.packets()
               for i in range(struct.unpack_from('>H',response,11)[0])]
        sent=next(prop for prop in props if prop[2]==101210)
        self.assertEqual(sent[9:11],(1,36))

    def test_vip_query_and_player_info_use_same_real_growth(self):
        """资料与3602都返回实际10500点及32位日期边界，不依赖伪造界面等级。"""
        self.prepare();bridge=self.bridge()
        reply=bridge.handle(packet(0x3601,dict(Padding=0)))
        self.assertEqual(len(reply),1)
        command_id,data=parse(reply[0]);self.assertEqual(command_id,0x3602)
        self.assertEqual(data['VipBaseInfo'],dict(VipPVEPoint=10500,VipPVEEndTime=2147483647,VipPVPPoint=0,VipPVPEndTime=0))
        with patch('tools.native_inventory_bridge.time.time',return_value=self.now):wire=bridge.inventory.player_info(b'Test')
        self.assertEqual(struct.unpack_from('>iiii',wire,8+18+5+284),(10500,2147483647,0,0))

    def test_claim_multiple_actual_rewards_idempotency_and_receipt(self):
        """三个权益分别产生144小时武器；同请求返回原结果，仍持有有效武器时不重复发放。"""
        self.prepare()
        request=self.meta(**self.base,action='pve.store.claim',benefit_id=10014,prop_ids=[100482])
        result=command(self.s,self.game,request);before=self.counts()
        self.assertEqual(command(self.s,self.game,request),result);self.assertEqual(self.counts(),before)
        self.call('pve.store.claim',benefit_id=10015,prop_ids=[100786])
        self.call('pve.store.claim',benefit_id=10016,prop_ids=[100924])
        before=self.counts()
        rows=[row for row in self.view('inventory')['items'] if row['item_id'] in (100482,100786,100924)]
        self.assertEqual(len(rows),3);self.assertTrue(all(row['expires_at']==self.now+144*3600 for row in rows))
        with self.assertRaises(Fault) as error:self.call('pve.store.claim',benefit_id=10014,prop_ids=[100482])
        self.assertEqual(error.exception.code,'PVE_STORE_TAKEN');self.assertEqual(self.counts(),before)

    def test_claim_tail_failure_rolls_back_reward_and_claim_marker(self):
        """最后通知失败同时回滚武器、领取标记及账本；同一领取随后可成功。"""
        self.prepare();before=self.counts()
        with patch('revival.native_actions.event',side_effect=DatabaseError('injected claim tail')):
            with self.assertRaises(DatabaseError):self.call('pve.store.claim',benefit_id=10014,prop_ids=[100482])
        self.assertEqual(self.counts(),before)
        self.call('pve.store.claim',benefit_id=10014,prop_ids=[100482])

    def test_claim_origin_survives_pages_delta_relogin_and_same_weapon_purchase(self):
        """旧领取实例由账本还原35；相同武器另行购买仍48，四种库存投影及原生帧一致。"""
        from revival.native_inventory import load
        from revival.native_features import item_delta
        self.prepare();result=self.call('pve.store.claim',benefit_id=10014,prop_ids=[100482])
        claimed=next(row for row in result['delta']['items'] if row['item_id']==100482)
        self.assertEqual(claimed['gain_type'],35)
        with self.s.db.transaction() as c:
            apply(self.s,c,self.account,item_rewards(self.s,[dict(item_id=100482)]),'shop.purchase')
            account=c.select('accounts',Q(id=self.account)).fetchone()
            delta=item_delta(c,account,{claimed['instance_id']})
        self.assertEqual(delta['items'][0]['gain_type'],35)
        rows=[row for row in self.view('inventory')['items'] if row['item_id']==100482]
        self.assertEqual(sorted(row['gain_type'] for row in rows),[35,48])
        legacy=[row for row in load(self.s,self.game,self.base)['items'] if row['item_id']==100482]
        self.assertEqual({row['gid']:row['gain_type'] for row in legacy},{row['gid']:row['gain_type'] for row in rows})
        bridge=self.bridge();codec=struct.Struct('>QQiQiqiiiQbiiB')
        props=[codec.unpack_from(response,13+70*i) for response in bridge.inventory.packets()
               for i in range(struct.unpack_from('>H',response,11)[0])]
        self.assertEqual({prop[1]:prop[-1] for prop in props if prop[2]==100482},{row['gid']:row['gain_type'] for row in rows})
        self.assertEqual(bridge.prop(next(row for row in bridge.inventory.items if row['gid']==claimed['gid']))['GainType'],35)

    def test_unconfirmed_wrong_owner_and_unreceipted_claim_source_do_not_mark_vip(self):
        """来源文字不能替代真实成功回执；未确认、他人及配方不符均不得标为VIP领用。"""
        from revival.native_pve_benefits import gain_types
        self.prepare();first=self.call('pve.store.claim',benefit_id=10014,prop_ids=[100482])
        reward=next(row for row in first['delta']['items'] if row['item_id']==100482)
        other,_,_,_=self.player_game('other')
        with self.s.db.transaction() as c:
            self.assertEqual(gain_types(c,other,{reward['instance_id']}),{})
            c.update('native_receipts',Q(id=first['receipt_id']),dict(confirmed=False))
        self.assertEqual(next(row for row in self.view('inventory')['items'] if row['gid']==reward['gid'])['gain_type'],48)
        with self.s.db.transaction() as c:
            c.update('native_receipts',Q(id=first['receipt_id']),dict(confirmed=True))
            apply(self.s,c,self.account,item_rewards(self.s,[dict(item_id=100786,duration_seconds=3600)]),'pve.store.claim')
        self.assertEqual(next(row for row in self.view('inventory')['items'] if row['item_id']==100786)['gain_type'],48)

    def test_claimed_weapon_cannot_be_engineering_material_but_ordinary_copy_can(self):
        """同款狂蟒VIP实例禁止工程分解且整单回滚，普通购买实例仍按原配方产出材料。"""
        from revival.native_engineering import RULE_VERSION
        self.prepare();result=self.call('pve.store.claim',benefit_id=10014,prop_ids=[100482])
        vip=next(row for row in result['delta']['items'] if row['item_id']==100482)
        with self.s.db.transaction() as c:
            apply(self.s,c,self.account,item_rewards(self.s,[dict(item_id=100482)]),'shop.purchase')
        ordinary=next(row for row in self.view('inventory')['items'] if row['item_id']==100482 and row['gain_type']==48)
        before=self.counts()
        with self.assertRaises(Fault) as error:
            self.call('engineering.decompose',rule_version=RULE_VERSION,gids=[ordinary['gid'],vip['gid']])
        self.assertEqual(error.exception.code,'ITEM_ACTION');self.assertEqual(self.counts(),before)
        result=self.call('engineering.decompose',rule_version=RULE_VERSION,gids=[ordinary['gid']])
        self.assertEqual(result['status'],'COMMITTED')
        self.assertEqual([row['gid'] for row in self.view('inventory')['items'] if row['item_id']==100482],[vip['gid']])

    def test_concurrent_different_requests_only_grant_once(self):
        """两个不同幂等键并发领取同一权益，部署事务锁与已领标记保证只有一次奖励。"""
        self.prepare()
        requests=[self.meta(**self.base,action='pve.store.claim',benefit_id=10014,prop_ids=[100482]) for _ in range(2)]
        def attempt(request):
            """每线程独立MySQL连接；记录实际提交或业务拒绝结果，不吞掉数据库故障。"""
            try:return command(self.s,self.game,request)['status']
            except Fault as error:return error.code
        with DatabasePool(max_workers=2) as pool:results=list(pool.map(attempt,requests))
        self.assertEqual(sorted(results),['COMMITTED','PVE_STORE_TAKEN'])
        self.assertEqual(sum(row['item_id']==100482 for row in self.view('inventory')['items']),1)

    def test_expiry_boundary_unknown_prop_and_bad_growth_are_not_success(self):
        """到期当秒停止权益，任意武器ID/重复权益/坏成长都拒绝，不能借查询或领奖增加资产。"""
        self.prepare();box=next(row for row in self.view('inventory')['items'] if row['item_id']==101296)
        before=self.counts()
        for values in ([100001],[100482,100482],[],[True],[{}],[10014]):
            with self.assertRaises(Fault):self.call('pve.store.claim',benefit_id=10014,prop_ids=values)
            self.assertEqual(self.counts(),before)
        with self.s.db.transaction() as c:c.update('inventory',Q(id=box['instance_id']),dict(expires_at=self.now))
        self.assertFalse(self.view('pve.benefits')['active'])
        with self.assertRaises(Fault) as error:self.call('pve.store.claim',benefit_id=10014,prop_ids=[100482])
        self.assertEqual(error.exception.code,'PVE_STORE_LEVEL')
        for points in (-1,True,20001):
            with self.assertRaises(ValueError):level(points)
        self.assertEqual(vip_info([dict(item_id=101296,expires_at=self.now,state=box['state'])],self.now)['VipPVEEndTime'],0)

    def test_v11_keeps_every_previous_schema_and_query_failure_keeps_connection(self):
        """升级协议目录不得改变旧462个结构；不持有百宝箱时领奖失败后仍能正常查询。"""
        old=json.loads((Path(__file__).resolve().parents[1]/'config/cn2020-feature-protocol-v10.json').read_text(encoding='utf-8'))
        self.assertTrue(all(metadata()['schemas'][key]==value for key,value in old['schemas'].items()))
        bridge=self.bridge()
        response=bridge.handle(packet(0x3603,dict(Seq=123,PropIds=[10014])))
        self.assertEqual(parse(response[0]),(0x3604,dict(Seq=123,Result=metadata()['constants']['ZONE_VIP_LEVEL_POINT_ERROR'])))
        self.assertEqual(parse(bridge.handle(packet(0x3601,dict(Padding=0)))[0])[1]['result'],metadata()['constants']['ZONE_ERR_SUCC'])

    def test_captured_native_seq_and_reward_id_request_grants_then_rejects_active_duplicate(self):
        """固定第71轮实收18字节请求，独立于编码器字段假设；领取成功后同连接和重复点击正常。"""
        self.prepare();bridge=self.bridge()
        request=bytes.fromhex('324336030008000a0000271e000100018882')
        reply=bridge.handle(request)
        self.assertEqual(parse(reply[-1]),(0x3604,dict(Seq=10014,Result=metadata()['constants']['ZONE_ERR_SUCC'])))
        self.assertEqual(sum(row['item_id']==100482 for row in bridge.inventory.items),1)
        self.assertEqual(parse(bridge.handle(request)[-1])[1]['Result'],metadata()['constants']['ZONE_ERR_SUCC'])
        # 避免两秒内幂等点击缓存掩盖持有检查，下一次正式业务请求必须拒绝新增。
        with self.assertRaises(Fault) as error:self.call('pve.store.claim',benefit_id=10014,prop_ids=[100482])
        self.assertEqual(error.exception.code,'PVE_STORE_TAKEN')

    def test_expired_reward_can_be_reacquired_with_history_and_old_gid_preserved(self):
        """六天有效期结束后可再次领用，旧实例和领取流水保留；同一旧请求仍只返回原回执。"""
        from revival import launcher
        from revival.security import new_token,token_hash
        self.prepare()
        first=self.call('pve.store.claim',benefit_id=10014,prop_ids=[100482])
        original=next(row for row in self.view('inventory')['items'] if row['item_id']==100482)
        self.now+=144*3600
        player=self.s.login('inventory-one@example.test',self.password)['token']
        verifier=new_token();scope={key:self.base[key] for key in ('protocol','client_sha256','rules_version')}
        ticket=launcher.issue(self.s,player,self.meta(**scope,verifier_sha256=token_hash(verifier)))
        self.game=launcher.redeem(self.s,dict(scope,ticket=ticket['ticket'],verifier=verifier))['game_token']
        second=self.call('pve.store.claim',benefit_id=10014,prop_ids=[100482])
        self.assertNotEqual(first['receipt_id'],second['receipt_id'])
        rows=[row for row in self.view('inventory')['items'] if row['item_id']==100482]
        self.assertEqual(len(rows),2);self.assertIn(original['gid'],[row['gid'] for row in rows])
        self.assertEqual(sorted(row['expires_at'] for row in rows),[self.now,self.now+144*3600])

    def test_signed_time_projection_boundary_never_expires_permanent_server_right(self):
        """2038协议日期上限不改变服务端永久有效性；真实领取仍须以数据库期限为准。"""
        from revival.native_pve_benefits import snapshot
        self.prepare();self.now=2147483648
        with self.s.db.transaction() as c:
            data=snapshot(self.s,c,c.select('accounts',Q(id=self.account)).fetchone())
        self.assertTrue(data['active']);self.assertEqual(data['vip_info']['VipPVEEndTime'],2147483647)
