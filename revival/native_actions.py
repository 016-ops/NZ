"""原生仓库和商城领域操作；所有变更在调用者的鉴权、幂等和MySQL事务内完成。"""
from datetime import datetime, timezone, timedelta
from django.db.models import Q
from .assets import mutate
from .native_features_catalog import feature_catalog, item_definition, exchange_catalog, FEATURE_VERSION, FRAGMENTS
from .service import Fault, identifier, integer


def owned(service, c, account_id, gid, *, expired=False):
    """只解析当前玩家的稳定GID；续费可明确读取过期实例，其他操作拒绝过期物品。"""
    integer(gid, 1001, 2**63-1, 'gid')
    binding = c.select('native_inventory_bindings', Q(id=gid-1000)).fetchone()
    item = c.select('inventory', Q(id=binding['inventory_id'], account_id=account_id)).fetchone() if binding else None
    if not item or (not expired and item['expires_at'] is not None and item['expires_at'] <= service.clock()):
        raise Fault('INVENTORY', '需要本人有效物品', 409)
    return item


def native_id(item):
    """把内部目录键转换为已知原生物品ID；调用方只传入本人原生实例。"""
    return int(item['item_key'].removeprefix('cn2020:'))


def event(service, c, account_id, kind, payload):
    """在同一业务事务保存接收者事件，掉线不会丢失通知，也不把入队记为界面已收到。"""
    c.insert('native_player_events', {'account_id':account_id, 'kind':kind,
                                    'payload':payload, 'created_at':service.clock()})


def apply(service, c, account_id, changes, source, actor_id=None):
    """调用统一资产账本；拆分为最多20项的批次仍被外层同一事务包住。"""
    operations = []
    for offset in range(0, len(changes), 20):
        result = mutate(service, c, {'id':actor_id or account_id, 'role':'player'}, account_id,
                        changes[offset:offset+20], source, source)
        operations.append(result['operation_id'])
    return operations


def item_rewards(service, rewards):
    """将服务端冻结的奖励展开为独立实例或计数材料；不接受未定义资源或无限批次。"""
    if not isinstance(rewards, list) or not 1 <= len(rewards) <= 240:
        raise Fault('INVALID', '奖励须为1至240项')
    changes = []
    for reward in rewards:
        if not isinstance(reward, dict):
            raise Fault('INVALID', '奖励格式不正确')
        if reward.get('type') == 'currency':
            code = reward.get('code')
            definition = service.catalog['currencies'].get(code)
            if not definition:
                raise Fault('UNVERIFIED_RESOURCE', '未知奖励币种')
            changes.append({'type':'currency','code':code,'delta':integer(reward.get('amount'),1,definition['max'],'amount')})
            continue
        key = 'cn2020:'+str(integer(reward.get('item_id'),1,2**31-1,'item_id'))
        definition = service.catalog['items'].get(key)
        if not definition:
            raise Fault('UNVERIFIED_RESOURCE', '未知奖励物品')
        quantity = integer(reward.get('quantity',1),1,1000,'quantity')
        duration = reward.get('duration_seconds')
        if duration is not None:
            integer(duration,1,definition['duration_max'],'duration_seconds')
        change = dict(type='item',item_key=key,quantity=quantity, duration_seconds=duration)
        if definition.get('storage_mode') == 'instance':
            changes.extend(dict(change,quantity=1) for _ in range(quantity))
        else:
            changes.append(change)
        if len(changes)>200:
            raise Fault('LIMIT', '单次最多发放200个独立实例')
    return changes


def consume_materials(service, c, account_id, materials, *, exclude=()):
    """按服务端配方从本人有效未装备库存扣除材料；不足时拒绝，不允许客户端指定别人的实例。"""
    if not isinstance(materials,list) or not 1 <= len(materials) <= 20:
        raise Fault('INVALID', '配方材料须为1至20项')
    totals = {}
    for material in materials:
        item_id = integer(material.get('item_id'),1,2**31-1,'item_id')
        count = integer(material.get('quantity'),1,2147483647,'quantity')
        totals[item_id] = totals.get(item_id,0)+count
    equipped = set(c.table('equipment').filter(account_id=account_id).values_list('inventory_id',flat=True))
    changes = []
    for item_id, remaining in totals.items():
        for row in c.select('inventory', Q(account_id=account_id,item_key='cn2020:'+str(item_id)) &
                            (Q(expires_at__isnull=True)|Q(expires_at__gt=service.clock())), order=('id',)):
            if row['id'] in equipped or row['id'] in exclude:
                continue
            take = min(remaining,row['quantity'])
            changes.append(dict(type='remove',instance_id=row['id'],quantity=take))
            remaining -= take
            if not remaining:
                break
        if remaining:
            raise Fault('MATERIALS', '可用且未装备的材料不足', 409)
    return changes


def inventory_action(service, c, account, action, data):
    """执行删除、换肤、吞噬和已发布配方；打开碎片界面本身不扣除材料。"""
    item = owned(service,c,account['id'],data.get('gid'),expired=action=='inventory.unswallow')
    item_id = native_id(item)
    definition = item_definition(item_id)
    if action == 'inventory.craft':
        return craft(service,c,account,item,data)
    state_row = c.select('native_item_states',Q(inventory_id=item['id'])).fetchone()
    state = dict(state_row['state']) if state_row else {}
    changes = []
    if action == 'inventory.delete':
        if 'delete' not in definition['actions'] or c.select('equipment',Q(inventory_id=item['id'])):
            raise Fault('ITEM_ACTION', '该物品不可删除，或仍在装备中',409)
        changes.append(dict(type='remove',instance_id=item['id'],quantity=integer(data.get('quantity',1),1,item['quantity'],'quantity')))
    elif action == 'inventory.skin':
        skin_gid = data.get('skin_gid')
        if skin_gid is None:
            state.pop('skin',None)
        else:
            skin = owned(service,c,account['id'],skin_gid)
            skin_id = native_id(skin)
            if {'weapon':item_id,'skin':skin_id} not in feature_catalog()['skins']:
                raise Fault('ITEM_ACTION','皮肤与主体武器不匹配',409)
            state['skin'] = {'item_id':skin_id,'gid':skin_gid}
        changes.append(dict(type='native_state',instance_id=item['id'],state=state))
    elif action in ('inventory.swallow','inventory.unswallow'):
        # 当前3932明确区分Equip/TakeOff，3931保留双方GID；融合是一对一可卸下关系，
        # 不是永久删除宠物。事务内保护被引用材料，不能同时装备或融合给另一主体。
        material = owned(service,c,account['id'],data.get('material_gid'),expired=action=='inventory.unswallow')
        material_id = native_id(material)
        if item['id']==material['id'] or not any(r['host']==item_id and r['material']==material_id for r in feature_catalog()['swallow']):
            raise Fault('ITEM_ACTION','不支持此吞噬组合',409)
        relation={'item_id':material_id,'gid':data['material_gid']}
        if state.get('swallowed'):
            raise Fault('ITEM_ACTION','旧融合记录需要按原流水补偿，不能丢弃历史效果',409)
        if action=='inventory.unswallow':
            if state.get('swallow')!=relation:
                raise Fault('ITEM_ACTION','融合关系已经变化，请刷新',409)
            state.pop('swallow')
        else:
            if c.select('equipment',Q(inventory_id=material['id'])):
                raise Fault('ITEM_ACTION','请先卸下融合材料',409)
            if state.get('swallow')==relation:
                return {'status':'UNCHANGED','operations':[]}
            if c.table('native_item_states').filter(state__swallow__gid=data['material_gid']).exists():
                raise Fault('ITEM_IN_USE','该宠物已被另一主体融合',409)
            if not state.get('swallow') and c.table('native_item_states').filter(
                    inventory__account_id=account['id'],state__swallow__isnull=False).count()>=8:
                raise Fault('LIMIT','当前客户端最多保存8组融合关系',409)
            state['swallow']=relation
        changes.append(dict(type='native_state',instance_id=item['id'],state=state,
                            allow_expired=action=='inventory.unswallow'))
    elif action == 'inventory.use' and item_id in FRAGMENTS.values():
        return {'status':'OPEN_ACTIVITY','pool':next(k for k,v in FRAGMENTS.items() if v==item_id),'operations':[]}
    else:
        from .native_rewards import active_rule
        rule = active_rule(service,c,'item:'+str(item_id)+':'+action.split('.')[-1])
        recipe = rule['definition']
        changes = consume_materials(service,c,account['id'],recipe['materials'],exclude=(item['id'],)) if recipe['materials'] else []
        if recipe.get('consume_subject'):
            if c.select('equipment',Q(inventory_id=item['id'])):
                raise Fault('ITEM_ACTION','请先卸下加工物品',409)
            changes.append(dict(type='remove',instance_id=item['id'],quantity=1))
        if recipe.get('rewards'):
            changes.extend(item_rewards(service,recipe['rewards']))
        if recipe.get('state'):
            state.update(recipe['state'])
            changes.append(dict(type='native_state',instance_id=item['id'],state=state))
    operations = apply(service,c,account['id'],changes,action)
    event(service,c,account['id'],'inventory.changed',{'operations':operations})
    return {'status':'COMMITTED','operations':operations}


def craft(service, c, account, item, data):
    """执行冻结的升级/转化配方：只扣当前主体及额外材料，0材料配方不误扣第二件主体。

    新产物获得独立GID；装备中的主体及被皮肤、宠物关系引用的实例拒绝消费，
    因此已有装备树不会悬空。失败由外层事务回滚材料、产物、流水和事件。
    """
    catalog=exchange_catalog()
    recipe=next((r for r in catalog['recipes'] if r['recipe_id']==data.get('recipe_id')),None)
    if not recipe or recipe['ExchangeItemId']!=native_id(item):
        raise Fault('ITEM_ACTION','配方与当前主体不匹配',409)
    if data.get('rule_version')!=catalog['version']:
        raise Fault('RULE_CONFLICT','加工规则已变化，请刷新',409)
    if c.select('equipment',Q(inventory_id=item['id'])):
        raise Fault('ITEM_ACTION','请先卸下加工主体',409)
    changes=[]
    if recipe['CostItemAmount']:
        changes=consume_materials(service,c,account['id'],[{'item_id':recipe['CostItemId'],
            'quantity':recipe['CostItemAmount']}],exclude=(item['id'],))
    changes.append(dict(type='remove',instance_id=item['id'],quantity=recipe['ExchangeItemAmount']))
    rewards=[dict(item_id=recipe['AchieveItemId'],quantity=recipe['AchieveItemAmount'],
        duration_seconds=None if recipe['AchieveItemAvailTime']==-1 else recipe['AchieveItemAvailTime']*3600)]
    changes.extend(item_rewards(service,rewards))
    operations=apply(service,c,account['id'],changes,'inventory.craft')
    result=dict(status='COMMITTED',operations=operations,recipe_id=recipe['recipe_id'],
                rule_version=catalog['version'],rewards=rewards)
    event(service,c,account['id'],'inventory.changed',result)
    return result


def shop_quote(service, c, account, data, *, renew_gid=None):
    """根据冻结目录计算购物车；期间以小时解释，计数材料的购买档位以数量解释并明确兼容版本。"""
    from .native_shop_compat import restored_offers
    restored=restored_offers()
    lines = data.get('lines')
    if not isinstance(lines,list) or not 1 <= len(lines) <= 20:
        raise Fault('INVALID','购物车需要1至20项')
    renewal_offers = set()
    if renew_gid is not None:
        # 仓库续费商品可以不在商城展示，但必须属于本人原实例的固定续费配置。
        # 此范围由续费命令单独传入，普通购买不能凭请求字段绕过商城可售校验。
        from .native_maintenance import catalog
        item = owned(service,c,account['id'],renew_gid,expired=True)
        definition = catalog().get(str(native_id(item)),{})
        if len(lines)!=1 or lines[0].get('quantity',1)!=1 or definition.get('ConsumeType')!=1:
            raise Fault('RENEW','物品不支持期限续费',409)
        renewal_offers = {definition.get('nRepairPrice'),definition.get('nRepairPrice1')} - {0,None}
        if not renewal_offers:
            raise Fault('RENEW','缺少物品对应的续费商品',409)
    result, totals = [], {}
    now = datetime.fromtimestamp(service.clock(),timezone(timedelta(hours=8)))
    date = int(now.strftime('%Y%m%d'))
    for line in lines:
        offer_id = integer(line.get('commodity_id'),1,2**31-1,'commodity_id')
        # 运营上下架、价格与期限覆盖同时作用于原生和门户购买，不能从旧客户端绕过。
        from .portal_operations import resolve_shop_offer, shop_revision
        offer = resolve_shop_offer(service,c,offer_id,allow_hidden=bool(renewal_offers))
        if renewal_offers and offer_id not in renewal_offers:
            raise Fault('RENEW','续费商品与原物品不匹配',409)
        if not offer or (not offer['bShowCommodity'] and offer_id not in renewal_offers and offer_id not in restored['offer_ids']):
            raise Fault('OFFER','商品不存在或未展示',409)
        # 与原生客户端一样比较自然日期和当天时间；恢复开关不绕过正式销售窗口。
        clock=int(now.strftime('%H%M%S'))
        on_date,off_date=offer['ItemOnSaleDate'],offer['ItemRecallDate']
        if ((on_date and date<on_date) or (date==on_date and clock<(offer['ItemOnSaleTime'] or 0))
                or (off_date and date>off_date) or (date==off_date and clock>(offer['ItemRecallTime'] or 0))):
            raise Fault('OFFER','商品不在销售时间内',409)
        code = 'cn2020:'+str(offer['sCommodityType'])
        if code not in service.catalog['currencies']:
            raise Fault('UNVERIFIED_RESOURCE','商品币种尚未确认',409)
        index = integer(line.get('price_index',0),0,len(offer['nPriceArray'] or [])-1,'price_index')
        count = integer(line.get('quantity',1),1,20,'quantity')
        price = integer(offer['nPriceArray'][index],0,2147483647,'price')
        period = offer['nAvailPreiodArray'][index]
        ids = offer['nItemIDArray']
        if not ids or len(ids)>12 or any('cn2020:'+str(i) not in service.catalog['items'] for i in ids):
            raise Fault('OFFER','商品内容不完整',409)
        rewards = []
        for i,item_id in enumerate(ids):
            actual_period = period
            if offer['bIsPackage']:
                periods = offer['PeriodArrayForSuite'] or []
                # 套装期限按购买档位排列，而非按子物品排列；-2沿用nAvailPreiodArray。
                # 例如角色包一个档位含九个部件，靶场礼包的材料数量与装备期限分别取两组数组。
                if len(periods)!=len(offer['nPriceArray']) or periods[index] < -2:
                    raise Fault('OFFER','礼包期限需要明确配置',409)
                actual_period = period if periods[index]==-2 else periods[index]
            counter = service.catalog['items']['cn2020:'+str(item_id)].get('storage_mode')=='counter'
            if counter:
                material_count=period if offer['bIsPackage'] and offer['bStackProp'] else actual_period
                if material_count<=0:
                    raise Fault('OFFER','计数材料购买数量未明确',409)
                rewards.append({'item_id':item_id,'quantity':material_count*count})
            else:
                if actual_period==0 or actual_period < -1:
                    raise Fault('OFFER','商品期限未明确',409)
                rewards.append({'item_id':item_id,'quantity':count,
                                # 当前目录的1752000小时是长效占位；兼容v2明确归为永久。
                                'duration_seconds':None if actual_period in (-1,1752000) else actual_period*3600})
        totals[code] = totals.get(code,0)+price*count
        if totals[code]>service.catalog['currencies'][code]['max']:
            raise Fault('LIMIT','购物车金额超过币种范围',409)
        item_rewards(service,rewards)
        result.append({'commodity_id':offer_id,'price_index':index,'quantity':count,'currency':code,
                       'price':price,'rewards':rewards,'giftable':bool((offer['bGiveAndAskable'] or [False])[0])})
    return {'version':FEATURE_VERSION,'shop_rule_version':'cn2020-shop-package-period-v2',
            'restored_offer_rule':restored['version'],'shop_revision':shop_revision(c),'lines':result,'totals':totals}


def shop_commit(service, c, account, action, data):
    """一次事务提交整车、赠礼或续费；金额由服务端重算，回执与收件通知一起保存。"""
    quote = shop_quote(service,c,account,data,renew_gid=data.get('gid') if action=='shop.renew' else None)
    if data.get('quote') != quote:
        raise Fault('QUOTE_CHANGED','请重新预览当前商品价格及内容',409)
    recipient = account
    if action=='shop.gift':
        if data.get('recipient_game_id'):
            recipient=c.select('accounts',Q(game_id=integer(data['recipient_game_id'],1,2**63-1,'recipient_game_id'))).fetchone()
            if not recipient:
                raise Fault('GIFT','接收者不存在',409)
        else:
            recipient = service._account(c,data.get('recipient_account_id'))
        service._active(c,recipient)
        if recipient['id']==account['id'] or not all(x['giftable'] for x in quote['lines']):
            raise Fault('GIFT','商品不可赠送或接收者无效',409)
    changes = [dict(type='currency',code=k,delta=-v) for k,v in quote['totals'].items() if v]
    rewards = [reward for line in quote['lines'] for reward in line['rewards']]
    item_rewards(service,rewards)  # 整车统一验证总实例数，不能逐行绕过200实例边界。
    line_operations=[]
    if action=='shop.renew':
        item = owned(service,c,account['id'],data.get('gid'),expired=True)
        if len(rewards)!=1 or rewards[0]['item_id']!=native_id(item) or not rewards[0].get('duration_seconds') or rewards[0]['quantity']!=1:
            raise Fault('RENEW','续费商品必须对应同一物品和一个有效期限',409)
        changes.append(dict(type='renew',instance_id=item['id'],duration_seconds=rewards[0]['duration_seconds']))
        operations = apply(service,c,account['id'],changes,action)
        line_operations=[operations]
    else:
        operations = apply(service,c,account['id'],changes,action)
        # 每行记录准确产出，不靠提交后按ItemId猜测；同种材料多行仍指向同一个计数GID。
        for line in quote['lines']:
            granted=apply(service,c,recipient['id'],item_rewards(service,line['rewards']),action,account['id'])
            line_operations.append(granted)
            operations.extend(granted)
    from .native_avatar import attach_owned_defaults
    instances=c.table('asset_entries').filter(operation_id__in=operations,instance_id__isnull=False).values_list('instance_id',flat=True)
    avatar_equipment=attach_owned_defaults(service,c,recipient['id'],list(instances))
    operations.extend(operation for row in avatar_equipment for operation in row.get('operations',[]))
    line_grants=[]
    for ids in line_operations:
        instances=c.table('asset_entries').filter(operation_id__in=ids,instance_id__isnull=False).values_list('instance_id',flat=True)
        grants=[]
        for row in c.select('inventory',Q(id__in=instances,account_id=recipient['id'])):
            binding=c.select('native_inventory_bindings',Q(inventory_id=row['id'])).fetchone()
            state=c.select('native_item_states',Q(inventory_id=row['id'])).fetchone()
            maximum=service.catalog['items'][row['item_key']]['native']['durability_max']
            grants.append({'gid':binding['id']+1000,'item_id':native_id(row),'expires_at':row['expires_at'],
                'quantity':row['quantity'],'durability':state['state'].get('durability',maximum) if state else maximum})
        line_grants.append(grants)
    receipt_id = identifier()
    result = {'receipt_id':receipt_id,'status':'COMMITTED','quote':quote,'operations':operations,
              'recipient_account_id':recipient['id'],'recipient_game_id':recipient['game_id'],'line_grants':line_grants,
              'avatar_equipment':avatar_equipment}
    c.insert('native_receipts',{'id':receipt_id,'account_id':account['id'],'kind':action,
                              'result':result,'created_at':service.clock()})
    event(service,c,recipient['id'],'shop.received',{'receipt_id':receipt_id,'rewards':rewards,'operations':operations})
    return result
