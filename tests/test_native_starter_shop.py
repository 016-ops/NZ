"""真实MySQL回归新账号配装和原生角色礼包；使用独立账号并核验事务尾故障。"""
from unittest.mock import patch
from django.db import DatabaseError
from django.db.models import Q
from django.test import override_settings
from .test_control_plane import Base
from . import test_native_features
from revival.catalogs import with_native_inventory
from revival.native_actions import apply
from revival.native_features_catalog import FEATURE_VERSION
from revival.native_starter import ensure,BAGS


class NativeStarterShopTests(Base):
    """新策略专项显式开启生产初装；商城场景仍用空仓，避免把旧fixture当玩家注册行为。"""
    player_game=test_native_features.NativeFeatureTests.player_game
    call=test_native_features.NativeFeatureTests.call
    view=test_native_features.NativeFeatureTests.view
    bridge=test_native_features.NativeFeatureTests.bridge

    def setUp(self):
        """当前固定目录与本例账户隔离，所有写入仍使用真实MySQL事务。"""
        super().setUp();self.s.catalog=with_native_inventory(self.s.catalog)

    def test_registration_equips_roles_first_loadout_and_every_bag_knife_once(self):
        """首次建角得到指定角色、首包主副刀雷及六个独立默认刀；重试不增发或覆盖选择。"""
        with override_settings(NZ_STARTER_EQUIPMENT_ENABLED=True):
            self.account,self.player_token,self.game,self.base=self.player_game()
        self.base['feature_version']=FEATURE_VERSION
        rows=self.view('inventory')['items'];self.assertEqual(len(rows),25)
        self.assertEqual({r['item_id'] for r in rows if r['location'] in (10,11)},{100216,100217})
        for role_id,components in ((100216,{100218,100323,300062,300063}),(100217,{100219,100324,300067,300068})):
            role=next(r for r in rows if r['item_id']==role_id)
            self.assertEqual({r['item_id'] for r in rows if (r['equipment_slot'] or '').startswith(f"native:{role['gid']}:")},components)
        first=next(r for r in rows if r['item_id']==100026)['gid']
        self.assertEqual({r['item_id'] for r in rows if (r['equipment_slot'] or '').startswith(f'native:{first}:')},{100292,100009,100013,100010})
        for bag in [r for r in rows if r['item_id'] in BAGS]:
            self.assertTrue(any(r['item_id']==100013 and r['equipment_slot']==f"native:{bag['gid']}:2" for r in rows))
        with self.s.db.transaction() as c:ensure(self.s,c,self.account)
        self.assertEqual(rows,self.view('inventory')['items'])

    def test_starter_tail_failure_rolls_back_character_wallet_and_all_assets(self):
        """初装最后事件失败时不留半套装备、角色、钱包、绑定或回执，重试可成功。"""
        account,token=self.player('starter-failure',character=False)
        with override_settings(NZ_STARTER_EQUIPMENT_ENABLED=True):
            with patch('revival.native_starter.event',side_effect=DatabaseError('injected starter tail')):
                with self.assertRaises(DatabaseError):self.command('character.create',dict(nickname='starter-failure'),token)
            with self.s.db.transaction() as c:
                for table in ('characters','wallets','inventory','equipment','native_receipts'):
                    self.assertFalse(c.select(table,Q(account_id=account)))
            self.command('character.create',dict(nickname='starter-failure'),token)
        with self.s.db.transaction() as c:self.assertEqual(len(c.select('inventory',Q(account_id=account))),25)

    def test_native_move_knife_fills_source_bag_and_failure_allows_next_equip(self):
        """跨包移动刀不留下空槽、不多发实例；复现Target0失败后同桥继续成功装备M4A1。"""
        import struct
        from tools.native_inventory_bridge import NativeEquipmentUnsupported
        with override_settings(NZ_STARTER_EQUIPMENT_ENABLED=True):
            self.account,self.player_token,self.game,self.base=self.player_game()
        self.base['feature_version']=FEATURE_VERSION
        features=self.bridge();inventory=features.inventory;inventory.features=features
        items=inventory.items;first=next(r for r in items if r['item_id']==100026)['gid']
        second=next(r for r in items if r['item_id']==100063)['gid']
        knife=next(r for r in items if r['equipment_slot']==f'native:{first}:2')
        packet=lambda subject,target:struct.pack('>HHHHhQQb',0x3243,0xa008,8,19,0,subject,target,-1)
        inventory.equip(packet(knife['gid'],second))
        self.assertEqual(len(inventory.items),25)
        self.assertEqual(len([r for r in inventory.items if (r['equipment_slot'] or '').endswith(':2')]),6)
        self.assertTrue(inventory.last_default_notifications)
        rifle=next(r for r in inventory.items if r['item_id']==100292)
        with self.assertRaises(NativeEquipmentUnsupported):inventory.equip(packet(rifle['gid'],0))
        self.assertEqual(struct.unpack_from('>H',inventory.equip(packet(rifle['gid'],first)),8)[0],0x8100)

    def test_reported_role_packages_use_tier_period_and_keep_every_component(self):
        """复现用户四个角色包：-2继承档位，长效永久、720小时角色及隐藏部件完整入库。"""
        self.account,self.player_token,self.game,self.base=self.player_game();self.base['feature_version']=FEATURE_VERSION
        with self.s.db.transaction() as c:
            apply(self.s,c,self.account,[dict(type='currency',code='cn2020:TP',delta=1000000)],'TEST_FIXTURE')
        for offer,total,duration in ((201941,9,None),(202075,9,None),(200724,11,None),(200663,9,720*3600)):
            lines=[dict(commodity_id=offer,price_index=0)];quote=self.view('shop.quote',lines=lines)
            self.assertEqual(len(quote['lines'][0]['rewards']),total)
            self.assertTrue(all(r['duration_seconds']==duration for r in quote['lines'][0]['rewards']))
            result=self.call('shop.purchase',lines=lines,quote=quote)
            self.assertEqual(len(result['line_grants'][0]),total)
        self.assertEqual(len(self.view('inventory')['items']),38)

    def test_role_purchase_attaches_defaults_and_preserves_gids_on_reload(self):
        """旧款两角色包买完即挂接三件装饰，重读和重复挂接不新建GID、不改变期限。"""
        from revival.native_avatar import attach_owned_defaults
        self.account,self.player_token,self.game,self.base=self.player_game();self.base['feature_version']=FEATURE_VERSION
        with self.s.db.transaction() as c:
            apply(self.s,c,self.account,[dict(type='currency',code='cn2020:TP',delta=10000)],'TEST_FIXTURE')
        lines=[dict(commodity_id=200663,price_index=0)]
        result=self.call('shop.purchase',lines=lines,quote=self.view('shop.quote',lines=lines))
        rows=self.view('inventory')['items'];by_id={r['item_id']:r for r in rows}
        self.assertEqual(len(result['avatar_equipment']),7)
        for child,parent,slot in ((100690,100688,0),(100701,100688,2),(100691,100689,0)):
            self.assertEqual(by_id[child]['equipment_slot'],f"native:{by_id[parent]['gid']}:{slot}")
            self.assertEqual(by_id[child]['expires_at'],by_id[parent]['expires_at'])
        with self.s.db.transaction() as c:
            self.assertEqual(attach_owned_defaults(self.s,c,self.account,[r['instance_id'] for r in rows]),[])
        self.assertEqual(rows,self.view('inventory')['items'])
        bridge=self.bridge().inventory
        self.assertEqual(len(bridge.items),9)
        self.assertTrue(bridge.packets())

    def test_legacy_owned_components_attach_on_role_equip_without_regrant(self):
        """模拟旧订单的组件未挂接；角色换装返回真实增量及A00B，重复操作不增发。"""
        import struct
        self.account,self.player_token,self.game,self.base=self.player_game();self.base['feature_version']=FEATURE_VERSION
        with self.s.db.transaction() as c:
            apply(self.s,c,self.account,[dict(type='currency',code='cn2020:TP',delta=10000)],'TEST_FIXTURE')
        lines=[dict(commodity_id=200663,price_index=0)]
        self.call('shop.purchase',lines=lines,quote=self.view('shop.quote',lines=lines))
        rows=self.view('inventory')['items'];original={r['gid'] for r in rows}
        amy=next(r for r in rows if r['item_id']==100688)
        with self.s.db.transaction() as c:
            c.delete('equipment',Q(account_id=self.account,slot__startswith=f"native:{amy['gid']}:"))
        features=self.bridge();bridge=features.inventory;bridge.features=features
        request=struct.pack('>HHHHhQQb',0x3243,0xa008,8,19,0,amy['gid'],1,-1)
        bridge.equip(request)
        self.assertEqual(len(bridge.last_default_notifications),4)
        self.assertEqual({r['gid'] for r in bridge.items},original)
        bridge.equip(request)
        self.assertEqual(bridge.last_default_notifications,[])
        self.assertEqual({r['gid'] for r in bridge.items},original)

    def test_component_wrong_family_and_weapon_parent_fail_without_losing_connection(self):
        """组件不能挂到不同家族或背包，武器不能挂到角色；失败后同桥仍可成功换装。"""
        import struct
        from tools.native_inventory_bridge import NativeEquipmentUnsupported
        with override_settings(NZ_STARTER_EQUIPMENT_ENABLED=True):
            self.account,self.player_token,self.game,self.base=self.player_game()
        self.base['feature_version']=FEATURE_VERSION
        bridge=self.bridge().inventory;rows={r['item_id']:r for r in bridge.items}
        packet=lambda child,parent:struct.pack('>HHHHhQQb',0x3243,0xa008,8,19,0,rows[child]['gid'],rows[parent]['gid'],-1)
        for child,parent in ((100323,100217),(100323,100026),(100292,100216)):
            with self.assertRaises(NativeEquipmentUnsupported):bridge.equip(packet(child,parent))
        bridge.equip(packet(100323,100216))
        bridge.equip(packet(100292,100063))
        self.assertEqual(next(r for r in bridge.items if r['item_id']==100292)['equipment_slot'],f"native:{rows[100063]['gid']}:0")

    def test_default_component_tail_failure_rolls_back_whole_cart(self):
        """装饰挂接末尾事件失败时，整车扣款、九件产物、所有装备和回执全部回滚。"""
        self.account,self.player_token,self.game,self.base=self.player_game();self.base['feature_version']=FEATURE_VERSION
        with self.s.db.transaction() as c:
            apply(self.s,c,self.account,[dict(type='currency',code='cn2020:TP',delta=10000)],'TEST_FIXTURE')
        lines=[dict(commodity_id=200663,price_index=0)];quote=self.view('shop.quote',lines=lines)
        before=self.view('inventory')
        original=__import__('revival.native_actions',fromlist=['event']).event
        def fail(service,c,account,kind,payload):
            """仅在挂接实际发生后抛错，验证数据库事务而非输入拒绝。"""
            if kind=='native.avatar.equipped':raise DatabaseError('injected avatar tail')
            return original(service,c,account,kind,payload)
        with patch('revival.native_actions.event',side_effect=fail):
            with self.assertRaises(DatabaseError):self.call('shop.purchase',lines=lines,quote=quote)
        self.assertEqual(before,self.view('inventory'))
        with self.s.db.transaction() as c:
            self.assertFalse(c.select('native_receipts',Q(account_id=self.account,kind='shop.purchase')))
            self.assertFalse(c.select('equipment',Q(account_id=self.account)))
