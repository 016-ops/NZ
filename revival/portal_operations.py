"""门户运营领域入口：复用真实资产、购物车和房间目录，不创建第二套玩家身份。

Service 在持有部署事务锁、完成 Bearer 权限校验后调用 command/query；本模块仍检查
管理员和玩家身份种类。规则使用不可变 NativeRuleRevision 快照及 CAS，现有 GID、
钱包、历史订单和合成 profile 均不重写。原生商店和房间调用下方 helper 共用运营策略。
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re

from django.db.models import Q

from .assets import mutate
from .native_features_catalog import feature_catalog
from .native_rooms import catalog as room_catalog
from .service import Fault, identifier, integer, text_field


SHOP_NAMESPACE = 'portal:shop'
PLAY_NAMESPACE = 'portal:play-policy'
DEFAULT_MAP_IDS = (113, 164, 191, 141, 140, 168, 96)


def _admin(actor):
    """仅接受已由 Service 验证的管理员；具体角色权限仍由统一权限矩阵决定。"""
    if actor.get('kind') != 'admin':
        raise Fault('FORBIDDEN', '此操作需要管理员身份', 403)


def _player(service, c, actor):
    """根据网页会话本人 ID 重新读账号及制裁，不接受请求中的目标账号覆盖身份。"""
    if actor.get('kind') != 'player':
        raise Fault('FORBIDDEN', '此入口需要玩家网页会话', 403)
    account = service._account(c, actor['id'])
    service._active(c, account)
    return account


def _latest(c, namespace):
    """在本次部署事务内缓存规则快照；购物车多行不反复读取整份JSON，下一请求会重读。

    缓存仅挂在每次 transaction 新建的 Repository，不跨事务或进程复用；_save写入后
    同步替换缓存，避免同一事务发布后仍读旧价。部署锁保证事务期间无其他发布者。
    """
    cache = getattr(c, '_portal_rule_cache', None)
    if cache is None:
        cache = c._portal_rule_cache = {}
    if namespace not in cache:
        cache[namespace] = c.select('native_rule_revisions', Q(namespace=namespace),
                                    order=('-revision',), limit=1).fetchone()
    return cache[namespace]


def _save(service, c, actor, namespace, definition, expected_revision):
    """核对全局修订后追加新快照；旧定义保留，重复命令由 Service 幂等层回放。"""
    current = _latest(c, namespace)
    revision = current['revision'] if current else 0
    if type(expected_revision) is not int or expected_revision != revision:
        raise Fault('RULE_CONFLICT', '运营配置已变化，请刷新后重试', 409)
    c.update('native_rule_revisions', Q(namespace=namespace, enabled=True), {'enabled': False})
    row = {'id': identifier(), 'namespace': namespace, 'revision': revision + 1,
           'definition': definition, 'enabled': True, 'created_at': service.clock(),
           'actor': actor['id']}
    c.insert('native_rule_revisions', row)
    c._portal_rule_cache[namespace] = row
    return row


def shop_revision(c):
    """返回整座商城的运营修订；加入报价，使下架或任意价格修改使旧报价失效。"""
    row = _latest(c, SHOP_NAMESPACE)
    return row['revision'] if row else 0


def _shop_overrides(c):
    """返回已持久化覆盖；固定商品目录始终是物品和礼包构成的唯一基础来源。"""
    row = _latest(c, SHOP_NAMESPACE)
    return row['definition'].get('offers', {}) if row else {}


def _default_offer_enabled(commodity_id, raw):
    """保留既有可见商品及已确认的续费商品恢复规则，不额外开放其他隐藏商品。"""
    from .native_shop_compat import restored_offers
    return bool(raw.get('bShowCommodity') or commodity_id in restored_offers()['offer_ids'])


def resolve_shop_offer(service, c, commodity_id, raw_offer=None, *, allow_hidden=False):
    """供原生报价共用：返回应用运营价格/期限的目录副本；下架或未知商品返回 None。

    native_actions.shop_quote 取商品时调用本函数，随后仍执行原销售时间、币种、
    礼包、数量及期限校验。严禁修改 feature_catalog 的缓存或信任客户端价格。
    """
    raw = raw_offer if raw_offer is not None else feature_catalog()['offers'].get(str(commodity_id))
    if raw is None:
        return None
    override = _shop_overrides(c).get(str(commodity_id), {})
    if not override.get('enabled', bool(allow_hidden) or _default_offer_enabled(commodity_id, raw)):
        return None
    offer = deepcopy(raw)
    offer['bShowCommodity'] = True
    if 'prices' in override:
        offer['nPriceArray'] = list(override['prices'])
    if 'period_hours' in override:
        offer['nAvailPreiodArray'] = list(override['period_hours'])
        if offer.get('bIsPackage'):
            # 非计数型礼包全部子项继承明确的购买期限，避免残留旧套装期限覆盖新值。
            offer['PeriodArrayForSuite'] = [-2] * len(override['period_hours'])
    return offer


def _period_editable(service, offer):
    """计数材料的购买档位表示数量，不能被后台误当小时；混合礼包保持冻结期限。"""
    ids = offer.get('nItemIDArray') or []
    return bool(ids) and all(service.catalog['items'].get('cn2020:' + str(item_id), {})
                             .get('storage_mode') == 'instance' for item_id in ids)


def _offer_row(service, commodity_id, raw, override, revision):
    """构造后台和玩家共用的脱敏商品摘要；原生静态字段和平台可售状态分别保留。"""
    ids = list(raw.get('nItemIDArray') or [])
    editable = _period_editable(service, raw)
    periods = list(override.get('period_hours', raw.get('nAvailPreiodArray') or []))
    if editable:
        # 原商城1752000小时是既有永久哨兵，编辑器统一显示-1；不把材料购买数量改成永久。
        periods = [-1 if value == 1752000 else value for value in periods]
    return {'commodity_id': commodity_id, 'label': raw.get('sItemName') or str(commodity_id),
            'enabled': override.get('enabled', _default_offer_enabled(commodity_id, raw)),
            'sort_order': override.get('sort_order', commodity_id),
            'prices': list(override.get('prices', raw.get('nPriceArray') or [])),
            'period_hours': periods, 'period_editable': editable,
            'currency': 'cn2020:' + str(raw.get('sCommodityType')), 'item_ids': ids,
            'items': [{'item_id': item_id, 'name': feature_catalog()['items'].get(str(item_id), {})
                       .get('name', str(item_id))} for item_id in ids],
            'revision': revision, 'evidence': 'COMPATIBILITY_INVENTORY_ONLY',
            'native_combat_verified': False}


def _sale_open(service, raw):
    """按与原生报价相同的北京时间日期/时分秒过滤玩家列表，后台仍可管理过期商品。"""
    now = datetime.fromtimestamp(service.clock(), timezone(timedelta(hours=8)))
    date, clock = int(now.strftime('%Y%m%d')), int(now.strftime('%H%M%S'))
    start, end = raw.get('ItemOnSaleDate', 0), raw.get('ItemRecallDate', 0)
    return not ((start and date < start) or (date == start and clock < (raw.get('ItemOnSaleTime') or 0))
                or (end and date > end) or (date == end and clock > (raw.get('ItemRecallTime') or 0)))


def _query_integer(value, minimum, maximum, name):
    """GET允许十进制字符串，POST查询仍拒绝布尔/浮点，防止int转换悄悄截断非法值。"""
    if isinstance(value, str) and re.fullmatch('[0-9]{1,20}', value):
        value = int(value)
    return integer(value, minimum, maximum, name)


def shop_view(service, c, filters, *, management=False):
    """按关键字和排序有界分页；玩家仅得到当前可售且完整的目录，后台得到全部定义。"""
    search = filters.get('search', '')
    if not isinstance(search, str) or len(search) > 200:
        raise Fault('INVALID', '商品搜索词过长')
    limit = _query_integer(filters.get('limit', 50), 1, 200, 'limit')
    offset = _query_integer(filters.get('offset', 0), 0, 1000000, 'offset')
    revision, overrides = shop_revision(c), _shop_overrides(c)
    rows = []
    for key, raw in feature_catalog()['offers'].items():
        commodity_id = int(key)
        row = _offer_row(service, commodity_id, raw, overrides.get(key, {}), revision)
        if search.lower() not in (row['label'] + ' ' + key).lower():
            continue
        if not management and (not row['enabled'] or not _sale_open(service, raw)
                or row['currency'] not in service.catalog['currencies']
                or not row['prices'] or not row['item_ids']
                or any('cn2020:' + str(i) not in service.catalog['items'] for i in row['item_ids'])):
            continue
        rows.append(row)
    rows.sort(key=lambda row: (row['sort_order'], row['commodity_id']))
    return {'revision': revision, 'total': len(rows), 'items': rows[offset:offset + limit]}


def _save_offer(service, c, actor, data):
    """只修改已有商品的价格档位/排序/上下架；数量型档位及未知期限不能被小时覆盖。"""
    commodity_id = integer(data.get('commodity_id'), 1, 2**31 - 1, 'commodity_id')
    raw = feature_catalog()['offers'].get(str(commodity_id))
    if raw is None:
        raise Fault('OFFER', '只能管理固定目录中已存在的商品', 409)
    if type(data.get('enabled')) is not bool:
        raise Fault('INVALID', 'enabled 必须是布尔值')
    overrides = deepcopy(_shop_overrides(c))
    entry = dict(overrides.get(str(commodity_id), {}), enabled=data['enabled'],
                 sort_order=integer(data.get('sort_order', commodity_id), 0, 2**31 - 1, 'sort_order'))
    slots = len(raw.get('nPriceArray') or [])
    if not slots:
        raise Fault('OFFER', '商品缺少可用价格档位', 409)
    if 'prices' in data:
        prices = data['prices']
        if not isinstance(prices, list) or len(prices) != slots:
            raise Fault('INVALID', '价格必须与固定目录档位数一致')
        entry['prices'] = [integer(value, 0, 2147483647, 'price') for value in prices]
    if 'period_hours' in data:
        if not _period_editable(service, raw):
            raise Fault('OFFER_PERIOD', '计数材料或混合礼包不能按小时修改档位', 409)
        periods = data['period_hours']
        if not isinstance(periods, list) or len(periods) != slots:
            raise Fault('INVALID', '期限必须与固定目录档位数一致')
        entry['period_hours'] = [value if type(value) is int and value == -1
                                 else integer(value, 1, 87600, 'period_hours') for value in periods]
    overrides[str(commodity_id)] = entry
    saved = _save(service, c, actor, SHOP_NAMESPACE, {'offers': overrides}, data.get('expected_revision'))
    return {'status': 'PUBLISHED', 'revision': saved['revision'],
            'offer': _offer_row(service, commodity_id, raw, entry, saved['revision'])}


def _default_policy():
    """首发七张地图继承各自原目录难度；160 变体不在白名单，和96按ID区分。"""
    maps = room_catalog()['maps']
    return {'maps': {str(map_id): {'enabled': True, 'submodes': list(maps[str(map_id)]['submodes'])}
                     for map_id in DEFAULT_MAP_IDS}}


def _policy(c):
    """未保存配置时使用明确首发白名单；空保存配置表示全部关闭，不能恢复默认。"""
    row = _latest(c, PLAY_NAMESPACE)
    return (row['revision'], row['definition']) if row else (0, _default_policy())


def check_play_policy(service, c, map_id, mode_id=None, submode_id=None):
    """建房/换图/入房/开局共用服务器白名单；禁用返回可直接显示的中文警告。"""
    map_id = integer(map_id, 1, 2**31 - 1, 'map_id')
    revision, definition = _policy(c)
    raw = room_catalog()['maps'].get(str(map_id))
    entry = definition['maps'].get(str(map_id), {})
    if not raw or not entry.get('enabled'):
        raise Fault('PLAY_DISABLED', '该地图或模式尚未开放，请选择当前开放的地图', 409)
    if mode_id is not None and (type(mode_id) is not int or mode_id != raw['mode_id']):
        raise Fault('PLAY_DISABLED', '该地图对应的模式未开放', 409)
    if submode_id is not None and (type(submode_id) is not int or submode_id not in entry['submodes']):
        raise Fault('PLAY_DISABLED', '该地图难度尚未开放，请刷新房间设置', 409)
    return {'revision': revision, 'map_id': map_id, 'mode_id': raw['mode_id'],
            'submodes': list(entry['submodes'])}


def filter_play_catalog(service, c, catalogue):
    """对原生目录的副本过滤地图与难度；固定缓存、资源证明及游戏人数规则均不改写。"""
    revision, definition = _policy(c)
    result = deepcopy(catalogue)
    result['maps'] = {key: dict(value, submodes=list(definition['maps'][key]['submodes']))
                      for key, value in catalogue['maps'].items()
                      if definition['maps'].get(key, {}).get('enabled')}
    result['play_policy_revision'] = revision
    return result


def play_policy_view(c):
    """后台返回全部固定地图及当前状态，玩家端可只选 enabled；不把开放等同原生验收。"""
    revision, definition = _policy(c)
    rows = []
    for key, raw in room_catalog()['maps'].items():
        entry = definition['maps'].get(key, {})
        rows.append({'map_id': int(key), 'name': raw['name'], 'mode_id': raw['mode_id'],
                     'kind': raw['kinds'][0], 'enabled': bool(entry.get('enabled')),
                     'submodes': list(entry.get('submodes', raw['submodes'])),
                     'available_submodes': list(raw['submodes']), 'player_counts': list(raw['player_counts']),
                     'native_combat_verified': False})
    return {'revision': revision, 'maps': sorted(rows, key=lambda row: row['map_id'])}


def _save_policy(service, c, actor, data):
    """全量替换白名单并核对原目录模式/难度，未知图、重复图和空启用难度均拒绝。"""
    rows = data.get('maps')
    if not isinstance(rows, list) or len(rows) > len(room_catalog()['maps']):
        raise Fault('INVALID', '地图白名单格式不正确')
    configured = {}
    for row in rows:
        if not isinstance(row, dict):
            raise Fault('INVALID', '地图配置必须是对象')
        map_id = integer(row.get('map_id'), 1, 2**31 - 1, 'map_id')
        raw = room_catalog()['maps'].get(str(map_id))
        if not raw or str(map_id) in configured or type(row.get('enabled')) is not bool:
            raise Fault('INVALID', '地图未知、重复或启用状态无效')
        submodes = row.get('submodes', raw['submodes'])
        if (not isinstance(submodes, list) or any(type(value) is not int for value in submodes)
                or len(set(submodes)) != len(submodes) or any(value not in raw['submodes'] for value in submodes)
                or (row['enabled'] and not submodes)):
            raise Fault('INVALID', '难度必须来自对应地图的固定目录')
        configured[str(map_id)] = {'enabled': row['enabled'], 'submodes': list(submodes)}
    saved = _save(service, c, actor, PLAY_NAMESPACE, {'maps': configured}, data.get('expected_revision'))
    return dict(play_policy_view(c), status='PUBLISHED', revision=saved['revision'])


def _remove(service, c, actor, data):
    """管理员按库存实例及预期版本删除；沿用装备树/皮肤/吞噬保护和不可改写资产账本。"""
    account = service._account(c, data.get('target_account_id'))
    item = c.select('inventory', Q(id=data.get('instance_id'), account_id=account['id'])).fetchone()
    if not item:
        raise Fault('INVENTORY', '目标账号没有此库存实例', 404)
    if type(data.get('expected_version')) is not int or data['expected_version'] != item['version']:
        raise Fault('REVISION_CONFLICT', '道具已变化，请刷新后再删除', 409)
    quantity = integer(data.get('quantity'), 1, item['quantity'], 'quantity')
    result = mutate(service, c, actor, account['id'],
                    [{'type': 'remove', 'instance_id': item['id'], 'quantity': quantity}],
                    text_field(data.get('reason'), 'reason', 500), 'ADMIN_REMOVE')
    from .native_actions import event
    event(service, c, account['id'], 'inventory.removed', {'operation_id': result['operation_id'],
                                                       'instance_id': item['id'], 'quantity': quantity})
    return dict(result, account_id=account['id'], instance_id=item['id'], quantity=quantity)


def command(service, c, actor, action, data):
    """统一运营命令分派；Service 提供外层权限、事务、审计和幂等，本模块不接受游戏令牌。"""
    if action in ('shop.offer.save', 'play-policy.save', 'asset.remove'):
        _admin(actor)
        handler = {'shop.offer.save': _save_offer, 'play-policy.save': _save_policy,
                   'asset.remove': _remove}[action]
        return handler(service, c, actor, data)
    if action in ('player.shop.quote', 'player.shop.purchase'):
        account = _player(service, c, actor)
        lines = data.get('lines')
        if (not isinstance(lines, list) or not 1 <= len(lines) <= 20
                or any(not isinstance(line, dict) or set(line) - {'commodity_id', 'price_index', 'quantity'} for line in lines)):
            raise Fault('INVALID', '购物车需要1至20项明确的商品/价格档位/数量')
        from .native_actions import shop_quote, shop_commit
        if action == 'player.shop.quote':
            return shop_quote(service, c, account, data)
        return shop_commit(service, c, account, 'shop.purchase', data)
    raise Fault('NOT_FOUND', '未知门户运营命令', 404)


def query(service, c, actor, view, filters):
    """门户读取入口；玩家只读本人的真实库存，商城与管理查询不返回游戏凭据。"""
    if view in ('shop-admin', 'play-policy'):
        _admin(actor)
        return shop_view(service, c, filters, management=True) if view == 'shop-admin' else play_policy_view(c)
    account = _player(service, c, actor)
    if view == 'player-shop':
        return shop_view(service, c, filters)
    if view == 'player-inventory':
        from .native_inventory import page
        data = dict(filters)
        for key, minimum, maximum in (('after_gid', 1000, 2**63 - 1), ('limit', 1, 200), ('revision', 0, 2**63 - 1)):
            if key in data:
                data[key] = _query_integer(data[key], minimum, maximum, key)
        result = page(service, c, account, data)
        for item in result['items']:
            # 网页直接使用真实目录中文名，原GID/实例/期限/分页合同保持不变。
            item['name'] = feature_catalog()['items'].get(str(item['item_id']), {}).get('name', str(item['item_id']))
        return result
    raise Fault('NOT_FOUND', '未知门户运营视图', 404)
