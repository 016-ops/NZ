"""原生库存桥接边界回归；夹具来自固定目录，不能替代当前游戏的显示和战斗验收。"""
import copy
import struct
import unittest
from unittest.mock import Mock,patch
from tools.native_inventory_bridge import NativeInventoryBridge, NativeEquipmentUnsupported, equipment_failure_response
from revival.catalogs import native_inventory_manifest, NATIVE_INVENTORY_VERSION, NATIVE_EQUIPMENT_PROJECTION
from revival.native_wallet import WALLET_FIELDS, WALLET_VERSION


class InventoryBridgeTests(unittest.TestCase):
    """防止错账号快照、重复GID、规则漂移及未提交装备被应答为成功。"""
    def test_gain_type_projection_rejects_unknown_boolean_and_overflow_without_cache_change(self):
        """客户端仅接受已知普通48与VIP35；字段损坏不能留下部分库存缓存。"""
        data=self.snapshot();client=Mock();client.request.return_value=data
        bridge=NativeInventoryBridge(client,'fixture','a'*64,10001)
        before=copy.deepcopy(bridge.items)
        for value in (True,-1,0,34,256,'35',None):
            bad=copy.deepcopy(data);bad['items'][0]['gain_type']=value;client.request.return_value=bad
            with self.assertRaisesRegex(ValueError,'INVENTORY_ITEM'):bridge.refresh()
            self.assertEqual(bridge.items,before)
            row=dict(before[0],instance_id='fixture-instance',state={},gain_type=value)
            with self.assertRaisesRegex(ValueError,'DELTA_ITEM'):
                bridge.apply_delta(dict(items=[row],removed_instance_ids=[],wallet=data['wallet'],revision=1))
            self.assertEqual(bridge.items,before)

    def test_calendar_expiration_preserves_absolute_second_across_relogin(self):
        """原生以获得时刻加小时求期限；接近整小时、跨日及重登均不能延长或变成1970年。"""
        from tools.native_prop_time import prop_time
        start=1792148148;expires=start+5*86400
        for now in [start,start+1,start+3601,expires-1,expires]:
            row=prop_time(expires,now)
            self.assertEqual(row['ObtainTime']+row['AvailbilityHour']*3600,expires)
            self.assertLessEqual(row['ObtainTime'],now)
        self.assertEqual(prop_time(None,start)['AvailbilityHour'],-1)
        # 日历型永久不能投影为1970年；当前角色消费以100年以上为永久显示。
        permanent=prop_time(None,start,consume_type=1)
        self.assertEqual(permanent['ObtainTime'],start)
        self.assertGreaterEqual(permanent['AvailbilityHour'],876000)

    def snapshot(self):
        """为全目录建立显式测试GID，仅用于独立编解码测试。"""
        items = [dict(x, gid=i+1001, quantity=1, expires_at=None, equipment_slot=None)
                 for i,x in enumerate(native_inventory_manifest()['items'])]
        return dict(rules_version=NATIVE_INVENTORY_VERSION, game_id=10001, items=items, item_count=len(items),
                    wallet=dict(rules_version=WALLET_VERSION,balances={key:0 for key in WALLET_FIELDS}))

    def compact(self, full):
        """从完整夹具派生权威装备视图，测试内容正确性而非依赖本机网络速度。"""
        rows=[dict(gid=x['gid'],equipment_slot=x['equipment_slot']) for x in full['items'] if x['equipment_slot']]
        return dict(projection=NATIVE_EQUIPMENT_PROJECTION,rules_version=full['rules_version'],game_id=full['game_id'],
                    equipment=rows,equipment_count=len(rows),wallet=copy.deepcopy(full['wallet']))

    def test_pve_role_automatic_slot_is_persisted_and_projected(self):
        """boot73初霜使用自动槽-1，必须解析为根1的64并回读；两阵营默认角色不能盖回PvE选择。"""
        data=self.snapshot();client=Mock();client.request.return_value=data
        bridge=NativeInventoryBridge(client,'fixture','a'*64,10001)
        role=next(x for x in data['items'] if x['item_id']==103696)
        self.assertEqual(role['location'],64)
        after=copy.deepcopy(data)
        next(x for x in after['items'] if x['gid']==role['gid'])['equipment_slot']='native:1:64'
        fields=dict(operation=0,subject=role['gid'],target=1,location=64)
        client.request.return_value=dict(status='SAVED',rules_version=NATIVE_INVENTORY_VERSION,**fields,equipment_state=self.compact(after))
        wire=bridge.equip(struct.pack('>HHHHhQQb',0x3243,0xA008,8,19,0,role['gid'],1,-1))
        self.assertEqual(struct.unpack('>HhQQb',wire[8:]),(0x8100,0,role['gid'],1,64))
        self.assertEqual(struct.unpack_from('>Q',bridge.player_info(b'GM'),79)[0],role['gid'])
        self.assertEqual(client.request.call_count,2)  # 登录一次全量，换装只有一次提交，无全仓回读。

    def test_wallet_maxima_roundtrip_login_and_player_info_and_reject_corruption(self):
        """七类币值在各自字段宽度内往返；错版本、负数、布尔和溢出不能进入游戏。"""
        data=self.snapshot();data['wallet']['balances']={k:v['max'] for k,v in WALLET_FIELDS.items()}
        client=Mock();client.request.return_value=data
        bridge=NativeInventoryBridge(client,'fixture','a'*64,10001)
        self.assertEqual(struct.unpack_from('>ii',bridge.login_response(1792076400),26),(2147483647,2147483647))
        info=bridge.player_info(b'GM')
        for key,spec in WALLET_FIELDS.items():
            self.assertEqual(struct.unpack_from('>'+spec['format'],info,29+spec['offset'])[0],spec['max'])
        for value in (-1,True,2147483648):
            bad=copy.deepcopy(data);bad['wallet']['balances']['cn2020:TP']=value;client.request.return_value=bad
            with self.assertRaisesRegex(ValueError,'WALLET'):
                bridge.refresh()
        bad=copy.deepcopy(data);bad['wallet']['rules_version']='future';client.request.return_value=bad
        with self.assertRaisesRegex(ValueError,'WALLET'):
            bridge.refresh()

    def test_all_traps_negative_root_coexists_with_expert_and_encodes(self):
        """真实-1全能卡根槽不得被解释成角色身体，A009及A006与36专家卡同时保留。"""
        data=self.snapshot()
        card=next(x for x in data['items'] if x['item_id']==102000)
        expert=next(x for x in data['items'] if x['item_id']==101210)
        card['equipment_slot']='native:1:-1';expert['equipment_slot']='native:1:36'
        client=Mock();client.request.return_value=data
        # 使用正式分页投影；旧诊断0/2模式本来不发送期限型卡，不能据此误判卡片丢失。
        with patch.object(NativeInventoryBridge,'read_pages',return_value=data):
            bridge=NativeInventoryBridge(client,'fixture','a'*64,10001,paged=True)
        fields=dict(operation=0,subject=card['gid'],target=1,location=-1)
        client.request.return_value=dict(status='UNCHANGED',rules_version=NATIVE_INVENTORY_VERSION,
                                         **fields,equipment_state=self.compact(data))
        result=bridge.equip(struct.pack('>HHHHhQQb',0x3243,0xa008,8,19,0,card['gid'],1,-1))
        self.assertEqual(struct.unpack('>HhQQb',result[8:]),(0x8100,0,card['gid'],1,-1))
        codec=struct.Struct('>QQiQiqiiiQbiiB')
        rows=[codec.unpack_from(p,13+70*i) for p in bridge.packets()
              for i in range(struct.unpack_from('>H',p,11)[0])]
        for item_id,location in ((102000,-1),(101210,36)):
            self.assertEqual(next(r for r in rows if r[2]==item_id)[9:11],(1,location))

    def test_tp_query_refreshes_real_wallet_and_rejects_foreign_or_failed_read(self):
        """A50E不使用陈旧快照；读取失败或其他账号查询不能发余额成功，通知不携带资产变更。"""
        data=self.snapshot();client=Mock();client.request.return_value=data
        bridge=NativeInventoryBridge(client,'fixture','a'*64,10001)
        request=struct.pack('>HHHHQ',0x3243,0xA50E,8,8,10001)
        after=copy.deepcopy(data);after['wallet']['balances']['cn2020:TP']=2147483647
        client.request.return_value=self.compact(after)
        response=bridge.tp_balance_response(request)
        self.assertEqual(response[:18],struct.pack('>HHHHiHi',0x3243,0xA00A,8,62,1,42,2147483647))
        self.assertEqual(response[18:48],bytes(30))
        self.assertEqual(response[48:],b'\x03'+bytes(21))
        calls=client.request.call_count
        with self.assertRaisesRegex(ValueError,'TP_BALANCE'):
            bridge.tp_balance_response(struct.pack('>HHHHQ',0x3243,0xA50E,8,8,10002))
        self.assertEqual(client.request.call_count,calls)
        client.request.side_effect=RuntimeError('read unavailable')
        with self.assertRaises(RuntimeError):
            bridge.tp_balance_response(request)

    def test_full_catalog_bounded_packets_and_exact_last_marker(self):
        """4323权益中2466个0/2类型分片，每片最多5件，只最后一片标记完成，不静默截断。"""
        client = Mock();client.request.return_value = self.snapshot()
        bridge = NativeInventoryBridge(client, 'fixture', 'a'*64, 10001)
        packets = bridge.packets()
        counts = [struct.unpack_from('>H', p, 11)[0] for p in packets]
        self.assertEqual(sum(counts), 2466)
        self.assertTrue(all(0 < n <= 5 for n in counts))
        self.assertEqual([p[10] for p in packets], [0]*(len(packets)-1)+[1])
        self.assertTrue(all(len(p)==13+70*n for p,n in zip(packets,counts)))
        # 未装备物品的Owner必须为0；根1会使当前客户端将整仓外观都视为装备。
        codec = struct.Struct('>QQiQiqiiiQbiiB')
        self.assertTrue(all(codec.unpack_from(p,13+70*i)[9:11] == (0,-1)
                            for p,n in zip(packets,counts) for i in range(n)))

    def test_equipment_projection_uses_actual_parent_only(self):
        """角色/背包引用根1、武器引用本人持久背包，其余库存保持未装备。"""
        data = self.snapshot()
        role = next(x for x in data['items'] if x['item_id'] == 100014)
        bag = next(x for x in data['items'] if x['item_id'] == 100026)
        weapon = next(x for x in data['items'] if x['location'] == 0 and x['consume_type'] == 0)
        role['equipment_slot'] = 'native:1:10'
        bag['equipment_slot'] = 'native:bag:100026'
        weapon['equipment_slot'] = f"native:{bag['gid']}:0"
        client = Mock();client.request.return_value=data
        packets = NativeInventoryBridge(client,'fixture','a'*64,10001).packets()
        codec = struct.Struct('>QQiQiqiiiQbiiB')
        rows = {r[1]:r for p in packets for r in (codec.unpack_from(p,13+70*i)
                for i in range(struct.unpack_from('>H',p,11)[0]))}
        self.assertEqual(rows[role['gid']][9:11], (1,10))
        self.assertEqual(rows[bag['gid']][9:11], (1,12))
        self.assertEqual(rows[weapon['gid']][9:11], (bag['gid'],0))

    def test_snapshot_rejects_wrong_identity_duplicate_gid_and_catalog_change(self):
        """服务端错配或数据漂移立即失败，不把异常实例发给客户端。"""
        original = self.snapshot()
        bad = copy.deepcopy(original);bad['items'][1]['gid'] = bad['items'][0]['gid']
        altered = copy.deepcopy(original);altered['items'][0]['location'] = 100
        for data in (dict(original,game_id=7),dict(original,rules_version='future'),bad,altered):
            client = Mock();client.request.return_value=data
            with self.assertRaises(ValueError):
                NativeInventoryBridge(client,'fixture','a'*64,10001)

    def test_equipment_requires_matching_committed_ack(self):
        """API提交失败或回显不符时不能生成A009/A00B，不会将未确认变更发给游戏。"""
        client = Mock();client.request.return_value = self.snapshot()
        bridge = NativeInventoryBridge(client,'fixture','a'*64,10001)
        request = struct.pack('>HHHHhQQb',0x3243,0xA008,8,19,0,1001,1,10)
        client.request.return_value={'status':'SAVED','rules_version':NATIVE_INVENTORY_VERSION}
        with self.assertRaisesRegex(ValueError,'UNCONFIRMED'):
            bridge.equip(request)
        client.request.side_effect=RuntimeError('API unavailable')
        with self.assertRaises(RuntimeError):
            bridge.equip(request)

    def test_automatic_location_commits_catalog_slot_and_notifies(self):
        """真实样式的Location=-1必须归一为目录槽位，并核对持久状态后发送两种应答。"""
        data = self.snapshot()
        weapon = next(x for x in data['items'] if x['location']==0 and not x['hidden'] and x['consume_type']==0)
        bag = next(x for x in data['items'] if x['item_id']==100026)
        client = Mock();client.request.return_value=data
        bridge = NativeInventoryBridge(client,'fixture','a'*64,10001)
        after = copy.deepcopy(data)
        next(x for x in after['items'] if x['gid']==weapon['gid'])['equipment_slot']=f"native:{bag['gid']}:0"
        fields = dict(operation=0,subject=weapon['gid'],target=bag['gid'],location=0)
        client.request.return_value=dict(status='SAVED',rules_version=NATIVE_INVENTORY_VERSION,**fields,equipment_state=self.compact(after))
        request=struct.pack('>HHHHhQQb',0x3243,0xA008,8,19,0,weapon['gid'],bag['gid'],-1)
        response=bridge.equip(request)
        self.assertEqual(client.request.call_args_list[-1].args[1]['location'],0)
        self.assertEqual(struct.unpack('>HhQQb',response[8:]),(0x8100,0,weapon['gid'],bag['gid'],0))
        self.assertEqual(bridge.last_notification,struct.pack('>HHHH',0x3243,0xA00B,8,19)+response[10:])

    def test_empty_backpack_business_failure_keeps_bridge_usable(self):
        """实机GID5377→Target0失败必须转换A009；认证失败仍拒绝，普通失败不产生装备通知。"""
        from tools.email_launcher import LoginError
        data=self.snapshot();client=Mock();client.request.return_value=data
        bridge=NativeInventoryBridge(client,'fixture','a'*64,10001)
        weapon=next(x for x in data['items'] if x['item_id']==104412)
        request=struct.pack('>HHHHhQQb',0x3243,0xa008,8,19,0,weapon['gid'],0,-1)
        client.request.side_effect=LoginError('NATIVE_EQUIPMENT')
        with self.assertRaises(NativeEquipmentUnsupported) as rejected:bridge.equip(request)
        self.assertEqual(struct.unpack_from('>H',equipment_failure_response(request,rejected.exception),8)[0],0x0109)
        self.assertIsNone(bridge.last_notification)
        client.request.side_effect=LoginError('AUTH')
        with self.assertRaises(LoginError):bridge.equip(request)
        client.request.side_effect=None;client.request.return_value=data;bridge.refresh()
        self.assertEqual(len(bridge.items),4323)

    def test_automatic_location_rejects_unknown_or_root_component(self):
        """不存在GID或把隐藏角色组件挂到根1时直接拒绝；正常组件需匹配角色父级。"""
        data=self.snapshot();client=Mock();client.request.return_value=data
        bridge=NativeInventoryBridge(client,'fixture','a'*64,10001)
        hidden=next(x for x in data['items'] if x['hidden'] and x['item_id'] not in (100013,100026,100063))
        for gid in (999999999,hidden['gid']):
            with self.assertRaisesRegex(ValueError,'UNVERIFIED_NATIVE_AUTOMATIC_LOCATION'):
                bridge.equip(struct.pack('>HHHHhQQb',0x3243,0xA008,8,19,0,gid,1,-1))
        self.assertEqual(client.request.call_count,1)
        self.assertIsNone(bridge.last_notification)

    def test_backpack_automatic_slot_uses_independent_root_membership(self):
        """重放boot78的背包请求结构；确认已有双包仍挂接，隐藏默认包可重复选择且无需全库存回读。"""
        data=self.snapshot()
        for row in data['items']:
            if row['item_id'] in (100026,100063):
                row['equipment_slot']=f"native:bag:{row['item_id']}"
        client=Mock();client.request.return_value=data
        bridge=NativeInventoryBridge(client,'fixture','a'*64,10001)
        for item_id in (100037,100026,100063):
            item=next(x for x in data['items'] if x['item_id']==item_id)
            item['equipment_slot']=f'native:bag:{item_id}'
            fields=dict(operation=0,subject=item['gid'],target=1,location=12)
            client.request.return_value=dict(status='SAVED',rules_version=NATIVE_INVENTORY_VERSION,
                **fields,equipment_state=self.compact(data))
            response=bridge.equip(struct.pack('>HHHHhQQb',0x3243,0xA008,8,19,0,item['gid'],1,-1))
            self.assertEqual(struct.unpack('>HhQQb',response[8:]),(0x8100,0,item['gid'],1,12))
            self.assertIsNotNone(bridge.last_notification)
        self.assertEqual(client.request.call_count,4)
        self.assertEqual({x['item_id'] for x in bridge.items if x['equipment_slot']},{100026,100037,100063})

    def test_attachment_wrong_parent_fails_without_commit_or_state_change(self):
        """核心不能挂在根角色上；失败后下一次合法换装仍可提交。"""
        data=self.snapshot();client=Mock();client.request.return_value=data
        bridge=NativeInventoryBridge(client,'fixture','a'*64,10001)
        attachment=next(x for x in data['items'] if x['item_id']==103379)
        self.assertEqual(attachment['location'],94)
        original=copy.deepcopy(bridge.items)
        for gid,location,code in ((attachment['gid'],-1,0x109),(attachment['gid'],94,0x109),(999999,-1,0x108)):
            request=struct.pack('>HHHHhQQb',0x3243,0xA008,8,19,0,gid,1,location)
            with self.assertRaises(NativeEquipmentUnsupported) as caught:
                bridge.equip(request)
            response=equipment_failure_response(request,caught.exception)
            self.assertEqual(struct.unpack('>HHHHH',response[:10]),(0x3243,0xA009,8,21,code))
            self.assertEqual(response[10:],request[8:])
            self.assertEqual(bridge.items,original)
            self.assertIsNone(bridge.last_notification)
        self.assertEqual(client.request.call_count,1)
        role=next(x for x in data['items'] if x['item_id']==100014)
        after=copy.deepcopy(data)
        next(x for x in after['items'] if x['gid']==role['gid'])['equipment_slot']='native:1:10'
        fields=dict(operation=0,subject=role['gid'],target=1,location=10)
        client.request.return_value=dict(status='SAVED',rules_version=NATIVE_INVENTORY_VERSION,
            **fields,equipment_state=self.compact(after))
        response=bridge.equip(struct.pack('>HHHHhQQb',0x3243,0xA008,8,19,0,role['gid'],1,-1))
        self.assertEqual(struct.unpack_from('>H',response,8)[0],0x8100)

    def test_committed_ack_without_matching_readback_is_not_notified(self):
        """即使API声称SAVED，回读未装备也不能向游戏确认成功，供崩溃/重试边界定位。"""
        data=self.snapshot();client=Mock();client.request.return_value=data
        bridge=NativeInventoryBridge(client,'fixture','a'*64,10001)
        role=next(x for x in data['items'] if x['item_id']==100014)
        fields=dict(operation=0,subject=role['gid'],target=1,location=10)
        client.request.return_value=dict(status='SAVED',rules_version=NATIVE_INVENTORY_VERSION,**fields,equipment_state=self.compact(data))
        with self.assertRaisesRegex(ValueError,'READBACK_MISMATCH'):
            bridge.equip(struct.pack('>HHHHhQQb',0x3243,0xA008,8,19,0,role['gid'],1,10))
        self.assertIsNone(bridge.last_notification)

    def test_compact_state_rejects_foreign_duplicate_slot_and_partial_corruption(self):
        """轻量结果不能引入外部GID、重复位置、错父对象或坏钱包；拒绝时缓存完全不变。"""
        data=self.snapshot();role=next(x for x in data['items'] if x['item_id']==103696)
        role['equipment_slot']='native:1:64';client=Mock();client.request.return_value=data
        bridge=NativeInventoryBridge(client,'fixture','a'*64,10001)
        valid=self.compact(data);variants=[]
        for key,value in (('game_id',7),('rules_version','future'),('projection','future'),('equipment_count',2)):
            bad=copy.deepcopy(valid);bad[key]=value;variants.append(bad)
        for row in (dict(gid=999999,equipment_slot='native:1:64'),dict(gid=role['gid'],equipment_slot='native:1001:64')):
            bad=copy.deepcopy(valid);bad['equipment']=[row];variants.append(bad)
        bad=copy.deepcopy(valid);bad['equipment']*=2;bad['equipment_count']=2;variants.append(bad)
        other=next(x for x in data['items'] if x['item_id']==103691)
        bad=copy.deepcopy(valid);bad['equipment'].append(dict(gid=other['gid'],equipment_slot='native:1:64'));bad['equipment_count']=2;variants.append(bad)
        bad=copy.deepcopy(valid);bad['wallet']['balances']['cn2020:TP']=-1;variants.append(bad)
        for candidate in variants:
            with self.assertRaises(ValueError):
                bridge.apply_equipment_state(candidate)
            self.assertEqual(bridge.items,data['items'])
            self.assertEqual(bridge.balances,data['wallet']['balances'])
        # 完整空装备快照才允许移除旧位置；不可由请求本身推导。
        cleared=copy.deepcopy(valid);cleared.update(equipment=[],equipment_count=0)
        bridge.apply_equipment_state(cleared)
        self.assertFalse(any(x['equipment_slot'] for x in bridge.items))
