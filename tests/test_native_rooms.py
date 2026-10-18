"""真实MySQL原生房间事务测试；不注入虚假节点就绪，不代表地图或多人实机通过。"""
import json
from unittest.mock import patch
from django.db import DatabaseError
from django.db.models import Q
from .test_control_plane import Base
from . import test_native_inventory as inventory_fixtures
from . import test_native_features as feature_fixtures
from .support import DatabasePool
from revival.catalogs import with_native_inventory
from revival import native_features as features
from revival.native_features_catalog import FEATURE_VERSION
from revival.native_rooms import VERSION,catalog
from revival.rooms import maintain
from revival.security import token_hash
from revival.service import Fault


class NativeRoomTests(Base):
    """每例通过真实启动票据创建游戏身份；所有账号和表均位于隔离测试库。"""
    player_game=inventory_fixtures.NativeInventoryTests.player_game
    bridge=feature_fixtures.NativeFeatureTests.bridge

    def setUp(self):
        """准备当前资产目录及一份有效游戏会话，不使用真实验收账号。"""
        super().setUp();self.s.catalog=with_native_inventory(self.s.catalog)
        self.account,self.player_token,self.game,self.base=self.player_game()
        self.base.update(feature_version=FEATURE_VERSION,room_rule_version=VERSION)

    def view(self,view='room.current',token=None,base=None,**data):
        """读取本人房间或公开频道；其他账号须显式传自己的游戏凭据。"""
        return features.query(self.s,token or self.game,dict(base or self.base,view=view,**data))

    def call(self,action,token=None,base=None,**data):
        """修改本人当前房间默认携带最新修订；显式旧修订仍用于冲突测试。"""
        token=token or self.game;base=base or self.base
        if action not in ('room.create','room.join') and 'expected_revision' not in data:
            current=self.view(token=token,base=base)
            if current:data['expected_revision']=current['revision']
        return features.command(self.s,token,self.meta(**base,action=action,**data))

    def room(self,**values):
        """空间站沿原目录4人及普通难度；战斗参数由实际地图决定而非测试自报。"""
        data=dict(name='空间站测试',settings=dict(ModeId=8197,MapId=168,SubModeId=4097),fighter_capacity=4,observer_capacity=0)
        data.update(values);return self.call('room.create',**data)

    def other(self,suffix):
        """第二身份仍走正式票据链，不伪造对端Uin或共享游戏令牌。"""
        aid,player,game,base=self.player_game(suffix);base.update(feature_version=FEATURE_VERSION,room_rule_version=VERSION)
        return aid,player,game,base

    def counts(self):
        """同时检查房间、分配、审计、事件、请求与资产，发现事务末尾留下的半份状态。"""
        with self.s.db.transaction() as c:
            return {n:c.table(n).count() for n in ('rooms','room_settings','room_members','native_room_bindings',
                'native_room_presence','matches','match_players','requests','audit_events','native_player_events','inventory','asset_operations')}

    def test_create_retry_keeps_id_and_assets_and_hides_password(self):
        """同一建房并发/重试只生成一个稳定房号，短原生口令慢哈希且不出现在公开快照。"""
        request=self.meta(**self.base,action='room.create',name='空间站测试',settings=dict(ModeId=8197,MapId=168,SubModeId=4097),
                          fighter_capacity=4,observer_capacity=0,password='12345')
        with DatabasePool(max_workers=2) as pool:results=list(pool.map(lambda _:features.command(self.s,self.game,request),range(2)))
        self.assertEqual(results[0],results[1]);room=results[0];before=self.counts()
        self.assertEqual(features.command(self.s,self.game,request),room);self.assertEqual(before,self.counts())
        self.assertGreater(room['native_room_id'],0);self.assertEqual(room['players'][0]['owner'],True)
        self.assertFalse(room['native_ready']);self.assertNotIn('12345',json.dumps(self.view('room.list'),ensure_ascii=False))
        self.assertEqual(self.view()['owner_password'],'12345')
        with self.s.db.transaction() as c:
            hashed=c.select('room_settings').fetchone()['password_hash']
            self.assertTrue(hashed.startswith('scrypt$'));self.assertEqual(c.table('inventory').count(),0)
            self.assertNotIn('12345',c.select('native_room_bindings').fetchone()['sealed_password'])
            self.assertNotIn('12345',c.select('requests',Q(scope='native:'+self.account)).fetchone()['result'])
        for token in (self.player_token,self.owner):
            with self.assertRaises(Fault) as error:self.view(token=token)
            self.assertEqual(error.exception.code,'AUTH')

    def test_password_transfer_is_private_and_bound_to_room_identity(self):
        """移交后的新房主可取得原生通知所需口令，其他成员/频道/历史回执仍不能读取。"""
        room=self.room(password='12345');other=self.other('secret')
        self.call('room.join',token=other[2],base=other[3],native_room_id=room['native_room_id'],password='12345')
        self.assertNotIn('owner_password',self.view(token=other[2],base=other[3]))
        self.call('room.leave')
        self.assertEqual(self.view(token=other[2],base=other[3])['owner_password'],'12345')
        from revival.native_rooms import room_secret
        with self.s.db.transaction() as c:
            sealed=c.select('native_room_bindings').fetchone()['sealed_password']
        with self.assertRaises(Fault):room_secret('different-room',sealed,decrypt=True)

    def test_last_fighter_seat_is_atomic_and_bad_password_preserves_room(self):
        """4人最后一席并发只有一人成功；错误口令不会创建幽灵席位或更改修订。"""
        room=self.room(password='123')
        others=[self.other(str(i)) for i in range(4)]
        before=self.counts()
        with self.assertRaises(Fault) as error:self.call('room.join',token=others[0][2],base=others[0][3],native_room_id=room['native_room_id'],password='bad')
        self.assertEqual(error.exception.code,'ROOM_PASSWORD');self.assertEqual(before,self.counts())
        for other in others[:2]:self.call('room.join',token=other[2],base=other[3],native_room_id=room['native_room_id'],password='123')
        def join(other):
            """竞争者使用独立连接，收集真实业务失败码而非吞掉事务异常。"""
            try:return self.call('room.join',token=other[2],base=other[3],native_room_id=room['native_room_id'],password='123')
            except Fault as error:return error.code
        with DatabasePool(max_workers=2) as pool:results=list(pool.map(join,others[2:]))
        self.assertEqual(sum(isinstance(x,dict) for x in results),1);self.assertIn('ROOM_FULL',results)
        current=self.view();self.assertEqual(len(current['players']),4);self.assertEqual(len({p['slot'] for p in current['players']}),4)

    def test_ready_revision_owner_kick_and_cross_room_reference(self):
        """准备、房主配置及踢人即时检查权限/修订，引用别房不能误操作本人或别人。"""
        room=self.room();other=self.other('peer')
        self.call('room.join',token=other[2],base=other[3],native_room_id=room['native_room_id'])
        with self.assertRaises(Fault) as error:self.call('room.start')
        self.assertEqual(error.exception.code,'NOT_READY')
        with self.assertRaises(Fault):self.call('room.ready',token=other[2],base=other[3],ready=True,native_room_id=room['native_room_id']+1)
        current=self.call('room.ready',token=other[2],base=other[3],ready=True)
        self.assertTrue(all(p['ready'] for p in current['players']))
        with self.assertRaises(Fault) as error:self.call('room.configure',token=other[2],base=other[3],settings=dict(ModeId=8197,MapId=168,SubModeId=4099))
        self.assertEqual(error.exception.code,'ROOM_OWNER')
        with self.assertRaises(Fault) as error:self.call('room.ready',ready=False,expected_revision=1)
        self.assertEqual(error.exception.code,'REVISION_CONFLICT')
        with self.s.db.transaction() as c:peer=c.select('accounts',Q(id=other[0])).fetchone()['game_id']
        current=self.call('room.kick',peer_game_id=peer)
        self.assertEqual(len(current['players']),1);self.assertIsNone(self.view(token=other[2],base=other[3]))

    def test_tail_event_fault_rolls_back_whole_creation(self):
        """建房最后接收者事件失败，房号、席位、口令、幂等、审计和资产均回到原状。"""
        before=self.counts()
        with patch('revival.native_rooms.event',side_effect=DatabaseError('injected room tail')):
            with self.assertRaises(DatabaseError):self.room(password='12345')
        self.assertEqual(before,self.counts());self.assertIsNone(self.view())

    def test_game_lease_revoke_releases_and_transfers_without_parent_logout(self):
        """来源玩家会话仍有效，独立游戏被撤销也应释放席位，剩余真实玩家成为房主。"""
        room=self.room();other=self.other('remaining')
        self.call('room.join',token=other[2],base=other[3],native_room_id=room['native_room_id'])
        with self.s.db.transaction() as c:c.update('game_sessions',Q(digest=token_hash(self.game)),dict(revoked=True))
        result=maintain(self.s);self.assertEqual(result['expired_native_members'],1)
        current=self.view(token=other[2],base=other[3]);self.assertEqual(len(current['players']),1);self.assertTrue(current['players'][0]['owner'])
        self.call('room.leave',token=other[2],base=other[3]);self.assertEqual(self.view('room.list',token=other[2],base=other[3])['total'],0)
        with self.s.db.transaction() as c:self.assertEqual(c.table('native_room_bindings').count(),1)

    def test_start_reserves_frozen_instance_but_cannot_fake_native_ready(self):
        """开局只冻结真实房间和装备；未加载节点不能报告已入局，超时后重新开放可再开局。"""
        self.room();current=self.call('room.start')
        self.assertEqual(current['state'],'ALLOCATING');self.assertFalse(current['native_ready'])
        with self.assertRaises(Fault) as error:self.call('room.in_match',in_match=True)
        self.assertEqual(error.exception.code,'NODE_NOT_READY')
        with self.s.db.transaction() as c:
            match=c.select('matches').fetchone();self.assertEqual(match['state'],'STARTING');self.assertEqual(match['node_token_hash'],'')
            self.assertEqual(json.loads(match['snapshot'])['native_settings']['MapId'],168)
        self.now+=181;maintain(self.s)
        current=self.view();self.assertEqual(current['state'],'OPEN');self.assertIsNone(current['match_id'])
        second=self.call('room.start');self.assertNotEqual(second['match_id'],match['id'])
        self.assertEqual(self.call('room.cancel')['state'],'OPEN')

    def test_settings_are_server_validated_and_missing_map_does_not_allocate(self):
        """服务器拒绝错误人数/模式/地图URL/战斗金钱；缺失的古墓主图不能假分配成功。"""
        for values in (dict(fighter_capacity=2),dict(settings=dict(ModeId=260,MapId=168,SubModeId=4097)),
                       dict(settings=dict(ModeId=8197,MapId=168,SubModeId=4098)),
                       dict(settings=dict(ModeId=8197,MapId=168,SubModeId=4097,InitialMoney=30000)),
                       dict(settings=dict(ModeId=8197,MapId=168,SubModeId=4097,MapString='other'))):
            with self.assertRaises(Fault):self.room(**values)
            self.assertIsNone(self.view())
        missing=catalog()['maps']['128'];self.assertIsNone(missing['main_resource'])
        self.room(settings=dict(ModeId=259,MapId=128,SubModeId=4097))
        with self.assertRaises(Fault) as error:self.call('room.start')
        self.assertEqual(error.exception.code,'UNVERIFIED_RESOURCE');self.assertEqual(self.view()['state'],'OPEN')

    def test_observer_has_no_reward_eligibility_and_owner_can_reconfigure(self):
        """原生观战与战斗席位分别计数；观战者进入冻结名单也没有持久奖励资格。"""
        room=self.room(observer_capacity=2);other=self.other('observer')
        self.call('room.join',token=other[2],base=other[3],native_room_id=room['native_room_id'])
        self.call('room.ready',token=other[2],base=other[3],ready=True,observer=True)
        self.call('room.configure',settings=dict(ModeId=8197,MapId=168,SubModeId=4099))
        current=self.view();self.assertEqual(current['settings']['SubModeId'],4099)
        self.call('room.start')
        with self.s.db.transaction() as c:
            self.assertEqual(c.select('match_players',Q(account_id=other[0])).fetchone()['eligible'],0)
            self.assertEqual(c.select('match_players',Q(account_id=self.account)).fetchone()['eligible'],1)

    def test_native_bridge_create_list_failure_then_continue_and_allocation(self):
        """完整原生字节走真实MySQL；建房进房可回读，失败保持连接，未就绪不发假开局地址。"""
        from adapters.cn_current.features import packet,parse
        bridge=self.bridge()
        listed=bridge.handle(packet(0xa100,dict(RoomCount=32)))
        self.assertEqual(parse(listed[0])[1]['RoomCount'],0)
        request=packet(0xa10a,dict(Name='空间站测试',MatchSettings=dict(ModeId=8197,MapId=168,SubModeId=4097),
            FighterCapacity=4,ObserverCapacity=0,GameServerInfo=dict(Ip=0x08080808,Port=1234)))
        replies=bridge.handle(request)
        self.assertEqual([parse(p)[0] for p in replies],[0xa10b,0xa105])
        info=parse(replies[1])[1]['RoomInfo'];self.assertEqual(info['PlayerInfos'][0]['Flags'],4)
        self.assertEqual(info['PlayerInfos'][0]['State'],9)
        bad=bridge.handle(packet(0xa11e,dict(MatchSettings=dict(ModeId=260,MapId=168,SubModeId=4097))))
        self.assertEqual(parse(bad[0])[1]['MatchSettings']['ModeId'],8197)
        replies=bridge.handle(packet(0xa113,dict(Padding=0)))
        self.assertEqual([parse(p)[0] for p in replies],[0xa114])
        self.assertEqual(parse(replies[0])[1]['Result'],0x8100)
        rejected=bridge.handle(packet(0xa115,dict(PlayMode=0)))
        self.assertEqual(parse(rejected[0])[0],0xa116);self.assertNotEqual(parse(rejected[0])[1]['Result'],0x8100)
        listed=bridge.handle(packet(0xa100,dict(RoomCount=32)))
        self.assertEqual(parse(listed[0])[1]['RoomCount'],1)
        self.assertEqual(parse(bridge.handle(packet(0xa107,dict(Reason=0)))[0])[0],0xa108)
        self.assertEqual(parse(bridge.handle(packet(0xa100,dict(RoomCount=32)))[0])[1]['RoomCount'],0)

    def test_native_create_unknown_commit_recovers_same_room_after_restart(self):
        """真实提交后丢响应，重建桥接查询原操作，不能产生第二房号或重复席位。"""
        from adapters.cn_current.features import packet,parse
        bridge=self.bridge();original=bridge.client.request
        request=packet(0xa10a,dict(Name='重试测试',MatchSettings=dict(ModeId=8197,MapId=168,SubModeId=4097),FighterCapacity=4))
        def lost(path,data,token):
            """仅丢已提交响应，错误不能伪装为提交前失败重新建房。"""
            value=original(path,data,token)
            if path=='/api/game/command':raise OSError('injected room response loss')
            return value
        with patch.object(bridge.client,'request',side_effect=lost):
            with self.assertRaises(OSError):bridge.handle(request)
        before=self.counts();restored=self.bridge();replies=restored.handle(request)
        self.assertEqual(parse(replies[0])[1]['Result'],0x8100);self.assertEqual(before,self.counts())
        self.assertEqual(self.view('room.list')['total'],1);self.assertFalse(restored.journal.exists())

    def test_room_receiver_events_ack_only_after_encoded_delivery(self):
        """两个独立游戏身份的入房/准备通知真实送到对端桥接，sendall确认前事件保持未送达。"""
        from types import SimpleNamespace
        from adapters.cn_current.features import packet,parse
        owner=self.bridge();owner.handle(packet(0xa10a,dict(Name='同步测试',MatchSettings=dict(ModeId=8197,MapId=168,SubModeId=4097),FighterCapacity=4)))
        other=self.other('notify');namespace=SimpleNamespace(s=self.s,account=other[0],game=other[2],base=other[3],
            directory=SimpleNamespace(name=self.directory.name+'/peer'))
        peer=feature_fixtures.NativeFeatureTests.bridge(namespace)
        number=self.view()['native_room_id'];peer.handle(packet(0xa103,dict(RoomId=number)))
        messages=owner.poll();self.assertIn(0xa106,[parse(p)[0] for p in messages])
        with self.s.db.transaction() as c:
            self.assertTrue(c.table('native_player_events').filter(account_id=self.account,kind='room.changed',acknowledged=False).exists())
        owner.delivered()
        with self.s.db.transaction() as c:
            self.assertFalse(c.table('native_player_events').filter(account_id=self.account,kind='room.changed',acknowledged=False).exists())
        peer.handle(packet(0xa110,dict(Padding=0)));owner.next_poll=0
        messages=owner.poll();ready=[parse(p)[1] for p in messages if parse(p)[0]==0xa112]
        self.assertEqual(len(ready),1);self.assertEqual(ready[0]['ReadyState'],9)

    def test_native_camp_seats_observer_and_capacity_failure_then_continue(self):
        """原生两阵营从0/16开始；满阵营失败不掉线，观战真实移席，返回战斗后保持资格。"""
        from adapters.cn_current.features import packet,parse
        self.room(observer_capacity=2);peer=self.other('camp');third=self.other('third')
        number=self.view()['native_room_id']
        self.call('room.join',token=peer[2],base=peer[3],native_room_id=number)
        self.call('room.join',token=third[2],base=third[3],native_room_id=number)
        self.assertEqual([p['slot'] for p in self.view()['players']],[0,1,16])
        from types import SimpleNamespace
        namespace=SimpleNamespace(s=self.s,account=peer[0],game=peer[2],base=peer[3],directory=SimpleNamespace(name=self.directory.name+'/camp'))
        bridge=feature_fixtures.NativeFeatureTests.bridge(namespace)
        rejected=bridge.handle(packet(0xa10d,dict(Camp=1,ObserverCamp=255)))
        self.assertNotEqual(parse(rejected[0])[1]['Result'],0x8100)
        messages=bridge.handle(packet(0xa10d,dict(Camp=2,ObserverCamp=255)))
        self.assertEqual(parse(messages[0])[1]['Result'],0x8100)
        own=next(p for p in self.view(token=peer[2],base=peer[3])['players'] if p['game_id']==bridge.inventory.game_id)
        self.assertEqual(own['slot'],2);self.assertTrue(own['observer'])
        messages=bridge.handle(packet(0xa601,dict(PlayMode=0)))
        self.assertEqual(parse(messages[0])[1]['Result'],0x8100)
        own=next(p for p in self.view(token=peer[2],base=peer[3])['players'] if p['game_id']==bridge.inventory.game_id)
        self.assertEqual(own['slot'],16);self.assertFalse(own['observer'])

    def test_rename_transfer_native_replies_and_settings_ack(self):
        """改名和移交有真实原生应答；旧房主权限立即失效，A330失败后仍能继续读取。"""
        from adapters.cn_current.features import packet,parse
        owner=self.bridge();owner.handle(packet(0xa10a,dict(Name='原名',
            MatchSettings=dict(ModeId=8197,MapId=168,SubModeId=4097),FighterCapacity=4)))
        peer=self.other('newowner');self.call('room.join',token=peer[2],base=peer[3],native_room_id=self.view()['native_room_id'])
        gid=self.view(token=peer[2],base=peer[3])['players'][1]['game_id']
        replies=owner.handle(packet(0xa603,dict(NewOwnerUin=gid)))
        self.assertEqual(parse(replies[0])[0],0xa604);self.assertEqual(parse(replies[0])[1]['Result'],0x8100)
        self.assertEqual(self.view(token=peer[2],base=peer[3])['owner_password'],'')
        denied=owner.handle(packet(0xa210,dict(NewRoomName='不能修改')))
        self.assertNotEqual(parse(denied[0])[1]['Result'],0x8100)
        from types import SimpleNamespace
        namespace=SimpleNamespace(s=self.s,account=peer[0],game=peer[2],base=peer[3],directory=SimpleNamespace(name=self.directory.name+'/newowner'))
        new=feature_fixtures.NativeFeatureTests.bridge(namespace);new.last_room=self.view(token=peer[2],base=peer[3])
        renamed=new.handle(packet(0xa210,dict(NewRoomName='新房名')))
        self.assertEqual([parse(p)[0] for p in renamed],[0xa211,0xa212]);self.assertEqual(self.view()['name'],'新房名')
        rejected=new.handle(packet(0xa11e,dict(MatchSettings=dict(ModeId=260,MapId=168,SubModeId=4097))))
        self.assertEqual(parse(rejected[0])[0],0xa330);self.assertNotEqual(parse(rejected[0])[1]['Result'],0x8100)
        accepted=new.handle(packet(0xa11e,dict(MatchSettings=dict(ModeId=8197,MapId=168,SubModeId=4099))))
        self.assertEqual(parse(accepted[0])[0],0xa330);self.assertEqual(parse(accepted[0])[1]['Result'],0x8100)

    def test_room_invitation_delivery_identity_expiry_and_blocking(self):
        """邀请只送接收者，五分钟权限不能跨账号/跨日重用；拉黑立即取消无口令加入资格。"""
        from adapters.cn_current.features import packet,parse
        # 口令通过领域接口创建，使Linux MySQL回归不冒充Windows DPAPI；实际DPAPI另有363三项。
        self.room(name='邀请测试',password='12345');owner=self.bridge();owner.last_room=self.view()
        peer=self.other('invite');stranger=self.other('stranger')
        with self.s.db.transaction() as c:gid=c.select('accounts',Q(id=peer[0])).fetchone()['game_id']
        number=self.view()['native_room_id'];owner.handle(packet(0xa14b,dict(Uins=[gid])))
        self.assertIsNone(self.view('room.invitation',token=stranger[2],base=stranger[3],native_room_id=number))
        from types import SimpleNamespace
        namespace=SimpleNamespace(s=self.s,account=peer[0],game=peer[2],base=peer[3],directory=SimpleNamespace(name=self.directory.name+'/invite'))
        recipient=feature_fixtures.NativeFeatureTests.bridge(namespace)
        messages=recipient.poll();invited=[parse(p)[1] for p in messages if parse(p)[0]==0xa14c]
        self.assertEqual(len(invited),1);self.assertEqual(invited[0]['RoomInfo']['Id'],number)
        self.assertNotIn('12345',json.dumps(invited));recipient.delivered()
        self.call('room.join',token=peer[2],base=peer[3],native_room_id=number)
        self.call('room.leave',token=peer[2],base=peer[3])
        with self.assertRaises(Fault):self.call('room.join',token=peer[2],base=peer[3],native_room_id=number)
        owner.last=None;owner.handle(packet(0xa14b,dict(Uins=[gid])))
        self.call('friend.block',peer_game_id=gid)
        self.assertIsNone(self.view('room.invitation',token=peer[2],base=peer[3],native_room_id=number))
        with self.assertRaises(Fault):self.call('room.join',token=peer[2],base=peer[3],native_room_id=number)
        self.call('friend.unblock',peer_game_id=gid);self.now+=301
        self.assertIsNone(self.view('room.invitation',token=peer[2],base=peer[3],native_room_id=number))

    def test_resize_reflows_legal_native_seats_and_leaving_revokes_reward(self):
        """八人房出现空位后缩为四人，只搬失效席且不冲突；已冻结成员离开立即失去奖励资格。"""
        room=self.room(settings=dict(ModeId=260,MapId=113,SubModeId=4097),fighter_capacity=8)
        others=[self.other('resize'+str(i)) for i in range(4)]
        for peer in others:self.call('room.join',token=peer[2],base=peer[3],native_room_id=room['native_room_id'])
        # 真实加入顺序是0/16/1/17/2，离开17后2在四人房中必须移到17。
        self.call('room.leave',token=others[2][2],base=others[2][3])
        current=self.call('room.configure',settings=dict(ModeId=8197,MapId=168,SubModeId=4097),fighter_capacity=4)
        self.assertEqual(sorted(p['slot'] for p in current['players']),[0,1,16,17])
        for peer in (others[0],others[1],others[3]):self.call('room.ready',token=peer[2],base=peer[3],ready=True)
        current=self.call('room.start');self.call('room.leave',token=others[3][2],base=others[3][3])
        with self.s.db.transaction() as c:
            self.assertEqual(c.select('match_players',Q(match_id=current['match_id'],account_id=others[3][0])).fetchone()['eligible'],0)
