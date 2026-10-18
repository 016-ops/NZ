"""真实 MySQL 礼包开启回归：使用隔离账号，核实原子扣箱、奖励快照和原生重试。"""
from unittest.mock import patch
from django.db import DatabaseError
from django.db.models import Q
from revival import native_features
from revival.catalogs import with_native_inventory
from revival.native_containers import catalog
from revival.service import Fault
from . import test_native_features as fixtures
from .test_control_plane import Base
from .support import DatabasePool


class NativeContainerTests(Base):
    """复用凭据夹具而不继承其他测试集；每个场景只操作自己的测试库账号。"""
    player_game=fixtures.NativeFeatureTests.player_game
    call=fixtures.NativeFeatureTests.call
    view=fixtures.NativeFeatureTests.view
    give=fixtures.NativeFeatureTests.give
    bridge=fixtures.NativeFeatureTests.bridge

    def setUp(self):
        """创建本例独立账号和原生目录，沿用正式游戏凭据鉴权。"""
        super().setUp()
        self.s.catalog=with_native_inventory(self.s.catalog)
        self.account,self.player_token,self.game,self.base=self.player_game()
        self.base['feature_version']=native_features.FEATURE_VERSION

    def payload(self, **overrides):
        """产生已冻结的鼠年礼包命令，允许负面测试覆盖单个字段。"""
        return dict(dict(container_protocol=307,count=1,rule_version=catalog()['version']),**overrides)

    def test_concurrent_replay_keeps_one_consumption_and_three_rewards(self):
        """并发重试同一请求只扣一个礼包、发三张卡；回执保持原始数量和永久期限。"""
        self.give(104047)
        request=self.meta(**self.base,action='inventory.open',**self.payload())
        with DatabasePool(max_workers=2) as pool:
            results=list(pool.map(lambda _:native_features.command(self.s,self.game,request),range(2)))
        self.assertEqual(results[0],results[1])
        self.assertEqual([(r['item_id'],r['quantity'],r['expires_at']) for r in sorted(
            results[0]['grants'],key=lambda r:r['item_id'])],[(i,1,None) for i in (104002,104003,104004)])
        self.assertEqual(len(self.view('inventory')['items']),3)
        with self.s.db.transaction() as c:
            self.assertEqual(c.count('native_receipts',Q(kind='inventory.open')).fetchone()[0],1)

    def test_shortage_expiry_foreign_account_and_tail_fault_preserve_assets(self):
        """数量不足、过期、他人持箱及末尾事件故障都不能产生半份奖励或遗留回执。"""
        original=self.give(104047)
        for values in (self.payload(count=2),self.payload(count=0),self.payload(rule_version='old'),self.payload(container_protocol=306)):
            with self.assertRaises(Fault):self.call('inventory.open',**values)
        _,_,game,base=self.player_game('boxforeign')
        with self.assertRaises(Fault):
            native_features.command(self.s,game,self.meta(**base,feature_version=native_features.FEATURE_VERSION,
                action='inventory.open',**self.payload()))
        with patch('revival.native_actions.event',side_effect=DatabaseError('container tail')):
            with self.assertRaises(DatabaseError):self.call('inventory.open',**self.payload())
        self.assertEqual(self.view('inventory')['items'],[original])
        with self.s.db.transaction() as c:
            self.assertEqual(c.count('native_receipts').fetchone()[0],0)
            c.update('inventory',Q(id=original['instance_id']),{'expires_at':self.now-1})
        with self.assertRaises(Fault):self.call('inventory.open',**self.payload())

    def test_protocol_failure_success_and_unknown_commit_recovery(self):
        """原生失败应答不断开；提交后响应丢失跨桥接重建恢复同一礼包回执，忽略预测奖励。"""
        from adapters.cn_current.features import packet,parse
        from tools.native_feature_bridge import NativeFeatureBridge
        from tools.email_launcher import LoginError
        bridge=self.bridge()
        raw=packet(0xa375,dict(ItemId=0,ItemGid=0,FortuneKeyType=307,FortuneKeyCount=1,
            PredictProp=dict(ItemId=100001,StackNum=999)))
        self.assertEqual(parse(bridge.handle(raw)[0])[1]['Result'],1799)
        self.give(104047)
        original_request=bridge.client.request
        def lose_response(path,data,token):
            """只在实际提交完成后抛出连接错误，验证恢复不能再次消费。"""
            result=original_request(path,data,token)
            if path=='/api/game/command':raise LoginError('NETWORK')
            return result
        bridge.client.request=lose_response
        with self.assertRaises(LoginError):bridge.handle(raw)
        bridge.client.request=original_request
        recovered=NativeFeatureBridge(bridge.inventory,bridge.journal)
        replies=recovered.handle(raw)
        command,result=parse(replies[-1])
        self.assertEqual((command,result['Result'],result['FortuneKeyCount']),(0xa376,1793,1))
        self.assertEqual(sorted(p['ItemId'] for p in result['Props']),[104002,104003,104004])
        self.assertTrue(any(parse(p)[0]==0xa00a for p in replies[:-1]))
        self.assertEqual(len(self.view('inventory')['items']),3)

    def test_existing_card_gets_independent_reward_without_false_capacity_error(self):
        """来源唯一性字段未确认账号独占；沿用既有模型生成独立卡片GID，不合并或误报满仓。"""
        original=self.give(104002)
        self.give(104047)
        result=self.call('inventory.open',**self.payload())
        cards=[r for r in self.view('inventory')['items'] if r['item_id']==104002]
        self.assertEqual(len(cards),2)
        self.assertEqual(len({r['gid'] for r in cards}),2)
        self.assertIn(original,cards)
        self.assertNotEqual(next(r['gid'] for r in result['grants'] if r['item_id']==104002),original['gid'])
        with self.s.db.transaction() as c:
            self.assertEqual(c.count('native_receipts').fetchone()[0],1)

    def test_random_concurrent_retry_and_legacy_fixed_recipe(self):
        """随机箱并发同一请求只能抽一次；旧版本307仍按原配方发奖，不能访问新奖池。"""
        self.give(101056)
        request=self.meta(**self.base,action='inventory.open',**self.payload(container_protocol=5))
        with patch('revival.native_containers.secrets.randbelow',return_value=0) as random:
            with DatabasePool(max_workers=2) as pool:
                results=list(pool.map(lambda _:native_features.command(self.s,self.game,request),range(2)))
        self.assertEqual(random.call_count,1)
        self.assertEqual(results[0],results[1])
        self.assertEqual(len(results[0]['grants']),1)
        self.assertEqual(results[0]['draws'][0]['rewards'],catalog()['recipes']['5']['pool'][0]['rewards'])
        with self.assertRaises(Fault):
            self.call('inventory.open',**self.payload(container_protocol=5,rule_version=catalog()['previous_version']))
        self.give(104047)
        old=self.call('inventory.open',**self.payload(rule_version=catalog()['previous_version']))
        self.assertEqual(old['rule_version'],'cn2020-container-compat-v1')
        self.assertEqual({r['item_id'] for r in old['grants']},{104002,104003,104004})

    def test_random_counter_counts_are_increment_only_and_tail_failure_rolls_back(self):
        """五碎片候选表示数量而非五小时；三箱发15且原有7个保持，末尾失败不扣箱或留奖。"""
        self.give(103771,7)
        self.give(103738,3)
        before=self.view('inventory')['items']
        last=len(catalog()['recipes']['291']['pool'])-1
        self.assertEqual(catalog()['recipes']['291']['pool'][last]['rewards'][0],
                         dict(item_id=103771,quantity=5,duration_seconds=None))
        with patch('revival.native_containers.secrets.randbelow',return_value=last):
            with patch('revival.native_actions.event',side_effect=DatabaseError('random box tail')):
                with self.assertRaises(DatabaseError):
                    self.call('inventory.open',**self.payload(container_protocol=291,count=3))
        self.assertEqual(self.view('inventory')['items'],before)
        with patch('revival.native_containers.secrets.randbelow',return_value=last):
            result=self.call('inventory.open',**self.payload(container_protocol=291,count=3))
        self.assertEqual(len(result['grants']),1)
        self.assertEqual(result['grants'][0]['quantity'],15)
        self.assertIsNone(result['grants'][0]['expires_at'])
        self.assertEqual(next(r['quantity'] for r in self.view('inventory')['items'] if r['item_id']==103771),22)

    def test_all_source_pools_and_lost_random_response_recover_same_receipt(self):
        """逐池验证原始候选可入库；随机提交后丢响应经新桥接恢复原奖，不再抽取。"""
        from adapters.cn_current.features import packet,parse
        from tools.native_feature_bridge import NativeFeatureBridge
        from tools.email_launcher import LoginError
        recipes={p:r for p,r in catalog()['recipes'].items() if r.get('mode')=='random_one'}
        self.assertEqual(len(recipes),37)
        for protocol,recipe in recipes.items():
            self.give(recipe['item_id'])
            with patch('revival.native_containers.secrets.randbelow',return_value=len(recipe['pool'])-1):
                result=self.call('inventory.open',**self.payload(container_protocol=int(protocol)))
            self.assertEqual(result['draws'][0]['rewards'],recipe['pool'][-1]['rewards'])
            self.assertEqual(sum(r['quantity'] for r in result['grants']),
                             sum(r['quantity'] for r in recipe['pool'][-1]['rewards']))
        self.give(101056)
        bridge=self.bridge()
        raw=packet(0xa375,dict(ItemId=0,ItemGid=0,FortuneKeyType=5,FortuneKeyCount=1,
                              PredictProp=dict(ItemId=100001,StackNum=999)))
        original=bridge.client.request
        def lose_response(path,data,token):
            """先完成服务端真实事务再丢弃回复，制造未知提交状态。"""
            result=original(path,data,token)
            if path=='/api/game/command':
                raise LoginError('NETWORK')
            return result
        bridge.client.request=lose_response
        with patch('revival.native_containers.secrets.randbelow',return_value=0) as random:
            with self.assertRaises(LoginError):
                bridge.handle(raw)
            bridge.client.request=original
            recovered=NativeFeatureBridge(bridge.inventory,bridge.journal).handle(raw)
        self.assertEqual(random.call_count,1)
        command,result=parse(recovered[-1])
        self.assertEqual((command,result['Result']),(0xa376,1793))
        self.assertEqual(result['Props'][0]['ItemId'],recipes['5']['pool'][0]['rewards'][0]['item_id'])
