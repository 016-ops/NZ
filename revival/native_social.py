"""好友、私聊与本地战队的持久业务；操作权限每次从当前事务读取，通知面向真实接收者。"""
from django.db.models import Q, F
from .native_actions import event
from .service import Fault, identifier, integer, text_field


def native_text(value, field, capacity, *, empty=False):
    """按当前TDR的CP936字节容量检查文本，避免事件提交后才因无法编码而阻塞接收者连接。"""
    if empty and value=='':
        return ''
    value=text_field(value,field,capacity-1)
    try:
        raw=value.encode('cp936')
    except UnicodeError:
        raise Fault('INVALID','当前客户端不支持该字符') from None
    if len(raw)>=capacity:
        raise Fault('INVALID',field+'超过当前客户端字节长度')
    return value


def peer_account(service, c, account, data):
    """按稳定游戏ID定位独立玩家，拒绝自己及无效账号，不暴露邮箱和登录资料。"""
    gid = integer(data.get('peer_game_id'),1,2**63-1,'peer_game_id')
    peer = c.select('accounts',Q(game_id=gid)).fetchone()
    if not peer or peer['id']==account['id']:
        raise Fault('NOT_FOUND','目标玩家不存在或为自己',404)
    service._active(c,peer)
    return peer


def friendship(service, c, account, action, data):
    """好友申请、答复、删除、备注和拉黑共享双向关系事务；私聊仅发给已建立关系的对端。"""
    if action in ('friend.group_create','friend.group_delete','friend.group_rename'):
        return friend_group(c,account,action,data)
    peer = peer_account(service,c,account,data)
    pair = Q(account_id=account['id'],peer_id=peer['id'])
    reverse = Q(account_id=peer['id'],peer_id=account['id'])
    mine = c.select('native_friends',pair).fetchone()
    theirs = c.select('native_friends',reverse).fetchone()
    if action=='friend.block':
        if (not mine or mine['status']!='BLOCKED') and c.count('native_friends',Q(account_id=account['id'],status='BLOCKED')).fetchone()[0]>=200:
            raise Fault('LIMIT','黑名单已达到当前原生列表200人上限',409)
        if mine:
            c.update('native_friends',pair,{'status':'BLOCKED'})
        else:
            c.insert('native_friends',{'account_id':account['id'],'peer_id':peer['id'],'status':'BLOCKED'})
        c.delete('native_friends',reverse & ~Q(status='BLOCKED'))
        event(service,c,peer['id'],'friend.deleted',{'game_id':account['game_id']})
        return {'status':'BLOCKED'}
    if action=='friend.unblock':
        c.delete('native_friends',pair & Q(status='BLOCKED'))
        return {'status':'REMOVED'}
    if (mine and mine['status']=='BLOCKED') or (theirs and theirs['status']=='BLOCKED'):
        raise Fault('SOCIAL_DENIED','无法对该玩家执行此操作',409)
    if action=='friend.request':
        if c.count('native_friends',Q(account_id=account['id'],status__in=['PENDING','ACCEPTED'])).fetchone()[0]>=200 and not mine:
            raise Fault('LIMIT','好友列表已达到当前原生列表200人上限',409)
        if mine and mine['status']=='ACCEPTED':
            return {'status':'ACCEPTED'}
        if not mine:
            c.insert('native_friends',{'account_id':account['id'],'peer_id':peer['id'],'status':'PENDING'})
            event(service,c,peer['id'],'friend.requested',{'game_id':account['game_id']})
        return {'status':'PENDING'}
    if action in ('friend.accept','friend.reject'):
        if not theirs or theirs['status']!='PENDING':
            raise Fault('SOCIAL_STATE','没有待处理的好友申请',409)
        if action=='friend.reject':
            c.delete('native_friends',reverse)
            event(service,c,peer['id'],'friend.rejected',{'game_id':account['game_id']})
            return {'status':'REJECTED'}
        if c.count('native_friends',Q(account_id=account['id'],status='ACCEPTED')).fetchone()[0]>=200:
            raise Fault('LIMIT','好友列表已满',409)
        c.update('native_friends',reverse,{'status':'ACCEPTED'})
        if mine:
            c.update('native_friends',pair,{'status':'ACCEPTED'})
        else:
            c.insert('native_friends',{'account_id':account['id'],'peer_id':peer['id'],'status':'ACCEPTED'})
        event(service,c,peer['id'],'friend.accepted',{'game_id':account['game_id']})
        event(service,c,account['id'],'friend.accepted',{'game_id':peer['game_id']})
        return {'status':'ACCEPTED'}
    if action=='friend.delete':
        c.delete('native_friends',pair|reverse)
        event(service,c,peer['id'],'friend.deleted',{'game_id':account['game_id']})
        return {'status':'REMOVED'}
    if not mine or not theirs or mine['status']!='ACCEPTED' or theirs['status']!='ACCEPTED':
        raise Fault('SOCIAL_STATE','需要已建立的好友关系',409)
    if action=='friend.edit':
        values = {}
        for key in ('remark','group'):
            if key not in data:
                continue  # 改分组不能清空备注，改备注也不能移出原组。
            value = data[key]
            values[key] = native_text(value,key,32,empty=True)
        if 'group' in values and values['group'] not in ('','0'):
            if not values['group'].isdigit() or not c.select('native_friend_groups',Q(account_id=account['id'],group_id=int(values['group']))):
                raise Fault('SOCIAL_STATE','好友分组不存在',409)
        c.update('native_friends',pair,values)
        return dict(status='SAVED',**values)
    if action=='friend.message':
        message = native_text(data.get('message'),'message',128)
        event(service,c,peer['id'],'friend.message',{'from_game_id':account['game_id'],'message':message})
        return {'status':'QUEUED','peer_game_id':peer['game_id']}
    raise Fault('NOT_FOUND','未知好友操作',404)


def friend_group(c, account, action, data):
    """在同一事务创建/重命名/删除账号自己的分组；保留被移动好友的关系与备注。"""
    groups=list(c.select('native_friend_groups',Q(account_id=account['id'])))
    if action=='friend.group_create':
        used={g['group_id'] for g in groups}
        group_id=next((i for i in range(1,11) if i not in used),None)
        if group_id is None:
            raise Fault('LIMIT','最多创建10个好友分组',409)
        name=native_text(data.get('name'),'group name',16)
        c.insert('native_friend_groups',{'account_id':account['id'],'group_id':group_id,'name':name})
    else:
        group_id=integer(data.get('group_id'),1,10,'group_id')
        condition=Q(account_id=account['id'],group_id=group_id)
        if not c.select('native_friend_groups',condition):
            raise Fault('SOCIAL_STATE','分组不存在',409)
        if action=='friend.group_delete':
            c.update('native_friends',Q(account_id=account['id'],group=str(group_id)),{'group':''})
            c.delete('native_friend_groups',condition)
        else:
            name=native_text(data.get('name'),'group name',16)
            c.update('native_friend_groups',condition,{'name':name})
    return {'status':'SAVED','group_id':group_id}


def public_player(c, account_id):
    """只投影游戏身份和昵称，社交查询不能读取邮箱、会话、钱包或其他私人数据。"""
    account = c.select('accounts',Q(id=account_id)).fetchone()
    character = c.select('characters',Q(account_id=account_id)).fetchone()
    return {'game_id':account['game_id'],'nickname':character['nickname'] if character else ''}


def player_online(service, c, account_id):
    """好友与战队共用真实心跳和会话有效性；撤销、封禁或过期后立即显示离线。"""
    presence = c.table('native_presence').filter(account_id=account_id,last_seen__gte=service.clock()-60,
        game__revoked=False,game__expires_at__gt=service.clock(),game__session__revoked=0,
        game__session__expires_at__gt=service.clock(),account__status='ACTIVE',
        game__security_version=F('account__security_version'),game__session__security_version=F('account__security_version')).exists()
    if presence:
        try:
            service._active(c,service._account(c,account_id))
        except Fault:
            presence=False
    return bool(presence)


def friends_view(service, c, account):
    """返回本人的有向关系及别人发来的申请；在线状态仅来自近期真实游戏心跳。"""
    rows = []
    for row in c.select('native_friends',Q(account_id=account['id'])):
        peer = public_player(c,row['peer_id'])
        peer.update(status=row['status'],remark=row['remark'],group=row['group'],
                    online=row['status']=='ACCEPTED' and player_online(service,c,row['peer_id']))
        rows.append(peer)
    requests = [public_player(c,r['account_id']) for r in c.select('native_friends',Q(peer_id=account['id'],status='PENDING'))]
    groups=list(c.select('native_friend_groups',Q(account_id=account['id']),columns=['group_id','name'],order=('group_id',)))
    return {'friends':rows,'requests':requests,'groups':groups}


def member(c, account_id, *, manager=False):
    """读取当前战队成员及权限；撤职、退出后下一请求立即失去管理能力。"""
    row = c.select('native_clan_members',Q(account_id=account_id)).fetchone()
    if not row or (manager and row['role'] not in ('owner','officer')):
        raise Fault('CLAN_PERMISSION','需要相应战队权限',403)
    clan = c.select('native_clans',Q(id=row['clan_id'],active=True)).fetchone()
    if not clan:
        raise Fault('CLAN_STATE','战队已解散',409)
    return row,clan


def clan_event(service, c, clan_id, kind, payload):
    """向事务内当前成员分别投递事件，不用全服广播泄漏战队私聊。"""
    for row in c.select('native_clan_members',Q(clan_id=clan_id)):
        event(service,c,row['account_id'],kind,payload)


def clan_command(service, c, account, action, data):
    """实现建队、申请邀请、成员权限与解散；同队唯一和容量检查均在部署锁内。"""
    if action=='clan.review':
        applications=data.get('applications')
        if not isinstance(applications,list) or not 1<=len(applications)<=100:
            raise Fault('INVALID','请提交1至100条审核')
        results=[]
        for entry in applications:
            if not isinstance(entry,dict) or type(entry.get('agree')) is not bool:
                raise Fault('INVALID','审核格式无效')
            results.append(clan_command(service,c,account,'clan.accept' if entry['agree'] else 'clan.reject',entry))
        return {'status':'REVIEWED','results':results}
    if action=='clan.create':
        if c.select('native_clan_members',Q(account_id=account['id'])):
            raise Fault('CLAN_STATE','已经加入战队',409)
        name = native_text(data.get('name'),'clan name',32)
        if c.select('native_clans',Q(name=name)):
            raise Fault('CLAN_NAME','战队名称已使用',409)
        clan_id = identifier()
        c.insert('native_clans',{'id':clan_id,'name':name,'created_at':service.clock()})
        c.insert('native_clan_bindings',{'clan_id':clan_id})
        c.insert('native_clan_members',{'account_id':account['id'],'clan_id':clan_id,'role':'owner','joined_at':service.clock()})
        return {'status':'CREATED','clan_id':clan_id,'rules':'LOCAL_CLAN_V1_FREE_CREATE_100_MEMBERS'}
    if action in ('clan.apply','clan.invite'):
        invited = action=='clan.invite'
        if invited:
            _,clan = member(c,account['id'],manager=True)
            target = peer_account(service,c,account,data)
        else:
            clan_id=data.get('clan_id')
            if 'native_clan_id' in data:
                binding=c.select('native_clan_bindings',Q(id=integer(data['native_clan_id'],1,2**63-1,'native_clan_id'))).fetchone()
                clan_id=binding['clan_id'] if binding else None
            clan = c.select('native_clans',Q(id=clan_id,active=True)).fetchone()
            target = account
        if not clan or c.select('native_clan_members',Q(account_id=target['id'])):
            raise Fault('CLAN_STATE','战队无效或玩家已有战队',409)
        condition = Q(clan_id=clan['id'],account_id=target['id'],invited=invited)
        previous = c.select('native_clan_applications',condition).fetchone()
        if (not previous or previous['status']!='PENDING') and c.count('native_clan_applications',Q(clan_id=clan['id'],invited=invited,status='PENDING')).fetchone()[0]>=100:
            raise Fault('LIMIT','当前战队待处理申请已达100条',409)
        if previous:
            c.update('native_clan_applications',condition,{'status':'PENDING'})
            application_id = previous['id']
        else:
            application_id = identifier()
            c.insert('native_clan_applications',{'id':application_id,'clan_id':clan['id'],'account_id':target['id'],'invited':invited})
            c.insert('native_clan_application_bindings',{'application_id':application_id})
        payload = {'clan_id':clan['id'],'application_id':application_id,'game_id':target['game_id']}
        payload.update(native_clan_id=c.select('native_clan_bindings',Q(clan_id=clan['id'])).fetchone()['id'],
            native_application_id=c.select('native_clan_application_bindings',Q(application_id=application_id)).fetchone()['id'],
            clan_name=clan['name'])
        if invited:
            event(service,c,target['id'],'clan.invited',payload)
        else:
            for manager in c.select('native_clan_members',Q(clan_id=clan['id'],role__in=['owner','officer'])):
                event(service,c,manager['account_id'],'clan.applied',payload)
        return dict(status='PENDING',**payload)
    if action in ('clan.accept','clan.reject'):
        application_id=data.get('application_id')
        if 'native_application_id' in data:
            binding=c.select('native_clan_application_bindings',Q(id=integer(data['native_application_id'],1,2**63-1,'native_application_id'))).fetchone()
            application_id=binding['application_id'] if binding else None
        application = c.select('native_clan_applications',Q(id=application_id,status='PENDING')).fetchone()
        if not application:
            raise Fault('CLAN_STATE','申请不存在或已处理',409)
        clan = c.select('native_clans',Q(id=application['clan_id'],active=True)).fetchone()
        if application['invited']:
            allowed = application['account_id']==account['id']
        else:
            me,_ = member(c,account['id'],manager=True)
            allowed = me['clan_id']==application['clan_id']
        if not allowed or not clan:
            raise Fault('CLAN_PERMISSION','不能处理此申请',403)
        if data.get('peer_game_id') is not None and public_player(c,application['account_id'])['game_id']!=data['peer_game_id']:
            raise Fault('CLAN_PERMISSION','审核对象与申请不匹配',403)
        if data.get('native_clan_id') is not None and c.select('native_clan_bindings',Q(clan_id=clan['id'])).fetchone()['id']!=data['native_clan_id']:
            raise Fault('CLAN_PERMISSION','邀请战队不匹配',403)
        if action=='clan.accept':
            target = service._account(c,application['account_id']); service._active(c,target)
            if c.select('native_clan_members',Q(account_id=target['id'])) or c.count('native_clan_members',Q(clan_id=clan['id'])).fetchone()[0]>=clan['capacity']:
                raise Fault('CLAN_STATE','玩家已有战队或战队已满',409)
            c.insert('native_clan_members',{'account_id':target['id'],'clan_id':clan['id'],'role':'member','joined_at':service.clock()})
        status = 'ACCEPTED' if action=='clan.accept' else 'REJECTED'
        c.update('native_clan_applications',Q(id=application['id']),{'status':status})
        event(service,c,application['account_id'],'clan.application.result',{'status':status,'clan_id':clan['id'],
            'native_clan_id':c.select('native_clan_bindings',Q(clan_id=clan['id'])).fetchone()['id'],'clan_name':clan['name']})
        return {'status':status,'clan_id':clan['id']}
    me,clan = member(c,account['id'],manager=action not in ('clan.leave','clan.message'))
    if action=='clan.message':
        clan_event(service,c,clan['id'],'clan.message',{'from_game_id':account['game_id'],'message':text_field(data.get('message'),'message',500)})
    elif action=='clan.announcement':
        c.update('native_clans',Q(id=clan['id']),{'announcement':native_text(data.get('announcement'),'announcement',128,empty=True)})
    elif action=='clan.leave':
        if me['role']=='owner':
            raise Fault('CLAN_OWNER','请先移交队长或解散战队',409)
        c.delete('native_clan_members',Q(account_id=account['id']))
    elif action in ('clan.kick','clan.role','clan.transfer'):
        peer = peer_account(service,c,account,data)
        target = c.select('native_clan_members',Q(account_id=peer['id'],clan_id=clan['id'])).fetchone()
        if not target or target['role']=='owner' or (me['role']!='owner' and (target['role']!='member' or action!='clan.kick')):
            raise Fault('CLAN_PERMISSION','不能修改此成员',403)
        if action=='clan.kick':
            c.delete('native_clan_members',Q(account_id=peer['id']))
        elif action=='clan.transfer':
            c.update('native_clan_members',Q(account_id=account['id']),{'role':'member'})
            c.update('native_clan_members',Q(account_id=peer['id']),{'role':'owner'})
        else:
            role = data.get('role')
            if role not in ('member','officer'):
                raise Fault('INVALID','无效战队职位')
            c.update('native_clan_members',Q(account_id=peer['id']),{'role':role})
        event(service,c,peer['id'],'clan.membership.changed',{'action':action,'clan_id':clan['id']})
    elif action=='clan.dismiss':
        if me['role']!='owner':
            raise Fault('CLAN_PERMISSION','只有队长可以解散战队',403)
        clan_event(service,c,clan['id'],'clan.dismissed',{'clan_id':clan['id']})
        c.delete('native_clan_members',Q(clan_id=clan['id']))
        c.update('native_clan_applications',Q(clan_id=clan['id'],status='PENDING'),{'status':'CANCELLED'})
        c.update('native_clans',Q(id=clan['id']),{'active':False})
    else:
        raise Fault('NOT_FOUND','未知战队操作',404)
    return {'status':'SAVED','clan_id':clan['id']}


def clan_view(service, c, account):
    """只向成员返回本队资料；申请详情仅队长和管理员可读，普通成员不可代审核。"""
    own = c.select('native_clan_members',Q(account_id=account['id'])).fetchone()
    if not own:
        return {'clan':None,'invitations':list(c.select('native_clan_applications',Q(account_id=account['id'],invited=True,status='PENDING')))}
    _,clan = member(c,account['id'])
    members = [dict(public_player(c,r['account_id']),role=r['role'],joined_at=r['joined_at'],
                    online=player_online(service,c,r['account_id'])) for r in c.select('native_clan_members',Q(clan_id=clan['id']))]
    applications = list(c.select('native_clan_applications',Q(clan_id=clan['id'],invited=False,status='PENDING'))) if own['role'] in ('owner','officer') else []
    for row in applications:
        row['native_id']=c.select('native_clan_application_bindings',Q(application_id=row['id'])).fetchone()['id']
        row.update(public_player(c,row['account_id']))
    return {'clan':clan_public(c,clan['id']),'role':own['role'],'members':members,'applications':applications}


def clan_public(c, clan_id):
    """公开队名、数字身份、队长和人数；不返回申请资料及私聊。"""
    clan=c.select('native_clans',Q(id=clan_id,active=True)).fetchone()
    if not clan:
        raise Fault('NOT_FOUND','战队不存在',404)
    binding=c.select('native_clan_bindings',Q(clan_id=clan_id)).fetchone()
    owner=c.select('native_clan_members',Q(clan_id=clan_id,role='owner')).fetchone()
    return dict(clan,native_id=binding['id'],captain=public_player(c,owner['account_id']),
                member_count=c.count('native_clan_members',Q(clan_id=clan_id)).fetchone()[0])
