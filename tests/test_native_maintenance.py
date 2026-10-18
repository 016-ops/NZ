"""真实MySQL维修、出售及续费回归；检查价格、幂等恢复、跨账号与事务末尾回滚。"""
from unittest.mock import patch
from django.db import DatabaseError
from django.db.models import Q
from .test_control_plane import Base
from . import test_native_features as feature_tests
from .support import DatabasePool
from revival import native_features as features
from revival.native_actions import apply
from revival.native_maintenance import catalog,price
from revival.service import Fault
from adapters.cn_current.features import packet,parse


class NativeMaintenanceTests(Base):
    """只复用业务夹具帮助方法，不继承其他测试用例，避免重复执行无关测试。"""
    player_game=feature_tests.NativeFeatureTests.player_game
    call=feature_tests.NativeFeatureTests.call
    view=feature_tests.NativeFeatureTests.view
    give=feature_tests.NativeFeatureTests.give
    balance=feature_tests.NativeFeatureTests.balance
    bridge=feature_tests.NativeFeatureTests.bridge

    def setUp(self):
        """建立自己的Base继承链，真实游戏票据与固定目录仅用于当前隔离测试库。"""
        super().setUp()
        from revival.catalogs import with_native_inventory
        from revival.native_features_catalog import FEATURE_VERSION
        self.s.catalog=with_native_inventory(self.s.catalog)
        self.account,self.player_token,self.game,self.base=self.player_game()
        self.base['feature_version']=FEATURE_VERSION

    def damage(self,item,durability):
        """仅通过资产账本准备损耗，现有皮肤等状态必须保留。"""
        with self.s.db.transaction() as c:
            apply(self.s,c,self.account,[dict(type='native_state',instance_id=item['instance_id'],
                state=dict(durability=durability,level=7))],'TEST_DURABILITY')

    def test_repair_batch_price_replay_and_preserved_state(self):
        """两件缺损一起扣GP并修满；同键重放不重复扣款，满耐久新请求无新资产操作。"""
        self.balance();a=self.give(100003);b=self.give(100009)
        self.damage(a,1080);self.damage(b,0)
        request=self.meta(**self.base,action='inventory.repair',gids=[a['gid'],b['gid']])
        result=features.command(self.s,self.game,request)
        self.assertEqual(result['totals']['cn2020:GP'],4275+6300)
        self.assertEqual(result,features.command(self.s,self.game,request))
        rows=self.view('inventory')['items']
        self.assertTrue(all(x['state']['durability']==2160 and x['state']['level']==7 for x in rows))
        self.assertEqual(self.call('inventory.repair',gids=[a['gid'],b['gid']])['operations'],[])

    def test_repair_batch_failure_is_atomic_and_cross_account_rejected(self):
        """事件末尾失败回滚两件耐久和钱包，外账号GID混入整单拒绝。"""
        self.balance();a=self.give(100003);self.damage(a,0)
        before=self.view('inventory')
        with patch('revival.native_maintenance.event',side_effect=DatabaseError('tail failure')):
            with self.assertRaises(DatabaseError):self.call('inventory.repair',gids=[a['gid']])
        self.assertEqual(before,self.view('inventory'))
        other,_=self.player('other')
        with self.s.db.transaction() as c:
            apply(self.s,c,other,[dict(type='item',item_key='cn2020:100009',quantity=1)],'TEST_OTHER')
            binding=c.table('native_inventory_bindings').filter(inventory__account_id=other).first()
        with self.assertRaises(Fault):self.call('inventory.repair',gids=[a['gid'],binding.id+1000])
        with self.assertRaises(Fault):self.call('inventory.repair',gids=[a['gid'],a['gid']])
        self.assertEqual(before,self.view('inventory'))

    def test_repair_wire_failure_then_success_and_concurrent_replay(self):
        """余额不足后同桥接可继续，随后并发同键只修一次；应答给原生耐久更新原因6。"""
        a=self.give(100003);self.damage(a,1080);bridge=self.bridge()
        raw=packet(0xa50b,dict(PropRenewLst=[a['gid']]))
        self.assertEqual(parse(bridge.handle(raw)[0])[1]['Result'],524)
        self.balance();request=self.meta(**self.base,action='inventory.repair',gids=[a['gid']])
        with DatabasePool(max_workers=2) as pool:
            results=list(pool.map(lambda _:features.command(self.s,self.game,request),range(2)))
        self.assertEqual(results[0],results[1])
        replies=bridge.handle(raw)
        self.assertEqual(parse(replies[0])[1]['Result'],33280)
        with self.s.db.transaction() as c:
            self.assertEqual(c.select('wallets',Q(account_id=self.account,currency='cn2020:GP')).fetchone()['balance'],995725)

    def test_sell_wire_response_loss_recovers_without_second_reward(self):
        """出售后丢应答，重建桥接恢复同一结果；物品已不存在也不能重复发退款。"""
        a=self.give(100003);self.damage(a,1080);bridge=self.bridge()
        raw=packet(0xa363,dict(PropGid=a['gid']));original=bridge.client.request
        def lose(path,data,token):
            """真正提交后才模拟断线，使测试覆盖不确定提交恢复。"""
            result=original(path,data,token)
            if path=='/api/game/command':raise OSError('lost response')
            return result
        with patch.object(bridge.client,'request',side_effect=lose):
            with self.assertRaises(OSError):bridge.handle(raw)
        restored=self.bridge();reply=parse(restored.handle(raw)[0])[1]
        self.assertEqual((reply['Result'],reply['AddGP']),(33024,1069))
        self.assertEqual(self.view('inventory')['items'],[])
        with self.s.db.transaction() as c:
            self.assertEqual(c.select('wallets',Q(account_id=self.account,currency='cn2020:GP')).fetchone()['balance'],1069)

    def test_sell_default_and_equipped_rejected(self):
        """默认小刀和仍装备的可售MP5不得出售；库存、装备和流水保持。"""
        a=self.give(100013)
        with self.assertRaises(Fault):self.call('inventory.sell',gid=a['gid'])
        b=self.give(100003)
        with self.s.db.transaction() as c:c.equip(self.account,'native:10001:0',b['instance_id'])
        with self.assertRaises(Fault):self.call('inventory.sell',gid=b['gid'])
        self.assertEqual(len(self.view('inventory')['items']),2)

    def test_renew_wire_retains_gid_and_expiry_and_rejects_permanent(self):
        """A509续费有限期SCAR，保留GID且延长原期限；永久物品负应答不影响后续请求。"""
        with self.s.db.transaction() as c:
            apply(self.s,c,self.account,[dict(type='item',item_key='cn2020:100027',quantity=1,duration_seconds=3600),
                dict(type='currency',code='cn2020:GP',delta=1000000)],'TEST_RENEW')
        row=self.view('inventory')['items'][0]
        normal=self.view('shop.quote',lines=[dict(commodity_id=200020,price_index=0)])
        quote=self.view('shop.quote',lines=[dict(commodity_id=200020,price_index=0)],renew_gid=row['gid'])
        self.assertEqual(normal,quote)  # 已恢复的商品报价与仓库一致，不向客户端展示假下架入口。
        line=quote['lines'][0];pay={'cn2020:GP':1,'cn2020:TP':2,'cn2020:MP':3}[line['currency']]
        if pay!=1:
            with self.s.db.transaction() as c:apply(self.s,c,self.account,[dict(type='currency',code=line['currency'],delta=1000000)],'TEST_CURRENCY')
        bridge=self.bridge();raw=packet(0xa509,dict(PropGid=row['gid'],CommodityId=200020,PriceIndex=0,PayType=pay,Price=line['price']))
        replies=[parse(message) for message in bridge.handle(raw)]
        self.assertEqual(replies[-1][0],0xa50a)  # 成功提示在仓库重绘之后，不能被随后增量立即关闭。
        self.assertTrue(any(command==0xa00a for command,_ in replies[:-1]))
        reply=replies[-1][1];self.assertEqual(reply['Result'],33280)
        self.assertEqual(reply['AvailableHour'],72)  # 新增72小时，不能包含原有1小时。
        after=self.view('inventory')['items'][0]
        self.assertEqual(after['gid'],row['gid'])
        self.assertEqual(after['expires_at'],row['expires_at']+line['rewards'][0]['duration_seconds'])
        projected=next(prop['Prop'] for command,fields in replies if command==0xa00a
                       for prop in fields.get('Props',[]) if prop['Prop']['GID']==row['gid'])
        self.assertEqual(projected['ObtainTime']+projected['AvailbilityHour']*3600,after['expires_at'])
        self.assertEqual(parse(bridge.handle(raw)[-1])[1],reply)
        # 永久SCAR和不对应当前物品的续费商品均返回业务失败，同一连接仍可读取状态。
        with self.s.db.transaction() as c:
            apply(self.s,c,self.account,[dict(type='item',item_key='cn2020:100027',quantity=1)],'TEST_PERMANENT')
        permanent=next(x for x in self.view('inventory')['items'] if x['expires_at'] is None)
        bridge=self.bridge()
        failed=parse(bridge.handle(packet(0xa509,dict(PropGid=permanent['gid'],CommodityId=200020,
            PriceIndex=0,PayType=pay,Price=line['price'])))[0])[1]
        self.assertEqual(failed['Result'],512)
        with self.assertRaises(Fault):self.view('shop.quote',lines=[dict(commodity_id=200001)],renew_gid=row['gid'])
        self.assertEqual(len(self.view('inventory')['items']),2)

    def test_expired_renew_and_transaction_tail_failures(self):
        """过期实例从当前时间续费；续费和出售在事件末尾失败均回滚钱包、实例及回执。"""
        with self.s.db.transaction() as c:
            apply(self.s,c,self.account,[dict(type='item',item_key='cn2020:100027',quantity=1,duration_seconds=1),
                dict(type='currency',code='cn2020:MP',delta=1000)],'TEST_EXPIRED_RENEW')
        item=self.view('inventory')['items'][0];self.now+=2
        lines=[dict(commodity_id=200020,price_index=0)]
        quote=self.view('shop.quote',lines=lines,renew_gid=item['gid'])
        with patch('revival.native_actions.event',side_effect=DatabaseError('renew tail')):
            with self.assertRaises(DatabaseError):self.call('shop.renew',gid=item['gid'],lines=lines,quote=quote)
        with self.s.db.transaction() as c:
            self.assertEqual(c.select('inventory',Q(id=item['instance_id'])).fetchone()['expires_at'],self.now-1)
            self.assertEqual(c.select('wallets',Q(account_id=self.account,currency='cn2020:MP')).fetchone()['balance'],1000)
            self.assertEqual(c.count('native_receipts').fetchone()[0],0)
        self.call('shop.renew',gid=item['gid'],lines=lines,quote=quote)
        self.assertEqual(self.view('inventory')['items'][0]['expires_at'],self.now+72*3600)
        sale=self.give(100003);before=self.view('inventory')
        with patch('revival.native_maintenance.event',side_effect=DatabaseError('sell tail')):
            with self.assertRaises(DatabaseError):self.call('inventory.sell',gid=sale['gid'])
        self.assertEqual(self.view('inventory'),before)

    def test_restored_offer_purchase_and_sale_window_remain_authoritative(self):
        """恢复SCAR正常购买且重复提交只发一份；未关联隐藏商品和已下架商品仍被拒绝。"""
        from revival.native_features_catalog import feature_catalog
        from revival.native_shop_compat import restored_offers
        restored=set(restored_offers()['offer_ids'])
        hidden=next(int(key) for key,value in feature_catalog()['offers'].items()
                    if not value['bShowCommodity'] and int(key) not in restored)
        with self.assertRaises(Fault):self.view('shop.quote',lines=[dict(commodity_id=hidden)])
        expired=next(key for key in restored if feature_catalog()['offers'][str(key)]['ItemRecallDate']<20200101)
        with self.assertRaises(Fault):self.view('shop.quote',lines=[dict(commodity_id=expired)])
        with self.s.db.transaction() as c:
            apply(self.s,c,self.account,[dict(type='currency',code='cn2020:MP',delta=1000)],'TEST_SHOP_RESTORE')
        lines=[dict(commodity_id=200020,price_index=0)]
        quote=self.view('shop.quote',lines=lines)
        result=self.call('shop.purchase',idempotency_key='restore-scar',lines=lines,quote=quote)
        self.assertEqual(self.call('shop.purchase',idempotency_key='restore-scar',lines=lines,quote=quote),result)
        items=self.view('inventory')['items']
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['expires_at'],self.now+72*3600)
        with self.s.db.transaction() as c:
            self.assertEqual(c.select('wallets',Q(account_id=self.account,currency='cn2020:MP')).fetchone()['balance'],500)
