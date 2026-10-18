"""当前兼容库存的真实资产发放、稳定GID和游戏会话投影；全部写入复用统一MySQL事务与账本。"""
from django.db.models import Q

from .assets import mutate
from .catalogs import NATIVE_INVENTORY_VERSION, NATIVE_EQUIPMENT_PROJECTION, native_inventory_manifest
from .launcher import authorize_game_session
from .models import NativeInventoryBinding
from .service import Fault, identifier, integer


def advance_revision(c, account_id, instance_ids=None):
    """调用者已经持有部署事务锁；每次资产或装备变更推进版本并参与相同回滚。"""
    row = c.select('native_sync_cursors', Q(account_id=account_id)).fetchone()
    revision = row['revision']+1 if row else 1
    if row:
        c.update('native_sync_cursors', Q(account_id=account_id), {'revision':revision})
    else:
        c.insert('native_sync_cursors', {'account_id':account_id,'revision':revision})
    c.insert('native_sync_changes', {'account_id':account_id,'revision':revision,
        'instance_ids':sorted(set(instance_ids)) if instance_ids is not None else None})
    return revision


def sync_revision(c, account_id):
    """尚无新写入的历史账号从版本零开始，不为只读请求制造资产变更。"""
    row = c.select('native_sync_cursors', Q(account_id=account_id)).fetchone()
    return row['revision'] if row else 0


def page(service, c, account, data):
    """按GID分页返回当前目录、数量、状态及挂接关系；限定单包大小而不限制总库存。"""
    revision = sync_revision(c,account['id'])
    if data.get('revision',revision)!=revision:
        raise Fault('SNAPSHOT_CHANGED','分页过程中资产发生变化，请重新读取',409)
    after = integer(data.get('after_gid',1000),1000,2**63-1,'after_gid')
    limit = integer(data.get('limit',100),1,200,'limit')
    bindings = c.table('native_inventory_bindings').filter(id__gt=after-1000,inventory__account_id=account['id']).select_related('inventory').order_by('id')[:limit+1]
    equipment = dict(c.table('equipment').filter(account_id=account['id']).values_list('inventory_id','slot'))
    states = dict(c.table('native_item_states').filter(inventory__account_id=account['id'],inventory_id__in=[b.inventory_id for b in bindings]).values_list('inventory_id','state'))
    from .native_pve_benefits import gain_types
    origins=gain_types(c,account['id'],[b.inventory_id for b in bindings[:limit]])
    items = []
    for binding in bindings[:limit]:
        item = binding.inventory
        definition = service.catalog['items'].get(item.item_key,{}).get('native')
        if not definition or binding.rules_version!=NATIVE_INVENTORY_VERSION:
            raise Fault('NATIVE_CATALOG_CONFLICT','库存目录与绑定版本不一致',409)
        items.append(dict(definition,gid=binding.id+1000,instance_id=item.id,quantity=item.quantity,
            expires_at=item.expires_at,version=item.version,state=states.get(item.id,{}),equipment_slot=equipment.get(item.id),gain_type=origins.get(item.id,48)))
    from .native_wallet import snapshot
    return dict(items=items,next_gid=items[-1]['gid'] if len(bindings)>limit else None,
                revision=revision,game_id=account['game_id'],rules_version=NATIVE_INVENTORY_VERSION,wallet=snapshot(c,account['id']))


def scope(service, version):
    """要求调用方携带固定资产规则版本，避免新目录静默改变既有原生实例的意义。"""
    if version != NATIVE_INVENTORY_VERSION or service.catalog.get('native_assets', {}).get('rules_version') != version:
        raise Fault('NATIVE_CATALOG_CONFLICT', 'Reload the current native inventory rules', 409)


def grant_full_inventory(service, account_id, *, rules_version, reason):
    """本地受控管理命令为指定既有账号补齐全目录；重试不增量重复、不回退旧资产或审计。

    一个部署事务包住所有20项资产批次、GID映射、默认装备、权益及审计/outbox。
    故障全部回滚，已有永久实例复用，有限期旧实例保留并增加独立永久权益。
    此入口不暴露为玩家HTTP，不授予后台管理权限，也不生成或发送邮件。
    """
    scope(service, rules_version)
    if not isinstance(reason, str) or not 1 <= len(reason) <= 500:
        raise Fault('INVALID', 'A bounded grant reason is required')
    with service.db.transaction() as c:
        service._account(c, account_id)
        if not c.select('characters', Q(account_id=account_id)).fetchone():
            raise Fault('CHARACTER', 'Existing character required', 409)
        entitlement = c.select('native_super_accounts', Q(account_id=account_id)).fetchone()
        if entitlement and entitlement['rules_version'] != rules_version:
            raise Fault('NATIVE_CATALOG_CONFLICT', 'Explicit entitlement migration is required', 409)
        owned = {row['item_key']: row for row in c.select('inventory', Q(account_id=account_id, expires_at__isnull=True), order=('id',))}
        keys = sorted(key for key in service.catalog['items'] if key.startswith('cn2020:'))
        missing = [key for key in keys if key not in owned]
        operations = []
        actor = {'id': 'local-management:super-account', 'role': 'owner'}
        for offset in range(0, len(missing), 20):
            changes = [{'type': 'item', 'item_key': key, 'quantity': 1} for key in missing[offset:offset+20]]
            operations.append(mutate(service, c, actor, account_id, changes, reason, 'NATIVE_SUPER_ACCOUNT')['operation_id'])
        owned = {row['item_key']: row for row in c.select('inventory', Q(account_id=account_id, expires_at__isnull=True), order=('id',))}
        # 部署锁已经串行化发放；一次读写映射，避免跨网络逐件get_or_create的数千次往返。
        query = NativeInventoryBinding.objects.using(c.alias)
        owned_ids = [owned[key]['id'] for key in keys]
        existing = {b.inventory_id: b for b in query.filter(inventory_id__in=owned_ids)}
        missing_bindings = [NativeInventoryBinding(inventory_id=i, rules_version=rules_version)
                            for i in owned_ids if i not in existing]
        query.bulk_create(missing_bindings, batch_size=1000)
        created_bindings = len(missing_bindings)
        stored = {b.inventory_id: b for b in query.filter(inventory_id__in=owned_ids)}
        bindings = {}
        for key in keys:
            binding = stored[owned[key]['id']]
            if binding.rules_version != rules_version:
                raise Fault('NATIVE_CATALOG_CONFLICT', 'Inventory GID belongs to another rules version', 409)
            bindings[key] = binding.id + 1000
        # 首次初始化提供两阵营角色、两个背包及四类基础武器；已有装备选择不被重试覆盖。
        selections = {row['slot']: row['inventory_id'] for row in c.select('equipment', Q(account_id=account_id))}
        starter = [('cn2020:100014', 'native:1:10'), ('cn2020:100016', 'native:1:11'),
                   ('cn2020:100026', 'native:bag:100026'), ('cn2020:100063', 'native:bag:100063')]
        # 具体武器ID由目录的固定槽和ConsumeType=0/2选择，避免未核验的限时计算阻断基础装备。
        bag_gid = bindings.get('cn2020:100026')
        for location in range(4):
            candidates = sorted(int(key.split(':')[1]) for key in keys
                if service.catalog['items'][key]['native']['location'] == location
                and service.catalog['items'][key]['native']['consume_type'] in (0, 2)
                and not service.catalog['items'][key]['native']['hidden'])
            if candidates and bag_gid:
                starter.append(('cn2020:'+str(candidates[0]), f'native:{bag_gid}:{location}'))
        for key, slot in starter:
            if entitlement is None and key in owned and slot not in selections:
                c.equip(account_id, slot, owned[key]['id'])
        changed = bool(missing) or bool(created_bindings) or entitlement is None or not entitlement['enabled']
        values = {'rules_version': rules_version, 'source_sha256': native_inventory_manifest()['source_sha256'],
                  'granted_at': service.clock(), 'enabled': True}
        if entitlement is None:
            c.insert('native_super_accounts', dict(values, account_id=account_id))
        elif changed:
            c.update('native_super_accounts', Q(account_id=account_id), values)
        summary = {'rules_version': rules_version, 'items_granted': len(missing), 'items_owned': len(keys),
                   'operation_count': len(operations), 'status': 'GRANTED' if changed else 'UNCHANGED',
                   'native_usability': 'REQUIRES_LIVE_ACCEPTANCE'}
        if changed:
            advance_revision(c, account_id)
            request_id = identifier()
            service._audit(c, actor['id'], 'native.super_account.grant', account_id, request_id, reason, summary)
            service._outbox(c, 'native-super:'+request_id, 'native.inventory.changed', account_id, summary)
        return summary


def load(service, game_token, data):
    """仅以有效游戏凭据读取本人持久库存；不同账号、失效凭据或规则版本均不能取得投影。"""
    scope(service, data.get('rules_version'))
    projection = equipment_projection(data)
    with service.db.transaction() as c:
        _, account = authorize_game_session(service, c, game_token, data)
        if projection:
            return equipment_state(service, c, account)
        inventory = {row['id']: row for row in c.select('inventory', Q(account_id=account['id']) &
                     (Q(expires_at__isnull=True) | Q(expires_at__gt=service.clock())))}
        rows = c.select('native_inventory_bindings', Q(inventory_id__in=list(inventory)), order=('id',))
        equipment = {row['inventory_id']: row['slot'] for row in c.select('equipment', Q(account_id=account['id']))}
        from .native_pve_benefits import gain_types
        origins=gain_types(c,account['id'],inventory)
        result = []
        for binding in rows:
            item = inventory[binding['inventory_id']]
            definition = service.catalog['items'].get(item['item_key'], {}).get('native')
            if not definition or binding['rules_version'] != NATIVE_INVENTORY_VERSION:
                raise Fault('NATIVE_CATALOG_CONFLICT', 'Inventory projection is outside the frozen catalog', 409)
            result.append(dict(definition, gid=binding['id']+1000, quantity=item['quantity'],
                               expires_at=item['expires_at'], equipment_slot=equipment.get(item['id']),gain_type=origins.get(item['id'],48)))
        entitlement = c.select('native_super_accounts', Q(account_id=account['id'], enabled=True)).fetchone()
        from .native_wallet import snapshot
        return {'rules_version': NATIVE_INVENTORY_VERSION, 'game_id': account['game_id'],
                'super_account': bool(entitlement), 'items': result, 'item_count': len(result),
                'wallet': snapshot(c, account['id'])}


def equipment_projection(data):
    """显式协商轻量装备视图；旧调用保留全库存合同，未知投影版本必须拒绝。"""
    value = data.get('projection')
    if value is not None and value != NATIVE_EQUIPMENT_PROJECTION:
        raise Fault('NATIVE_PROJECTION_CONFLICT', 'Unknown equipment projection', 409)
    return value is not None


def equipment_state(service, c, account):
    """在已鉴权且锁定的同一事务中回读全部原生装备和钱包，不扫描或传输未装备目录。

    换装响应包含该次持久结果，退出外层事务提交成功后HTTP层才发送。
    仅映射本人有效库存；缺失或版本冲突不能伪装成完整成功快照。
    """
    equipment = c.select('equipment', Q(account_id=account['id'], slot__startswith='native:'), order=('slot',))
    bindings = {b.inventory_id: b for b in NativeInventoryBinding.objects.using(c.alias).filter(
        inventory_id__in=[x['inventory_id'] for x in equipment], inventory__account_id=account['id']).filter(
        Q(inventory__expires_at__isnull=True) | Q(inventory__expires_at__gt=service.clock()))}
    rows = []
    for item in equipment:
        binding = bindings.get(item['inventory_id'])
        if binding is None or binding.rules_version != NATIVE_INVENTORY_VERSION:
            raise Fault('NATIVE_CATALOG_CONFLICT', 'Equipment projection needs inventory reload', 409)
        rows.append(dict(gid=binding.id+1000, equipment_slot=item['slot']))
    from .native_wallet import snapshot
    return dict(projection=NATIVE_EQUIPMENT_PROJECTION, rules_version=NATIVE_INVENTORY_VERSION,
                game_id=account['game_id'], equipment=rows, equipment_count=len(rows),
                wallet=snapshot(c,account['id']))


def equip(service, game_token, data):
    """验证本账号原生GID、目标背包和固定槽后持久换装；重复请求不重复审计，跨账号引用拒绝。

    开放基础类型、100013默认匕首回退、固定角色装饰、鲲核心和七个已核实功能卡；其他父子类型拒绝，
    不因超级账号权益跳过协议校验；响应只在事务提交后由HTTP适配层返回。
    """
    scope(service, data.get('rules_version'))
    projection = equipment_projection(data)
    operation, subject, target, location = (data.get(k) for k in ('operation', 'subject', 'target', 'location'))
    if (type(operation) is not int or operation not in (0, 1) or type(subject) is not int
            or not 1000 < subject < 2**63 or type(target) is not int or not 0 <= target < 2**63
            or (operation == 0 and target == 0)
            or type(location) is not int or location not in (-1, 0, 1, 2, 3, 10, 11, 12, 36, 42, 50, 64, 94)):
        raise Fault('NATIVE_EQUIPMENT', 'Unsupported equipment operation', 409)
    with service.db.transaction() as c:
        _, account = authorize_game_session(service, c, game_token, data)
        def owned(gid):
            """将不可信GID解析为本人仍有效的库存，不泄露其他账号的物品内容。"""
            binding = c.select('native_inventory_bindings', Q(id=gid-1000, rules_version=NATIVE_INVENTORY_VERSION)).fetchone()
            item = c.select('inventory', Q(id=binding['inventory_id'], account_id=account['id']) &
                            (Q(expires_at__isnull=True) | Q(expires_at__gt=service.clock()))).fetchone() if binding else None
            if not item:
                raise Fault('NATIVE_EQUIPMENT', 'Active owned instance required', 409)
            return item, service.catalog['items'].get(item['item_key'], {}).get('native', {})
        item, definition = owned(subject)
        from .native_pve_benefits import functional
        from .native_avatar_body import catalog as body_catalog
        is_body=str(definition.get('item_id')) in body_catalog()['parts']
        if is_body and operation==1:
            raise Fault('NATIVE_EQUIPMENT','角色默认身体部件不能单独卸下',409)
        if operation==1 and definition.get('item_id')==100013:
            raise Fault('NATIVE_EQUIPMENT','Default knife must remain equipped',409)
        if operation==0 and c.table('native_item_states').filter(state__swallow__gid=subject).exists():
            raise Fault('NATIVE_EQUIPMENT','Detach the fusion material before equipping it',409)
        from .catalogs import native_equipment_available
        if not (operation == 1 and target == 0) and (definition.get('location') != location
                or not native_equipment_available(definition)):
            raise Fault('NATIVE_EQUIPMENT', 'Item slot or availability is not verified', 409)
        # boot67当前客户端真实A008卸下请求使用Target=0、Location=0，资源本身Location=65。
        # 因此该分支的Location不是物品槽；只能以服务端持久位置决定实际移除对象。
        # 不能把0当其他账号/背包通配符；仅解析subject当前归属，已经未装备则幂等返回。
        if operation == 1 and target == 0:
            equipped = c.select('equipment', Q(account_id=account['id'], inventory_id=item['id']))
            expected_bag = f"native:bag:{definition.get('item_id')}" if definition.get('location') == 12 else None
            if len(equipped) > 1 or (equipped and equipped[0]['slot'] != expected_bag
                    and (not equipped[0]['slot'].startswith('native:')
                         or not equipped[0]['slot'].endswith(':'+str(definition.get('location'))))):
                raise Fault('NATIVE_EQUIPMENT_CONFLICT', 'Owned instance location has changed', 409)
            slot = equipped[0]['slot'] if equipped else f'native:0:{location}'
        elif location in (10, 11, 12, 36, 42, 50, 64) or (location==-1 and functional(definition)):
            # 功能卡与角色同属根1；-1只允许已核实的全能体验卡，不能挤占专家卡或放进背包。
            if target != 1:
                raise Fault('NATIVE_EQUIPMENT', 'Root owner required', 409)
            # 多个背包同时挂在根1的槽12，各自保留独立的武器子树。
            # 不用单一native:1:12覆盖其他背包，也不移动或删除其中已装备武器。
            slot = f"native:bag:{definition['item_id']}" if location == 12 else f'native:{target}:{location}'
        else:
            _, parent = owned(target)
            from .native_features_catalog import attachment_compatible, avatar_compatible, avatar_catalog
            from .native_avatar_body import compatible as body_compatible, slot as body_slot
            is_avatar=str(definition.get('item_id')) in avatar_catalog()['components']
            compatible=(body_compatible(definition,parent) if location==-1 else
                        attachment_compatible(definition,parent) if location==94 else
                        avatar_compatible(definition,parent) if is_avatar else parent.get('location')==12)
            if not compatible:
                raise Fault('NATIVE_EQUIPMENT', 'Owned backpack required', 409)
            slot = body_slot(definition['item_id'],target) if location==-1 else f'native:{target}:{location}'
            if is_body and c.table('equipment').filter(account_id=account['id'],inventory_id=item['id']).exclude(slot=slot).exists():
                raise Fault('NATIVE_EQUIPMENT','默认身体部件不能从另一角色移走',409)
        current = c.select('equipment', Q(account_id=account['id'], slot=slot)).fetchone()
        same = current is not None and current['inventory_id'] == item['id']
        changed = (operation == 0 and not same) or (operation == 1 and same)
        if operation == 1 and current is not None and not same:
            raise Fault('NATIVE_EQUIPMENT_CONFLICT', 'Equipment slot has changed', 409)
        defaults=[]
        if changed:
            advance_revision(c, account['id'], instance_ids=[item['id']]+([current['inventory_id']] if current else []))
            if operation == 0:
                # 同一原生实例只有一个OwnerGID；换背包必须同时释放原位置。
                c.delete('equipment', Q(account_id=account['id'], inventory_id=item['id']))
                c.equip(account['id'], slot, item['id'])
            else:
                c.delete('equipment', Q(account_id=account['id'], slot=slot, inventory_id=item['id']))
            from .native_starter import restore_default_knives
            # 对已有初装策略的账号维持每包默认刀；历史账号首次补包也走同一事务。
            defaults=restore_default_knives(service,c,account['id'])
            summary = dict(operation=operation, subject=subject, target=target, location=location,
                           rules_version=NATIVE_INVENTORY_VERSION)
            request_id = identifier()
            service._audit(c, account['id'], 'native.equipment.change', item['id'], request_id, 'native equipment change', summary)
            service._outbox(c, 'native-equip:'+request_id, 'native.equipment.changed', account['id'], summary)
        from .native_avatar import attach_owned_defaults
        avatar_defaults=attach_owned_defaults(service,c,account['id'],[item['id']]) if operation==0 else []
        result = {'status': 'SAVED' if changed or avatar_defaults else 'UNCHANGED', 'operation': operation, 'subject': subject,
                'target': target, 'location': location, 'rules_version': NATIVE_INVENTORY_VERSION}
        if projection:
            result['equipment_state'] = equipment_state(service, c, account)
        if defaults or avatar_defaults:
            from .native_features import item_delta
            ids=[r['instance_id'] for r in defaults+avatar_defaults]+[item['id']]+([current['inventory_id']] if current else [])
            result['inventory_delta']=item_delta(c,account,set(ids))
            result['default_equipment']=[dict(gid=r['gid'],target=r['target']) for r in defaults]
            result['avatar_equipment']=avatar_defaults
        return result
