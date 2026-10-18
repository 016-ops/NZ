"""游戏凭据专用业务入口：鉴权、幂等、事务提交和本人增量回执，不接受管理员身份替代游戏身份。"""
from django.db.models import Q
import json
from .launcher import authorize_game_session
from .native_actions import inventory_action, shop_quote, shop_commit
from .native_features_catalog import FEATURE_VERSION, feature_catalog, exchange_catalog
from .native_rewards import checkin_status, checkin_claim, draw, confirm, selection, active_rule, drawn_keys, DRAW_NAMES
from .native_social import friendship, friends_view, clan_command, clan_view, clan_public, public_player
from .service import Fault, integer, text_field
from .native_rooms import ACTIONS as ROOM_ACTIONS

ITEM_ACTIONS = {'inventory.'+x for x in ('delete','skin','swallow','unswallow','use','upgrade','reborn','disassemble','exchange','repay','craft')}
FRIEND_ACTIONS = {'friend.'+x for x in ('request','accept','reject','delete','edit','block','unblock','message','group_create','group_delete','group_rename')}
CLAN_ACTIONS = {'clan.'+x for x in ('create','apply','invite','accept','reject','review','message','announcement','leave','kick','role','transfer','dismiss')}
ENGINEERING_ACTIONS = {'engineering.'+x for x in ('compose','decompose','material_compose','material_decompose')}
ACTIONS = ITEM_ACTIONS | FRIEND_ACTIONS | CLAN_ACTIONS | ENGINEERING_ACTIONS | ROOM_ACTIONS | {'inventory.open','inventory.repair','inventory.sell','shop.purchase','shop.gift','shop.renew','checkin.claim','draw.commit','draw.confirm','draw.select','draw.reset','workshop.draw','workshop.confirm','pve.store.claim','events.ack'}


def scope(data):
    """新功能合同必须显式协商版本；旧换装接口继续使用原兼容版本。"""
    if data.get('feature_version') != FEATURE_VERSION:
        raise Fault('FEATURE_VERSION','需要当前功能目录版本',409)


def delta(service, c, account, operations, extra_ids=()):
    """根据本次资产流水回读本人变化实例和钱包；耗尽实例用removed表达，不下载整个仓库。"""
    own_operations = c.table('asset_operations').filter(id__in=operations,account_id=account['id']).values('id')
    ids = set(c.table('asset_entries').filter(operation_id__in=own_operations,instance_id__isnull=False).values_list('instance_id',flat=True))
    # 购买时复用旧组件只改变挂接，不产生资产流水；只加入本人实例，不让赠礼接收者混入付款人增量。
    ids.update(c.table('inventory').filter(account_id=account['id'],id__in=extra_ids).values_list('id',flat=True))
    return item_delta(c,account,ids)


def item_delta(c, account, ids):
    """按已授权实例集合回读状态，已耗尽实例显式删除；不会暴露付款人给其他接收者的库存。"""
    from .native_wallet import snapshot
    changed = []
    equipment = dict(c.table('equipment').filter(account_id=account['id']).values_list('inventory_id','slot'))
    from .native_pve_benefits import gain_types
    origins=gain_types(c,account['id'],ids)
    for row in c.select('inventory',Q(id__in=ids,account_id=account['id'])):
        binding = c.select('native_inventory_bindings',Q(inventory_id=row['id'])).fetchone()
        state = c.select('native_item_states',Q(inventory_id=row['id'])).fetchone()
        if binding:
            changed.append({'gid':binding['id']+1000,'instance_id':row['id'],'item_id':int(row['item_key'].split(':')[1]),
                'quantity':row['quantity'],'expires_at':row['expires_at'],'version':row['version'],
                'state':state['state'] if state else {},'equipment_slot':equipment.get(row['id']),'gain_type':origins.get(row['id'],48)})
    visible_ids = {r['instance_id'] for r in changed}
    # 已删除GID可由请求和旧缓存关联；返回不可重用的内部实例ID，避免制造不存在的绑定。
    from .native_inventory import sync_revision
    return {'items':changed,'removed_instance_ids':sorted(set(ids)-visible_ids),'wallet':snapshot(c,account['id']),
            'revision':sync_revision(c,account['id'])}


def sync_changes(c, account, data):
    """按连续资产版本补取最多200实例；未记录的历史间隙明确要求重载，不跳过接收者变化。"""
    from .native_inventory import sync_revision
    current=sync_revision(c,account['id'])
    since=integer(data.get('after_revision'),0,current,'after_revision')
    if since==current:
        return {'revision':current,'more':False,'delta':None}
    records=c.select('native_sync_changes',Q(account_id=account['id'],revision__gt=since),order=('revision',),limit=200)
    expected=since+1;ids=set();revision=since
    for row in records:
        if row['revision']!=expected or row['instance_ids'] is None:
            return {'reload':True,'revision':current}
        if len(ids | set(row['instance_ids']))>200:
            if not ids:
                return {'reload':True,'revision':current}
            break
        ids.update(row['instance_ids']);revision=row['revision'];expected+=1
    if revision==since:
        return {'reload':True,'revision':current}
    result=item_delta(c,account,ids)
    # 状态读取来自本次锁内最新事实，游标只推进至实际覆盖的记录，后页仍会覆盖后来变更。
    result['revision']=revision
    return {'revision':revision,'more':revision<current,'delta':result}


def pending_events(service, c, session, account, data):
    """更新本人的真实在线心跳并读取有界未送达事件；旧事件接口与会话快照共用过滤规则。"""
    values = {'game_id':session['digest'],'last_seen':service.clock()}
    if c.select('native_presence',Q(account_id=account['id'])):
        c.update('native_presence',Q(account_id=account['id']),values)
    else:
        c.insert('native_presence',dict(values,account_id=account['id']))
    after = integer(data.get('after_id',0),0,2**63-1,'after_id')
    condition=Q(account_id=account['id'],acknowledged=False,id__gt=after)
    if 'kinds' in data:
        kinds=data['kinds']
        if not isinstance(kinds,list) or not 1<=len(kinds)<=20 or any(not isinstance(k,str) or len(k)>80 for k in kinds):
            raise Fault('INVALID','事件类型过滤无效')
        condition &= Q(kind__in=kinds)
    return list(c.select('native_player_events',condition,order=('id',),limit=100))


def session_snapshot(service, c, session, account, data):
    """一次鉴权事务读取增量、好友、战队与本人事件，减少慢网络心跳阻塞按钮请求。

    开奖动画期间可暂缓资产投影，但事件和在线状态继续同步。事件附带的发送者仅含
    游戏号与昵称，不返回邮箱、凭据或对端库存；分页和历史版本空洞仍按原同步合同处理。
    """
    include_inventory=data.get('include_inventory',True)
    if type(include_inventory) is not bool:
        raise Fault('INVALID','库存同步开关必须为布尔值')
    events=pending_events(service,c,session,account,data)
    game_ids={row['payload'].get('game_id',row['payload'].get('from_game_id')) for row in events
              if row['kind'].startswith('friend.')}
    players=c.select('accounts',Q(game_id__in=[i for i in game_ids if type(i) is int]),columns=['id','game_id'])
    names=dict(c.table('characters').filter(account_id__in=[p['id'] for p in players]).values_list('account_id','nickname'))
    senders={p['game_id']:{'game_id':p['game_id'],'nickname':names.get(p['id'],'')} for p in players}
    for row in events:
        sender=senders.get(row['payload'].get('game_id',row['payload'].get('from_game_id')))
        if sender:
            row['sender']=sender
    include_room=data.get('include_room',False)
    if type(include_room) is not bool:raise Fault('INVALID','房间同步开关必须为布尔值')
    room=None
    if include_room:
        from .native_rooms import query as room_query
        room=room_query(service,c,account,session,'room.current',data)
    return {'inventory':sync_changes(c,account,data) if include_inventory else None,
            'friends':friends_view(service,c,account),'clan':clan_view(service,c,account),'events':events,'room':room}


def command(service, game_token, data):
    """游戏命令在统一部署事务中重新鉴权；幂等回执仅在提交成功后由HTTP发送。"""
    scope(data)
    action = data.get('action')
    if not isinstance(action,str) or action not in ACTIONS:
        raise Fault('NOT_FOUND','未知游戏命令',404)
    request_id,key,reason = service._metadata(data)
    summary = {k:v for k,v in data.items() if k not in ('request_id','idempotency_key')}
    if action in ROOM_ACTIONS and 'password' in summary:
        # 房间口令低熵，幂等摘要使用服务密钥HMAC，避免requests表形成快速离线口令猜测器。
        import hashlib,hmac
        from django.conf import settings
        summary['password']={'proof':hmac.new(settings.SECRET_KEY.encode(),json.dumps(summary['password'],ensure_ascii=True).encode(),hashlib.sha256).hexdigest()}
    with service.db.transaction() as c:
        session,account = authorize_game_session(service,c,game_token,data)
        request_scope = 'native:'+account['id']
        previous = service._prior(c,request_scope,key,summary)
        if previous is not None:
            return previous
        if action in ROOM_ACTIONS:
            from .native_rooms import command as room_command
            result=room_command(service,c,account,session,action,data)
        elif action in ('inventory.repair','inventory.sell'):
            from . import native_maintenance
            result = native_maintenance.command(service,c,account,action,data)
        elif action=='inventory.open':
            from .native_containers import open_container
            result = open_container(service,c,account,data)
        elif action in ITEM_ACTIONS:
            result = inventory_action(service,c,account,action,data)
        elif action.startswith('shop.'):
            result = shop_commit(service,c,account,action,data)
        elif action in ENGINEERING_ACTIONS:
            from . import native_engineering
            result = native_engineering.command(service,c,account,action,data)
        elif action in FRIEND_ACTIONS:
            result = friendship(service,c,account,action,data)
        elif action in CLAN_ACTIONS:
            result = clan_command(service,c,account,action,data)
        elif action=='checkin.claim':
            result = checkin_claim(service,c,account,data)
        elif action=='draw.commit':
            result = draw(service,c,account,data)
        elif action=='draw.confirm':
            result = confirm(service,c,account,data)
        elif action in ('draw.select','draw.reset'):
            result = selection(service,c,account,action,data)
        elif action=='pve.store.claim':
            from .native_pve_benefits import claim
            result=claim(service,c,account,data)
        elif action in ('workshop.draw','workshop.confirm'):
            from . import native_workshop
            result = (native_workshop.draw if action=='workshop.draw' else native_workshop.confirm)(service,c,account,data)
        else:
            ids = data.get('event_ids')
            if not isinstance(ids,list) or len(ids)>100 or any(type(i) is not int or i<1 for i in ids):
                raise Fault('INVALID','事件确认列表无效')
            if c.count('native_player_events',Q(account_id=account['id'],id__in=ids)).fetchone()[0]!=len(set(ids)):
                raise Fault('FORBIDDEN','只能确认本人的事件',403)
            c.update('native_player_events',Q(account_id=account['id'],id__in=ids),{'acknowledged':True})
            result = {'status':'ACKNOWLEDGED'}
        result = dict(result,feature_version=FEATURE_VERSION,game_id=account['game_id'],request_id=request_id)
        if result.get('operations'):
            result['delta'] = delta(service,c,account,result['operations'],
                [row['instance_id'] for row in result.get('avatar_equipment',[])])
        service._audit(c,account['id'],action,account['id'],request_id,reason,
                       {k:v for k,v in result.items() if k not in ('delta','definition')})
        service._remember(c,request_scope,key,summary,result)
        return result


def query(service, game_token, data):
    """本人功能读视图和有界事件读取；规则、订单与玩家身份不跨账号泄露。"""
    scope(data)
    view = data.get('view')
    with service.db.transaction() as c:
        session,account = authorize_game_session(service,c,game_token,data)
        if view=='operation':
            # 不确定提交先查此本人范围回执；未找到才能使用同一请求、同一幂等键重发。
            key = text_field(data.get('idempotency_key'),'idempotency_key',128)
            row = c.select('requests',Q(scope='native:'+account['id'],key=key)).fetchone()
            return {'status':'COMMITTED','result':json.loads(row['result'])} if row else {'status':'NOT_FOUND'}
        if view in ('room.catalog','room.list','room.current','room.peer','room.invitation'):
            from .native_rooms import query as room_query
            return room_query(service,c,account,session,view,data)
        if view=='shop.quote':
            return shop_quote(service,c,account,data,renew_gid=data.get('renew_gid'))
        if view=='pve.benefits':
            from .native_pve_benefits import snapshot
            return snapshot(service,c,account)
        if view=='engineering':
            from .native_engineering import snapshot
            return snapshot(service,c,account)
        if view=='inventory.recipes':
            catalog=exchange_catalog()
            item_id=integer(data.get('item_id'),1,2**31-1,'item_id')
            return {'rule_version':catalog['version'],
                    'recipes':[r for r in catalog['recipes'] if r['ExchangeItemId']==item_id]}
        if view=='friends':
            return friends_view(service,c,account)
        if view=='clan':
            return clan_view(service,c,account)
        if view=='clans':
            search = text_field(data['search'],'search',32) if data.get('search') else ''
            rows=c.select('native_clans',Q(active=True,name__icontains=search),columns=['id'],order=('created_at','id'),limit=36)
            return [clan_public(c,r['id']) for r in rows]
        if view=='clan.public':
            binding=c.select('native_clan_bindings',Q(id=integer(data.get('native_clan_id'),1,2**63-1,'native_clan_id'))).fetchone()
            if not binding:
                raise Fault('NOT_FOUND','战队不存在',404)
            return clan_public(c,binding['clan_id'])
        if view=='players':
            search = text_field(data.get('search'),'search',32)
            rows = c.select('characters',Q(nickname__icontains=search),columns=['account_id'],limit=20)
            return [public_player(c,r['account_id']) for r in rows]
        if view=='player':
            game_id=data.get('game_id')
            if game_id:
                row=c.select('accounts',Q(game_id=integer(game_id,1,2**63-1,'game_id'),status='ACTIVE')).fetchone()
                if row:
                    return public_player(c,row['id'])
            else:
                name=text_field(data.get('nickname'),'nickname',32)
                character=c.select('characters',Q(nickname=name)).fetchone()
                if character:
                    service._active(c,service._account(c,character['account_id']))
                    return public_player(c,character['account_id'])
            raise Fault('NOT_FOUND','玩家不存在',404)
        if view=='checkin':
            return checkin_status(service,c,account['id'],data.get('campaign','default'))
        if view=='workshop.state':
            from .native_workshop import state
            return state(service,c,account)
        if view in ('draw','draw.state'):
            name = 'draw:'+text_field(data.get('pool'),'pool',30)
            if name not in DRAW_NAMES:
                raise Fault('INVALID','未知抽奖类别')
            rule=dict(active_rule(service,c,name))
            round_id=''
            if name=='draw:selected':
                saved=c.select('native_draw_selections',Q(account_id=account['id'],rule_id=rule['id'])).fetchone()
                rule['selected']=saved['selected'] if saved else []
                rule['pool_keys']=(saved['pool_keys'] or saved['selected']) if saved else []
                round_id=saved['round_id'] if saved else ''
            rule['drawn_keys']=drawn_keys(c,account['id'],rule['id'],round_id)
            if view=='draw.state':
                # 原生页签短时间会连续查询；一次事务同时返回规则和待确认回执，避免跨网络逐次往返。
                records=list(c.select('native_receipts',Q(account_id=account['id'],kind='draw',result__pool=data['pool']),
                    order=('confirmed','-created_at','id'),limit=1))
                return {'rule':rule,'receipts':records}
            return rule
        if view=='receipts':
            condition=Q(account_id=account['id'])
            if data.get('pool'):
                if 'draw:'+str(data['pool']) not in DRAW_NAMES:
                    raise Fault('INVALID','未知抽奖类别')
                condition &= Q(kind='draw',result__pool=data['pool'])
            # 未确认回执优先，避免大量购买把唯一待确认结果挤出读窗口。
            return list(c.select('native_receipts',condition,order=('confirmed','-created_at','id'),limit=50))
        if view=='inventory':
            from .native_inventory import page
            return page(service,c,account,data)
        if view=='inventory.changes':
            return sync_changes(c,account,data)
        if view=='session.sync':
            return session_snapshot(service,c,session,account,data)
        if view=='events':
            return pending_events(service,c,session,account,data)
        if view=='capabilities':
            return {'feature_version':FEATURE_VERSION,'inventory_limit':None,'actions':sorted(ACTIONS),
                    'native_wire_status':'NOT_YET_BOUND','combat_verified':False,'item_count':len(feature_catalog()['items'])}
        raise Fault('NOT_FOUND','未知游戏读视图',404)
