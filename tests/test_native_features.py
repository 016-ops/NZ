"""真实MySQL的新原生业务回归；验证资产/社交事务，不能代替客户端协议与界面验收。"""
import copy
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch
from django.db import DatabaseError
from django.db.models import Q
from .test_control_plane import Base
from . import test_native_inventory
from .support import DatabasePool
from revival import native_features as features
from revival.catalogs import with_native_inventory
from revival.native_features_catalog import FEATURE_VERSION, feature_catalog
from revival.native_actions import apply
from revival.service import Fault


class NativeFeatureTests(Base):
    """复用真实游戏票据夹具，使用少量本人资产验证新行为和跨账号隔离。"""
    player_game = test_native_inventory.NativeInventoryTests.player_game

    def setUp(self):
        """每例建立隔离MySQL账号与当前目录，不复用真实玩家或发送邮件。"""
        super().setUp()
        self.s.catalog = with_native_inventory(self.s.catalog)
        self.account, self.player_token, self.game, self.base = self.player_game()
        self.base['feature_version'] = FEATURE_VERSION

    def call(self, action, **data):
        """通过正式游戏命令入口执行，带唯一请求标识和当前功能版本。"""
        return features.command(self.s,self.game,self.meta(**self.base,action=action,**data))

    def view(self, view, **data):
        """通过正式鉴权读入口读取本人视图，不直接拼接客户端成功状态。"""
        return features.query(self.s,self.game,dict(self.base,view=view,**data))

    def give(self, item_id, quantity=1):
        """测试准备通过统一资产账本发放，自动分配稳定GID。"""
        with self.s.db.transaction() as c:
            apply(self.s,c,self.account,[dict(type='item',item_key='cn2020:'+str(item_id),quantity=quantity)],'TEST_FIXTURE')
        return next(x for x in self.view('inventory')['items'] if x['item_id']==item_id)

    def balance(self, amount=1000000):
        """给测试账号准备当前币种；不修改测试外账号，不绕过余额账本。"""
        with self.s.db.transaction() as c:
            apply(self.s,c,self.account,[dict(type='currency',code='cn2020:GP',delta=amount)],'TEST_FIXTURE')

    def rule(self, namespace, definition, enabled=True, expected_revision=0):
        """Owner发布测试规则，签到生产环境仍不自动创建或启用奖励。"""
        return self.command('native.rules.publish',dict(namespace=namespace,definition=definition,enabled=enabled,expected_revision=expected_revision))

    def test_purchase_duplicate_instances_no_capacity_and_idempotency(self):
        """同一永久武器可重复购买形成不同GID；重复提交原请求只能扣款一次。"""
        self.balance()
        lines = [{'commodity_id':200001,'price_index':0}]
        quote = self.view('shop.quote',lines=lines)
        request = self.meta(**self.base,action='shop.purchase',lines=lines,quote=quote)
        first = features.command(self.s,self.game,request)
        self.assertEqual(first,features.command(self.s,self.game,request))
        second = self.call('shop.purchase',lines=lines,quote=quote)
        self.assertNotEqual(first['delta']['items'][0]['gid'],second['delta']['items'][0]['gid'])
        self.assertEqual(len(self.view('inventory')['items']),2)
        self.assertIsNone(self.s.catalog['inventory_limit'])

    def test_purchase_event_failure_rolls_back_money_items_and_receipt(self):
        """最终接收事件失败时，购物车不得保留扣款、GID或订单。"""
        self.balance()
        lines = [{'commodity_id':200001}]
        quote = self.view('shop.quote',lines=lines)
        with patch('revival.native_actions.event',side_effect=DatabaseError('injected event failure')):
            with self.assertRaises(DatabaseError):
                self.call('shop.purchase',lines=lines,quote=quote)
        self.assertEqual(self.view('inventory')['items'],[])
        with self.s.db.transaction() as c:
            self.assertEqual(c.select('wallets',Q(account_id=self.account,currency='cn2020:GP')).fetchone()['balance'],1000000)
            self.assertEqual(c.count('native_receipts').fetchone()[0],0)

    def test_fragment_stack_and_draw_confirmation_are_atomic(self):
        """材料数量累加，开奖扣一次、入库一次；重复确认和并发重试返回同一结果。"""
        self.give(102111,5)
        self.give(102111,3)
        self.assertEqual(self.view('inventory')['items'][0]['quantity'],8)
        rule = self.rule('draw:epic',{'materials':[{'item_id':102111,'quantity':2}],
            'pool':[{'key':'rifle','rewards':[{'item_id':100001,'quantity':1}]}]})
        request = self.meta(**self.base,action='draw.commit',pool='epic',rule_id=rule['id'])
        with DatabasePool(max_workers=2) as pool:
            results = list(pool.map(lambda _:features.command(self.s,self.game,request),range(2)))
        self.assertEqual(results[0],results[1])
        for _ in range(2):
            result = self.call('draw.confirm',receipt_id=results[0]['receipt_id'])
            self.assertEqual(result['award']['key'],'rifle')
        items = self.view('inventory')['items']
        self.assertEqual(next(i for i in items if i['item_id']==102111)['quantity'],6)
        self.assertEqual(len([i for i in items if i['item_id']==100001]),1)

    def test_draw_failure_restores_exhausted_material_binding(self):
        """耗尽碎片会删除实例与绑定，但最后事件故障须将它们连同数量全部恢复。"""
        original = self.give(102111,1)
        rule = self.rule('draw:epic',{'materials':[{'item_id':102111,'quantity':1}],
            'pool':[{'key':'rifle','rewards':[{'item_id':100001}]}]})
        with patch('revival.native_rewards.event',side_effect=DatabaseError('injected event failure')):
            with self.assertRaises(DatabaseError):
                self.call('draw.commit',pool='epic',rule_id=rule['id'])
        self.assertEqual(self.view('inventory')['items'],[original])

    def test_selected_pool_rejects_unknown_choice_without_consumption(self):
        """自选只允许当前奖池中指定数量的奖项，错误选择不能扣材料。"""
        original = self.give(102432,2)
        rule = self.rule('draw:selected',{'selection_count':1,'materials':[{'item_id':102432,'quantity':1}],
            'pool':[{'key':'rifle','rewards':[{'item_id':100001}]}]})
        with self.assertRaises(Fault):
            self.call('draw.commit',pool='selected',rule_id=rule['id'],selected=['unknown'])
        self.assertEqual(self.view('inventory')['items'],[original])

    def test_skin_relationship_and_cross_account_rejected(self):
        """皮肤仅绑定已定义的主体且必须本人持有；主体和皮肤数量均不减少。"""
        pair = next(p for p in feature_catalog()['skins'] if str(p['skin']) in feature_catalog()['items'] and p['skin']!=p['weapon'])
        weapon = self.give(pair['weapon']); skin = self.give(pair['skin'])
        self.call('inventory.skin',gid=weapon['gid'],skin_gid=skin['gid'])
        self.assertEqual(next(i for i in self.view('inventory')['items'] if i['gid']==weapon['gid'])['state']['skin']['item_id'],pair['skin'])
        _,_,other_game,base = self.player_game('other')
        with self.assertRaises(Fault):
            features.command(self.s,other_game,self.meta(**base,feature_version=FEATURE_VERSION,action='inventory.skin',gid=weapon['gid'],skin_gid=skin['gid']))
        self.assertEqual(len(self.view('inventory')['items']),2)

    def test_swallow_reversible_relation_preserves_gids_and_replays(self):
        """原生融合/卸下不删除材料，重登回放真实双方GID；引用中的材料不能被加工消费。"""
        from adapters.cn_current.features import packet,parse
        pair = feature_catalog()['swallow'][0]
        host = self.give(pair['host']); material = self.give(pair['material'])
        bridge=self.bridge()
        fields=dict(OperatorType=1,ItemId=pair['host'],ItemGid=host['gid'],SwollowItemId=pair['material'],SwollowItemGid=material['gid'])
        replies=bridge.handle(packet(0x3932,fields))
        self.assertEqual(parse(replies[0])[1]['Result'],9590)
        remaining = self.view('inventory')['items']
        self.assertEqual({i['gid'] for i in remaining},{host['gid'],material['gid']})
        snapshot=parse(self.bridge().swallow_snapshot())[1]['PlayerKunTaoPetInfo']['KunTaoPetInfoList']
        self.assertEqual(snapshot[0]['SwollowItemGid'],material['gid'])
        with self.assertRaises(Fault):
            with self.s.db.transaction() as c:
                apply(self.s,c,self.account,[dict(type='remove',instance_id=material['instance_id'],quantity=1)],'TEST_CONSUME')
        with patch('revival.native_actions.event',side_effect=DatabaseError('injected fusion detach')):
            with self.assertRaises(DatabaseError):
                self.call('inventory.unswallow',gid=host['gid'],material_gid=material['gid'])
        self.assertEqual(parse(self.bridge().swallow_snapshot())[1]['PlayerKunTaoPetInfo']['Count'],1)
        replies=self.bridge().handle(packet(0x3932,dict(fields,OperatorType=2)))
        self.assertEqual(parse(replies[0])[1]['Result'],9590)
        self.assertEqual(parse(self.bridge().swallow_snapshot())[1]['PlayerKunTaoPetInfo']['Count'],0)
        self.assertEqual({i['gid'] for i in self.view('inventory')['items']},{host['gid'],material['gid']})

    def test_kernel_attachment_parent_persistence_and_cross_account(self):
        """隐藏103379按UI固定关系挂在103428；错误主体/跨账号拒绝，卸下保留两件资产。"""
        import struct
        from revival import native_inventory
        host=self.give(103428);kernel=self.give(103379);wrong=self.give(100001)
        bridge=self.bridge().inventory
        request=struct.pack('>HHHHhQQb',0x3243,0xa008,8,19,0,kernel['gid'],host['gid'],-1)
        self.assertEqual(struct.unpack_from('>H',bridge.equip(request),8)[0],0x8100)
        self.assertEqual(next(i for i in self.view('inventory')['items'] if i['gid']==kernel['gid'])['equipment_slot'],f"native:{host['gid']}:94")
        _,_,other,base=self.player_game('kernel-other')
        for token,parent in ((self.game,wrong['gid']),(other,host['gid'])):
            with self.assertRaises(Fault):
                native_inventory.equip(self.s,token,dict(self.base,operation=0,subject=kernel['gid'],target=parent,location=94))
        self.assertEqual(struct.unpack_from('>H',bridge.equip(struct.pack('>HHHHhQQb',0x3243,0xa008,8,19,1,kernel['gid'],0,0)),8)[0],0x8100)
        self.assertEqual(len(self.view('inventory')['items']),3)

    def test_checkin_disabled_by_default_and_no_repeat_after_rule_change(self):
        """正式签到默认停用；同一天改版也不能重复领，次日重新具备资格。"""
        self.assertFalse(self.view('checkin')['enabled'])
        definition = {'daily':{str(day):[{'item_id':102111,'quantity':1}] for day in range(1,32)}}
        first = self.rule('checkin:default',definition)
        result = self.call('checkin.claim',campaign='default',rule_id=first['id'])
        second = self.rule('checkin:default',definition,expected_revision=1)
        again = self.call('checkin.claim',campaign='default',rule_id=second['id'])
        self.assertEqual(result['rule_id'],again['rule_id'])
        self.assertEqual(self.view('inventory')['items'][0]['quantity'],1)
        # 游戏凭据有期限，跨日边界用同一UTC日中的北京时间零点附近重新设定测试时钟。
        self.assertTrue(self.view('checkin')['claimed_today'])

    def test_friend_delivery_block_and_unrelated_event_ack(self):
        """申请与私聊投递给真实对端，第三方不能确认事件，拉黑后私聊拒绝。"""
        other,_,other_game,other_base = self.player_game('other')
        with self.s.db.transaction() as c:
            peer_id = c.select('accounts',Q(id=other)).fetchone()['game_id']
            my_id = c.select('accounts',Q(id=self.account)).fetchone()['game_id']
        self.call('friend.request',peer_game_id=peer_id)
        other_base['feature_version'] = FEATURE_VERSION
        def other_call(action, **data):
            """模拟第二个真实游戏身份调用业务入口，并非原生双客户端验收。"""
            return features.command(self.s,other_game,self.meta(**other_base,action=action,**data))
        other_call('friend.accept',peer_game_id=my_id)
        self.call('friend.message',peer_game_id=peer_id,message='hello')
        events = features.query(self.s,other_game,dict(other_base,view='events'))
        self.assertEqual(events[-1]['payload']['message'],'hello')
        with self.assertRaises(Fault):
            self.call('events.ack',event_ids=[events[-1]['id']])
        other_call('friend.block',peer_game_id=my_id)
        with self.assertRaises(Fault):
            self.call('friend.message',peer_game_id=peer_id,message='blocked')

    def test_clan_invitation_transfer_and_old_owner_permission(self):
        """邀请需对端接受；移交后旧队长不能再解散，成员退出不会误删队伍。"""
        other,_,game,base = self.player_game('other')
        with self.s.db.transaction() as c:
            peer_id = c.select('accounts',Q(id=other)).fetchone()['game_id']
        self.call('clan.create',name='test-clan')
        invite = self.call('clan.invite',peer_game_id=peer_id)
        features.command(self.s,game,self.meta(**base,feature_version=FEATURE_VERSION,action='clan.accept',application_id=invite['application_id']))
        self.call('clan.transfer',peer_game_id=peer_id)
        with self.assertRaises(Fault):
            self.call('clan.dismiss')
        self.call('clan.leave')
        self.assertIsNone(self.view('clan')['clan'])

    def test_game_api_rejects_player_token_and_changed_retry(self):
        """普通玩家Bearer不能冒充游戏会话；相同幂等键修改载荷必须冲突。"""
        with self.assertRaises(Fault):
            features.query(self.s,self.player_token,dict(self.base,view='capabilities'))
        request = self.meta(**self.base,action='clan.create',name='one')
        features.command(self.s,self.game,request)
        with self.assertRaises(Fault) as error:
            features.command(self.s,self.game,dict(request,name='two'))
        self.assertEqual(error.exception.code,'IDEMPOTENCY_CONFLICT')

    def bridge(self):
        """HTTP边界使用正式鉴权领域入口；网络字节另由当前DLL及实机分别验证。"""
        from tools.native_inventory_bridge import NativeInventoryBridge
        from tools.native_feature_bridge import NativeFeatureBridge
        from tools.email_launcher import LoginError
        case=self
        class Client:
            """将真实业务异常转换为启动器安全错误码，不伪造提交结果。"""
            def request(self, path, data, token):
                """复用正式读、命令和轻量装备入口，保持与HTTP服务同样的授权边界。"""
                try:
                    if path=='/api/game/view':
                        return {'data':features.query(case.s,token,data)}
                    if path=='/api/game/command':
                        return features.command(case.s,token,data)
                    if path=='/api/game/inventory/equip':
                        from revival.native_inventory import equip
                        return equip(case.s,token,data)
                    raise ValueError(path)
                except Fault as error:
                    raise LoginError(error.code) from None
        with self.s.db.transaction() as c:
            game_id=c.select('accounts',Q(id=self.account)).fetchone()['game_id']
        inventory=NativeInventoryBridge(Client(),self.game,self.base['client_sha256'],game_id,paged=True)
        return NativeFeatureBridge(inventory,Path(self.directory.name)/'operations.json')

    def test_native_shop_failure_then_commit_and_incremental_cache(self):
        """余额不足得到商城失败应答，随后同连接付款成功且只增量更新一件；余额不伪造。"""
        from adapters.cn_current.features import packet,parse
        bridge=self.bridge()
        request=packet(0xa505,dict(CommodityList=[dict(CommodityId=200001,PriceIndex=0,Price=12500)],PayType=1))
        rejected=bridge.handle(request)
        self.assertEqual(parse(rejected[0])[1]['Result'],524)
        self.assertEqual(self.view('inventory')['items'],[])
        self.balance()
        replies=bridge.handle(request)
        self.assertEqual(parse(replies[0])[1]['Result'],33280)
        update=parse(replies[1])[1]
        self.assertEqual((update['Count'],update['Props'][0]['OpType']),(1,1))
        self.assertEqual(len(bridge.inventory.items),1)
        self.assertEqual(bridge.inventory.balances['cn2020:GP'],987500)
        bridge.handle(request)
        self.assertEqual(len(self.view('inventory')['items']),1)

    def test_native_unknown_commit_recovery_survives_bridge_restart(self):
        """服务端提交后丢失HTTP响应，重建桥接先查原键，不再购买或扣款。"""
        from adapters.cn_current.features import packet,parse
        self.balance();bridge=self.bridge()
        request=packet(0xa505,dict(CommodityList=[dict(CommodityId=200001,PriceIndex=0,Price=12500)],PayType=1))
        original=bridge.client.request
        def lose_response(path,data,token):
            """只在业务真正提交后注入传输故障，区别于普通事务回滚。"""
            result=original(path,data,token)
            if path=='/api/game/command':
                raise OSError('injected lost response')
            return result
        with patch.object(bridge.client,'request',side_effect=lose_response):
            with self.assertRaises(OSError):
                bridge.handle(request)
        restored=self.bridge()
        self.assertEqual(parse(restored.handle(request)[0])[1]['Result'],33280)
        self.assertEqual(len(self.view('inventory')['items']),1)
        self.assertFalse(restored.journal.exists())

    def test_native_skin_and_social_packets_use_current_result_domains(self):
        """皮肤落库后按当前布局回放，战队成功码必须使用CLAN域而非ZONE域。"""
        from adapters.cn_current.features import packet,parse
        pair=next(p for p in feature_catalog()['skins'] if str(p['skin']) in feature_catalog()['items'] and p['skin']!=p['weapon'])
        weapon=self.give(pair['weapon']);skin=self.give(pair['skin']);bridge=self.bridge()
        replies=bridge.handle(packet(0x37a2,dict(WeaponGID=weapon['gid'],WeaponResID=pair['weapon'],SkinGID=skin['gid'],SkinResId=pair['skin'])))
        self.assertEqual(parse(replies[0])[1]['Result'],33024)
        snapshot=parse(self.bridge().skin_snapshot())[1]
        self.assertEqual(snapshot['EquippedSkinInfo']['SkinDataList'][0]['SkinPropGID'],skin['gid'])
        reply=bridge.handle(packet(0xb001,dict(Uin=bridge.inventory.game_id,ClanName='测试战队')))
        self.assertEqual(parse(reply[0])[1]['Result'],36864)

    def test_pagination_rejects_mixed_versions_and_old_limit_is_removed(self):
        """超过旧10000槽仍可购买，分页中插入资产会使旧版本明确冲突，不拼接半新半旧快照。"""
        from revival.models import Inventory,NativeInventoryBinding
        from revival.catalogs import NATIVE_INVENTORY_VERSION
        # 大型初始仓库仅是测试夹具；实际购买仍走统一账本及幂等事务。
        items=[Inventory(id=f'large-{i:08}',account_id=self.account,item_key='cn2020:100001',quantity=1) for i in range(10001)]
        Inventory.objects.bulk_create(items,batch_size=1000)
        NativeInventoryBinding.objects.bulk_create([NativeInventoryBinding(inventory_id=i.id,rules_version=NATIVE_INVENTORY_VERSION) for i in items],batch_size=1000)
        first=self.view('inventory',limit=200)
        self.assertEqual(len(first['items']),200)
        self.balance()
        lines=[{'commodity_id':200001}];quote=self.view('shop.quote',lines=lines)
        self.call('shop.purchase',lines=lines,quote=quote)
        with self.assertRaises(Fault) as error:
            self.view('inventory',after_gid=first['next_gid'],revision=first['revision'])
        self.assertEqual(error.exception.code,'SNAPSHOT_CHANGED')
        self.assertEqual(len(self.bridge().inventory.items),10002)

    def test_checkin_beijing_midnight_boundary(self):
        """同一UTC日跨北京时间午夜只间隔两秒，两天各领一次且重试不重复。"""
        self.now=int(datetime(2027,1,15,15,59,59,tzinfo=timezone.utc).timestamp())
        from revival.security import otp
        self.owner=self.s.login('owner',self.password,kind='admin',otp_code=otp(self.admin['otp_secret'],self.now//30))['token']
        # 原票据已过期，使用新测试身份，不通过修改会话期限绕过真实鉴权。
        self.account,self.player_token,self.game,self.base=self.player_game('midnight')
        self.base['feature_version']=FEATURE_VERSION
        rule=self.rule('checkin:default',{'daily':{'15':[{'item_id':102111}], '16':[{'item_id':102111}]}})
        first=self.call('checkin.claim',campaign='default',rule_id=rule['id'])
        self.now+=2
        second=self.call('checkin.claim',campaign='default',rule_id=rule['id'])
        self.assertEqual((first['day'],second['day']),('2027-01-15','2027-01-16'))
        self.assertEqual(self.view('inventory')['items'][0]['quantity'],2)

    def test_native_selected_pool_persists_and_pending_result_cannot_be_reset(self):
        """原生选池、重连查询、开奖和确认闭环；待确认时换键抽取及重置均不能重复扣材料。"""
        from adapters.cn_current.features import packet,parse
        self.give(102432,3)
        rule=self.rule('draw:selected',{'selection_count':1,'materials':[{'item_id':102432,'quantity':1}],
            'pool':[{'key':'rifle','rewards':[{'item_id':100001}]}]})
        bridge=self.bridge()
        self.assertEqual(parse(bridge.handle(packet(0xfa18,{}))[0])[1]['Count'],1)
        bridge.handle(packet(0xfa1a,dict(SelectItem=[100001])))
        bridge=self.bridge()
        self.assertEqual(parse(bridge.handle(packet(0xfa1e,{}))[0])[1]['Items'][0]['PropId'],100001)
        first=bridge.handle(packet(0xfa0c,dict(FragmentId=102432,ExchangeId=rule['revision'])))
        self.assertEqual(parse(first[0])[1]['PropId'],100001)
        with self.assertRaises(Fault) as error:
            self.call('draw.reset',rule_id=rule['id'])
        self.assertEqual(error.exception.code,'PENDING_DRAW')
        pending=self.call('draw.commit',pool='selected',rule_id=rule['id'])
        self.assertEqual(next(i for i in self.view('inventory')['items'] if i['item_id']==102432)['quantity'],2)
        self.call('draw.confirm',receipt_id=pending['receipt_id'])
        self.call('draw.reset',rule_id=rule['id'])
        self.assertEqual(self.view('draw',pool='selected')['selected'],[])

    def test_workshop_uses_recipe_material_and_keeps_label_weapon(self):
        """宝物工坊使用配置首条碎片数量，不消费101518标签枪；重放同原生请求不重复合成。"""
        from revival.native_draw_defaults import definitions
        from adapters.cn_current.features import packet,parse
        definition=definitions()['draw:workshop']
        recipe=definition['recipes'][0]
        material=recipe['materials'][0]
        self.give(material['item_id'],material['quantity'])
        label=self.give(101518)
        self.rule('draw:workshop',definition)
        bridge=self.bridge()
        request=packet(0xfa05,{'Index':0})
        first=bridge.handle(request)
        self.assertEqual(parse(first[0])[1]['ExchangeResult'],9473)
        bridge.handle(request)
        items=self.view('inventory')['items']
        self.assertIn(label,items)
        self.assertEqual(len([i for i in items if i['item_id']==recipe['pool'][0]['rewards'][0]['item_id']]),1)
        self.assertFalse(any(i['item_id']==material['item_id'] for i in items))

    def test_native_unsupported_reward_rejected_before_consumption(self):
        """网页允许货币组合奖励，原生单道具消息必须在开奖前拒绝不可表达配置，保留全部材料。"""
        from adapters.cn_current.features import packet,parse
        original=self.give(102111,3)
        rule=self.rule('draw:epic',{'materials':[{'item_id':102111,'quantity':1}],
            'pool':[{'key':'cash','rewards':[{'type':'currency','code':'cn2020:GP','amount':1}]}]})
        reply=self.bridge().handle(packet(0xfa0c,dict(FragmentId=102111,ExchangeId=rule['revision'])))
        self.assertNotEqual(parse(reply[0])[1]['Result'],0x8100)
        self.assertEqual(self.view('inventory')['items'],[original])

    def test_default_rules_validate_all_recipes_without_publishing_checkin(self):
        """当前24奖项和155配方都可校验，初始规则只有五类抽奖，不包含正式签到。"""
        from revival.native_draw_defaults import definitions
        from revival.native_rewards import validate_rule
        rules=definitions()
        self.assertEqual(len(rules),5)
        self.assertEqual(len(rules['draw:workshop']['recipes']),155)
        self.assertEqual([len(rules['draw:'+name]['pool']) for name in ('epic','fine','rare')],[6,9,9])
        for namespace,definition in rules.items():
            validate_rule(self.s,namespace,definition)
        self.assertFalse(self.view('checkin')['enabled'])

    def test_cart_same_material_twice_returns_each_line_and_one_stack(self):
        """购物车两行相同材料返回两行真实GID，不能在已扣费后因重复GID匹配失败而断连。"""
        from adapters.cn_current.features import packet,parse
        offer=feature_catalog()['offers']['201663']
        with self.s.db.transaction() as c:
            apply(self.s,c,self.account,[dict(type='currency',code='cn2020:'+offer['sCommodityType'],delta=1000000)],'TEST_FIXTURE')
        bridge=self.bridge()
        row=dict(CommodityId=offer['nCommodityID'],PriceIndex=0,Price=offer['nPriceArray'][0])
        reply=bridge.handle(packet(0xa505,dict(BuyType=1,CommodityList=[row,row],PayType=1)))
        result=parse(reply[0])[1]
        self.assertEqual(result['Result'],33280)
        gids=[next(p['GID'] for p in r['PropLst'] if p['ItemId']==102111) for r in result['CommodityList']]
        self.assertEqual(gids[0],gids[1])
        self.assertEqual(next(r for r in self.view('inventory')['items'] if r['item_id']==102111)['quantity'],offer['nAvailPreiodArray'][0]*2)

    def test_gift_other_session_incremental_sync_and_local_purchase_keeps_all_changes(self):
        """赠礼入接收者仓库；接收者未轮询前又买东西，连续游标同时补齐礼物和本人购买，不丢变化。"""
        from tools.native_inventory_bridge import NativeInventoryBridge
        from tools.native_feature_bridge import NativeFeatureBridge
        from adapters.cn_current.features import packet,parse
        recipient,_,game,base=self.player_game('recipient')
        with self.s.db.transaction() as c:
            recipient_id=c.select('accounts',Q(id=recipient)).fetchone()['game_id']
            apply(self.s,c,recipient,[dict(type='currency',code='cn2020:GP',delta=1000000)],'TEST_FIXTURE')
        self.balance()
        payer=self.bridge()
        receiver=NativeFeatureBridge(NativeInventoryBridge(payer.client,game,base['client_sha256'],recipient_id,paged=True),Path(self.directory.name)/'recipient.json')
        receiver.friend_snapshot()
        offer=feature_catalog()['offers']['200821']
        self.assertTrue(offer['bGiveAndAskable'][0])
        row=dict(CommodityId=200821,PriceIndex=0,Price=20000)
        receipt=payer.handle(packet(0xa505,dict(BuyType=1,Consignne=recipient_id,CommodityList=[row])))
        self.assertEqual(parse(receipt[0])[1]['Result'],33280)
        self.assertEqual(payer.inventory.items,[])
        own=receiver.handle(packet(0xa505,dict(CommodityList=[row])))
        self.assertEqual(parse(own[0])[1]['Result'],33280)
        self.assertEqual(len(receiver.inventory.items),2)
        self.assertEqual(len(receiver.poll()),0)

    def test_sync_more_than_one_page_does_not_skip_instances(self):
        """超过200变化实例分片补取，不直接把第一片标为最终版本而漏掉余下物品。"""
        bridge=self.bridge()
        for _ in range(11):
            with self.s.db.transaction() as c:
                apply(self.s,c,self.account,[dict(type='item',item_key='cn2020:100001',quantity=1) for _ in range(20)],'TEST_FIXTURE')
        packets=bridge.synchronize()
        self.assertGreaterEqual(len(packets),3)
        self.assertEqual(len(bridge.inventory.items),220)
        self.assertEqual(bridge.synchronize(),[])

    def test_checkin_native_family_does_not_grant_from_rank_award(self):
        """签到只接DailyNotice/活动按钮；排行1112必须拒绝，停用资源锁定且同日多入口仅领取一次。"""
        from adapters.cn_current.features import packet,parse
        bridge=self.bridge()
        self.assertEqual(parse(bridge.handle(packet(0xc119,{}))[0])[1]['IsLock'],1)
        today=self.view('checkin')['today']
        self.rule('checkin:default',{'daily':{str(int(today[-2:])):[{'item_id':102111}]}})
        rejected=bridge.handle(packet(0x1112,dict(Uin=bridge.inventory.game_id,Type=1,Date=int(today[-2:]))))
        self.assertEqual(parse(rejected[0])[1]['Result'],1)
        self.assertEqual(self.view('inventory')['items'],[])
        resource=parse(bridge.handle(packet(0xc119,{}))[0])[1]
        self.assertEqual((resource['IsLock'],resource['Count']),(0,1))
        bridge.handle(packet(0xc107,dict(Type=6,ID=90001601,Stage=1)))
        bridge.handle(packet(0xc117,{}))
        self.assertEqual(self.view('inventory')['items'][0]['quantity'],1)

    def test_native_friend_groups_preserve_remark_and_blacklist(self):
        """原生分组/删除组/黑名单回放真实关系；单改分组不能抹掉备注，超长消息不能进入事件箱。"""
        from adapters.cn_current.features import packet,parse
        other,_,game,base=self.player_game('friend')
        with self.s.db.transaction() as c:
            peer=c.select('accounts',Q(id=other)).fetchone()['game_id']
            own=c.select('accounts',Q(id=self.account)).fetchone()['game_id']
        self.call('friend.request',peer_game_id=peer)
        features.command(self.s,game,self.meta(**base,feature_version=FEATURE_VERSION,action='friend.accept',peer_game_id=own))
        self.call('friend.edit',peer_game_id=peer,remark='备注')
        bridge=self.bridge()
        bridge.handle(packet(0xa31f,dict(GroupName='好友组')))
        bridge.handle(packet(0xa311,dict(FriendUin=peer,NewGroupId=1)))
        row=self.view('friends')['friends'][0]
        self.assertEqual((row['group'],row['remark']),('1','备注'))
        bridge.handle(packet(0xa321,dict(GroupId=1)))
        self.assertEqual(self.view('friends')['friends'][0]['remark'],'备注')
        with self.assertRaises(Fault):
            self.call('friend.message',peer_game_id=peer,message='中'*64)
        bridge.handle(packet(0xa30b,dict(EnemyUin=peer)))
        snapshot=parse(self.bridge().friend_snapshot()[0])[1]
        self.assertEqual((snapshot['FriendCount'],snapshot['EnemyCount']),(0,1))

    def test_native_friend_decision_matches_live_request_and_preserves_unknown(self):
        """实收同意33537建立双向关系；通用成功33536不得当成拒绝，专用拒绝769仅移除待办。"""
        from adapters.cn_current.features import packet,parse
        from tools.native_inventory_bridge import NativeInventoryBridge
        from tools.native_feature_bridge import NativeFeatureBridge
        receiver=self.bridge()
        peer_account,_,game,base=self.player_game('decision')
        with self.s.db.transaction() as c:
            peer=c.select('accounts',Q(id=peer_account)).fetchone()['game_id']
        sender=NativeFeatureBridge(NativeInventoryBridge(receiver.client,game,base['client_sha256'],peer,paged=True),
                                   Path(self.directory.name)/'native-friend-sender.json')
        own=receiver.inventory.game_id
        sender.handle(packet(0xa305,dict(RespondentUin=own)))
        request=next(parse(raw)[1] for raw in receiver.poll() if parse(raw)[0]==0xa306)
        receiver.delivered()
        decision=dict(RequestId=request['RequestId'],ProposerUin=peer,ProposerNickname=request['ProposerNickname'])
        # 畸形决定不能清掉有效申请，且同一连接下一次真实同意仍可继续。
        bad=receiver.handle(packet(0xa307,dict(decision,Result=33536)))
        self.assertEqual(parse(bad[0])[1]['Result'],768)
        self.assertEqual(len(receiver.view('friends')['requests']),1)
        self.assertEqual(receiver.handle(packet(0xa307,dict(decision,Result=33537))),[])
        self.assertEqual(receiver.view('friends')['friends'][0]['status'],'ACCEPTED')
        self.assertEqual(sender.view('friends')['friends'][0]['status'],'ACCEPTED')
        accepted=next(parse(raw)[1] for raw in sender.poll() if parse(raw)[0]==0xa308)
        self.assertEqual(accepted['Result'],33537)
        sender.delivered()
        sender.handle(packet(0xa329,dict(FriendUin=own,Msg='同意后私聊')))
        receiver.next_poll=0
        message=next(parse(raw)[1] for raw in receiver.poll() if parse(raw)[0]==0xa406)
        self.assertEqual(message['ChatMsgBuf'],'同意后私聊')
        receiver.delivered()
        # 第二个申请人在同一连接明确拒绝，不能删除刚建立的其他好友。
        rejected_account,_,rejected_game,rejected_base=self.player_game('refuse')
        with self.s.db.transaction() as c:
            rejected_id=c.select('accounts',Q(id=rejected_account)).fetchone()['game_id']
        reject_sender=NativeFeatureBridge(NativeInventoryBridge(receiver.client,rejected_game,rejected_base['client_sha256'],rejected_id,paged=True),
                                         Path(self.directory.name)/'native-friend-refused.json')
        reject_sender.handle(packet(0xa305,dict(RespondentUin=own)))
        receiver.next_poll=0
        request=next(parse(raw)[1] for raw in receiver.poll() if parse(raw)[0]==0xa306)
        receiver.delivered()
        receiver.handle(packet(0xa307,dict(RequestId=request['RequestId'],Result=769,ProposerUin=rejected_id)))
        self.assertEqual(receiver.view('friends')['requests'],[])
        self.assertEqual(receiver.view('friends')['friends'][0]['game_id'],peer)
        refusal=next(parse(raw)[1] for raw in reject_sender.poll() if parse(raw)[0]==0xa308)
        self.assertEqual(refusal['Result'],769)

    def test_native_clan_identity_queries_and_review_permissions(self):
        """创建后查询真实数字ID/队长/成员；原生申请按持久申请ID审核，普通成员无法晋升自己。"""
        from adapters.cn_current.features import packet,parse
        from tools.native_inventory_bridge import NativeInventoryBridge
        from tools.native_feature_bridge import NativeFeatureBridge
        bridge=self.bridge()
        bridge.handle(packet(0xb001,dict(Uin=bridge.inventory.game_id,ClanName='协议战队')))
        own=self.view('clan')
        clan_id=own['clan']['native_id']
        detail=parse(bridge.handle(packet(0xb009,dict(Uin=bridge.inventory.game_id)))[0])[1]
        self.assertEqual(detail['ClanDetailedInfo']['Clanid'],clan_id)
        search=parse(bridge.handle(packet(0xb005,dict(ClanName='协议')))[0])[1]
        self.assertEqual(search['ClanID'],[clan_id])
        other,_,game,base=self.player_game('applicant')
        with self.s.db.transaction() as c:
            peer=c.select('accounts',Q(id=other)).fetchone()['game_id']
        applicant=NativeFeatureBridge(NativeInventoryBridge(bridge.client,game,base['client_sha256'],peer,paged=True),Path(self.directory.name)/'clan-applicant.json')
        joined=applicant.handle(packet(0xb013,dict(Uin=peer,ClanID=clan_id,JoinMsg='申请')))
        self.assertEqual(parse(joined[0])[1]['Result'],36864)
        pending=parse(bridge.handle(packet(0xb015,dict(Uin=bridge.inventory.game_id)))[0])[1]['ClanApproveList'][0]
        bridge.handle(packet(0xb019,dict(Uin=bridge.inventory.game_id,PlayerInfoList=[dict(IsAgree=1,ReqUin=peer,ReqMsgId=pending['ReqMsgId'])])))
        members=parse(applicant.handle(packet(0xb00b,dict(Uin=peer,IsAll=1)))[0])[1]
        self.assertEqual(members['TotalCount'],2)
        denied=applicant.handle(packet(0xb020,dict(Uin=peer,ObjUin=peer,PlayerNewRole=2)))
        self.assertNotEqual(parse(denied[0])[1]['Result'],36864)
        bridge.handle(packet(0xb02f,dict(Uin=bridge.inventory.game_id,PostIndex=0,Post='测试公告')))
        self.assertEqual(parse(applicant.handle(packet(0xb02d,dict(Uin=peer)))[0])[1]['Post'],'测试公告')
        applicant.handle(packet(0xb024,dict(Uin=peer)))
        self.assertEqual(len(self.view('clan')['members']),1)
