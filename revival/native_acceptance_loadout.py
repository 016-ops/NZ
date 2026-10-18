"""受控管理入口配置验收账号诸神套装；所有账号共享同一事务，不覆盖既有资产或历史回执。"""
from django.db.models import Q
from .native_actions import apply,event,item_rewards
from .native_inventory import advance_revision
from .native_pve_benefits import VERSION as BENEFIT_VERSION,level
from .service import Fault,identifier,text_field

VERSION='cn2020-acceptance-zhushen-v1'
WEAPONS=((103981,0),(103983,1),(103985,2),(103987,3))
CARDS=((101210,36),(101211,42),(101149,50))
EXTENDED_VERSION='cn2020-acceptance-five-bags-v2'
COMPLETE_VERSION='cn2020-acceptance-six-bags-v3'
# 按原背包编号及物品目录固定五套主/副/刀/雷；同名PVP/换肤产物不替代这些可售原版。
FIVE_BAGS=(
    (100026,WEAPONS),
    (100063,((103716,0),(103717,1),(103718,2),(103719,3))),
    (100035,((103428,0),(103429,1),(103430,2),(103431,3))),
    (100036,((102762,0),(102764,1),(102763,2),(102765,3))),
    (100037,((102118,0),(102119,1),(102120,2),(102121,3))),
)
# 第六套沿用同一已存在背包GID；元素永久版为ConsumeType0，不能用同名限时版替代。
# 原WeaponMagicUpgradeList明确魔化卡作用于天鲲四个主体；独立实例保证两包可同时使用。
# v2仍保留原直接产物方案及回执；v3按原生库存/换肤关系准备魔鲲，不删旧103716等物品。
SIX_BAGS=(FIVE_BAGS[0],(100063,FIVE_BAGS[2][1]),*FIVE_BAGS[2:],
          (100933,((104259,0),(104261,1),(104263,2),(104265,3))))
MAGIC_SKINS={103428:103726,103429:103727,103430:103728,103431:103729}


def prepare(service,game_ids,*,request_id,reason,profile='zhushen'):
    """显式指定已有启用账号，原子补齐永久物品、首包套装和3级百宝箱，重试返回原回执。

    这是本机管理命令调用的维护操作，不暴露为玩家HTTP，不创建或启用账号、不授予GM。
    新请求可重新应用配装；同一请求重试不覆盖玩家后来手动更换的装备，也不会重复发放。
    """
    request_id=text_field(request_id,'request_id',128)
    reason=text_field(reason,'reason',500)
    if profile not in ('zhushen','five-bags','six-bags'):
        raise Fault('INVALID','未知验收配装方案')
    # 新增方案独立版本和回执种类；旧v1请求的重放不能偷偷扩大奖励或重置后来的配装。
    version={'zhushen':VERSION,'five-bags':EXTENDED_VERSION,'six-bags':COMPLETE_VERSION}[profile]
    sets=((100026,WEAPONS),) if profile=='zhushen' else (SIX_BAGS if profile=='six-bags' else FIVE_BAGS)
    cards=CARDS if profile=='zhushen' else CARDS+((102000,-1),)
    if (not isinstance(game_ids,list) or not 1<=len(game_ids)<=32
            or any(type(value) is not int or not 1<=value<=2147483647 for value in game_ids)
            or len(set(game_ids))!=len(game_ids)):
        raise Fault('INVALID','必须显式指定1至32个不同的验收账号')
    with service.db.transaction() as c:
        accounts=c.select('accounts',Q(game_id__in=game_ids),order=('game_id',))
        if len(accounts)!=len(game_ids) or any(row['status']!='ACTIVE' for row in accounts):
            raise Fault('ACCOUNT','验收账号不存在或未启用',409)
        results=[]
        for account in accounts:
            aid=account['id']
            prior=c.select('native_receipts',Q(account_id=aid,kind=version,result__request_id=request_id)).fetchone()
            if prior:
                if prior['result'].get('requested_game_ids')!=sorted(game_ids):
                    raise Fault('IDEMPOTENCY','请求标识已用于其他账号集合',409)
                results.append(prior['result']);continue
            if not c.select('characters',Q(account_id=aid)):
                raise Fault('CHARACTER','验收账号尚未建角',409)
            operations=[];changed=set()

            def acquire(item_id,*,exclude=(),preferred=None):
                """复用本人永久实例及其GID；只有缺项经统一资产账本新增，不删除限时旧实例。"""
                query=Q(account_id=aid,item_key=f'cn2020:{item_id}',expires_at__isnull=True)&~Q(id__in=exclude)
                row=c.select('inventory',query&Q(id=preferred),limit=1).fetchone() if preferred else None
                row=row or c.select('inventory',query,order=('id',),limit=1).fetchone()
                if row is None:
                    operations.extend(apply(service,c,aid,item_rewards(service,[dict(item_id=item_id)]),
                                            version,actor_id='local-management:acceptance'))
                    row=c.select('inventory',query,order=('id',),limit=1).fetchone()
                binding=c.select('native_inventory_bindings',Q(inventory_id=row['id'])).fetchone()
                if binding is None or binding['rules_version']!=service.catalog['items'][row['item_key']]['rules_version']:
                    raise Fault('NATIVE_CATALOG_CONFLICT','验收物品缺少当前稳定GID',409)
                return row,binding['id']+1000

            def equip(row,slot):
                """替换目标槽但保留原物品；同步记录移出槽的实例，防止客户端缓存残留。"""
                current=c.select('equipment',Q(account_id=aid,slot=slot)).fetchone()
                if current and current['inventory_id']==row['id']:
                    return
                if current:changed.add(current['inventory_id'])
                changed.add(row['id'])
                c.delete('equipment',Q(account_id=aid,inventory_id=row['id']))
                c.equip(aid,slot,row['id'])

            equipped=[];bag_targets={};reserved={};claimed=set()
            for bag_id,weapons in sets:
                bag,bag_gid=acquire(bag_id);equip(bag,f'native:bag:{bag_id}')
                bag_targets[bag_id]=bag_gid
                for item_id,location in weapons:
                    key=f'native:{bag_gid}:{location}'
                    current=c.select('equipment',Q(account_id=aid,slot=key)).fetchone()
                    row=c.select('inventory',Q(id=current['inventory_id'],item_key=f'cn2020:{item_id}',expires_at__isnull=True)).fetchone() if current else None
                    if row:reserved[key]=row['id']
            for bag_id,weapons in sets:
                bag_gid=bag_targets[bag_id]
                for item_id,location in weapons:
                    key=f'native:{bag_gid}:{location}';preferred=reserved.get(key)
                    # 不从另一个仍需保留的背包偷走同款枪；已有正确实例原位复用，只为缺项新增。
                    row,gid=acquire(item_id,exclude=claimed|set(reserved.values())-({preferred} if preferred else set()),preferred=preferred)
                    claimed.add(row['id']);equip(row,key)
                    equipped.append(dict(item_id=item_id,gid=gid,target=bag_gid,location=location))
                    if profile=='six-bags' and bag_id==100063:
                        skin,skin_gid=acquire(MAGIC_SKINS[item_id])
                        previous=c.select('native_item_states',Q(inventory_id=row['id'])).fetchone()
                        state=dict(previous['state']) if previous else {}
                        desired=dict(item_id=MAGIC_SKINS[item_id],gid=skin_gid)
                        if state.get('skin')!=desired:
                            state['skin']=desired
                            operations.extend(apply(service,c,aid,[dict(type='native_state',instance_id=row['id'],state=state)],
                                                    version,actor_id='local-management:acceptance'))
            for item_id,location in cards:
                row,gid=acquire(item_id)
                equip(row,f'native:1:{location}')
                equipped.append(dict(item_id=item_id,gid=gid,target=1,location=location))
            kernels=[]
            if profile=='six-bags':
                from .native_features_catalog import crafting_catalog
                # 满核心是七种永久解锁，加上原界面必需誓约；已有主动核心挂接不被重置。
                for item_id in (103438,*crafting_catalog()['kernels']):
                    row,gid=acquire(item_id)
                    kernels.append(dict(item_id=item_id,gid=gid))
            box,box_gid=acquire(101296)
            previous=c.select('native_item_states',Q(inventory_id=box['id'])).fetchone()
            state=dict(previous['state']) if previous else {}
            store=state.get('pve_store')
            if store is not None and (not isinstance(store,dict) or store.get('version')!=BENEFIT_VERSION):
                raise Fault('NATIVE_CATALOG_CONFLICT','百宝箱已有未知成长版本，拒绝覆盖',409)
            points=store.get('points',0) if store else 0
            level(points)
            points=max(10500,points)
            desired=dict(store or {},version=BENEFIT_VERSION,points=points)
            if store!=desired:
                state['pve_store']=desired
                operations.extend(apply(service,c,aid,[dict(type='native_state',instance_id=box['id'],state=state)],
                                        version,actor_id='local-management:acceptance'))
            from .native_starter import restore_default_knives
            defaults=restore_default_knives(service,c,aid)
            changed.update(row['instance_id'] for row in defaults)
            if changed:advance_revision(c,aid,list(changed))
            result=dict(rule_version=version,request_id=request_id,requested_game_ids=sorted(game_ids),game_id=account['game_id'],
                        status='COMMITTED',operations=operations,equipped=equipped,
                        store=dict(item_id=101296,gid=box_gid,points=points,level=level(points),expires_at=None),
                        native_battle_effects_verified=False)
            if kernels:result['kun_unlocks']=kernels
            c.insert('native_receipts',dict(id=identifier(),account_id=aid,kind=version,result=result,
                                           created_at=service.clock(),confirmed=True))
            service._audit(c,'local-management:acceptance',version,aid,request_id,reason,result)
            event(service,c,aid,'acceptance.loadout.equipped',result)
            results.append(result)
        return dict(rule_version=version,accounts=results)
