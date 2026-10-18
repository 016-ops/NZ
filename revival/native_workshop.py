"""宝物工坊礼品券/宝物券抽奖；与碎片兑换配方分开，免费日历、批量材料及奖励共用事务。"""
from datetime import datetime
import secrets
from django.db.models import Q
from .native_actions import apply, consume_materials, item_rewards, event
from .native_rewards import active_rule, BEIJING, validate_materials
from .service import Fault, identifier, integer, text_field


def validate(service, definition):
    """严格验证当前原生消息可表达的单物品奖励、分类、成本及十连机制；后台预览同样检查。"""
    if not isinstance(definition,dict):
        raise Fault('INVALID','工坊券规则必须是对象')
    for name in ('free','paid'):
        validate_materials(service,[definition.get(name+'_material')])
    integer(definition.get('ten_min_tier'),1,5,'ten_min_tier')
    if type(definition.get('refund_owned')) is not bool:
        raise Fault('INVALID','重复奖励退款必须为布尔值')
    pool=definition.get('pool')
    if not isinstance(pool,list) or not 1<=len(pool)<=256:
        raise Fault('INVALID','工坊奖池需要1至256项')
    seen=set()
    for entry in pool:
        if not isinstance(entry,dict):
            raise Fault('INVALID','奖项必须是对象')
        key=text_field(entry.get('key'),'pool key',64)
        if key in seen:
            raise Fault('INVALID','工坊奖项标识重复')
        seen.add(key)
        integer(entry.get('weight'),1,1000000,'weight')
        integer(entry.get('tier'),1,5,'tier')
        for flag in ('free','paid'):
            if type(entry.get(flag)) is not bool:
                raise Fault('INVALID','工坊奖池归属必须为布尔值')
        if not entry['free'] and not entry['paid']:
            raise Fault('INVALID','奖项必须属于至少一个奖池')
        for field in ('show_id','pic_id'):
            integer(entry.get(field,0),0,2147483647,field)
        reward=entry.get('reward')
        if not isinstance(reward,dict) or 'item_id' not in reward:
            raise Fault('INVALID','当前工坊只接受单道具奖励')
        item_rewards(service,[reward])
        seconds=reward.get('duration_seconds')
        if seconds is not None and (seconds%3600 or seconds//3600>2147483647):
            raise Fault('INVALID','工坊期限必须是原生可表达的整小时')
    if not any(e['free'] for e in pool) or not any(e['paid'] and e['tier']>=definition['ten_min_tier'] for e in pool):
        raise Fault('INVALID','免费池和付费十连保底池不能为空')


def state(service, c, account):
    """读取本人免费日期和未送达结果；规则被停用时仍能恢复已经提交的奖励。"""
    saved=c.select('native_workshop_states',Q(account_id=account['id'])).fetchone()
    pending=c.select('native_receipts',Q(account_id=account['id'],kind='workshop',confirmed=False),
                     order=('created_at','id'),limit=1).fetchone()
    try:
        rule=active_rule(service,c,'draw:workshop')
        if 'vouchers' not in rule['definition']:
            rule=None
    except Fault as error:
        if error.code not in ('ACTIVITY_DISABLED','ACTIVITY_CLOSED'):
            raise
        rule=None
    last=saved['last_free_at'] if saved else 0
    today=datetime.fromtimestamp(service.clock(),BEIJING).date()
    available=not last or datetime.fromtimestamp(last,BEIJING).date()!=today
    return {'rule':rule,'last_free_at':last,'free_available':available,'pending':pending}


def owned_permanent(c, account_id, reward):
    """只对永久装备退款；计数材料即使已有也正常叠加，不把券或碎片当作独占装备。"""
    from .native_features_catalog import counter_item_ids
    return (reward.get('duration_seconds') is None and reward['item_id'] not in counter_item_ids()
            and bool(c.select('inventory',Q(account_id=account_id,item_key='cn2020:'+str(reward['item_id']),expires_at__isnull=True))))


def draw(service, c, account, data):
    """串行事务中服务端抽取1/10项；重连待确认优先，免费每天一次且不随规则版本重置。"""
    mode=data.get('mode')
    if mode not in ('free','paid') or type(data.get('count')) is not int or data['count'] not in (1,10):
        raise Fault('INVALID','工坊只支持礼品券/宝物券单抽或十连')
    snapshot=state(service,c,account)
    if snapshot['pending']:
        return snapshot['pending']['result']
    rule=snapshot['rule']
    if not rule:
        raise Fault('ACTIVITY_DISABLED','工坊券抽奖尚未启用',409)
    if data.get('rule_id')!=rule['id']:
        raise Fault('RULE_CONFLICT','请刷新工坊奖池',409)
    definition=rule['definition']['vouchers'];count=data['count']
    free=mode=='free' and count==1 and snapshot['free_available']
    material=definition[mode+'_material']
    # 十连始终按十张券计费；每日免费只用于礼品池单抽。先验证全额成本，退款不能绕过持有检查。
    full_cost=material['quantity']*count
    if not free:
        consume_materials(service,c,account['id'],[dict(material,quantity=full_cost)])
    pool=[e for e in definition['pool'] if e[mode]]
    awards=[];changes=[];refunded=0;batch_owned=set()
    for index in range(count):
        choices=pool
        if mode=='paid' and count==10 and index==9 and not any(a['entry']['tier']>=definition['ten_min_tier'] for a in awards):
            choices=[e for e in pool if e['tier']>=definition['ten_min_tier']]
        value=secrets.randbelow(sum(e['weight'] for e in choices))
        for entry in choices:
            value-=entry['weight']
            if value<0:
                break
        reward=entry['reward']
        is_owned=definition['refund_owned'] and (owned_permanent(c,account['id'],reward) or reward['item_id'] in batch_owned)
        awards.append({'entry':entry,'refunded':bool(is_owned)})
        if is_owned:
            refunded+=0 if free else material['quantity']
        else:
            changes.extend(item_rewards(service,[reward]))
            from .native_features_catalog import counter_item_ids
            if reward.get('duration_seconds') is None and reward['item_id'] not in counter_item_ids():
                batch_owned.add(reward['item_id'])
    if not free and full_cost>refunded:
        changes=consume_materials(service,c,account['id'],[dict(material,quantity=full_cost-refunded)])+changes
    operations=apply(service,c,account['id'],changes,'workshop.draw') if changes else []
    last=snapshot['last_free_at']
    if free:
        last=service.clock()
        if c.select('native_workshop_states',Q(account_id=account['id'])):
            c.update('native_workshop_states',Q(account_id=account['id']),{'last_free_at':last})
        else:
            c.insert('native_workshop_states',{'account_id':account['id'],'last_free_at':last})
    result={'receipt_id':identifier(),'status':'DRAWN','pool':'workshop','rule_id':rule['id'],
            'mode':mode,'count':count,'free':free,'last_free_at':last,'awards':awards,
            'refunded_quantity':refunded,'operations':operations}
    c.insert('native_receipts',{'id':result['receipt_id'],'account_id':account['id'],'kind':'workshop',
                              'result':result,'created_at':service.clock(),'confirmed':False})
    event(service,c,account['id'],'workshop.completed',result)
    return result


def confirm(service, c, account, data):
    """传输层确认只回读本人原回执，不表示真人已读；重复确认不会重新消耗或抽取。"""
    row=c.select('native_receipts',Q(id=data.get('receipt_id'),account_id=account['id'],kind='workshop')).fetchone()
    if not row:
        raise Fault('NOT_FOUND','工坊回执不存在',404)
    c.update('native_receipts',Q(id=row['id']),{'confirmed':True})
    return row['result']
