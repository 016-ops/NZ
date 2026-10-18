"""原生频道/房间序列化夹具；使用虚拟标识和回环地址，不启动服务器或投影实际玩家。"""
from .native_room_protocol import COMMANDS


def fixtures():
    """覆盖每个新增结构、中文、64位房号、IPv4、空列表和32席上限；并非成功业务证明。"""
    rows=[(command,{}) for command in sorted(COMMANDS)]
    settings=dict(ModeId=8197,MapId=168,MapString='',SubModeId=4098,Flags=0,Type=0,Value=15,
                  RespawnTime=0,RecodeType=0,LiveDelaySec=0,InitialMoney=1000)
    server=dict(Ip=0x0100007f,Port=7777,DSKey=list(range(16)),Domain='',IsUseDomain=0)
    player=dict(Uin=2**40+10001,NickName='房间测试',State=1,Flags=0,SeatIndex=0,SpecEquipFlags=0,
                PMTitleName='',CardId=0,KZFightPower=0)
    room=dict(RoomId=2**40+1001,DisplayeId=1,QQTalkRoomId=0,SubChannelId=1,Name='空间站验收',
              MatchSettings=settings,FighterCapacity=8,ObserverCapacity=0,FighterCount=1,
              ObserverCount=0,PlayerInfos=[player],ClanName='')
    basic={k:v for k,v in room.items() if k not in ('RoomId','SubChannelId','PlayerInfos')}
    basic.update(Id=room['RoomId'],OwnerName='房间测试')
    rows.extend([
        (0xa100,dict(FilterInfo=dict(ModeId=8197,MapId=168,Flags=0,AtLeastCount=1),StartIndex=0,RoomCount=32)),
        (0xa102,dict(Result=0x8100,TotalRoomNum=1,RoomInfos=[basic])),
        (0xa102,dict(Result=0x8100,TotalRoomNum=32,RoomInfos=[dict(basic,Id=2**40+i,DisplayeId=i+1) for i in range(32)])),
        (0xa10a,dict(Name='空间站验收',MatchSettings=settings,FighterCapacity=8,ObserverCapacity=0,
                    Password='test123',GameServerInfo=server)),
        (0xa10b,dict(Result=0x8100)),
        (0xa10b,dict(Result=0x198)),
        (0xa103,dict(RoomId=room['RoomId'],Password='test123',IsTracing=0)),
        (0xa105,dict(Result=0x8100,RoomInfo=room)),
        (0xa105,dict(Result=0x8100,RoomInfo=dict(room,FighterCapacity=32,FighterCount=32,
                    PlayerInfos=[dict(player,Uin=2**40+i,SeatIndex=i,State=i%3) for i in range(32)]))),
        (0xa105,dict(Result=0x191)),
        (0xa106,dict(PlayerInfo=player)),
        (0xa108,dict(Result=0x8100,Reason=0)),
        (0xa109,dict(SeatIndex=31,Reason=0x170)),
        (0xa112,dict(SeatIndex=31,ReadyState=1)),
        (0xa114,dict(Result=0x19f)),
        (0xa116,dict(Result=0x8100,DSInfo=server,Members=[])),
        (0xa11a,dict(Result=0x8100,DSInfo=server,Members=[])),
        (0xa11e,dict(MatchSettings=settings)),
        (0xa122,dict(RoomFlags=0xffffffff)),
        (0xa10d,dict(Camp=2,ObserverCamp=255)),
        (0xa14b,dict(Uins=[10001,10002])),
        (0xa14c,dict(InviteeUin=10001,InviteeNickname='房主',SubChannelId=1,RoomInfo=basic)),
        (0xa210,dict(NewRoomName='改名验证')),
        (0xa330,dict(Result=0x8100,MatchSettings=settings)),
        (0xa601,dict(PlayMode=1)),
        (0xa603,dict(NewOwnerUin=10002)),
    ])
    return rows
