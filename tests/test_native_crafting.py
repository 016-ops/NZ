"""使用真实MySQL隔离账号验证当前139条配方，覆盖材料不足、跨账号和事务尾故障。"""
from unittest.mock import patch
from django.db import DatabaseError
from revival import native_features
from revival.native_features_catalog import exchange_catalog
from revival.native_rewards import validate_rule
from revival.service import Fault
from . import test_native_features as fixtures
from .test_control_plane import Base
from revival.catalogs import with_native_inventory


class NativeCraftingTests(Base):
    """复用正式游戏凭据与资产夹具；本类仅添加加工场景，既有功能仍由原测试覆盖。"""
    player_game=fixtures.NativeFeatureTests.player_game
    call=fixtures.NativeFeatureTests.call
    view=fixtures.NativeFeatureTests.view
    give=fixtures.NativeFeatureTests.give
    bridge=fixtures.NativeFeatureTests.bridge

    def setUp(self):
        """创建隔离账号及当前物品目录，不执行继承来的整套既有测试。"""
        super().setUp()
        self.s.catalog=with_native_inventory(self.s.catalog)
        self.account,self.player_token,self.game,self.base=self.player_game()
        self.base['feature_version']=native_features.FEATURE_VERSION

    def test_frozen_recipes_all_execute_and_consume_exact_inputs(self):
        """139条配方均走同一正式入口；同物品转化与0额外材料不能丢失或多扣主体。"""
        for recipe in exchange_catalog()['recipes']:
            with self.subTest(recipe=recipe['recipe_id']):
                # 每个配方使用新账号，产物不成为后续配方的隐形材料，避免假阳性。
                self.account,self.player_token,self.game,self.base=self.player_game('craft'+recipe['recipe_id'].replace(':',''))
                self.base['feature_version']=native_features.FEATURE_VERSION
                subject=self.give(recipe['ExchangeItemId'])
                if recipe['CostItemAmount']:
                    self.give(recipe['CostItemId'],recipe['CostItemAmount'])
                request=self.meta(**self.base,action='inventory.craft',gid=subject['gid'],
                    recipe_id=recipe['recipe_id'],rule_version=exchange_catalog()['version'])
                first=native_features.command(self.s,self.game,request)
                self.assertEqual(first,native_features.command(self.s,self.game,request))
                items=self.view('inventory')['items']
                self.assertEqual([(i['item_id'],i['quantity']) for i in items],[(recipe['AchieveItemId'],recipe['AchieveItemAmount'])])
                self.assertIsNone(items[0]['expires_at'])
                self.assertEqual(first['rule_version'],exchange_catalog()['version'])

    def test_craft_shortage_wrong_recipe_foreign_gid_and_fault_are_atomic(self):
        """不足、错配、跨账号、最后事件失败都保留原仓库；成功后旧请求只能重放。"""
        recipe=exchange_catalog()['recipes'][0]
        subject=self.give(recipe['ExchangeItemId'])
        payload=dict(gid=subject['gid'],recipe_id=recipe['recipe_id'],rule_version=exchange_catalog()['version'])
        original=self.view('inventory')['items']
        with self.assertRaises(Fault):self.call('inventory.craft',**payload)
        self.assertEqual(original,self.view('inventory')['items'])
        self.give(recipe['CostItemId'])
        original=self.view('inventory')['items']
        with self.assertRaises(Fault):self.call('inventory.craft',**dict(payload,recipe_id='100915:999999'))
        _,_,game,base=self.player_game('craftforeign')
        with self.assertRaises(Fault):
            native_features.command(self.s,game,self.meta(**base,feature_version=native_features.FEATURE_VERSION,action='inventory.craft',**payload))
        with patch('revival.native_actions.event',side_effect=DatabaseError('craft final event fault')):
            with self.assertRaises(DatabaseError):self.call('inventory.craft',**payload)
        self.assertEqual(original,self.view('inventory')['items'])

    def test_subject_only_custom_recipe_rejects_free_reward(self):
        """后台允许消耗主体的0额外材料配方，但拒绝没有任何消耗的空材料配方。"""
        value=dict(materials=[],consume_subject=True,rewards=[dict(item_id=100001)])
        validate_rule(self.s,'item:100915:exchange',value)
        with self.assertRaises(Fault):validate_rule(self.s,'item:100915:exchange',dict(value,consume_subject=False))

    def test_native_upgrade_failure_then_success_keeps_connection(self):
        """原生A375材料不足返回1799，随后补足材料仍能在同一个桥上成功并增量更新。"""
        from adapters.cn_current.features import packet,parse
        recipe=exchange_catalog()['recipes'][-1]
        subject=self.give(recipe['ExchangeItemId'])
        bridge=self.bridge()
        raw=packet(0xa375,dict(ItemId=recipe['ExchangeItemId'],ItemGid=subject['gid'],
            FortuneKeyType=recipe['protocol_value'],FortuneKeyCount=1,
            PredictProp=dict(ItemId=100001,StackNum=999)))
        failed=bridge.handle(raw)
        self.assertEqual(parse(failed[0])[1]['Result'],1799)
        self.give(recipe['CostItemId'])
        succeeded=bridge.handle(raw)
        command,response=parse(succeeded[0])
        self.assertEqual((command,response['Result']),(0xa376,1793))
        self.assertEqual(response['Props'][0]['ItemId'],recipe['AchieveItemId'])
        self.assertTrue(any(parse(p)[0]==0xa00a for p in succeeded[1:]))
        self.assertEqual([r['item_id'] for r in self.view('inventory')['items']],[recipe['AchieveItemId']])

        repeated=bridge.handle(raw)
        self.assertEqual(parse(repeated[0]),parse(succeeded[0]))
        self.assertEqual(len(repeated),1)  # 主体已消耗仍重放原结果，不再发送资产增量。
