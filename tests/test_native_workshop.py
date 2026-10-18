"""真实MySQL工坊券抽奖回归：免费跨日、十连、退款、故障回滚、断线回执和原生失败恢复。"""
import copy
from unittest.mock import patch
from django.db import DatabaseError
from django.db.models import Q
from .test_control_plane import Base
from . import test_native_features as fixtures
from .support import DatabasePool
from revival.catalogs import with_native_inventory
from revival.native_features_catalog import FEATURE_VERSION
from revival.native_draw_defaults import definitions
from revival import native_features
from revival.native_rewards import validate_rule
from revival.service import Fault


class NativeWorkshopTests(Base):
    """各例隔离MySQL账号，任何发奖只进入测试库；不启用生产签到。"""
    player_game=fixtures.NativeFeatureTests.player_game
    call=fixtures.NativeFeatureTests.call
    view=fixtures.NativeFeatureTests.view
    give=fixtures.NativeFeatureTests.give
    rule=fixtures.NativeFeatureTests.rule
    bridge=fixtures.NativeFeatureTests.bridge

    def setUp(self):
        """发布包含真实配方和小型确定性券池的隔离规则。"""
        super().setUp()
        self.s.catalog=with_native_inventory(self.s.catalog)
        self.account,self.player_token,self.game,self.base=self.player_game()
        self.base['feature_version']=FEATURE_VERSION
        self.definition=definitions()['draw:workshop']
        self.definition['vouchers']['pool']=[dict(key='material',tier=4,free=True,paid=True,
            weight=1,show_id=0,pic_id=0,reward={'item_id':102111,'quantity':2})]
        self.config=self.rule('draw:workshop',self.definition)

    def draw(self,mode='free',count=1):
        """复用正式游戏命令入口，默认申请礼品池单抽。"""
        return self.call('workshop.draw',mode=mode,count=count,rule_id=self.config['id'])

    def test_free_then_paid_and_rule_change_cannot_reset_day(self):
        """免费一次直接入库；确认后第二次需券，规则改版和重登不能重新免费。"""
        first=self.draw()
        self.assertTrue(first['free'])
        self.assertEqual(self.draw()['receipt_id'],first['receipt_id'])
        self.call('workshop.confirm',receipt_id=first['receipt_id'])
        self.config=self.rule('draw:workshop',self.definition,expected_revision=1)
        self.assertFalse(self.view('workshop.state')['free_available'])
        with self.assertRaises(Fault) as error:self.draw()
        self.assertEqual(error.exception.code,'MATERIALS')
        self.give(103745,2)
        second=self.draw()
        self.assertFalse(second['free'])
        self.assertEqual(next(i['quantity'] for i in self.view('inventory')['items'] if i['item_id']==103745),1)
        self.assertEqual(next(i['quantity'] for i in self.view('inventory')['items'] if i['item_id']==102111),4)

    def test_ten_atomic_concurrent_retries_and_tail_failure(self):
        """十连尾部故障回滚十券和全部奖品；两个并发同键只生成一个持久结果。"""
        self.give(103746,10)
        request=self.meta(**self.base,action='workshop.draw',mode='paid',count=10,rule_id=self.config['id'])
        with patch('revival.native_workshop.event',side_effect=DatabaseError('tail failure')):
            with self.assertRaises(DatabaseError):native_features.command(self.s,self.game,request)
        self.assertEqual([(i['item_id'],i['quantity']) for i in self.view('inventory')['items']],[(103746,10)])
        with DatabasePool(max_workers=2) as pool:
            results=list(pool.map(lambda _:native_features.command(self.s,self.game,request),range(2)))
        self.assertEqual(results[0],results[1])
        self.assertEqual(len(results[0]['awards']),10)
        self.assertEqual([(i['item_id'],i['quantity']) for i in self.view('inventory')['items']],[(102111,20)])
        # 不同键在未确认时也返回原结果；跨账号不能确认本人回执。
        self.assertEqual(self.draw('paid',10)['receipt_id'],results[0]['receipt_id'])
        other,_,token,base=self.player_game('work2')
        with self.assertRaises(Fault):
            native_features.command(self.s,token,self.meta(**base,feature_version=FEATURE_VERSION,
                action='workshop.confirm',receipt_id=results[0]['receipt_id']))
        confirmed=self.call('workshop.confirm',receipt_id=results[0]['receipt_id'])
        self.assertEqual(confirmed['awards'],self.call('workshop.confirm',receipt_id=results[0]['receipt_id'])['awards'])

    def test_refund_owned_and_batch_duplicate_preserve_voucher_gid(self):
        """永久重复项退款但不跳过全额持有校验；同批第一件入库后其余九件退券，计数材料不退款。"""
        self.definition['vouchers']['pool'][0]['reward']={'item_id':100001,'quantity':1}
        self.config=self.rule('draw:workshop',self.definition,expected_revision=1)
        voucher=self.give(103746,10)
        result=self.draw('paid',10)
        self.assertEqual(result['refunded_quantity'],9)
        inventory=self.view('inventory')['items']
        saved=next(i for i in inventory if i['item_id']==103746)
        self.assertEqual((saved['gid'],saved['quantity']),(voucher['gid'],9))
        self.assertEqual(len([i for i in inventory if i['item_id']==100001]),1)
        self.call('workshop.confirm',receipt_id=result['receipt_id'])
        with self.assertRaises(Fault):self.draw('paid',10)
        self.give(103746,1)
        again=self.draw('paid',10)
        self.assertEqual(again['refunded_quantity'],10)
        self.assertEqual(again['operations'],[])

    def test_free_day_boundary_and_failure_leave_no_state(self):
        """北京时间午夜之前与之后分别仅一次；提交尾部故障不能占用免费日或产生半份奖品。"""
        # 使用隔离时钟附近的日界线，凭据在测试中显式放宽至下一日而非复用真实凭据。
        boundary=1792166400  # 2026-10-17 00:00:00 +08:00
        with self.s.db.transaction() as c:
            c.update('sessions',Q(subject_id=self.account),{'expires_at':boundary+86400})
            c.update('game_sessions',Q(account_id=self.account),{'expires_at':boundary+86400})
        with patch.object(self.s,'clock',return_value=boundary-1):
            with patch('revival.native_workshop.event',side_effect=DatabaseError('tail failure')):
                with self.assertRaises(DatabaseError):self.draw()
            with self.s.db.transaction() as c:
                self.assertFalse(c.select('native_workshop_states',Q(account_id=self.account)))
            first=self.draw();self.call('workshop.confirm',receipt_id=first['receipt_id'])
        with patch.object(self.s,'clock',return_value=boundary):
            self.assertTrue(self.view('workshop.state')['free_available'])
            second=self.draw()
            self.assertTrue(second['free'])
            self.assertNotEqual(first['receipt_id'],second['receipt_id'])

    def test_guarantee_and_invalid_configuration_rejected_before_cost(self):
        """普通奖项连续九次后第十次落在配置的保底分类；无保底池或非法原生期限发布失败。"""
        pool=self.definition['vouchers']['pool']
        pool.insert(0,dict(pool[0],key='low',tier=1,reward={'item_id':101517,'quantity':1}))
        self.config=self.rule('draw:workshop',self.definition,expected_revision=1)
        self.give(103746,10)
        with patch('revival.native_workshop.secrets.randbelow',return_value=0):result=self.draw('paid',10)
        self.assertEqual([a['entry']['tier'] for a in result['awards']],[1]*9+[4])
        for change in ('tier','duration'):
            invalid=copy.deepcopy(self.definition)
            if change=='tier':invalid['vouchers']['pool']=invalid['vouchers']['pool'][:1]
            else:invalid['vouchers']['pool'][0]['reward']['duration_seconds']=1
            with self.assertRaises(Fault):self.rule('draw:workshop',invalid,expected_revision=2)

    def test_native_pending_reconnect_failure_and_delivery(self):
        """原生免费开奖后断线，查询重放原奖；传输确认后余额不足有失败应答，仍可查询。"""
        from adapters.cn_current.features import packet,parse
        bridge=self.bridge()
        response=bridge.handle(packet(0xfa20,dict(IsFree=1,Lottery10Time=0)))
        self.assertEqual([parse(p)[0] for p in response],[0xfa22,0xfa21])
        self.assertEqual(parse(response[-1])[1]['Count'],1)
        pending=self.view('workshop.state')['pending']['id']
        restarted=self.bridge()
        recovered=restarted.handle(packet(0xfa23,dict(Padding=0)))
        self.assertEqual([parse(p)[0] for p in recovered],[0xfa24,0xfa22,0xfa22,0xfa21])
        self.assertEqual(parse(recovered[0])[1]['Items'][0]['BelongPoolID'],2)
        self.assertEqual(restarted.workshop_receipt,pending)
        restarted.delivered()
        self.assertIsNone(self.view('workshop.state')['pending'])
        failed=restarted.handle(packet(0xfa20,dict(IsFree=0,Lottery10Time=1)))
        self.assertNotEqual(parse(failed[0])[1]['Result'],33024)
        self.assertEqual(parse(restarted.handle(packet(0xfa23,{}))[0])[0],0xfa24)

    def test_all_current_voucher_definitions_validate(self):
        """136条当前候选和155条独立配方均通过服务端校验；不发布测试外配置。"""
        definition=definitions()['draw:workshop']
        self.assertEqual(len(definition['vouchers']['pool']),136)
        validate_rule(self.s,'draw:workshop',definition)
