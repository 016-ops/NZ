"""真实MySQL核对追加五背包方案、全能体验卡、旧回执兼容及原生负槽位编码。"""
import struct
from unittest.mock import patch
from django.db import DatabaseError
from django.db.models import Q

from .test_control_plane import Base
from . import test_native_features as fixtures
from .support import DatabasePool
from revival.catalogs import with_native_inventory,NATIVE_INVENTORY_VERSION
from revival.native_acceptance_loadout import prepare,FIVE_BAGS,SIX_BAGS,CARDS,EXTENDED_VERSION,COMPLETE_VERSION
from revival.native_inventory import equip,grant_full_inventory
from revival.native_features_catalog import FEATURE_VERSION
from revival.native_pve_benefits import functional,FUNCTIONAL_VERSION
from revival.service import Fault


class ExtendedAcceptanceTests(Base):
    """全部资产和凭据来自隔离数据库，不操作DEV验收账号或已有4323项权益。"""
    player_game=fixtures.NativeFeatureTests.player_game
    view=fixtures.NativeFeatureTests.view
    bridge=fixtures.NativeFeatureTests.bridge

    def setUp(self):
        """为当前测试创建真实游戏凭据，注入当前冻结物品目录。"""
        super().setUp();self.s.catalog=with_native_inventory(self.s.catalog)
        self.account,self.player_token,self.game,self.base=self.player_game()
        self.base['feature_version']=FEATURE_VERSION
        with self.s.db.transaction() as c:self.game_id=c.select('accounts',Q(id=self.account)).fetchone()['game_id']

    def extended(self,ids=None):
        """固定请求标识，方便并发与未知提交重试核对。"""
        return prepare(self.s,ids or [self.game_id],request_id='extended-acceptance-v2-test',
                       reason='隔离五套配装验收',profile='five-bags')

    def counts(self):
        """含账本和事件的完整计数，用于发现末尾故障留下的半笔配装。"""
        with self.s.db.transaction() as c:
            return {n:c.count(n).fetchone()[0] for n in ('inventory','equipment','asset_entries','asset_operations',
                'native_inventory_bindings','native_receipts','native_sync_changes','native_player_events','audit_events')}

    def test_five_exact_bags_with_concurrent_retry_and_original_receipt(self):
        """原v1回执保留；新v2并发只提交一次，五套20件和四卡永久入库并准确挂接。"""
        old=prepare(self.s,[self.game_id],request_id='original-v1',reason='旧方案夹具')
        with DatabasePool(max_workers=2) as pool:results=list(pool.map(lambda _:self.extended(),range(2)))
        self.assertEqual(results[0],results[1]);before=self.counts()
        self.assertEqual(prepare(self.s,[self.game_id],request_id='original-v1',reason='旧请求重放'),old)
        self.assertEqual(self.counts(),before)
        items={r['item_id']:r for r in self.view('inventory')['items']}
        self.assertEqual(len(items),30)
        for bag_id,weapons in FIVE_BAGS:
            bag=items[bag_id];self.assertEqual(bag['equipment_slot'],f'native:bag:{bag_id}')
            for item_id,location in weapons:
                row=items[item_id]
                self.assertEqual((row['quantity'],row['expires_at'],row['equipment_slot']),
                    (1,None,f"native:{bag['gid']}:{location}"))
        for item_id,location in CARDS+((102000,-1),):
            self.assertEqual(items[item_id]['equipment_slot'],f'native:1:{location}')
        benefit=self.view('pve.benefits')
        self.assertEqual(benefit['functional_rule_version'],FUNCTIONAL_VERSION)
        self.assertTrue(next(r for r in benefit['functional_items'] if r['item_id']==102000)['equipped'])
        self.assertEqual(results[0]['rule_version'],EXTENDED_VERSION)

    def test_negative_root_slot_roundtrip_does_not_replace_expert_card(self):
        """卡片-1是明确根槽；A008/A009/A006可往返，与36专家槽并存，错父和过期继续拒绝。"""
        self.extended();bridge=self.bridge();inventory=bridge.inventory
        card=next(r for r in inventory.items if r['item_id']==102000)
        bag=next(r for r in inventory.items if r['item_id']==100026)
        with self.assertRaises(Fault):equip(self.s,self.game,dict(self.base,operation=0,subject=card['gid'],target=bag['gid'],location=-1))
        reply=inventory.equip(struct.pack('>HHHHhQQb',0x3243,0xa008,8,19,0,card['gid'],1,-1))
        self.assertEqual(struct.unpack('>HhQQb',reply[8:]),(0x8100,0,card['gid'],1,-1))
        codec=struct.Struct('>QQiQiqiiiQbiiB')
        props=[codec.unpack_from(p,13+70*i) for p in inventory.packets() for i in range(struct.unpack_from('>H',p,11)[0])]
        for item_id,location in ((102000,-1),(101210,36)):
            self.assertEqual(next(r for r in props if r[2]==item_id)[9:11],(1,location))
        with self.s.db.transaction() as c:c.update('inventory',Q(id=card['instance_id']),dict(expires_at=self.now-1))
        with self.assertRaises(Fault):equip(self.s,self.game,dict(self.base,operation=0,subject=card['gid'],target=1,location=-1))
        self.assertFalse(functional(dict(item_id=104047,location=-1,consume_type=1,hidden=False)))

    def test_entire_extended_batch_rolls_back_on_second_account_failure(self):
        """新增方案仍共用一笔MySQL事务；第二账号事件失败不能留下首账号的套装或卡。"""
        other,_,_,_=self.player_game('extendedother')
        with self.s.db.transaction() as c:other_game_id=c.select('accounts',Q(id=other)).fetchone()['game_id']
        before=self.counts()
        with patch('revival.native_acceptance_loadout.event',side_effect=[None,DatabaseError('extended tail')]):
            with self.assertRaises(DatabaseError):self.extended([self.game_id,other_game_id])
        self.assertEqual(self.counts(),before)

    def test_full_gm_entitlements_and_gids_are_reused(self):
        """4323项永久GM库存完全复用原实例；新方案不得重发套装、全能卡或删除任何旧权益。"""
        grant_full_inventory(self.s,self.account,rules_version=NATIVE_INVENTORY_VERSION,reason='隔离全目录夹具')
        with self.s.db.transaction() as c:
            before=set(c.table('native_inventory_bindings').filter(inventory__account_id=self.account).values_list('inventory_id','id'))
        self.extended()
        with self.s.db.transaction() as c:
            after=set(c.table('native_inventory_bindings').filter(inventory__account_id=self.account).values_list('inventory_id','id'))
            self.assertEqual(c.count('inventory',Q(account_id=self.account)).fetchone()[0],4323)
        self.assertEqual(before,after)

    def test_sixth_element_set_and_all_kernels_preserve_previous_receipt(self):
        """追加六号元素及七种核心，旧五套回执不变；资料包按真实库存投影五级。"""
        from revival.native_kun import snapshot
        from adapters.cn_current.features import decode
        old=self.extended()
        with self.s.db.transaction() as c:
            before=dict(c.table('native_inventory_bindings').filter(inventory__account_id=self.account).values_list('inventory_id','id'))
        result=prepare(self.s,[self.game_id],request_id='six-bags-v3-test',reason='元素套及满核心',profile='six-bags')
        self.assertEqual(result['rule_version'],COMPLETE_VERSION)
        self.assertEqual(self.extended(),old)
        counts=self.counts()
        self.assertEqual(prepare(self.s,[self.game_id],request_id='six-bags-v3-test',reason='安全重试',profile='six-bags'),result)
        self.assertEqual(self.counts(),counts)
        items=self.view('inventory')['items'];by_id={r['item_id']:r for r in items};slots={r['equipment_slot']:r for r in items if r['equipment_slot']}
        for bag_id,weapons in SIX_BAGS:
            for item_id,location in weapons:
                row=slots[f"native:{by_id[bag_id]['gid']}:{location}"]
                self.assertEqual(row['item_id'],item_id)
                self.assertIsNone(row['expires_at'])
                if bag_id==100063:
                    self.assertEqual(row['state']['skin']['item_id'],{103428:103726,103429:103727,103430:103728,103431:103729}[item_id])
                if bag_id==100035:self.assertNotIn('skin',row['state'])
        state=snapshot(items,self.now)
        self.assertEqual((state['level'],state['all_kernels_owned'],state['suit_license']),(5,True,True))
        self.assertEqual(state['weapons'],[103428,103716])
        with self.s.db.transaction() as c:
            after=dict(c.table('native_inventory_bindings').filter(inventory__account_id=self.account).values_list('inventory_id','id'))
        self.assertTrue(all(after.get(key)==value for key,value in before.items()))
        bridge=self.bridge()
        with patch('tools.native_inventory_bridge.time.time',return_value=self.now):
            wire=bridge.inventory.player_info(b'CoreTest')
        self.assertEqual(struct.unpack_from('>H',wire,2)[0],0xa005)
        data,end=decode('PlayerInfo',wire,offset=10)
        self.assertEqual(end,len(wire))
        self.assertEqual(data['KunTaoEvolutionLevel'],5)

    def test_kernel_level_rejects_expired_duplicate_and_missing_weapon(self):
        """同种核心多份不叠级，过期不计入；只持核心而没有武器不赋予武器进化。"""
        from revival.native_kun import snapshot
        def item(item_id,expires=None):
            """构造没有账户权限含义的纯库存读投影，期限边界明确取当前秒。"""
            return dict(item_id=item_id,quantity=1,expires_at=expires)
        kernels=[item(103377),item(103377),item(103378,self.now),item(103379,self.now+1)]
        self.assertEqual(snapshot(kernels,self.now)['level'],0)
        state=snapshot(kernels+[item(103716),item(103438)],self.now)
        self.assertEqual((state['level'],state['kernels']),(3,[103377,103379]))
        self.assertFalse(state['all_kernels_owned'])
