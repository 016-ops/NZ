"""版本化活动规则、北京时间签到和服务端抽奖；发布、领取及确认均使用真实资产事务。"""
from datetime import datetime, timezone, timedelta
import secrets
from django.db.models import Q
from .native_actions import apply, consume_materials, item_rewards, event
from .native_features_catalog import FRAGMENTS
from .service import Fault, identifier, integer, text_field

BEIJING = timezone(timedelta(hours=8))
DRAW_NAMES = {'draw:'+name for name in (*FRAGMENTS,'workshop')}


def active_rule(service, c, namespace):
    """读取最新启用快照；活动时间由服务端判断，不信任客户端日历。"""
    rule = c.select('native_rule_revisions',Q(namespace=namespace,enabled=True),order=('-revision',),limit=1).fetchone()
    if not rule:
        raise Fault('ACTIVITY_DISABLED','活动尚未配置或已停用',409)
    definition = rule['definition']
    if definition.get('starts_at',0)>service.clock() or (definition.get('ends_at') is not None and service.clock()>=definition['ends_at']):
        raise Fault('ACTIVITY_CLOSED','活动不在开放时间内',409)
    return rule


def validate_rule(service, namespace, definition):
    """发布前严格检查资源、周期和权重；后台预览复用此函数但不写数据库。"""
    if not isinstance(definition,dict):
        raise Fault('INVALID','规则必须是对象')
    if namespace=='draw:workshop' and 'vouchers' in definition and 'recipes' not in definition:
        raise Fault('INVALID','工坊券规则必须保留独立碎片兑换配方')
    for key in ('starts_at','ends_at'):
        if definition.get(key) is not None:
            integer(definition[key],0,2**63-1,key)
    if definition.get('ends_at') is not None and definition['ends_at']<=definition.get('starts_at',0):
        raise Fault('INVALID','活动结束必须晚于开始')
    if namespace.startswith('checkin:'):
        text_field(namespace[8:],'campaign',80)
        for group in ('daily','milestones'):
            mapping = definition.get(group,{})
            if not isinstance(mapping,dict):
                raise Fault('INVALID','签到奖励必须按天数配置')
            for day,rewards in mapping.items():
                if not isinstance(day,str) or not day.isdigit() or str(int(day))!=day:
                    raise Fault('INVALID','签到天数格式不正确')
                integer(int(day),1,31,'day')
                item_rewards(service,rewards)
        if not definition.get('daily') and not definition.get('milestones'):
            raise Fault('INVALID','至少配置一项签到奖励')
    elif namespace=='draw:workshop' and 'recipes' in definition:
        recipes=definition['recipes']
        if not isinstance(recipes,list) or not 1<=len(recipes)<=256:
            raise Fault('INVALID','工坊需要1至256条配方')
        seen=set()
        for recipe in recipes:
            if not isinstance(recipe,dict):
                raise Fault('INVALID','工坊配方必须是对象')
            index=integer(recipe.get('index'),0,255,'recipe index')
            if index in seen:
                raise Fault('INVALID','工坊配方索引重复')
            seen.add(index)
            validate_rule(service,'draw:epic',recipe)
        if 'vouchers' in definition:
            from .native_workshop import validate
            validate(service,definition['vouchers'])
    elif namespace in DRAW_NAMES:
        materials = definition.get('materials')
        validate_materials(service,materials)
        pool = definition.get('pool')
        if not isinstance(pool,list) or not 1<=len(pool)<=200:
            raise Fault('INVALID','奖池需要1至200项')
        seen = set()
        for entry in pool:
            if not isinstance(entry,dict):
                raise Fault('INVALID','奖项必须是对象')
            key = text_field(entry.get('key'),'pool key',64)
            if key in seen:
                raise Fault('INVALID','奖项标识重复')
            seen.add(key)
            integer(entry.get('weight',1),1,1000000,'weight')
            item_rewards(service,entry.get('rewards'))
        if namespace=='draw:selected':
            integer(definition.get('selection_count',1),1,len(pool),'selection_count')
            if 'pool_size' in definition:
                integer(definition['pool_size'],definition.get('selection_count',1),min(12,len(pool)),'pool_size')
            if definition.get('reset_cost',0)!=0:
                raise Fault('INVALID','当前自选兼容版本只支持免费重置')
        for flag in ('without_replacement','refund_owned'):
            if flag in definition and type(definition[flag]) is not bool:
                raise Fault('INVALID','抽奖机制必须是布尔值')
        if definition.get('duplicate_policy','allow') not in ('allow','exclude_owned'):
            raise Fault('INVALID','未知重复奖励策略')
    elif namespace.startswith('item:'):
        parts = namespace.split(':')
        if len(parts)!=3 or parts[2] not in ('use','upgrade','reborn','disassemble','exchange','repay') or 'cn2020:'+parts[1] not in service.catalog['items']:
            raise Fault('INVALID','物品配方命名空间无效')
        # 只消耗主体的转化配方允许没有额外材料；不能把空列表变成无限免费奖励。
        if definition.get('materials')!=[] or definition.get('consume_subject') is not True:
            validate_materials(service,definition.get('materials'))
        if definition.get('rewards'):
            item_rewards(service,definition['rewards'])
        if definition.get('consume_subject') and definition.get('state'):
            raise Fault('INVALID','不能同时消耗主体并修改主体状态')
        if not definition.get('rewards') and not definition.get('state'):
            raise Fault('INVALID','配方必须有产出或状态变更')
        if definition.get('state') is not None:
            state = definition['state']
            if not isinstance(state,dict) or set(state)-{'level','experience','durability','unlocks'}:
                raise Fault('INVALID','成长状态包含不允许的字段')
            for key in ('level','experience','durability'):
                if key in state:
                    integer(state[key],0,2147483647,key)
            if 'unlocks' in state and (not isinstance(state['unlocks'],list) or len(state['unlocks'])>100 or any(type(x) is not int or x<1 for x in state['unlocks'])):
                raise Fault('INVALID','解锁列表不正确')
    else:
        raise Fault('INVALID','未知活动命名空间')
    return definition


def validate_materials(service, materials):
    """材料只引用当前目录并使用正整数数量，不接收负数退款或客户端价格。"""
    if not isinstance(materials,list) or not 1<=len(materials)<=20:
        raise Fault('INVALID','需要1至20项材料')
    for row in materials:
        if not isinstance(row,dict) or 'cn2020:'+str(row.get('item_id')) not in service.catalog['items']:
            raise Fault('INVALID','未知材料')
        integer(row.get('quantity'),1,2147483647,'material quantity')


def publish(service, c, actor, action, data):
    """Owner显式预览或发布新规则；旧快照保留，版本竞争拒绝，正式签到默认没有启用规则。"""
    namespace = text_field(data.get('namespace'),'namespace',100)
    definition = validate_rule(service,namespace,data.get('definition'))
    current = c.select('native_rule_revisions',Q(namespace=namespace),order=('-revision',),limit=1).fetchone()
    revision = current['revision'] if current else 0
    if data.get('expected_revision') != revision:
        raise Fault('RULE_CONFLICT','规则版本已变化，请重新读取',409)
    enabled = data.get('enabled',False)
    if type(enabled) is not bool:
        raise Fault('INVALID','enabled必须是布尔值')
    result = {'namespace':namespace,'revision':revision+1,'enabled':enabled,'definition':definition}
    if action=='native.rules.preview':
        return dict(result,status='PREVIEW')
    c.update('native_rule_revisions',Q(namespace=namespace,enabled=True),{'enabled':False})
    result['id'] = identifier()
    c.insert('native_rule_revisions',dict(result,created_at=service.clock(),actor=actor['id']))
    return dict(result,status='PUBLISHED')


def checkin_status(service, c, account_id, campaign):
    """返回月历及累计签到，不存在正式配置时清晰显示停用，而非伪造可领取奖励。"""
    today = datetime.fromtimestamp(service.clock(),BEIJING).date()
    try:
        rule = active_rule(service,c,'checkin:'+text_field(campaign,'campaign',80))
    except Fault as error:
        if error.code in ('ACTIVITY_DISABLED','ACTIVITY_CLOSED'):
            return {'enabled':False,'reason':error.code,'today':today.isoformat()}
        raise
    rows = c.select('native_checkins',Q(account_id=account_id,campaign=campaign,day__gte=today.replace(day=1),day__lte=today),order=('day',))
    return {'enabled':True,'today':today.isoformat(),'rule_id':rule['id'],'revision':rule['revision'],
            'definition':rule['definition'],'days':[r['day'].isoformat() for r in rows],
            'claimed_today':any(r['day']==today for r in rows)}


def checkin_claim(service, c, account, data):
    """同活动同日只领一次；每日和累计奖励在一笔事务提交，规则换版也不能重复领。"""
    campaign = text_field(data.get('campaign'),'campaign',80)
    today = datetime.fromtimestamp(service.clock(),BEIJING).date()
    previous = c.select('native_checkins',Q(account_id=account['id'],campaign=campaign,day=today)).fetchone()
    if previous:
        return previous['result']
    rule = active_rule(service,c,'checkin:'+campaign)
    if data.get('rule_id')!=rule['id']:
        raise Fault('RULE_CONFLICT','请刷新签到配置',409)
    count = c.count('native_checkins',Q(account_id=account['id'],campaign=campaign,day__gte=today.replace(day=1),day__lt=today)).fetchone()[0]+1
    definition = rule['definition']
    rewards = definition.get('daily',{}).get(str(today.day),[]) + definition.get('milestones',{}).get(str(count),[])
    operations = apply(service,c,account['id'],item_rewards(service,rewards),'checkin.claim') if rewards else []
    result = {'status':'CLAIMED','day':today.isoformat(),'count':count,'rule_id':rule['id'],'rewards':rewards,'operations':operations}
    c.insert('native_checkins',{'id':identifier(),'account_id':account['id'],'campaign':campaign,'day':today,'rule_id':rule['id'],'result':result})
    event(service,c,account['id'],'checkin.claimed',result)
    return result


def draw(service, c, account, data):
    """从冻结候选池服务端抽样，材料、奖励、结果和通知原子保存；确认不会重新开奖。"""
    namespace = 'draw:'+text_field(data.get('pool'),'pool',30)
    if namespace not in DRAW_NAMES:
        raise Fault('INVALID','未知抽奖类别')
    # 待确认结果是数据库事实，不能通过换请求键、跨连接或换规则绕过而再次扣费。
    pending=c.select('native_receipts',Q(account_id=account['id'],kind='draw',confirmed=False,
        result__pool=data['pool']),order=('created_at','id'),limit=1).fetchone()
    if pending:
        return pending['result']
    rule = active_rule(service,c,namespace)
    if data.get('rule_id')!=rule['id']:
        raise Fault('RULE_CONFLICT','请刷新奖池',409)
    definition = rule['definition']
    recipe_index=None
    if namespace=='draw:workshop' and 'recipes' in definition:
        recipe_index=integer(data.get('recipe_index'),0,255,'recipe_index')
        definition=next((r for r in definition['recipes'] if r['index']==recipe_index),None)
        if definition is None:
            raise Fault('INVALID','工坊配方不存在')
    pool = definition['pool']
    round_id=''
    if namespace=='draw:selected':
        saved=c.select('native_draw_selections',Q(account_id=account['id'],rule_id=rule['id'])).fetchone()
        selected = data.get('selected',saved['selected'] if saved else None)
        if not isinstance(selected,list) or any(not isinstance(x,str) for x in selected) or len(selected)!=definition.get('selection_count',1) or len(set(selected))!=len(selected):
            raise Fault('INVALID','请选定正确数量的不重复奖项')
        chosen=selected
        if 'pool_size' in definition:
            if not saved or selected!=saved['selected'] or len(saved['pool_keys'])!=definition['pool_size']:
                raise Fault('INVALID','请先提交自选并生成完整奖池')
            chosen=saved['pool_keys']
            round_id=saved['round_id']
        pool = [entry for entry in pool if entry['key'] in chosen]
        if len(pool)!=len(chosen):
            raise Fault('INVALID','选择了奖池外的物品')
    if definition.get('without_replacement'):
        already=drawn_keys(c,account['id'],rule['id'],round_id)
        pool=[entry for entry in pool if entry['key'] not in already]
    if definition.get('duplicate_policy')=='exclude_owned':
        owned = set(c.table('inventory').filter(Q(account_id=account['id']) & (Q(expires_at__isnull=True)|Q(expires_at__gt=service.clock()))).values_list('item_key',flat=True))
        pool = [entry for entry in pool if not any('cn2020:'+str(r.get('item_id')) in owned for r in entry['rewards'])]
    if not pool:
        raise Fault('POOL_EMPTY','当前奖池已无可抽取奖励',409)
    changes = consume_materials(service,c,account['id'],definition['materials'])
    random_value = secrets.randbelow(sum(entry.get('weight',1) for entry in pool))
    for selected in pool:
        random_value -= selected.get('weight',1)
        if random_value<0:
            break
    # 原生说明明确已有永久道具退回成本：先验证材料足够，再直接提交净零资产回执，
    # 不删除再重建计数材料GID，也不制造重复永久道具；本轮仍记为已抽取。
    refunded=bool(definition.get('refund_owned') and all('item_id' in reward
        and reward.get('duration_seconds') is None and c.select('inventory',Q(account_id=account['id'],
        item_key='cn2020:'+str(reward['item_id']),expires_at__isnull=True)) for reward in selected['rewards']))
    operations = [] if refunded else apply(service,c,account['id'],changes+item_rewards(service,selected['rewards']),'draw.commit')
    result = {'receipt_id':identifier(),'status':'DRAWN','pool':data['pool'],'rule_id':rule['id'],
              'award':selected,'operations':operations,'probability_basis':'VERSIONED_SERVER_WEIGHTS',
              'recipe_index':recipe_index,'round_id':round_id,'refunded':refunded,
              'refunded_materials':definition['materials'] if refunded else []}
    c.insert('native_receipts',{'id':result['receipt_id'],'account_id':account['id'],'kind':'draw',
                              'result':result,'created_at':service.clock(),'confirmed':namespace=='draw:workshop'})
    event(service,c,account['id'],'draw.completed',result)
    return result


def confirm(service, c, account, data):
    """本人确认持久开奖结果；新幂等键、断线和重复确认均不重新扣材料或重抽。"""
    receipt = c.select('native_receipts',Q(id=data.get('receipt_id'),account_id=account['id'],kind='draw')).fetchone()
    if not receipt:
        raise Fault('NOT_FOUND','未找到本人抽奖记录',404)
    c.update('native_receipts',Q(id=receipt['id']),{'confirmed':True})
    return receipt['result']


def selection(service, c, account, action, data):
    """事务内保存或清空本人自选列表；待确认开奖结果不能通过重置丢弃或重新开奖。"""
    rule=active_rule(service,c,'draw:selected')
    if data.get('rule_id')!=rule['id']:
        raise Fault('RULE_CONFLICT','请刷新自选候选列表',409)
    if c.select('native_receipts',Q(account_id=account['id'],kind='draw',confirmed=False,result__pool='selected')):
        raise Fault('PENDING_DRAW','请先确认已有开奖结果',409)
    saved=c.select('native_draw_selections',Q(account_id=account['id'],rule_id=rule['id'])).fetchone()
    if saved and rule['definition'].get('without_replacement'):
        drawn=drawn_keys(c,account['id'],rule['id'],saved['round_id'])
        if drawn and len(drawn)<len(saved['pool_keys'] or saved['selected']):
            raise Fault('PENDING_DRAW','本轮开始后须抽完全部道具才能重置',409)
    selected=data.get('selected',[]) if action=='draw.select' else []
    if action=='draw.select':
        expected=rule['definition'].get('selection_count',1)
        available={entry['key'] for entry in rule['definition']['pool']}
        if (not isinstance(selected,list) or any(not isinstance(x,str) for x in selected)
                or len(selected)!=expected or len(set(selected))!=expected or not set(selected)<=available):
            raise Fault('INVALID','请选择规定数量且不重复的奖项')
    pool_keys=list(selected)
    if selected and 'pool_size' in rule['definition']:
        remaining=[entry['key'] for entry in rule['definition']['pool'] if entry['key'] not in selected]
        while len(pool_keys)<rule['definition']['pool_size']:
            pool_keys.append(remaining.pop(secrets.randbelow(len(remaining))))
    values={'rule_id':rule['id'],'selected':selected,'pool_keys':pool_keys,'round_id':identifier()}
    if c.select('native_draw_selections',Q(account_id=account['id'])):
        c.update('native_draw_selections',Q(account_id=account['id']),values)
    else:
        c.insert('native_draw_selections',dict(values,account_id=account['id']))
    return {'status':'SELECTED' if selected else 'RESET','rule_id':rule['id'],'selected':selected,
            'pool_keys':pool_keys,'round_id':values['round_id']}


def drawn_keys(c, account_id, rule_id, round_id=''):
    """从本人持久回执读取本轮已抽奖项，不受最近50笔商城订单或确认状态影响。"""
    rows=c.select('native_receipts',Q(account_id=account_id,kind='draw',result__rule_id=rule_id))
    return list(dict.fromkeys(row['result']['award']['key'] for row in rows
        if row['result'].get('round_id','')==round_id))
