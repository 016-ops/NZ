"""隔离MySQL工程回归：真实资产事务、幂等、制造成长和失败回滚，不替代原生UI验收。"""
from unittest.mock import patch
from django.db import DatabaseError
from django.db.models import Q
from .test_control_plane import Base
from . import test_native_features
from revival import native_features as features
from revival.native_actions import apply, item_rewards
from revival.native_engineering import RULE_VERSION
from revival.native_features_catalog import engineering_catalog
from revival.catalogs import with_native_inventory
from revival.service import Fault


class NativeEngineeringTests(Base):
    """复用游戏凭据助手但不继承其整套测试，避免重复收集旧用例。"""
    player_game=test_native_features.NativeFeatureTests.player_game
    call=test_native_features.NativeFeatureTests.call
    view=test_native_features.NativeFeatureTests.view
    give=test_native_features.NativeFeatureTests.give
    bridge=test_native_features.NativeFeatureTests.bridge

    def setUp(self):
        """建立本类独立隔离账号和完整原生目录，不调用其他测试类的super绑定。"""
        super().setUp()
        self.s.catalog=with_native_inventory(self.s.catalog)
        self.account,self.player_token,self.game,self.base=self.player_game()
        self.base['feature_version']=features.FEATURE_VERSION

    def prepare(self,recipe,count=1):
        """只为当前隔离账号按账本准备配方材料和付费制造余额。"""
        with self.s.db.transaction() as c:
            apply(self.s,c,self.account,item_rewards(self.s,[dict(m,quantity=m['quantity']*count) for m in recipe['materials'] if m['quantity']>0])+
                  [dict(type='currency',code='cn2020:TP',delta=1000)],'TEST_ENGINEERING')

    def test_decompose_all_types_idempotent_and_batch_atomic(self):
        """三类分解各消耗本人1件，材料数量正确；幂等重放不能再次移除或发放。"""
        catalog=engineering_catalog();expected={}
        for kind in (1,2,3):
            source=next(r for r in catalog['decomposable'] if r['type']==kind)
            item=self.give(source['item_id'])
            request=self.meta(**self.base,action='engineering.decompose',rule_version=RULE_VERSION,gids=[item['gid']])
            result=features.command(self.s,self.game,request)
            self.assertEqual(result,features.command(self.s,self.game,request))
            for i in catalog['decompose_outputs'][str(kind)]:expected[i]=expected.get(i,0)+1
        self.assertEqual({i['item_id']:i['quantity'] for i in self.view('inventory')['items']},expected)
        before=self.view('inventory')['items']
        with self.assertRaises(Fault):
            self.call('engineering.decompose',rule_version=RULE_VERSION,gids=[before[0]['gid'],999999])
        self.assertEqual(before,self.view('inventory')['items'])

    def test_all_37_recipes_keep_cost_result_and_progress(self):
        """包括隐藏和随机项的全部配方逐一制造；精确扣料、有效期限、熟练度和回执同存。"""
        for recipe in engineering_catalog()['recipes']:
            with self.subTest(recipe=recipe['nSyntheticPropID']):
                self.prepare(recipe)
                result=self.call('engineering.compose',rule_version=RULE_VERSION,recipe_id=recipe['nSyntheticPropID'],count=1,mode=1)
                self.assertEqual(result['successes'],1)
                self.assertEqual(result['proficiency'],1)
                self.assertEqual(result['rewards'][0]['duration_seconds'],recipe['NormalAvaibleTimeArray'][0]*3600)
                self.assertIn(result['rewards'][0]['item_id'],[recipe['nItemID']] if recipe['nItemID'] else [100465,100474,100542,100543])
                self.assertFalse([i for i in self.view('inventory')['items'] if i['item_id'] in (100764,100765,100766,100767)])
        self.assertEqual(len(self.view('engineering')['progress']),37)

    def test_paid_batch_failure_rolls_back_currency_parts_progress_and_receipt(self):
        """最后事件故障发生时，不留下部分奖励、扣款或先增加的熟练度。"""
        recipe=engineering_catalog()['recipes'][1];self.prepare(recipe,2)
        before=self.view('inventory')['items']
        with self.s.db.transaction() as c:
            balance=c.select('wallets',Q(account_id=self.account,currency='cn2020:TP')).fetchone()['balance']
        with patch('revival.native_engineering.event',side_effect=DatabaseError('injected engineering final event')):
            with self.assertRaises(DatabaseError):
                self.call('engineering.compose',rule_version=RULE_VERSION,recipe_id=recipe['nSyntheticPropID'],count=2,mode=2)
        self.assertEqual(before,self.view('inventory')['items']);self.assertEqual(self.view('engineering')['progress'],[])
        with self.s.db.transaction() as c:
            self.assertEqual(c.select('wallets',Q(account_id=self.account,currency='cn2020:TP')).fetchone()['balance'],balance)
            self.assertFalse(c.select('native_receipts',Q(account_id=self.account)))

    def test_material_conversion_preserves_quantity_and_rejects_underflow(self):
        """计数部件5合1再1拆5保持总量；重复GID拆分按实际数量合并，余额不足全回滚。"""
        iron=self.give(100764,10)
        self.call('engineering.material_compose',rule_version=RULE_VERSION,item_id=100765)
        copper=next(i for i in self.view('inventory')['items'] if i['item_id']==100765)
        with self.assertRaises(Fault):
            self.call('engineering.material_decompose',rule_version=RULE_VERSION,gids=[copper['gid'],copper['gid']])
        self.call('engineering.material_decompose',rule_version=RULE_VERSION,gids=[copper['gid']])
        inventory=self.view('inventory')['items']
        self.assertEqual(len(inventory),1);self.assertEqual(inventory[0]['gid'],iron['gid']);self.assertEqual(inventory[0]['quantity'],10)

    def test_owner_expiry_duplicate_and_rule_version_rejected(self):
        """跨账号、过期、重复装备引用、畸形GID和过期规则均不可消费。"""
        from revival.service import Fault
        item=self.give(engineering_catalog()['decomposable'][0]['item_id'])
        for data in [dict(rule_version='old',gids=[item['gid']]),dict(rule_version=RULE_VERSION,gids=[item['gid']]*2),
                     dict(rule_version=RULE_VERSION,gids=[{}])]:
            with self.assertRaises(Fault):self.call('engineering.decompose',**data)
        _,_,other,base=self.player_game('eng-other')
        with self.assertRaises(Fault):
            features.command(self.s,other,self.meta(**base,feature_version=self.base['feature_version'],action='engineering.decompose',rule_version=RULE_VERSION,gids=[item['gid']]))
        with self.s.db.transaction() as c:
            c.update('inventory',Q(id=item['instance_id']),dict(expires_at=self.s.clock()-1))
        with self.assertRaises(Fault):self.call('engineering.decompose',rule_version=RULE_VERSION,gids=[item['gid']])

    def test_native_failure_then_success_and_progress_relogin(self):
        """同连接材料不足返回工厂失败应答；补足后制造成功，重建桥后熟练度仍为1。"""
        from adapters.cn_current.features import packet,parse
        recipe=engineering_catalog()['recipes'][1]
        bridge=self.bridge();fields=dict(Uin=bridge.inventory.game_id,PropIndex=recipe['nSyntheticPropID'],ComposeType=2,ComposeCount=1)
        self.assertEqual(parse(bridge.handle(packet(0xf404,fields))[0])[1]['Result'],9862)
        self.prepare(recipe)
        replies=bridge.handle(packet(0xf404,fields))
        self.assertEqual(parse(replies[0])[1]['Result'],9856)
        self.assertEqual(parse(replies[0])[1]['SuccCount'],1)
        # 在线制造只发送单配方F405，避免F401全量Flash刷新覆盖即时熟练度；重登仍独立回放。
        self.assertEqual(parse(replies[0])[1]['SpecialtyLevel'],1)
        self.assertNotIn(0xf401,[parse(reply)[0] for reply in replies])
        # NZ余额必须使用独立Reason42；制造Reason84继续负责材料和武器增量。
        updates=[parse(reply)[1] for reply in replies if parse(reply)[0]==0xa00a]
        self.assertTrue(any(r['Reason']==84 and r['Count']>0 for r in updates))
        self.assertTrue(any(r['Reason']==42 and r['TGamePoint']==1000-recipe['PriceValueSucceed_NZ'] for r in updates))
        progress=parse(self.bridge().engineering_snapshot())[1]['PlayerPropProductInfo']['PropSpecialtyInfo']
        self.assertEqual(next(r for r in progress if r['PropNO']==recipe['nSyntheticPropID'])['Level'],1)
