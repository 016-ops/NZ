"""合并心跳的真实MySQL回归：鉴权隔离、实际在线状态、传输确认与开奖延迟资产同步。"""
from pathlib import Path
from unittest.mock import patch
from django.db.models import Q
from revival import native_features
from revival.catalogs import with_native_inventory
from revival.native_actions import event
from revival.native_features_catalog import FEATURE_VERSION
from revival.service import Fault
from .test_control_plane import Base
from . import test_native_features as fixtures
from revival.service import token_hash


class NativeSessionSyncTests(Base):
    """每例隔离账号，完整走游戏凭据入口，不以伪造在线位代替心跳。"""
    player_game=fixtures.NativeFeatureTests.player_game
    call=fixtures.NativeFeatureTests.call
    view=fixtures.NativeFeatureTests.view
    give=fixtures.NativeFeatureTests.give
    bridge=fixtures.NativeFeatureTests.bridge

    def setUp(self):
        """初始化当前资产目录和首位玩家。"""
        super().setUp()
        self.s.catalog=with_native_inventory(self.s.catalog)
        self.account,self.player_token,self.game,self.base=self.player_game()
        self.base['feature_version']=FEATURE_VERSION

    def test_snapshot_is_private_and_does_not_ack_events(self):
        """别人的事件与资产不进入快照；发送者只有公开身份，读取不把未送达事件提前确认。"""
        other,_,token,base=self.player_game('sync2')
        self.give(102111,2)
        with self.s.db.transaction() as c:
            mine=c.select('accounts',Q(id=self.account)).fetchone()['game_id']
            theirs=c.select('accounts',Q(id=other)).fetchone()['game_id']
            event(self.s,c,self.account,'friend.message',{'from_game_id':theirs,'message':'test'})
            event(self.s,c,other,'friend.message',{'from_game_id':mine,'message':'private'})
        result=self.view('session.sync',after_revision=0,kinds=['friend.message'])
        self.assertEqual(len(result['events']),1)
        self.assertEqual(result['events'][0]['payload']['message'],'test')
        self.assertEqual(set(result['events'][0]['sender']),{'game_id','nickname'})
        self.assertFalse(result['events'][0]['acknowledged'])
        peer=native_features.query(self.s,token,dict(base,feature_version=FEATURE_VERSION,
            view='session.sync',after_revision=0,kinds=['friend.message']))
        self.assertIsNone(peer['inventory']['delta'])
        self.assertEqual(peer['events'][0]['payload']['message'],'private')
        held=self.view('session.sync',include_inventory=False,kinds=['friend.message'])
        self.assertIsNone(held['inventory'])
        self.assertEqual(held['events'],result['events'])
        with self.assertRaises(Fault):self.view('session.sync',include_inventory=1)

    def test_clan_online_follows_heartbeat_and_logout(self):
        """加入战队不会直接变在线；有效游戏心跳变在线，退出来源会话后立即离线。"""
        other,source,token,base=self.player_game('sync3')
        self.call('clan.create',name='心跳验收队')
        with self.s.db.transaction() as c:
            peer=c.select('accounts',Q(id=other)).fetchone()['game_id']
        invite=self.call('clan.invite',peer_game_id=peer)
        scope=dict(base,feature_version=FEATURE_VERSION)
        native_features.command(self.s,token,self.meta(**scope,action='clan.accept',application_id=invite['application_id']))
        self.assertFalse(next(p for p in self.view('clan')['members'] if p['game_id']==peer)['online'])
        native_features.query(self.s,token,dict(scope,view='session.sync',include_inventory=False))
        self.assertTrue(next(p for p in self.view('clan')['members'] if p['game_id']==peer)['online'])
        with self.s.db.transaction() as c:
            c.update('sessions',Q(token_hash=token_hash(source)),{'revoked':1})
            self.s._detach(c,other)
        self.assertFalse(next(p for p in self.view('clan')['members'] if p['game_id']==peer)['online'])

    def test_bridge_poll_uses_one_view_and_respects_animation_hold(self):
        """空闲心跳只用一个HTTP读，开奖延迟期间仍处理社交但不提前投影奖励。"""
        from adapters.cn_current.features import parse
        bridge=self.bridge()
        bridge.friend_snapshot();bridge.clan_snapshot()
        self.give(102111,15)
        bridge.draw_update_until=10**12
        with patch.object(bridge,'view',wraps=bridge.view) as read:
            self.assertEqual(bridge.poll(),[])
            self.assertEqual([call.args[0] for call in read.call_args_list],['session.sync'])
        self.assertEqual(bridge.inventory.items,[])
        bridge.next_poll=0;bridge.draw_update_until=0
        with patch.object(bridge,'view',wraps=bridge.view) as read:
            self.assertEqual([parse(p)[0] for p in bridge.poll()],[0xa00a])
            self.assertEqual([call.args[0] for call in read.call_args_list],['session.sync'])
        self.assertEqual(bridge.inventory.items[0]['quantity'],15)
