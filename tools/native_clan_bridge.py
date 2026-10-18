"""当前构建战队消息：数字身份绑定真实战队，成员与权限始终从同一业务服务读取。"""
import json
from adapters.cn_current.features import metadata, packet
from tools.email_launcher import LoginError

CLAN_COMMANDS={0xb001,0xb005,0xb009,0xb00b,0xb00d,0xb00f,0xb013,0xb015,0xb017,0xb019,
               0xb01e,0xb020,0xb022,0xb024,0xb02b,0xb02d,0xb02f,0xb039}
ROLES={'owner':1,'officer':2,'member':4}  # 当前TDR的ECLANROLE_CAPTAIN/VICECAPTAIN/PLAYER。
SUCCESS=36864


class NativeClanMessages:
    """组合到已鉴权功能桥；依赖view/commit方法，不接收客户端声称的管理权限。"""

    def clan_failure(self, command, fields):
        """按各消息定义返回失败码；退出通知没有专用应答，返回资料失败让界面重新读取。"""
        target={0xb024:0xb00a}.get(command,command+1)
        key='ErrorCode' if command in (0xb02b,0xb039) else 'Result'
        values={key:4096}
        if command in (0xb020,0xb022,0xb01e):
            values['ObjUin']=fields['ObjUin']
        return packet(target,values)

    def clan_detail(self, clan):
        """仅填真实成员数、创建时间及公告；没有战斗统计的字段维持零，不能制造经验与战绩。"""
        return dict(Clanid=clan['native_id'],ClanName=clan['name'],MaxMembers=clan['capacity'],
            Count=clan['member_count'],CreateTime=clan['created_at'],Introduction=clan['announcement'])

    def clan_common(self, clan):
        """公开战队资料用于查找和加入确认；队长来自当前成员权限而非历史缓存。"""
        return dict(ClanID=clan['native_id'],ClanName=clan['name'],CaptainID=clan['captain']['game_id'],
            MaxMembers=clan['capacity'],CurMembers=clan['member_count'],CreateTime=clan['created_at'],Introduction=clan['announcement'])

    def clan_snapshot(self, view=None, *, changed_only=False):
        """刷新本队资料和完整成员；没有加入战队时清除缓存，变更后不沿用旧职位。"""
        view=view or self.view('clan')
        fingerprint=json.dumps(view,sort_keys=True)
        if changed_only and fingerprint==getattr(self,'last_clan_state',None):
            return []
        clan=view['clan'];self.inventory.native_clan_id=clan['native_id'] if clan else 0
        packets=[]
        if clan:
            packets.append(packet(0xb00a,dict(Result=SUCCESS,ClanDetailedInfo=self.clan_detail(clan))))
            packets.extend(self.clan_members(view))
        elif getattr(self,'last_clan_state',None) is not None:
            packets.append(packet(0xb00a,dict(Result=4096)))
        self.last_clan_state=fingerprint
        return packets

    def clan_members(self, view):
        """当前200成员包完整表达当前最多100人初始战队；PackageFlag=0为单包兼容合同。"""
        members=view['members']
        rows=[dict(Uin=r['game_id'],Nickname=r['nickname'],Role=ROLES[r['role']],JoinClanTime=r['joined_at']) for r in members]
        return [packet(0xb00c,dict(Result=SUCCESS,IsAll=1,TotalCount=len(rows),PackageFlag=0,
            MemberInfo=dict(ClanPlayerInfo=rows,Status=[int(r.get('online',False)) for r in members],NextLevelPrestige=[0]*len(rows))))]

    def clan_handle(self, command, fields):
        """逐请求核对身份与目标；同意/拒绝按持久申请ID授权，批量审核在一笔事务执行。"""
        for key in ('Uin','ManagerUin'):
            if key in fields and fields[key]!=self.inventory.game_id:
                raise ValueError('CLAN_IDENTITY_MISMATCH')
        if command==0xb001:
            self.commit('clan.create',name=fields['ClanName'])
            return [packet(0xb002,dict(Result=SUCCESS,ClanName=fields['ClanName'])),*self.clan_snapshot()]
        if command in (0xb005,0xb00f):
            clans=self.view('clans',search=fields['ClanName'])
            if command==0xb00f:
                return [packet(0xb010,dict(Result=4096 if any(c['name']==fields['ClanName'] for c in clans) else SUCCESS,ClanName=fields['ClanName']))]
            values=dict(Result=SUCCESS,SearchName=fields['ClanName'],ClanScaleType=fields['ClanScaleType'])
            # 当前搜索响应有多组共享Count的平行数组，全部同长，不能只填队名而漏填其他列。
            mapping={'ClanID':'native_id','ClanName':'name','CurMembers':'member_count','MaxMembers':'capacity','ClanIntroduction':'announcement'}
            for field in metadata()['schemas']['ZN2C_ResSearchClanByName']['fields']:
                if field['capacity']<=1:
                    continue
                name=field['name']
                values[name]=[c[mapping[name]] if name in mapping else c['captain']['nickname'] if name=='CaptainName'
                    else {} if field['type']==1 else '' if field['type']==21 else 0 for c in clans]
            return [packet(0xb006,values)]
        if command==0xb00d:
            clan=self.view('clan.public',native_clan_id=fields['ClanID'])
            return [packet(0xb00e,dict(Result=SUCCESS,ClanCommonInfo=self.clan_common(clan),CaptainUin=clan['captain']['game_id'],CaptainName=clan['captain']['nickname']))]
        if command==0xb013:
            result=self.commit('clan.apply',native_clan_id=fields['ClanID'])
            return [packet(0xb014,dict(Result=SUCCESS,ClanID=fields['ClanID'],ClanName=result['clan_name']))]
        if command==0xb02b:
            self.commit('clan.invite',peer_game_id=fields['InvitedUin'])
            return [packet(0xb02c,dict(ErrorCode=SUCCESS,InvitedUin=fields['InvitedUin']))]
        if command==0xb017:
            if fields['IsAgree'] not in (0,1):
                raise LoginError('INVALID')
            self.commit('clan.accept' if fields['IsAgree'] else 'clan.reject',native_application_id=fields['Invite_id'],native_clan_id=fields['ClanID'])
            clan=self.view('clan.public',native_clan_id=fields['ClanID'])
            return [packet(0xb018,dict(Result=SUCCESS,ClanID=fields['ClanID'],ClanName=clan['name'])),*self.clan_snapshot()]
        view=self.view('clan');clan=view['clan']
        if not clan:
            raise LoginError('CLAN_STATE')
        if command==0xb009:
            return [packet(0xb00a,dict(Result=SUCCESS,ClanDetailedInfo=self.clan_detail(clan)))]
        if command==0xb00b:
            return self.clan_members(view)
        if command==0xb015:
            if view['role'] not in ('owner','officer'):
                raise LoginError('CLAN_PERMISSION')
            rows=[dict(ClanId=clan['native_id'],ReqMsgId=r['native_id'],ReqUin=r['game_id'],ReqNickname=r['nickname']) for r in view['applications']]
            return [packet(0xb016,dict(Result=SUCCESS,ClanApproveList=rows))]
        if command==0xb019:
            rows=fields['PlayerInfoList']
            if any(r['IsAgree'] not in (0,1) for r in rows):
                raise LoginError('INVALID')
            self.commit('clan.review',applications=[dict(native_application_id=r['ReqMsgId'],peer_game_id=r['ReqUin'],agree=bool(r['IsAgree'])) for r in rows])
            return [*[packet(0xb01a,dict(Result=SUCCESS,ReqUin=r['ReqUin'],ReqMsgId=r['ReqMsgId'])) for r in rows],*self.clan_snapshot()]
        if command==0xb01e:
            self.commit('clan.kick',peer_game_id=fields['ObjUin'])
        elif command in (0xb020,0xb022):
            role={2:'officer',4:'member'}.get(fields['PlayerNewRole'])
            if role is None:
                raise LoginError('INVALID')
            self.commit('clan.role',peer_game_id=fields['ObjUin'],role=role)
        elif command==0xb024:
            self.commit('clan.leave')
            return self.clan_snapshot()
        elif command==0xb02d:
            return [packet(0xb02e,dict(Result=SUCCESS,PostCount=1,Post=clan['announcement']))]
        elif command==0xb02f:
            if fields['PostIndex']!=0:
                raise LoginError('INVALID')
            self.commit('clan.announcement',announcement=fields['Post'])
            return [packet(0xb030,dict(Result=SUCCESS,PostIndex=0)),*self.clan_snapshot()]
        elif command==0xb039:
            self.commit('clan.dismiss')
            return [packet(0xb03a,dict(ErrorCode=SUCCESS)),*self.clan_snapshot()]
        else:
            raise ValueError('UNBOUND_CLAN_COMMAND')
        return [packet(command+1,dict(Result=SUCCESS,ObjUin=fields['ObjUin'])),*self.clan_snapshot()]
