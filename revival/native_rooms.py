"""游戏凭据驱动原生房间、席位和分配请求；数据库分配不冒充已加载的原生服务器。"""
from functools import lru_cache
import base64
import hashlib
import json
from pathlib import Path
import secrets
from django.db.models import F,Q
from adapters.cn_current.features import encode,decode
from .native_actions import event
from .native_features_catalog import ROOM_VERSION as VERSION
from .security import verify_password,token_hash
from .service import Fault,identifier,integer

CATALOG_SHA256='42ca95ef3345b82ddab14bad614aa0186747197906776cb91972dac1fd93becc'
ACTIONS={'room.'+name for name in ('create','join','leave','ready','configure','kick','start','cancel','in_match','rename','transfer','invite')}
# 只开放原脚本声明的普通房间选项；INMATCH/FULL/LOCKED由服务端事实计算，不接受客户端置位。
OPTION_FLAGS=0x0008|0x0010|0x0020|0x0100|0x0200|0x0400|0x0800|0x1000|0x2000|0x4000|0x8000|0x10000|0x20000|0x40000|0x100000


@lru_cache(maxsize=1)
def catalog():
    """只加载冻结的55图规则，不因客户端传入地址、人数或地图名临时扩大目录。"""
    raw=(Path(__file__).resolve().parents[1]/'config/cn2020-native-room-compat-v2.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=CATALOG_SHA256:raise Fault('RULE_CONFLICT','原生房间目录摘要变化',409)
    result=json.loads(raw)
    if result['version']!=VERSION:raise Fault('RULE_CONFLICT','原生房间目录版本变化',409)
    return result


def scope(data):
    """房间独立协商规则版本；旧资产接口不需要增加新字段。"""
    if data.get('room_rule_version')!=VERSION:raise Fault('RULE_CONFLICT','需要当前房间规则版本',409)


def native_text(value,capacity,*,empty=True):
    """按原生CP936字节上限验证名称/口令；不截断、多字节不拆分、不接受控制字符。"""
    if not isinstance(value,str) or any(ord(ch)<32 for ch in value) or not empty and not value.strip():
        raise Fault('ROOM_SETTINGS','房间文本格式错误')
    try:size=len(value.encode('cp936'))
    except UnicodeError:raise Fault('ROOM_SETTINGS','原生房间不支持该字符') from None
    if size>=capacity:raise Fault('ROOM_SETTINGS','房间文本超出原生长度')
    return value


def password_digest(value):
    """房间口令遵循原生1–7字节合同，单独慢哈希；不降低账号密码的12字符要求。"""
    value=native_text(value,8)
    if not value:return ''
    salt=secrets.token_bytes(16)
    hashed=hashlib.scrypt(value.encode('utf-8'),salt=salt,n=32768,r=8,p=3,maxmem=128*1024*1024)
    return 'scrypt$32768$8$3$'+salt.hex()+'$'+hashed.hex()


def room_secret(room_id,value,*,decrypt=False):
    """AES-GCM封装绑定房间ID；只为原生移交通知恢复口令，不与账号密码存储策略混用。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from django.conf import settings as django_settings
    key=hashlib.sha256((django_settings.SECRET_KEY+'\0native-room-password-v1').encode()).digest()
    aad=('native-room-password-v1:'+room_id).encode()
    try:
        if decrypt:
            if not value:return ''
            raw=base64.b64decode(value,validate=True)
            if not 29<=len(raw)<=64:raise ValueError('ROOM_SECRET_SIZE')
            return AESGCM(key).decrypt(raw[:12],raw[12:],aad).decode('utf-8')
        if not value:return ''
        nonce=secrets.token_bytes(12)
        return base64.b64encode(nonce+AESGCM(key).encrypt(nonce,value.encode('utf-8'),aad)).decode('ascii')
    except Exception:
        raise Fault('ROOM_DATA_CORRUPT','房间私有数据无法校验',409) from None


def settings(data):
    """服务器核定地图、模式、人数、难度和允许选项；不接收客户端DS目标或地图URL。"""
    raw=data.get('settings')
    if not isinstance(raw,dict):raise Fault('ROOM_SETTINGS','缺少原生房间设置')
    try:
        wire=encode('MatchSettings',raw);value,end=decode('MatchSettings',wire)
    except ValueError:raise Fault('ROOM_SETTINGS','原生房间设置字段无效') from None
    if end!=len(wire) or value['MapString'] or value['Flags']&~(OPTION_FLAGS|7):
        raise Fault('ROOM_SETTINGS','不能选择额外地图URL或保留标志')
    # 配置界面可能回显服务端的FULL/LOCKED/INMATCH，重新按真实状态计算而非信任回显。
    value['Flags']&=OPTION_FLAGS
    rule=catalog()['maps'].get(str(value['MapId']))
    if not rule or value['ModeId']!=rule['mode_id'] or value['SubModeId'] not in rule['submodes']:
        raise Fault('ROOM_SETTINGS','模式、地图或难度不在服务端规则中')
    fighters=integer(data.get('fighter_capacity',max(rule['player_counts'])),1,32,'fighter_capacity')
    observers=integer(data.get('observer_capacity',0),0,14,'observer_capacity')
    if fighters not in rule['player_counts'] or observers not in [0,*catalog()['observer_counts']] or fighters+observers>32:
        raise Fault('ROOM_SETTINGS','房间人数不在当前模式配置中')
    # PvE默认不设置击杀/限时/初始金钱覆盖；模式使用自身真实地图规则，不允许客户端注入数值。
    if any(value[k] for k in ('Type','Value','RespawnTime','RecodeType','LiveDelaySec','InitialMoney')):
        raise Fault('ROOM_SETTINGS','此PvE规则不支持自报战斗参数')
    return value,rule,fighters,observers


def seats(fighters,*,observer=False,camp=None):
    """当前32席两阵营各从0/16开始，战斗位取各半人数；其余才是观战位，不用总容量作连续席号。"""
    half=fighters//2
    if fighters%2 or not 0<half<=16:raise Fault('ROOM_SETTINGS','战斗人数不能映射到原生席位')
    groups=[list(range(half,16)),list(range(16+half,32))] if observer else [list(range(half)),list(range(16,16+half))]
    if camp is not None:return groups[0 if camp==1 else 1]
    return [slot for pair in zip(*groups) for slot in pair]


def free_seat(c,room_id,fighters,*,observer=False,camp=None,exclude=None):
    """只在合法战斗/观战席中找空位；当前本人切换可复用原席，唯一约束仍保护并发。"""
    used={r['slot'] for r in c.select('room_members',Q(room_id=room_id)) if r['account_id']!=exclude}
    return next((slot for slot in seats(fighters,observer=observer,camp=camp) if slot not in used),None)


def invitation_for(c,room_id,account_id,now):
    """邀请绑定实际接收者且只活五分钟；与当前房主存在拉黑关系时立即失效，不绕过口令。"""
    room=c.select('rooms',Q(id=room_id)).fetchone()
    if not room or room['state']!='OPEN':return None
    blocked=(Q(account_id=account_id,peer_id=room['owner'])|Q(account_id=room['owner'],peer_id=account_id))&Q(status='BLOCKED')
    if c.select('native_friends',blocked):return None
    return c.select('room_invitations',Q(room_id=room_id,account_id=account_id,expires_at__gt=now)).fetchone()


def reflow_seats(c,room_id,fighters):
    """缩容或切图仅搬离失效席号，保留仍合法的位置；先去临时席防止MySQL逐行唯一键冲突。"""
    entries=list(c.select('room_members',Q(room_id=room_id),order=('slot',)))
    presence={r['account_id']:r['observer'] for r in c.select('native_room_presence',Q(account_id__in=[e['account_id'] for e in entries]))}
    used={e['slot'] for e in entries if e['slot'] in seats(fighters,observer=presence[e['account_id']])}
    moves=[]
    for entry in entries:
        allowed=seats(fighters,observer=presence[entry['account_id']])
        if entry['slot'] in allowed:continue
        slot=next((p for p in allowed if p not in used),None)
        if slot is None:raise Fault('ROOM_FULL','新人数不能映射当前成员')
        used.add(slot);moves.append((entry['account_id'],slot))
    for index,(aid,_) in enumerate(moves):c.update('room_members',Q(account_id=aid),dict(slot=64+index))
    for aid,slot in moves:c.update('room_members',Q(account_id=aid),dict(slot=slot))


def changed(service,c,room_id,kind='room.changed'):
    """按接收者记录变更，只有实际成员收到；具体公开快照在读取时按当前权限构造。"""
    room=c.select('rooms',Q(id=room_id)).fetchone()
    binding=c.select('native_room_bindings',Q(room_id=room_id)).fetchone()
    if not room or not binding:return
    for member in c.select('room_members',Q(room_id=room_id)):
        event(service,c,member['account_id'],kind,dict(native_room_id=binding['id'],revision=room['revision']))


def detach(service,c,account_id,reason):
    """释放本人席位、按原最小席位移交房主；最后成员退出仍保留房号和历史实例。"""
    member=c.select('room_members',Q(account_id=account_id)).fetchone()
    c.delete('native_room_presence',Q(account_id=account_id))
    if member:
        room=c.select('rooms',Q(id=member['room_id'])).fetchone()
        if room['match_id']:c.update('match_players',Q(match_id=room['match_id'],account_id=account_id),dict(eligible=0))
        from .rooms import leave_room
        leave_room(service,c,account_id,member['room_id'])
        # 房主退出优先移交给仍在战斗位的成员，防止低号观战位抢先而让开始按钮失效。
        if room['owner']==account_id:
            fighters=c.table('room_members').filter(room_id=room['id'],account_id__in=c.table('native_room_presence').filter(observer=False).values('account_id')).order_by('slot').first()
            if fighters:c.update('rooms',Q(id=room['id']),dict(owner=fighters.account_id))
        changed(service,c,member['room_id'])
        event(service,c,account_id,'room.left',dict(reason=reason))


def expire(service,c):
    """撤销、到期、停用和安全版本变化均释放席位；活跃玩家会话不能保住已撤销游戏的房间。"""
    invalid=(Q(session__revoked=True)|Q(session__expires_at__lte=service.clock())|
        Q(session__session__revoked=1)|Q(session__session__expires_at__lte=service.clock())|
        ~Q(account__status='ACTIVE')|~Q(account__security_version=F('session__security_version')))
    ids=list(c.table('native_room_presence').filter(invalid).values_list('account_id',flat=True)[:1000])
    for aid in ids:detach(service,c,aid,'GAME_SESSION_EXPIRED')
    return len(ids)


def membership(c,account,session,*,required=True):
    """账号与游戏会话共同授权，另一有效连接不能抢占或引用他人的房号修改席位。"""
    present=c.select('native_room_presence',Q(account_id=account['id'])).fetchone()
    member=c.select('room_members',Q(account_id=account['id'])).fetchone()
    if present and present['session_id']!=session['digest']:raise Fault('ROOM_SESSION','房间由另一个游戏会话控制',409)
    if not present or not member:
        if required:raise Fault('ROOM_MEMBER','当前不在原生房间',409)
        return None
    binding=c.select('native_room_bindings',Q(room_id=member['room_id'])).fetchone()
    if not binding:raise Fault('ROOM_MEMBER','当前席位不是原生房间',409)
    return member,present,binding,c.select('rooms',Q(id=member['room_id'])).fetchone()


def snapshot(c,room_id,*,owner_account=None):
    """仅返回房间公开字段，不返回密码摘要、游戏凭据、玩家库存或节点令牌。"""
    room=c.select('rooms',Q(id=room_id)).fetchone()
    binding=c.select('native_room_bindings',Q(room_id=room_id)).fetchone()
    if not room or not binding or room['state']=='CLOSED':return None
    access=c.select('room_settings',Q(room_id=room_id)).fetchone()
    players=[]
    for member in c.select('room_members',Q(room_id=room_id),order=('slot',)):
        account=c.select('accounts',Q(id=member['account_id'])).fetchone()
        character=c.select('characters',Q(account_id=member['account_id'])).fetchone()
        presence=c.select('native_room_presence',Q(account_id=member['account_id'])).fetchone()
        if not presence:raise Fault('ROOM_MEMBER','原生席位缺少游戏会话绑定',409)
        players.append(dict(game_id=account['game_id'],nickname=character['nickname'],slot=member['slot'],
            ready=bool(member['ready']),observer=presence['observer'],in_match=presence['in_match'],
            owner=room['owner']==member['account_id']))
    flags=binding['settings']['Flags']
    flags|=4 if access and access['password_hash'] else 0
    fighters=sum(not p['observer'] for p in players);observers=len(players)-fighters
    if fighters>=binding['fighter_capacity'] and observers>=binding['observer_capacity']:flags|=2
    if room['state']=='RUNNING':flags|=1
    value=dict(binding['settings'],Flags=flags)
    result=dict(room_id=room_id,native_room_id=binding['id'],display_id=binding['display_id'],
        name=binding['name'],main_channel=binding['main_channel'],sub_channel=binding['sub_channel'],
        settings=value,fighter_capacity=binding['fighter_capacity'],observer_capacity=binding['observer_capacity'],
        players=players,revision=room['revision'],state=room['state'],match_id=room['match_id'],
        password_required=bool(access and access['password_hash']),rule_version=binding['rule_version'],native_ready=False)
    if owner_account==room['owner']:
        # 只用于本人实时读视图；命令回执、频道列表和审计保持不含明文口令。
        result['owner_password']=room_secret(room_id,binding['sealed_password'],decrypt=True)
    return result


def command(service,c,account,session,action,data):
    """与外层幂等、审计共用一笔MySQL事务；失败不保留半个房间、席位或分配请求。"""
    scope(data);expire(service,c)
    if action=='room.create':
        if c.select('room_members',Q(account_id=account['id'])):raise Fault('ALREADY_IN_ROOM','请先退出当前房间',409)
        value,rule,fighters,observers=settings(data)
        name=native_text(data.get('name'),32,empty=False);hashed=password_digest(data.get('password',''))
        channel=integer(data.get('sub_channel',1),1,1,'sub_channel')
        used=set(c.table('native_room_bindings').exclude(room__state='CLOSED').filter(sub_channel=channel).values_list('display_id',flat=True))
        display=next((i for i in range(1,32768) if i not in used),None)
        if display is None:raise Fault('ROOM_FULL','频道展示房号已用尽',409)
        rid=identifier()
        c.insert('rooms',dict(id=rid,owner=account['id'],map_key='cn2020:'+str(rule['map_id']),capacity=fighters+observers,state='OPEN'))
        c.insert('room_settings',dict(room_id=rid,channel='native:1',difficulty=str(value['SubModeId']),password_hash=hashed,invite_only=False))
        c.insert('native_room_bindings',dict(room_id=rid,display_id=display,main_channel=1,sub_channel=channel,
            name=name,fighter_capacity=fighters,observer_capacity=observers,settings=value,
            sealed_password=room_secret(rid,data.get('password','')),rule_version=VERSION,created_at=service.clock()))
        c.insert('room_members',dict(account_id=account['id'],room_id=rid,slot=0,ready=1,joined_at=service.clock()))
        c.insert('native_room_presence',dict(account_id=account['id'],session_id=session['digest']))
        changed(service,c,rid);return snapshot(c,rid)
    if action=='room.join':
        if c.select('room_members',Q(account_id=account['id'])):raise Fault('ALREADY_IN_ROOM','请先退出当前房间',409)
        number=integer(data.get('native_room_id'),1,2**63-1,'native_room_id')
        binding=c.select('native_room_bindings',Q(id=number)).fetchone()
        room=c.select('rooms',Q(id=binding['room_id'])).fetchone() if binding else None
        if not room or room['state']=='CLOSED':raise Fault('NOT_FOUND','房间不存在',404)
        if room['state']!='OPEN':raise Fault('ROOM_STATE','房间正在分配或游戏中',409)
        access=c.select('room_settings',Q(room_id=room['id'])).fetchone()
        password=native_text(data.get('password',''),8)
        invitation=invitation_for(c,room['id'],account['id'],service.clock())
        if not invitation and access['password_hash'] and not verify_password(password,access['password_hash']):raise Fault('ROOM_PASSWORD','房间口令不正确',403)
        members=c.select('room_members',Q(room_id=room['id']))
        observers=c.table('native_room_presence').filter(account_id__in=[m['account_id'] for m in members],observer=True).count()
        if len(members)-observers>=binding['fighter_capacity']:raise Fault('ROOM_FULL','战斗席位已满',409)
        slot=free_seat(c,room['id'],binding['fighter_capacity'])
        if slot is None:raise Fault('ROOM_FULL','没有合法战斗席位',409)
        c.insert('room_members',dict(account_id=account['id'],room_id=room['id'],slot=slot,ready=0,joined_at=service.clock()))
        c.insert('native_room_presence',dict(account_id=account['id'],session_id=session['digest']))
        if invitation:c.delete('room_invitations',Q(digest=invitation['digest']))
        c.update('rooms',Q(id=room['id']),dict(revision=F('revision')+1))
        changed(service,c,room['id']);return snapshot(c,room['id'])
    member,present,binding,room=membership(c,account,session)
    rid=room['id']
    if 'native_room_id' in data and data['native_room_id']!=binding['id']:
        raise Fault('ROOM_MEMBER','请求房号与本人席位不一致',409)
    if action=='room.leave':detach(service,c,account['id'],'PLAYER_LEFT');return dict(status='LEFT',native_room_id=binding['id'])
    if data.get('expected_revision')!=room['revision']:raise Fault('REVISION_CONFLICT','房间已变化，请刷新',409)
    if action in ('room.configure','room.kick','room.start','room.cancel','room.rename','room.transfer','room.invite') and room['owner']!=account['id']:
        raise Fault('ROOM_OWNER','仅房主可执行该操作',403)
    if action=='room.cancel':
        if room['state'] not in ('ALLOCATING','LOADING'):raise Fault('ROOM_STATE','当前没有等待中的分配',409)
        c.update('matches',Q(id=room['match_id']),dict(state='STOPPED'))
        c.update('rooms',Q(id=rid),dict(state='OPEN',match_id=None,revision=F('revision')+1))
        changed(service,c,rid);return snapshot(c,rid)
    if action=='room.in_match':
        value=data.get('in_match')
        if type(value) is not bool:raise Fault('INVALID','入局状态必须为布尔值')
        if value and room['state']!='RUNNING':raise Fault('NODE_NOT_READY','原生实例尚未就绪',409)
        c.update('native_room_presence',Q(account_id=account['id']),dict(in_match=value))
        c.update('rooms',Q(id=rid),dict(revision=F('revision')+1));changed(service,c,rid);return snapshot(c,rid)
    if room['state']!='OPEN':raise Fault('ROOM_STATE','分配后配置和席位已冻结',409)
    if action=='room.ready':
        ready=data.get('ready');observer=data.get('observer',present['observer'])
        if type(ready) is not bool or type(observer) is not bool:raise Fault('INVALID','准备和观战必须为布尔值')
        camp=data.get('camp')
        if camp is not None:camp=integer(camp,0,1,'camp')
        current=snapshot(c,rid);others=[p for p in current['players'] if p['game_id']!=account['game_id']]
        if observer and sum(p['observer'] for p in others)>=binding['observer_capacity'] or not observer and sum(not p['observer'] for p in others)>=binding['fighter_capacity']:
            raise Fault('ROOM_FULL','目标席位已满',409)
        if observer and room['owner']==account['id']:raise Fault('ROOM_OWNER','房主需先移交后观战',409)
        # 不指定阵营的单纯准备保留原席；切换观战/阵营必须真实移动到对应区间。
        slot=member['slot'] if member['slot'] in seats(binding['fighter_capacity'],observer=observer,camp=camp) else free_seat(c,rid,binding['fighter_capacity'],observer=observer,camp=camp,exclude=account['id'])
        if slot is None:raise Fault('ROOM_FULL','目标阵营席位已满',409)
        c.update('room_members',Q(account_id=account['id']),dict(ready=int(ready),slot=slot))
        c.update('native_room_presence',Q(account_id=account['id']),dict(observer=observer))
    elif action=='room.rename':
        c.update('native_room_bindings',Q(id=binding['id']),dict(name=native_text(data.get('name'),32,empty=False)))
    elif action=='room.transfer':
        target=c.select('accounts',Q(game_id=integer(data.get('peer_game_id'),1,2**31-1,'peer_game_id'))).fetchone()
        if not target or target['id']==account['id'] or not c.select('room_members',Q(account_id=target['id'],room_id=rid)) or not c.select('native_room_presence',Q(account_id=target['id'],observer=False)):
            raise Fault('ROOM_MEMBER','只能移交给同房战斗成员',409)
        c.update('rooms',Q(id=rid),dict(owner=target['id']))
        c.update('room_members',Q(account_id=target['id']),dict(ready=1))
    elif action=='room.invite':
        targets=data.get('peer_game_ids')
        if not isinstance(targets,list) or not 1<=len(targets)<=16 or any(type(gid) is not int for gid in targets) or len(set(targets))!=len(targets):raise Fault('INVALID','邀请人数或重复目标无效')
        inviter=c.select('characters',Q(account_id=account['id'])).fetchone()['nickname']
        for gid in targets:
            peer=c.select('accounts',Q(game_id=integer(gid,1,2**31-1,'peer_game_id'))).fetchone()
            if not peer or peer['id']==account['id']:raise Fault('NOT_FOUND','邀请对象不存在',404)
            service._active(c,peer)
            if c.select('native_friends',(Q(account_id=account['id'],peer_id=peer['id'])|Q(account_id=peer['id'],peer_id=account['id']))&Q(status='BLOCKED')):
                raise Fault('SOCIAL_DENIED','不能向拉黑关系发送邀请',403)
            c.delete('room_invitations',Q(room_id=rid,account_id=peer['id']))
            c.insert('room_invitations',dict(digest=token_hash(secrets.token_urlsafe(32)),room_id=rid,account_id=peer['id'],expires_at=service.clock()+300))
            event(service,c,peer['id'],'room.invited',dict(native_room_id=binding['id'],inviter_game_id=account['game_id'],inviter_nickname=inviter))
    elif action=='room.kick':
        target=c.select('accounts',Q(game_id=integer(data.get('peer_game_id'),1,2**31-1,'peer_game_id'))).fetchone()
        if not target or target['id']==account['id'] or not c.select('room_members',Q(account_id=target['id'],room_id=rid)):
            raise Fault('ROOM_MEMBER','不能踢自己或其他房间的玩家',409)
        detach(service,c,target['id'],'KICKED_BY_OWNER')
        return snapshot(c,rid)
    elif action=='room.configure':
        value,rule,fighters,observers=settings(dict(data,fighter_capacity=data.get('fighter_capacity',binding['fighter_capacity']),observer_capacity=data.get('observer_capacity',binding['observer_capacity'])))
        current=snapshot(c,rid)
        if sum(not p['observer'] for p in current['players'])>fighters or sum(p['observer'] for p in current['players'])>observers:
            raise Fault('ROOM_FULL','新配置不能容纳当前成员',409)
        reflow_seats(c,rid,fighters)
        c.update('native_room_bindings',Q(id=binding['id']),dict(settings=value,fighter_capacity=fighters,observer_capacity=observers))
        c.update('rooms',Q(id=rid),dict(map_key='cn2020:'+str(rule['map_id']),capacity=fighters+observers))
        c.update('room_settings',Q(room_id=rid),dict(difficulty=str(value['SubModeId'])))
        c.update('room_members',Q(room_id=rid),dict(ready=0))
        c.update('room_members',Q(account_id=account['id']),dict(ready=1))
    elif action=='room.start':
        current=snapshot(c,rid);rule=catalog()['maps'][str(binding['settings']['MapId'])]
        if not rule['main_resource']:raise Fault('UNVERIFIED_RESOURCE','当前构建缺少主地图资源',409)
        players=[p for p in current['players'] if not p['observer']]
        # 原生开始按钮属于房主，房主不另按准备；分配超时后也可直接重试下一局。
        if not players or any(not p['ready'] and not p['owner'] for p in players):raise Fault('NOT_READY','战斗成员尚未全部准备',409)
        match_id,instance,epoch=identifier(),identifier(),identifier();roster=[]
        for entry in c.select('room_members',Q(room_id=rid),order=('slot',)):
            who=service._account(c,entry['account_id']);service._active(c,who)
            presence=c.select('native_room_presence',Q(account_id=who['id'])).fetchone()
            roster.append(dict(account_id=who['id'],game_id=who['game_id'],security_version=who['security_version'],
                slot=entry['slot'],observer=presence['observer'],equipment=list(c.equipment_snapshot(who['id'],service.clock()))))
        frozen=dict(catalog=service.catalog['id'],map_key=room['map_key'],revision=room['revision'],players=roster,
            native_room_rule=VERSION,native_settings=binding['settings'],map=rule,reward={},evidence='NATIVE_ALLOCATION_PENDING')
        c.insert('matches',dict(id=match_id,room_id=rid,instance_id=instance,epoch=epoch,state='STARTING',
            snapshot=json.dumps(frozen,sort_keys=True,separators=(',',':')),node_token_hash='',created_at=service.clock(),lease_until=service.clock()+180))
        for who in roster:c.insert('match_players',dict(match_id=match_id,account_id=who['account_id'],security_version=who['security_version'],eligible=int(not who['observer'])))
        c.update('rooms',Q(id=rid),dict(state='ALLOCATING',match_id=match_id))
    else:raise Fault('NOT_FOUND','未知原生房间操作',404)
    c.update('rooms',Q(id=rid),dict(revision=F('revision')+1));changed(service,c,rid)
    return snapshot(c,rid)


def query(service,c,account,session,view,data):
    """公开频道列表有界分页；本人当前房间必须经过会话绑定，不把任意房号当本人权限。"""
    scope(data);expire(service,c)
    if view=='room.catalog':return catalog()
    if view=='room.invitation':
        number=integer(data.get('native_room_id'),1,2**63-1,'native_room_id')
        binding=c.select('native_room_bindings',Q(id=number)).fetchone()
        if not binding or not invitation_for(c,binding['room_id'],account['id'],service.clock()):return None
        room=snapshot(c,binding['room_id'])
        return room if room and room['state']=='OPEN' else None
    if view=='room.current':
        own=membership(c,account,session,required=False)
        return snapshot(c,own[3]['id'],owner_account=account['id']) if own else None
    if view=='room.peer':
        peer=c.select('accounts',Q(game_id=integer(data.get('peer_game_id'),1,2**31-1,'peer_game_id'))).fetchone()
        if not peer:return None
        if not c.select('native_friends',Q(account_id=account['id'],peer_id=peer['id'],status='ACCEPTED')) or c.select('native_friends',Q(account_id=peer['id'],peer_id=account['id'],status='BLOCKED')):
            raise Fault('SOCIAL_DENIED','仅可跟随有效好友的公开房间',403)
        member=c.select('room_members',Q(account_id=peer['id'])).fetchone()
        return snapshot(c,member['room_id']) if member else None
    if view!='room.list':raise Fault('NOT_FOUND','未知原生房间读视图',404)
    start=integer(data.get('start',0),0,32767,'start');count=integer(data.get('count',32),1,32,'count')
    bindings=c.table('native_room_bindings').exclude(room__state='CLOSED').filter(sub_channel=1)
    mode=integer(data.get('mode_id',0),0,2**31-1,'mode_id');map_id=integer(data.get('map_id',0),-1,32767,'map_id')
    if mode:bindings=bindings.filter(settings__ModeId=mode)
    if map_id>0:bindings=bindings.filter(settings__MapId=map_id)
    total=bindings.count()
    return dict(total=total,rooms=[snapshot(c,b.room_id) for b in bindings.order_by('display_id','id')[start:start+count]],rule_version=VERSION)
