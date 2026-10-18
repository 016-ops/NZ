"""MySQL验证自选轮次、不重复抽取、已有道具退料及原生奖池初始化字段。"""
from unittest.mock import patch
from django.db import DatabaseError
from revival.native_features_catalog import FRAGMENTS,FEATURE_VERSION
from revival.catalogs import with_native_inventory
from revival.native_draw_defaults import definitions
from revival.native_rewards import validate_rule
from revival.service import Fault
from . import test_native_features as fixtures
from .test_control_plane import Base


class NativeDrawRoundTests(Base):
    """隔离真实MySQL测试账号，不写正式签到或GM库存。"""
    player_game=fixtures.NativeFeatureTests.player_game
    call=fixtures.NativeFeatureTests.call
    view=fixtures.NativeFeatureTests.view
    give=fixtures.NativeFeatureTests.give
    rule=fixtures.NativeFeatureTests.rule
    bridge=fixtures.NativeFeatureTests.bridge

    def setUp(self):
        """只创建本例账号和当前目录，避免继承执行加工全目录回归。"""
        super().setUp()
        self.s.catalog=with_native_inventory(self.s.catalog)
        self.account,self.player_token,self.game,self.base=self.player_game()
        self.base['feature_version']=FEATURE_VERSION

    def test_two_choices_freeze_twelve_prizes_and_lock_until_finished(self):
        """选择2项补足12格后，重连仍读同一池；抽中不重复，半轮不能重置，结束后可以。"""
        definition=definitions()['draw:selected']
        rule=self.rule('draw:selected',definition)
        selected=[r['key'] for r in definition['pool'][:2]]
        self.give(FRAGMENTS['selected'],15*12)
        result=self.call('draw.select',rule_id=rule['id'],selected=selected)
        self.assertEqual(len(result['pool_keys']),12)
        self.assertTrue(set(selected)<=set(result['pool_keys']))
        self.assertEqual(self.view('draw',pool='selected')['pool_keys'],result['pool_keys'])
        awarded=[]
        for index in range(12):
            receipt=self.call('draw.commit',pool='selected',rule_id=rule['id'])
            # 不同幂等键仍返回相同未确认结果，不能形成下一轮消费。
            self.assertEqual(receipt['receipt_id'],self.call('draw.commit',pool='selected',rule_id=rule['id'])['receipt_id'])
            self.call('draw.confirm',receipt_id=receipt['receipt_id'])
            awarded.append(receipt['award']['key'])
            if index<11:
                with self.assertRaises(Fault):self.call('draw.reset',rule_id=rule['id'])
        self.assertEqual(len(set(awarded)),12)
        self.assertEqual(set(awarded),set(result['pool_keys']))
        with self.assertRaises(Fault):self.call('draw.commit',pool='selected',rule_id=rule['id'])
        self.assertEqual(self.call('draw.reset',rule_id=rule['id'])['status'],'RESET')

    def test_refund_owned_preserves_material_gid_and_rollback_round(self):
        """已有永久奖品净零扣费，保留材料GID，尾部故障不推进轮次或留下回执。"""
        definition=dict(materials=[dict(item_id=102111,quantity=15)],without_replacement=True,refund_owned=True,
                        pool=[dict(key='owned',rewards=[dict(item_id=100001)])])
        rule=self.rule('draw:epic',definition)
        self.give(102111,15);self.give(100001)
        before=self.view('inventory')['items']
        with patch('revival.native_rewards.event',side_effect=DatabaseError('final draw event')):
            with self.assertRaises(DatabaseError):self.call('draw.commit',pool='epic',rule_id=rule['id'])
        self.assertEqual(self.view('draw',pool='epic')['drawn_keys'],[])
        result=self.call('draw.commit',pool='epic',rule_id=rule['id'])
        self.assertTrue(result['refunded'])
        self.assertEqual(result['refunded_materials'],definition['materials'])
        self.assertEqual(before,self.view('inventory')['items'])
        self.call('draw.confirm',receipt_id=result['receipt_id'])
        with self.assertRaises(Fault):self.call('draw.commit',pool='epic',rule_id=rule['id'])

    def test_native_pool_dates_cost_and_choice_types(self):
        """原生初始化返回有效开放区间、实际成本、剩余列表及合法自选类型，避免整页锁死。"""
        from adapters.cn_current.features import parse,packet
        for namespace,definition in definitions().items():
            validate_rule(self.s,namespace,definition)
            self.rule(namespace,definition)
        bridge=self.bridge()
        response=parse(bridge.handle(packet(0xfa16,dict(FragmentId=102111)))[0])[1]
        self.assertEqual(response['Result'],0x8100)  # 当前原生消费者明确要求此码，9473会丢弃整个奖池。
        data=response['Data']
        self.assertGreater(data['EndTime'],data['BeginTime'])
        self.assertEqual(data['PropCount'],6)
        data=parse(bridge.handle(packet(0xfa0a,dict(FragmentId=102111)))[0])[1]
        self.assertEqual(data['Result'],0x8100)
        self.assertEqual(data['VoucherCount'],15)
        self.assertEqual(set(data['PropIds']),{e['rewards'][0]['item_id'] for e in definitions()['draw:epic']['pool']})
        data=parse(bridge.handle(packet(0xfa18,{}))[0])[1]
        self.assertEqual({r['Type'] for r in data['Items']},{1})
        chosen=[entry['PropId'] for entry in data['Items'][:2]]
        data=parse(bridge.handle(packet(0xfa1a,dict(SelectItem=chosen)))[0])[1]
        self.assertEqual(data['Result'],0x8100)
        self.assertEqual(len(data['Items']),12)
        self.assertEqual(sum(r['Type']==1 for r in data['Items']),2)
        data=parse(bridge.handle(packet(0xfa1e,{}))[0])[1]
        self.assertEqual(data['Result'],0x8100)
        self.give(102432,15)
        bridge=self.bridge()
        revision=self.view('draw',pool='selected')['revision']
        responses=bridge.handle(packet(0xfa0c,dict(FragmentId=102432,ExchangeId=revision)))
        self.assertEqual([parse(row)[0] for row in responses],[0xfa0d])
        reply=parse(responses[0])[1]
        self.assertEqual(reply['Result'],0x8100)
        self.assertEqual(reply['Flag'],1)
        self.assertEqual(bridge.synchronize(),[])  # 心跳不得中途重绘开奖弹层。
        acknowledged=bridge.handle(packet(0xfa0a,dict(FragmentId=102432)))
        self.assertEqual([parse(row)[0] for row in acknowledged],[0xa00a,0xfa0b])
        self.assertEqual(parse(acknowledged[0])[1]['Reason'],40)
        material=next(row for row in parse(acknowledged[0])[1]['Props'] if row['Prop']['ItemId']==102432)
        self.assertEqual(material['OpType'],2)  # 本例15片用尽，必须删除该实例。
        pending_ids=parse(acknowledged[1])[1]['PropIds']
        self.assertEqual(len(pending_ids),11)
        self.assertNotIn(reply['PropId'],pending_ids)  # 当前UI用同连接开奖后FA0A收悉，FA0E重放仍须成功。
        confirmed=parse(bridge.handle(packet(0xfa0e,dict(FragmentId=102432,PropId=reply['PropId'],Comfirm=1)))[0])[1]
        self.assertEqual(confirmed['Result'],0x8100)

        remaining=parse(bridge.handle(packet(0xfa0a,dict(FragmentId=102432)))[0])[1]['PropIds']
        self.assertEqual(len(remaining),11)
        self.assertNotIn(reply['PropId'],remaining)

    def test_animation_sync_timeout_recovers_assets_without_confirming_receipt(self):
        """原生弹层被异常关闭且没有确认查询时，延迟到期仍补发资产，开奖回执保留可恢复。"""
        from adapters.cn_current.features import parse,packet
        rule=self.rule('draw:epic',definitions()['draw:epic'])
        self.give(102111,30);bridge=self.bridge()
        bridge.handle(packet(0xfa0c,dict(FragmentId=102111,ExchangeId=rule['revision'])))
        with patch('tools.native_feature_bridge.time.monotonic',return_value=bridge.draw_update_until+1):
            self.assertEqual([parse(row)[0] for row in bridge.synchronize()],[0xa00a])
        self.assertFalse(self.view('receipts',pool='epic')[0]['confirmed'])
        self.assertEqual(bridge.synchronize(),[])

    def test_material_partial_consume_uses_stack_update_and_context_is_private(self):
        """部分扣料使用专用计数通知，合并视图仅返回本人回执，减少原生开奖往返。"""
        from adapters.cn_current.features import parse,packet
        rule=self.rule('draw:epic',definitions()['draw:epic'])
        self.give(102111,30);bridge=self.bridge()
        bridge.handle(packet(0xfa0c,dict(FragmentId=102111,ExchangeId=rule['revision'])))
        context=self.view('draw.state',pool='epic')
        self.assertEqual(len(context['receipts']),1)
        other,_,token,base=self.player_game('ctx2')
        from revival import native_features
        self.assertEqual(native_features.query(self.s,token,dict(base,feature_version=FEATURE_VERSION,view='draw.state',pool='epic'))['receipts'],[])
        response=bridge.handle(packet(0xfa0a,dict(FragmentId=102111)))
        row=next(row for row in parse(response[0])[1]['Props'] if row['Prop']['ItemId']==102111)
        self.assertEqual((row['OpType'],row['Prop']['StackNum']),(4,15))

    def test_native_reconnect_preserves_unseen_result_then_refresh_acknowledges(self):
        """丢线后首次查池不确认，重放同一奖品后客户端刷新确认；全过程只扣一次15片。"""
        from adapters.cn_current.features import parse,packet
        definition=definitions()['draw:epic'];rule=self.rule('draw:epic',definition)
        self.give(102111,30)
        result=self.call('draw.commit',pool='epic',rule_id=rule['id'])
        bridge=self.bridge()
        query=packet(0xfa0a,dict(FragmentId=102111))
        self.assertIn(result['award']['rewards'][0]['item_id'],parse(bridge.handle(query)[0])[1]['PropIds'])
        self.assertFalse(self.view('receipts',pool='epic')[0]['confirmed'])
        response=parse(bridge.handle(packet(0xfa0c,dict(FragmentId=102111,ExchangeId=rule['revision'])))[0])[1]
        self.assertEqual(response['PropId'],result['award']['rewards'][0]['item_id'])
        self.assertEqual(len(parse(bridge.handle(query)[0])[1]['PropIds']),5)
        records=self.view('receipts',pool='epic')
        self.assertEqual(len(records),1);self.assertTrue(records[0]['confirmed'])
        self.assertEqual(next(i for i in self.view('inventory')['items'] if i['item_id']==102111)['quantity'],15)
