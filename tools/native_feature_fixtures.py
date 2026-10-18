"""无真实账号的功能协议夹具；独立Python编码与完整x86主机数据交给当前DLL互证。"""
from adapters.cn_current.features import host_fixture


def fixtures():
    """覆盖零/非零数组、中文字符串、64位GID及正负结果；不发送任何应用包。"""
    rows = [
        (0x37a2,dict(WeaponGID=1001,WeaponResID=101147,SkinGID=2002,SkinResId=102517)),
        (0x37a3,dict(Result=0x8100,SkinData=dict(WeaponGID=1001,SkinPropGID=2002,SkinResId=102517))),
        (0x37a1,dict(EquippedSkinInfo=dict(SkinDataList=[dict(WeaponGID=1001,SkinPropGID=2002,SkinResId=102517)]))),
        (0x3932,dict(OperatorType=0,ItemId=103542,ItemGid=1001,SwollowItemId=102304,SwollowItemGid=2002)),
        (0x3934,dict(Result=0x8100,OperatorType=0,ItemId=103542,ItemGid=1001,SwollowItemId=102304,SwollowItemGid=2002)),
        (0x3931,dict(PlayerKunTaoPetInfo=dict(Uin=10001,KunTaoPetInfoList=[
            dict(ItemId=103542,ItemGid=1001,SwollowItemId=102304,SwollowItemGid=2002)]))),
        (0x3932,dict(OperatorType=2,ItemId=103542,ItemGid=1001,SwollowItemId=102304,SwollowItemGid=2002)),
        (0xa505,dict(BuyType=0,Consignne=10001,ConsignneNickName='测试昵称',
                    CommodityList=[dict(CommodityId=200001,PriceIndex=0,Price=12500,IsPermanent=1)],PayType=1,Remark='')),
        (0xa506,dict(Result=0x8100,BuyType=0,PayType=1,ConsignneNickName='测试昵称',ConsumeGP=12500,
                    CommodityList=[dict(CommodityId=200001,PriceIndex=0,PropLst=[dict(GID=2002,ItemId=100001,AvalidHour=-1,Durability=2160)])],ConsignneUin=10001)),
        (0xa50b,dict(PropRenewLst=[1001,2002])),
        (0xa50c,dict(Result=0x8100,PropGidLst=[1001,2002],ConsumeTP=10,TPBalance=100)),
        (0xa509,dict(PropGid=1001,PriceIndex=1,PayType=2,CommodityId=200020,Price=2000)),
        (0xa50a,dict(Result=33280,PropGid=1001,AvailableHour=720,Durability=2160,PayType=2,ConsumeTP=2000,TPBalance=123456,CommodityId=200020)),
        (0xa50a,dict(Result=512,PropGid=1001,PayType=2,CommodityId=200020)),
        (0xa363,dict(PropGid=1001)),
        (0xa364,dict(Result=33024,PropGid=1001,ItemId=100003,AddGP=2138)),
        (0xa364,dict(Result=16,PropGid=1001)),
        (0x1112,dict(Uin=10001,Type=1,Date=16,Pos=0)),
        (0x1113,dict(Uin=10001,Result=0,Award=1,Type=1,Date=16)),
        (0x1114,dict(Uin=10001,Type=1,Date=16,Pos=0)),
        (0x1115,dict(Uin=10001,Type=1,Date=16,Awarded=1)),
        (0xa303,dict(FriendUinLst=[10001,10002],Type=0)),
        (0xa304,dict(FriendStatusLst=[dict(FriendUin=10002,Status=1)],Type=0)),
        (0xa305,dict(RespondentUin=10002,RespondentNickname='好友',Remark='hello')),
        (0xa306,dict(RequestId=123,ProposerUin=10001,ProposerNickname='好友',Remark='hello',Time=1800000000,MsgId=5)),
        # 真实客户端绿色同意发送0x8301，拒绝为0x0301；不是通用SNS成功0x8300。
        (0xa307,dict(RequestId=123,Result=33537,ProposerUin=10001,ProposerNickname='好友')),
        (0xa308,dict(Result=33537,RespondentUin=10002,RespondentNickName='好友',Time=1800000000,MsgId=5)),
        (0xa307,dict(RequestId=124,Result=769,ProposerUin=10001,ProposerNickname='好友')),
        (0xa308,dict(Result=769,RespondentUin=10002,RespondentNickName='好友',Time=1800000001,MsgId=6)),
        (0xa329,dict(FriendUin=10002,Msg='hello')),
        (0xa32a,dict(Result=0x8100,FriendUin=10002)),
        (0xb001,dict(Uin=10001,ClanName='测试战队',SafeCode='',CertifiedMail='')),
        (0xb002,dict(Result=0x8100,ClanName='测试战队')),
        (0xfa0c,dict(FragmentId=102111,ExchangeId=1)),
        (0xfa0d,dict(Result=0x8100,FragmentId=102111,PropId=100001)),
        (0xfa0e,dict(Comfirm=1,FragmentId=102111,PropId=100001,ExchangeId=1)),
        (0xfa0f,dict(Result=0x8100,FragmentId=102111,PropId=100001)),
        (0xfa16,dict(FragmentId=101144)),
        (0xfa17,dict(Result=0x8100,Data=dict(FragmentId=101144,PropInfos=[dict(PropId=100001,PropPeriod=-1,PropCount=1)],ExchangeId=1))),
    ]
    # 分领域结果码来自当前TDR；离线编码验证不把任意整数当作业务成功。
    from adapters.cn_current.features import metadata
    constants=metadata()['constants']
    for command,values in rows:
        if 'Result' in values and values['Result']==0x8100:
            key=('SHOP_ERR_SUCC' if command in (0xa506,0xa50c) else 'SNS_ERR_SUCC' if 0xa300<=command<=0xa32a
                 else 'CLAN_ERR_SUCC' if command==0xb002
                 else 'KUNTAO_PET_SWOLLOW_SUCCESS' if command==0x3934 else 'ZONE_ERR_SUCC')
            values['Result']=constants[key]
    rows.extend([
        (0xfa20,dict(IsFree=1,Lottery10Time=0)),
        (0xfa20,dict(IsFree=0,Lottery10Time=1)),
        (0xfa21,dict(Result=constants['ZONE_ERR_SUCC'],Items=[dict(IsReturnBack=i%2,PropId=100001+i,PropCount=1,PropPeriod=-1) for i in range(10)])),
        (0xfa21,dict(Result=constants['LOTTERY_ERR_EXCHANGE_NOTENOUGH'])),
        (0xfa22,dict(LastFreeLotteryTm=1800000000)),
        (0xfa23,dict(Padding=0)),
        (0xfa24,dict(Result=constants['ZONE_ERR_SUCC'],Items=[dict(BelongPoolID=4,PropId=100001+i,PropCount=1,PropPeriod=-1,ShowId=0,PicId=100001+i,InFreePool=1,InPayPool=1) for i in range(256)])),
        (0xfa24,dict(Result=constants['LOTTERY_ERR_FAIL'])),
        (0xb005,dict(ClanName='测试战队')),
        (0xb009,dict(Uin=10001)),
        (0xb00a,dict(Result=36864,ClanDetailedInfo=dict(Clanid=1,ClanName='测试战队',MaxMembers=100,Count=1,CreateTime=1792100000,Introduction='公告'))),
        (0xb00b,dict(Uin=10001,IsAll=1)),
        (0xb00c,dict(Result=36864,IsAll=1,TotalCount=1,PackageFlag=0,MemberInfo=dict(ClanPlayerInfo=[dict(Uin=10001,Nickname='队长',Role=1,JoinClanTime=1792100000)],Status=[0],NextLevelPrestige=[0]))),
        (0xb00d,dict(Uin=10001,ClanID=1)),
        (0xb00e,dict(Result=36864,ClanCommonInfo=dict(ClanID=1,ClanName='测试战队',CaptainID=10001,MaxMembers=100,CurMembers=1),CaptainUin=10001,CaptainName='队长')),
        (0xb00f,dict(Uin=10001,ClanName='测试战队')),
        (0xb010,dict(Result=36864,ClanName='测试战队')),
        (0xb013,dict(Uin=10001,ClanID=1,JoinMsg='加入')),
        (0xb014,dict(Result=36864,ClanID=1,ClanName='测试战队')),
        (0xb015,dict(Uin=10001,NickName='')),
        (0xb016,dict(Result=36864,ClanApproveList=[dict(ClanId=1,ReqMsgId=1,ReqUin=10002,ReqNickname='队员')])),
        (0xb017,dict(IsAgree=1,Uin=10002,ClanID=1,Invite_id=1)),
        (0xb018,dict(Result=36864,ClanID=1,ClanName='测试战队')),
        (0xb019,dict(Uin=10001,PlayerInfoList=[dict(IsAgree=1,ReqUin=10002,ReqMsgId=1)])),
        (0xb01a,dict(Result=36864,ReqUin=10002,ReqMsgId=1)),
        (0xb01b,dict(ClanID=1,ClanName='测试战队',IsAgree=1,OffMsgID=1)),
        (0xb01c,dict(ClanID=1,ClanName='测试战队',Invite_id=1)),
        (0xb01e,dict(Uin=10001,ObjUin=10002)),
        (0xb01f,dict(Result=36864,ObjUin=10002)),
        (0xb020,dict(Uin=10001,ObjUin=10002,PlayerNewRole=2)),
        (0xb021,dict(Result=36864,ObjUin=10002)),
        (0xb022,dict(Uin=10001,ObjUin=10002,PlayerNewRole=4)),
        (0xb023,dict(Result=36864,ObjUin=10002)),
        (0xb024,dict(Uin=10002)),
        (0xb02b,dict(ManagerUin=10001,InvitedUin=10002)),
        (0xb02c,dict(InvitedUin=10002,ErrorCode=36864)),
        (0xb02d,dict(Uin=10001)),
        (0xb02e,dict(Result=36864,PostCount=1,Post='公告')),
        (0xb02f,dict(Uin=10001,PostIndex=0,Post='公告')),
        (0xb030,dict(Result=36864,PostIndex=0)),
        (0xb039,dict(Uin=10001)),
        (0xb03a,dict(ErrorCode=36864)),
        (0xa323,dict(FriendList=[dict(Uin=10002,GroupId=1,Remark='备注')],EnemyList=[dict(Uin=10003)],GroupList=[dict(GroupId=1,GroupName='好友')],)),
        (0xa31f,dict(GroupName='好友')),
        (0xa320,dict(Result=33536,SnsGroupInfo=[dict(GroupId=1,GroupName='好友')])),
        (0xa321,dict(GroupId=1)),
        (0xa322,dict(SnsGroupInfo=dict(GroupId=1,GroupName='朋友'))),
        (0xa30b,dict(EnemyUin=10002,EnemyNickName='好友')),
        (0xa30c,dict(AddEnemyUin=10002,Result=33536)),
        (0xa30d,dict(EnemyUin=10002)),
        (0xa30e,dict(DelEnemyUin=10002,Result=33536)),
        (0xc119,dict(Padding=0)),
        (0xc11a,dict(IsLock=0,AccumuMPAward=[0]*31,Stages=[dict(ID=90001601,Stage=1,ActType=0,ButtonType=6,StartManual=1,ActName='每日签到',ActContent='测试配置',ActFlow='签到',ActStgAward='道具直接入库')])),
        (0xc107,dict(Type=6,ID=90001601,Stage=1)),
        (0xc108,dict(ID=90001601,Stage=1)),
        (0xc109,dict(Result=35329,ID=90001601,Stage=1,DeltaValue=1)),
        (0xc110,dict(Padding=0)),
        (0xc104,dict(ActvList=[dict(Id=90001601,StageInfo=[dict(Data=1,State=1)]+[{}]*9)])),
        (0xc106,dict(Padding=0)),
        (0xc101,dict(DayOfMonthBegin=1790784000,TodayIndex=15,Calendar=[dict(Date=1790784000,ActvInfo=[dict(ID=90001601,Stage=[1])])])),
        (0xc117,dict(Padding=0)),
        (0xfa05,dict(Index=0)),
        (0xfa06,dict(ExchangeResult=constants['LOTTERY_ERR_SUCC'],Index=0)),
        (0xfa18,dict(Padding=0)),
        (0xfa19,dict(Result=constants['LOTTERY_ERR_SUCC'],Items=[dict(Type=0,PropId=100001,PropCount=1,PropPeriod=-1)])),
        (0xfa1a,dict(SelectItem=[100001,100002])),
        (0xfa1b,dict(Result=constants['LOTTERY_ERR_SUCC'],ResetCost=0,Items=[dict(Type=0,PropId=100001,PropCount=1,PropPeriod=-1)])),
        (0xfa1c,dict(ResetType=0,NZPoint=0,PropId=0)),
        (0xfa1d,dict(Result=constants['LOTTERY_ERR_SUCC'])),
        (0xfa1e,dict(Padding=0)),
        (0xfa1f,dict(Result=constants['LOTTERY_ERR_SUCC'],ResetCost=0,Items=[dict(Type=0,PropId=100001,PropCount=1,PropPeriod=-1)])),
        (0xa167,dict(PropGid=1001,ItemId=102111)),
        (0xa168,dict(Result=0x8100,PropGid=1001,ItemId=102111)),
        (0xa00a,dict(UpdateFlag=1|2|8,Reason=2,TGamePoint=100,GoldPoint=200,
            Props=[dict(OpType=1,Prop=dict(Uin=10001,GID=1001,ItemId=102111,StackNum=8,Location=-1,AvailbilityHour=-1,GainType=48))],TalentSkills={'Version':3})),
        (0xa405,dict(C2ZNP2PType=256,RecipientUin=10002,RecipientNickName='好友',ChatMsgBuf='你好')),
        (0xa406,dict(ZN2CP2PType=256,RequisitionerUin=10001,RequisitionerNickname='好友',ChatMsgBuf='你好',RecipientUin=10002)),
        (0xa402,dict(ChatType=256,RecipientUin=10002,ErrorInfo=768)),
        (0xa30f,dict(Type=0,FriendUin=10002,FriendNickName='好友')),
        (0xa310,dict(Result=33536,FriendUin=10002,FriendNickName='好友')),
        (0xa309,dict(FriendUin=10002)),
        (0xa30a,dict(Result=33536,DelFriendUin=10002)),
        (0xa311,dict(FriendUin=10002,NewGroupId=1)),
        (0xa312,dict(Result=33536,FriendUin=10002)),
        (0xa324,dict(FriendUin=10002,FriendNickname='好友',Time=1800000000,MsgId=4)),
    ])
    search=dict(Result=36864,SearchName='测试')
    for field in metadata()['schemas']['ZN2C_ResSearchClanByName']['fields']:
        if field['capacity']>1:
            search[field['name']]=[{} if field['type']==1 else '测试' if field['type']==21 else 1]
    rows.append((0xb006,search))
    # 实机升级心灵守卫请求对应311；覆盖64位GID、零预测值、成功产物和材料不足应答。
    rows.extend([
        (0xa375,dict(FortuneKeyType=311,ItemId=104157,ItemGid=2**40+1001,FortuneKeyCount=1)),
        (0xa376,dict(Result=constants['EXCHANGE_ERR_SUCC'],FortuneKeyType=311,FortuneKeyCount=1,
                    Props=[dict(Uin=10001,GID=2**40+1002,ItemId=104282,StackNum=1,AvailbilityHour=-1,Location=-1)])),
        (0xa376,dict(Result=constants['EXCHANGE_ERR_PROPNOTENOUGH'],FortuneKeyType=311,FortuneKeyCount=1)),
        (0xfa17,dict(Result=constants['ZONE_ERR_SUCC'],Data=dict(FragmentId=102111,ExchangeId=2,
            BeginTime=1800000000,EndTime=2147483647,ResetTime='2038-01-19 11:14:07',
            PropInfos=[dict(PropId=100001,PropCount=1,PropPeriod=-1)]))),
        (0xfa0b,dict(Result=constants['ZONE_ERR_SUCC'],FragmentId=102111,VoucherCount=15,PropIds=[],ResetTime='2038-01-19 11:14:07')),
        (0xfa1f,dict(Result=constants['ZONE_ERR_SUCC'],Items=[dict(Type=1 if i<2 else 3,
            PropId=100001+i,PropCount=1,PropPeriod=-1) for i in range(12)])),
    ])
    # 工程完整请求/应答和37配方熟练度通知；最大60个GID与失败后重试形状分别核验。
    rows.extend([
        (0xf401,dict(Uin=10001,PlayerPropProductInfo=dict(Version=3,PropSpecialtyInfo=[dict(PropNO=90001,Level=50,Stage=2)]),
                     PlayerPropCoolDownInfo=dict(Version=3,PropCoolDownInfo=[dict(PropNO=90001,CommonCoolDownTime=0,AdvanceCoolDownTime=0)]))),
        (0xf401,dict(Uin=10001,PlayerPropProductInfo=dict(Version=3,PropSpecialtyInfo=[]),PlayerPropCoolDownInfo=dict(Version=3,PropCoolDownInfo=[]))),
        (0xf402,dict(Uin=10001,PropGIDList=[2**40+i for i in range(60)])),
        (0xf403,dict(Uin=10001,Result=constants['PROPFACTORY_ERR_SUCC'],OutputItemID=100764)),
        (0xf403,dict(Uin=10001,Result=constants['PROPFACTORY_ERR_NOENOUGHRESOURCE'])),
        (0xf404,dict(Uin=10001,PropIndex=90037,ComposeType=2,ComposeCount=5)),
        (0xf405,dict(Uin=10001,Result=constants['PROPFACTORY_ERR_SUCC'],PropIndex=90037,ComposeType=2,ComposeCount=5,SuccCount=5,SpecialtyLevel=999,CoolDownTime=0)),
        (0xf406,dict(Uin=10001,MaterialGIDList=[2**40+i for i in range(30)])),
        (0xf407,dict(Uin=10001,Result=constants['PROPFACTORY_ERR_SUCC'])),
        (0xf408,dict(Uin=10001,MaterialId=100765)),
        (0xf409,dict(Uin=10001,Result=constants['PROPFACTORY_ERR_SUCC'])),
    ])
    # 仓库礼包走同一A375族但没有主体GID；多奖品和数组边界分别交给原生DLL互证。
    rows.append((0xa375,dict(FortuneKeyType=307,ItemId=0,ItemGid=0,FortuneKeyCount=1)))
    for count in (3,100):
        rows.append((0xa376,dict(Result=constants['EXCHANGE_ERR_SUCC'],FortuneKeyType=307,FortuneKeyCount=1,
            Props=[dict(Uin=10001,GID=2**40+i,ItemId=104002+i%3,StackNum=1,
                        AvailbilityHour=-1,OwnerGID=0,Location=-1) for i in range(count)])))
    # 两个真实默认身体部件同属一个角色，原生 Location 均为 -1；内部唯一槽不进入线包。
    rows.append((0xa00a,dict(UpdateFlag=8,Reason=2,TalentSkills=dict(Version=3),Props=[
        dict(OpType=1,Prop=dict(Uin=10001,GID=2**40+i,ItemId=item_id,StackNum=1,
            AvailbilityHour=-1,OwnerGID=2**40+100,Location=-1))
        for i,item_id in enumerate((300062,300063))])))
    # 百宝箱等级实际由VipPVEPoint区间计算，四个VIP字段及Seq均经当前DLL往返验证。
    rows.extend([
        (0x3601,dict(Padding=0)),
        (0x3602,dict(result=constants['ZONE_ERR_SUCC'],VipBaseInfo=dict(VipPVEPoint=10500,VipPVEEndTime=2147483647))),
        (0x3602,dict(result=constants['ZONE_VIP_LEVEL_POINT_ERROR'])),
        (0x3603,dict(Seq=2**32-1,PropIds=[10014,10015,10016])),
        (0x3603,dict(Seq=1,PropIds=list(range(32)))),
        (0x3604,dict(Seq=2**32-1,Result=constants['ZONE_ERR_SUCC'])),
        (0x3604,dict(Seq=1,Result=constants['ZONE_VIP_PROP_HAD_TAKEN'])),
        (0x3603,dict(Seq=10014,PropIds=[100482])),
        (0x3604,dict(Seq=10014,Result=constants['ZONE_ERR_SUCC'])),
    ])
    # VIP领用来源35与普通购买48同屏共存，确保来源字节在A00A实际DLL中完整往返。
    rows.append((0xa00a,dict(UpdateFlag=8,Reason=2,TalentSkills=dict(Version=3),Props=[
        dict(OpType=1,Prop=dict(Uin=10001,GID=2**40+i,ItemId=100482,StackNum=1,
            ObtainTime=1792300000,AvailbilityHour=144,OwnerGID=0,Location=-1,GainType=origin))
        for i,origin in enumerate((35,48))])))
    # 全能体验卡保留原始-1槽但根Owner为1，与36专家卡同包；期限型永久只投影日历，不改数据库NULL。
    rows.append((0xa00a,dict(UpdateFlag=8,Reason=2,TalentSkills=dict(Version=3),Props=[
        dict(OpType=1,Prop=dict(Uin=10001,GID=2**40+10+i,ItemId=item,StackNum=1,
            ObtainTime=1792300000,AvailbilityHour=1752000,OwnerGID=1,Location=location,GainType=48))
        for i,(item,location) in enumerate(((102000,-1),(101210,36)))])))
    # 新猎场箱的叠加材料只报告本次15个；同类角色的200年哨兵与独立武器可在A376表达。
    rows.extend([
        (0xa375,dict(FortuneKeyType=5,ItemId=0,ItemGid=0,FortuneKeyCount=1)),
        (0xa376,dict(Result=constants['EXCHANGE_ERR_SUCC'],FortuneKeyType=291,FortuneKeyCount=3,
            Props=[dict(Uin=10001,GID=2**40+20,ItemId=103771,StackNum=15,
                        AvailbilityHour=-1,OwnerGID=0,Location=-1)])),
    ])
    # 原生房间新增结构另存职责模块；所有旧功能夹具和A005等级夹具继续核验。
    from .native_room_fixtures import fixtures as room_fixtures
    return rows+room_fixtures()


def write_fixtures(output):
    """只写本次新建的私人目录；DLL应逐文件核对完整主机和Python线上字节。"""
    for index,(command,values) in enumerate(fixtures()):
        host,wire=host_fixture(command,values)
        label=f'feature_{index:02d}_{command:04x}'
        (output/(label+'.host')).write_bytes(host)
        (output/(label+'.python.bin')).write_bytes(wire)
    # A005已有独立库存编码器，不在功能请求白名单；直接用PlayerInfo真实布局生成无账号夹具。
    import struct
    from adapters.cn_current.features import decode,encode
    from adapters.cn_current.zone import build_research_player_info,ZONE_EMPTY_ASSET_PLAYER_INFO_PROFILE
    for level in (0,5):
        nickname=b'KunCoreFixture'
        wire=bytearray(build_research_player_info(game_id=10001,nickname_bytes=nickname,
                                                 profile=ZONE_EMPTY_ASSET_PLAYER_INFO_PROFILE))
        struct.pack_into('>H',wire,8+18+len(nickname)+1+308,level)
        values,end=decode('PlayerInfo',wire,offset=10)
        if end!=len(wire) or values['KunTaoEvolutionLevel']!=level:
            raise ValueError('KUN_PLAYER_INFO_FIXTURE_MISMATCH')
        host=(struct.pack('<HHHHH',0x3243,0xa005,8,len(wire)-8,0x8100)+
              encode('PlayerInfo',values,host=True)).ljust(1025331,b'\0')
        label=f'feature_kun_level_{level}_a005'
        (output/(label+'.host')).write_bytes(host)
        (output/(label+'.python.bin')).write_bytes(wire)
