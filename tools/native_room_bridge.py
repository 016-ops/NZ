"""把原生频道/房间消息接到游戏凭据业务；开局通知必须等真实原生节点，不填假DS地址。"""
import copy
from adapters.cn_current.features import metadata,packet
from revival.native_features_catalog import ROOM_VERSION as VERSION
from tools.email_launcher import LoginError

ROOM_COMMANDS={0xa100,0xa103,0xa104,0xa107,0xa10a,0xa10d,0xa110,0xa111,0xa113,0xa115,
               0xa117,0xa11c,0xa11e,0xa120,0xa12e,0xa13d,0xa14b,0xa210,0xa601,0xa603}
ROOM_ERRORS={'ALREADY_IN_ROOM','ROOM_SETTINGS','ROOM_FULL','ROOM_PASSWORD','ROOM_SESSION','ROOM_MEMBER',
             'ROOM_STATE','ROOM_OWNER','REVISION_CONFLICT','NOT_READY','NODE_NOT_READY'}
C=metadata()['constants']


class NativeRoomMessages:
    """继承方提供已鉴权view/commit和本人game_id；不从客户端字段选择其他账号或服务器。"""

    def room_view(self):
        """取得当前本人公开房间与仅房主可见口令，HTTP响应不写普通诊断日志。"""
        return self.view('room.current',room_rule_version=VERSION)

    def room_commit(self,action,current=None,**values):
        """非建房/入房按实际修订提交，回执仍走统一持久幂等及未知结果恢复。"""
        if action not in ('room.create','room.join'):
            current=current or self.room_view()
            if not current:raise LoginError('ROOM_MEMBER')
            values.update(expected_revision=current['revision'],native_room_id=current['native_room_id'])
        return self.commit(action,room_rule_version=VERSION,**values)

    def room_player(self,value):
        """使用当前原脚本状态8/9/10/12及房主标志4；不伪造VIP、战队或战斗力。"""
        state=12 if value['in_match'] else 10 if value['observer'] and value['ready'] else 9 if value['ready'] else 8
        return dict(Uin=value['game_id'],NickName=value['nickname'],State=state,
                    Flags=4 if value['owner'] else 0,SeatIndex=value['slot'])

    def room_wire(self,current,*,basic=False):
        """从同一服务端事实生成列表/入房结构，不混用旧短房号与真实64位房号。"""
        players=current['players'];fighters=sum(not p['observer'] for p in players)
        result=dict(DisplayeId=current['display_id'],QQTalkRoomId=0,Name=current['name'],
            MatchSettings=current['settings'],FighterCapacity=current['fighter_capacity'],
            ObserverCapacity=current['observer_capacity'],FighterCount=fighters,
            ObserverCount=len(players)-fighters,ClanName='')
        if basic:
            result.update(Id=current['native_room_id'],OwnerName=next((p['nickname'] for p in players if p['owner']),''))
        else:
            result.update(RoomId=current['native_room_id'],SubChannelId=current['sub_channel'],
                          PlayerInfos=[self.room_player(p) for p in players])
        return result

    def room_handle(self,command,fields):
        """执行真实列表、建房、加入、退出和准备；成功分配应答不直接通知进入一个未就绪的游戏。"""
        success=C['ZONE_ERR_SUCC']
        if command==0xa100:
            f=fields['FilterInfo']
            found=self.view('room.list',room_rule_version=VERSION,start=max(0,fields['StartIndex']),
                count=min(32,max(1,fields['RoomCount'])),mode_id=max(0,f['ModeId']),map_id=max(0,f['MapId']))
            return [packet(0xa102,dict(Result=success,TotalRoomNum=min(32767,found['total']),
                RoomInfos=[self.room_wire(r,basic=True) for r in found['rooms']]))]
        if command==0xa10a:
            # GameServerInfo来自不可信客户端，仅保留编码核验，绝不作为SSH/游戏的目标。
            current=self.room_commit('room.create',name=fields['Name'],settings=fields['MatchSettings'],
                fighter_capacity=fields['FighterCapacity'],observer_capacity=fields['ObserverCapacity'],password=fields['Password'])
            self.last_room=copy.deepcopy(current)
            return [packet(0xa10b,dict(Result=success)),packet(0xa105,dict(Result=success,RoomInfo=self.room_wire(current)))]
        if command in (0xa103,0xa120):
            number=fields['RoomId']
            if command==0xa120 and not number:
                peer=self.view('room.peer',room_rule_version=VERSION,peer_game_id=fields['FriendUin'])
                if not peer:raise LoginError('NOT_FOUND')
                number=peer['native_room_id']
            current=self.room_commit('room.join',native_room_id=number,password=fields['Password'])
            self.last_room=copy.deepcopy(current)
            response=0xa105 if command==0xa103 else 0xa121
            return [packet(response,dict(Result=success,RoomInfo=self.room_wire(current)))]
        if command==0xa104:
            requested=fields['MatchSettings'];choices=self.view('room.list',room_rule_version=VERSION,
                mode_id=max(0,requested['ModeId']),map_id=max(0,requested['MapId']),start=0,count=32)
            available=[r for r in choices['rooms'] if r['state']=='OPEN' and not r['password_required'] and
                sum(not p['observer'] for p in r['players'])<r['fighter_capacity'] and
                r['settings']['SubModeId']==requested['SubModeId']]
            if available:
                current=self.room_commit('room.join',native_room_id=available[0]['native_room_id'],password='')
            else:
                rules=self.view('room.catalog',room_rule_version=VERSION)
                requested=dict(requested)
                if requested['MapId']<=0:
                    permitted=[r['map_id'] for r in rules['maps'].values() if r['mode_id']==requested['ModeId'] and
                        requested['SubModeId'] in r['submodes'] and r['main_resource'] and
                        (not fields['MapIds'] or r['map_id'] in fields['MapIds'])]
                    if not permitted:raise LoginError('UNVERIFIED_RESOURCE')
                    requested['MapId']=min(permitted)
                current=self.room_commit('room.create',name=fields['RoomName'] or '原生房间',settings=requested,
                    fighter_capacity=fields['FighterCapacity'],observer_capacity=fields['ObserverCapacity'],password='')
            self.last_room=copy.deepcopy(current)
            return [packet(0xa105,dict(Result=success,RoomInfo=self.room_wire(current)))]
        if command==0xa12e:
            if fields['SubChannelId']!=1:raise LoginError('ROOM_SETTINGS')
            return [packet(0xa12f,dict(Result=success,SubChannelId=1))]
        current=self.room_view()
        if not current:raise LoginError('ROOM_MEMBER')
        own=next(p for p in current['players'] if p['game_id']==self.inventory.game_id)
        if command==0xa107:
            self.room_commit('room.leave',current);self.last_room=None
            return [packet(0xa108,dict(Result=success,Reason=fields['Reason']))]
        if command in (0xa110,0xa111):
            updated=self.room_commit('room.ready',current,ready=not own['ready'] or own['observer']!=(command==0xa111),observer=command==0xa111)
            return self.room_snapshot(updated)
        if command==0xa10d:
            # GfxMovie_TGBase与当前TDR同时证实Regular=1/Rebel=0/Observer=2、ObserverNone=-1。
            camp=fields['Camp'];observer_camp=fields['ObserverCamp']
            # 原脚本-1在TDR无符号byte线上表示255；不能把负整数交给编码器。
            if camp not in (0,1,2) or observer_camp not in (255,0,1):raise LoginError('ROOM_SETTINGS')
            values=dict(ready=False,observer=camp==2)
            if camp!=2:values['camp']=camp
            elif observer_camp!=255:values['camp']=1 if observer_camp==0 else 0
            updated=self.room_commit('room.ready',current,**values)
            return [packet(0xa10e,dict(Result=success)),*self.room_snapshot(updated)]
        if command==0xa11e:
            updated=self.room_commit('room.configure',current,settings=fields['MatchSettings'])
            return [packet(0xa330,dict(Result=success,MatchSettings=updated['settings'])),*self.room_snapshot(updated)]
        if command==0xa210:
            updated=self.room_commit('room.rename',current,name=fields['NewRoomName'])
            return [packet(0xa211,dict(Result=success)),*self.room_snapshot(updated)]
        if command==0xa603:
            updated=self.room_commit('room.transfer',current,peer_game_id=fields['NewOwnerUin'])
            return [packet(0xa604,dict(Result=success)),*self.room_snapshot(updated)]
        if command==0xa14b:
            self.room_commit('room.invite',current,peer_game_ids=fields['Uins'])
            return []  # 原消息无应答，接收者通过持久事件得到A14C；不能虚构一个成功包。
        if command==0xa601:
            if fields['PlayMode'] not in (0,1):raise LoginError('ROOM_SETTINGS')
            updated=self.room_commit('room.ready',current,ready=False,observer=bool(fields['PlayMode']))
            return [packet(0xa602,dict(Result=success)),*self.room_snapshot(updated)]
        if command==0xa13d:
            updated=self.room_commit('room.kick',current,peer_game_id=fields['PlayerUin'])
            return [packet(0xa13e,dict(Result=success)),*self.room_snapshot(updated)]
        if command==0xa113:
            updated=self.room_commit('room.start',current)
            return [packet(0xa114,dict(Result=success)),*self.room_snapshot(updated)]
        if command==0xa115:
            # 不使用资源上传完成、控制面STARTING或客户端地址拼接一个虚假DS应答。
            raise LoginError('NODE_NOT_READY')
        if command in (0xa117,0xa11c):
            updated=self.room_commit('room.in_match',current,in_match=bool(fields['IsInMatch']) if command==0xa11c else False)
            return ([packet(0xa118,dict(Result=success))] if command==0xa117 else [])+self.room_snapshot(updated)
        raise ValueError('UNBOUND_ROOM_COMMAND')

    def room_failure(self,command,fields,code):
        """普通业务失败对应各自失败应答；无Result通知只回读真实状态，认证/损坏不在此掩盖。"""
        result=C[{'ROOM_PASSWORD':'ZONE_ROOM_PWD_ERROR','ROOM_FULL':'ZONE_ROOM_ROOMFULL',
            'NOT_FOUND':'ZONE_FAIL_BADROOMID','ALREADY_IN_ROOM':'ZONE_ROOM_ALLREADY_IN_ROOM',
            'ROOM_MEMBER':'ZONE_ROOM_PLAYER_NOT_INROOM','NOT_READY':'ZONE_GAME_NO_PLAYER_READY_TO_FIGHT',
            'NODE_NOT_READY':'ZONE_GAME_CANNOT_FIND_DSAGENT'}.get(code,'ZONE_ERR_FAIL')]
        responses={0xa100:0xa102,0xa103:0xa105,0xa104:0xa105,0xa107:0xa108,0xa10a:0xa10b,
            0xa10d:0xa10e,0xa113:0xa114,0xa115:0xa116,0xa117:0xa118,0xa120:0xa121,0xa12e:0xa12f,0xa13d:0xa13e,
            0xa210:0xa211,0xa601:0xa602,0xa603:0xa604}
        if command in responses:
            values=dict(Result=result)
            if command==0xa107:values['Reason']=fields['Reason']
            if command==0xa12e:values['SubChannelId']=fields['SubChannelId']
            return [packet(responses[command],values)]
        current=self.room_view()
        if not current:return []
        if command==0xa11e:return [packet(0xa330,dict(Result=result,MatchSettings=current['settings']))]
        own=next(p for p in current['players'] if p['game_id']==self.inventory.game_id)
        if command in (0xa110,0xa111):return [packet(0xa112,dict(SeatIndex=own['slot'],ReadyState=self.room_player(own)['State']))]
        return self.room_snapshot(current)

    def room_snapshot(self,current):
        """仅发实际变化的成员/准备/房主/配置；失去席位时发送真实离房，不下载全仓或冒充开局。"""
        previous=self.last_room;packets=[]
        if previous is None:
            if current:packets.append(packet(0xa105,dict(Result=C['ZONE_ERR_SUCC'],RoomInfo=self.room_wire(current))))
        elif current is None:
            own=next((p for p in previous['players'] if p['game_id']==self.inventory.game_id),None)
            if own:packets.append(packet(0xa109,dict(SeatIndex=own['slot'],Reason=C['ZONE_KICKOUTOFROOM_BY_ROOMOWNER'])))
        elif previous['native_room_id']!=current['native_room_id']:
            packets.append(packet(0xa105,dict(Result=C['ZONE_ERR_SUCC'],RoomInfo=self.room_wire(current))))
        else:
            old={p['game_id']:p for p in previous['players']};new={p['game_id']:p for p in current['players']}
            old_owner=next((p for p in old.values() if p['owner']),None);new_owner=next((p for p in new.values() if p['owner']),None)
            if old_owner and new_owner and old_owner['game_id']!=new_owner['game_id']:
                password=current.get('owner_password','') if new_owner['game_id']==self.inventory.game_id else ''
                packets.append(packet(0xa10c,dict(OldSeatIndex=old_owner['slot'],NewSeatIndex=new_owner['slot'],Password=password)))
            for uin in old.keys()-new.keys():packets.append(packet(0xa109,dict(SeatIndex=old[uin]['slot'],Reason=0)))
            for uin in new.keys()-old.keys():packets.append(packet(0xa106,dict(PlayerInfo=self.room_player(new[uin]))))
            for uin in new.keys()&old.keys():
                before,after=old[uin],new[uin]
                if before['slot']!=after['slot']:packets.append(packet(0xa10f,dict(OldSeatIndex=before['slot'],NewSeatIndex=after['slot'])))
                if (before['ready'],before['observer'])!=(after['ready'],after['observer']):
                    packets.append(packet(0xa112,dict(SeatIndex=after['slot'],ReadyState=self.room_player(after)['State'])))
                if not before['in_match'] and after['in_match']:packets.append(packet(0xa11d,dict(SeatIndex=after['slot'])))
                if before['in_match'] and not after['in_match']:packets.append(packet(0xa119,dict(SeatIndex=after['slot'])))
            if previous['name']!=current['name']:
                packets.append(packet(0xa212,dict(NewRoomName=current['name'])))
            if previous['settings']!=current['settings']:
                packets.append(packet(0xa11f,dict(MatchSettings=current['settings'])))
                packets.append(packet(0xa122,dict(RoomFlags=current['settings']['Flags'])))
            if previous['state'] in ('ALLOCATING','LOADING') and current['state']=='OPEN':
                packets.append(packet(0xa11a,dict(Result=C['ZONE_GAME_DSAGENTS_ARE_BUSY'])))
        self.last_room=copy.deepcopy(current)
        return packets
