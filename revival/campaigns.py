"""开服活动与分人数排行榜；正式对局来源默认关闭，合成成绩不能进入正式分榜。

管理员命令由调用方持有部署事务、认证并写通用审计/幂等回执；本模块仍检查角色。
普通累计奖励由玩家点击领取；榜奖封榜后由维护器逐人通过既有 mutate 账本发放。
"""
from datetime import datetime
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db.models import Q
from django.db import DatabaseError, transaction

from .assets import mutate
from .security import token_hash
from .service import Fault, canonical, identifier, integer, text_field

KINDS = {'CHECKIN_DAYS', 'LAUNCHER_LOGIN_DAYS', 'PLAYTIME_SECONDS', 'CLEAR_COUNT', 'LEADERBOARD'}
HUNT_MAPS = {113, 164, 191}
TOWER_MAPS = {141, 140, 168, 96}
MAPS = HUNT_MAPS | TOWER_MAPS
COMMANDS = {'campaign.save', 'campaign.publish', 'campaign.pause', 'campaign.end',
            'campaign.checkin', 'campaign.claim', 'leaderboard.finalize'}
VIEWS = {'campaigns', 'campaign-admin', 'leaderboards'}


def _owner(actor):
    """最终封榜只允许 Owner，避免普通运营角色不可逆地冻结整个赛季结果。"""
    if actor.get('kind') != 'admin' or actor.get('role') != 'owner':
        raise Fault('FORBIDDEN', '仅 Owner 可以管理活动和封榜', 403)


def _manage(actor, *, read=False):
    """资源管理员可以运营活动，审计管理员仅可读取；用户管理员不能越权管理奖励。"""
    allowed = {'owner', 'resources', 'auditor'} if read else {'owner', 'resources'}
    if actor.get('kind') != 'admin' or actor.get('role') not in allowed:
        raise Fault('FORBIDDEN', '没有活动管理权限', 403)


def _player(service, c, actor):
    """领取与签到只使用当前认证玩家，不接受请求中的目标账号覆盖本人。"""
    if actor.get('kind') != 'player':
        raise Fault('FORBIDDEN', '此操作仅限玩家本人', 403)
    account = service._account(c, actor['id'])
    service._active(c, account)
    return account


def _campaign(service, c, value):
    """读取本环境的活动；测试活动即使误入同一库，也不能被正式服务读取或领取。"""
    row = c.select('campaigns', Q(id=text_field(value, 'campaign_id', 32), test_only=service.test_profile)).fetchone()
    if not row:
        raise Fault('NOT_FOUND', '活动不存在', 404)
    return row


def _rewards(service, changes):
    """奖励只接受正数量道具和目录允许的期限，禁止负数、货币或任意资产写入。"""
    if not isinstance(changes, list) or not 1 <= len(changes) <= 20:
        raise Fault('INVALID', '每档奖励必须包含 1 至 20 项道具')
    result = []
    for item in changes:
        if not isinstance(item, dict) or set(item) - {'type', 'item_key', 'quantity', 'duration_seconds'} or item.get('type') != 'item':
            raise Fault('INVALID', '活动奖励只能发放目录中的道具')
        key = item.get('item_key')
        definition = service.catalog['items'].get(key) if isinstance(key, str) else None
        if not definition:
            raise Fault('UNVERIFIED_RESOURCE', '奖励道具不在当前目录中', 409)
        reward = {'type': 'item', 'item_key': key, 'quantity': integer(item.get('quantity'), 1, definition['stack'], 'quantity')}
        if item.get('duration_seconds') is not None:
            reward['duration_seconds'] = integer(item['duration_seconds'], 1, definition['duration_max'], 'duration_seconds')
        elif not definition['permanent']:
            raise Fault('INVALID', '此道具必须设置有效期限')
        result.append(reward)
    return result


def _positive_list(value, allowed, label):
    """榜奖过滤条件只能缩小已开放范围，拒绝空列表、重复值和布尔数值。"""
    if not isinstance(value, list) or not value or any(type(x) is not int or x not in allowed for x in value) or len(set(value)) != len(value):
        raise Fault('INVALID', f'{label} 包含未知或重复值')
    return sorted(value)


def validate_definition(service, definition):
    """保存和发布都校验完整规则；时间以 UTC 秒传输，自然日按配置时区累计且跨月不重置。"""
    if not isinstance(definition, dict) or set(definition) - {'starts_at', 'ends_at', 'timezone', 'rewards'}:
        raise Fault('INVALID', '活动定义字段不正确')
    starts = integer(definition.get('starts_at'), 0, 253402214400, 'starts_at')
    ends = integer(definition.get('ends_at'), starts + 1, 253402214400, 'ends_at')
    zone = definition.get('timezone', 'Asia/Shanghai')
    if not isinstance(zone, str):
        raise Fault('INVALID', '活动时区不正确')
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise Fault('INVALID', '未知活动时区') from None
    rules = definition.get('rewards')
    if not isinstance(rules, list) or not 1 <= len(rules) <= 100:
        raise Fault('INVALID', '活动需要 1 至 100 档奖励')
    seen, rewards = set(), []
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) - {'key', 'title', 'type', 'threshold', 'changes', 'priority', 'rank_from', 'rank_to', 'map_ids', 'difficulties', 'player_counts'}:
            raise Fault('INVALID', '奖励规则字段不正确')
        key = text_field(rule.get('key'), 'reward key', 64)
        if not re.fullmatch(r'[A-Za-z0-9_-]+', key) or key in seen or not isinstance(rule.get('type'), str) or rule['type'] not in KINDS:
            raise Fault('INVALID', '奖励标识重复或奖励类型未知')
        seen.add(key)
        value = {'key': key, 'title': text_field(rule.get('title', key), 'reward title', 120),
                 'type': rule['type'], 'changes': _rewards(service, rule.get('changes'))}
        if rule['type'] == 'LEADERBOARD':
            value.update(priority=integer(rule.get('priority', 1), 1, 1000000, 'priority'),
                         rank_from=integer(rule.get('rank_from', 1), 1, 100000, 'rank_from'))
            value['rank_to'] = integer(rule.get('rank_to'), value['rank_from'], 100000, 'rank_to')
            if 'threshold' in rule:
                raise Fault('INVALID', '排行榜奖励使用名次范围，不使用累计阈值')
            for field, allowed in (('map_ids', MAPS), ('difficulties', set(range(4097, 4103))), ('player_counts', set(range(1, 9)))):
                if field in rule:
                    value[field] = _positive_list(rule[field], allowed, field)
        else:
            if set(rule) & {'priority', 'rank_from', 'rank_to', 'map_ids', 'difficulties', 'player_counts'}:
                raise Fault('INVALID', '累计奖励不能包含排行榜过滤字段')
            value['threshold'] = integer(rule.get('threshold'), 1, 2147483647, 'threshold')
        rewards.append(value)
    return {'starts_at': starts, 'ends_at': ends, 'timezone': zone, 'rewards': rewards}


def _definition(row):
    """读取发布快照而不是可编辑草稿，避免领取时临时改变已公告的奖励。"""
    return row['published_definition'] or row['definition']


def _open(service, row):
    """签到和登录资格仅在发布且处于活动期时累计；暂停和结束不产生新日期。"""
    definition = _definition(row)
    return row['state'] == 'PUBLISHED' and definition['starts_at'] <= service.clock() < definition['ends_at']


def _day(service, row):
    """用服务端时间计算活动自然日；客户端不能通过传日期补签或跨时区重复领取。"""
    return datetime.fromtimestamp(service.clock(), ZoneInfo(_definition(row)['timezone'])).date().isoformat()


def record_launcher_login(service, c, account_id, launch_id=None):
    """由专用 Launcher 登录成功或票据兑换路径调用；同一自然日多次登录只累计一次。

    调用方必须在已验证账号身份的事务中调用，不能将普通 Web 登录接入本函数。
    渠道区分不等于桌面二进制证明；正式分发验证需在实际客户端环境独立验收。
    launch_id 仅用于调用接口兼容，不保存凭据或把客户端自报 ID 当成授权证据。
    """
    account = service._account(c, account_id)
    service._active(c, account)
    registered = 0
    for row in c.select('campaigns', Q(state='PUBLISHED', test_only=service.test_profile)):
        if _open(service, row):
            lookup = Q(campaign_id=row['id'], account_id=account_id, kind='LAUNCHER_LOGIN', local_day=_day(service, row))
            if not c.select('campaign_days', lookup).fetchone():
                c.insert('campaign_days', {'campaign_id': row['id'], 'account_id': account_id,
                         'kind': 'LAUNCHER_LOGIN', 'local_day': _day(service, row), 'created_at': service.clock()})
                registered += 1
    return {'recorded_campaigns': registered}


def _progress(service, c, row, account_id):
    """个人进度只由去重日记录和受信结算推导；观战、增员和未完成结算不累计游玩。"""
    progress = {'checkin_days': 0, 'launcher_login_days': 0, 'playtime_seconds': 0, 'clear_count': 0}
    days = c.select('campaign_days', Q(campaign_id=row['id'], account_id=account_id))
    progress['checkin_days'] = sum(day['kind'] == 'CHECKIN' for day in days)
    progress['launcher_login_days'] = sum(day['kind'] == 'LAUNCHER_LOGIN' for day in days)
    milliseconds = 0
    for result in c.select('campaign_results', Q(campaign_id=row['id'], valid=True, test_only=service.test_profile)):
        if account_id in result['member_ids']:
            milliseconds += result['duration_ms']
            progress['clear_count'] += int(result['rank_eligible'])
    progress['playtime_seconds'] = milliseconds // 1000
    return progress


def _base(row):
    """公开活动规则和生命周期，不返回内部操作者、凭据或奖励到账之外的私人字段。"""
    return {key: row[key] for key in ('id', 'title', 'state', 'revision', 'test_only')} | {'definition': _definition(row)}


def _freeze(c, campaign_id):
    """封榜快照只读；没有快照时不允许把暂时名次当作可以领取的榜奖。"""
    return c.select('campaign_board_freezes', Q(campaign_id=campaign_id)).fetchone()


def _eligibility(service, c, row, account_id, progress):
    """普通累计奖励逐档领取；榜奖展示冻结后的自动发放状态，不再提供手动领取机会。"""
    claims = {r['reward_key'] for r in c.select('campaign_claims', Q(campaign_id=row['id'], account_id=account_id))}
    frozen = _freeze(c, row['id'])
    award = frozen['awards'].get(account_id) if frozen else None
    counters = {'CHECKIN_DAYS': 'checkin_days', 'LAUNCHER_LOGIN_DAYS': 'launcher_login_days',
                'PLAYTIME_SECONDS': 'playtime_seconds', 'CLEAR_COUNT': 'clear_count'}
    result = []
    for rule in _definition(row)['rewards']:
        eligible = bool(award and award['reward_key'] == rule['key']) if rule['type'] == 'LEADERBOARD' else progress[counters[rule['type']]] >= rule['threshold']
        automatic = rule['type'] == 'LEADERBOARD'
        result.append(dict(rule, eligible=eligible and not automatic and row['state'] in ('PUBLISHED', 'ENDED'),
            claimed=rule['key'] in claims, delivery='AUTOMATIC' if automatic else 'CLAIM',
            delivery_status=award.get('status', 'PENDING') if automatic and award and award['reward_key'] == rule['key'] else None))
    return result


def _boards(service, c, row):
    """每图、难度、实际人数独立排序；相同成员组合只保留最优成功成绩，毫秒并列稳定排序。"""
    nicknames = {character['account_id']: character['nickname'] for character in c.select('characters')}
    grouped, seen = {}, set()
    for result in c.select('campaign_results', Q(campaign_id=row['id'], valid=True, rank_eligible=True,
                                                 test_only=service.test_profile), order=('duration_ms', 'settled_at', 'id')):
        partition = (result['map_id'], result['difficulty'], result['player_count'])
        duplicate = (*partition, result['team_key'])
        if duplicate in seen:
            continue
        seen.add(duplicate)
        entries = grouped.setdefault(partition, [])
        entries.append({'rank': len(entries) + 1, 'result_id': result['id'], 'member_ids': result['member_ids'],
                        'nicknames': [nicknames.get(aid, '未命名玩家') for aid in result['member_ids']],
                        'duration_ms': result['duration_ms'], 'settled_at': result['settled_at']})
    return [{'map_id': key[0], 'difficulty': key[1], 'player_count': key[2], 'entries': entries}
            for key, entries in sorted(grouped.items())]


def _finalize(service, c, row):
    """活动结束后冻结榜和每人最高档；维护器按冻结名单自动发放，单个失败不重发其他人。"""
    previous = _freeze(c, row['id'])
    if previous:
        return {'campaign_id': row['id'], 'finalized': True, 'boards': len(previous['boards']), 'winners': len(previous['awards'])}
    if not row['published_definition'] or (row['state'] != 'ENDED' and service.clock() < _definition(row)['ends_at']):
        raise Fault('CAMPAIGN_RUNNING', '请先结束活动或等待活动期结束后封榜', 409)
    boards, awards = _boards(service, c, row), {}
    rules = sorted((r for r in _definition(row)['rewards'] if r['type'] == 'LEADERBOARD'), key=lambda r: (-r['priority'], r['key']))
    for rule in rules:
        for board in boards:
            if any(field in rule and board[dimension] not in rule[field]
                   for field, dimension in (('map_ids', 'map_id'), ('difficulties', 'difficulty'), ('player_counts', 'player_count'))):
                continue
            for entry in board['entries']:
                if rule['rank_from'] <= entry['rank'] <= rule['rank_to']:
                    for account_id in entry['member_ids']:
                        awards.setdefault(account_id, {'reward_key': rule['key'], 'changes': rule['changes'],
                                                      'result_id': entry['result_id'], 'rank': entry['rank'], 'status': 'PENDING', 'attempts': 0})
    c.insert('campaign_board_freezes', {'campaign_id': row['id'], 'boards': boards, 'awards': awards, 'created_at': service.clock()})
    c.update('campaigns', Q(id=row['id']), {'state': 'ENDED', 'ended_at': row['ended_at'] or service.clock(), 'updated_at': service.clock()})
    return {'campaign_id': row['id'], 'finalized': True, 'boards': len(boards), 'winners': len(awards)}


def command(service, c, actor, action, data):
    """在调用方事务内执行资源运营/Owner封榜或本人领取；唯一键补充通用幂等保护。"""
    if action not in COMMANDS:
        raise Fault('NOT_FOUND', '未知活动命令', 404)
    if action == 'leaderboard.finalize':
        _owner(actor)
    elif action in ('campaign.save', 'campaign.publish', 'campaign.pause', 'campaign.end'):
        _manage(actor)
    else:
        _player(service, c, actor)
    if action == 'campaign.save':
        definition = validate_definition(service, data.get('definition'))
        title = text_field(data.get('title'), 'title', 120)
        if data.get('campaign_id'):
            row = _campaign(service, c, data['campaign_id'])
            if row['state'] != 'DRAFT' or row['published_definition'] is not None:
                raise Fault('CAMPAIGN_IMMUTABLE', '已发布活动规则已冻结；修改奖励请创建新活动', 409)
            if data.get('expected_revision') != row['revision']:
                raise Fault('REVISION_CONFLICT', '活动草稿已变化，请刷新后重试', 409)
            c.update('campaigns', Q(id=row['id']), {'title': title, 'definition': definition,
                     'revision': row['revision'] + 1, 'updated_at': service.clock()})
            campaign_id = row['id']
        else:
            campaign_id = identifier()
            c.insert('campaigns', {'id': campaign_id, 'title': title, 'definition': definition,
                     'test_only': service.test_profile, 'created_at': service.clock(), 'updated_at': service.clock()})
        return _base(_campaign(service, c, campaign_id))
    row = _campaign(service, c, data.get('campaign_id'))
    if action == 'campaign.publish':
        if row['state'] not in ('DRAFT', 'PAUSED', 'PUBLISHED') or service.clock() >= _definition(row)['ends_at']:
            raise Fault('CAMPAIGN_CLOSED', '已结束活动不能重新发布', 409)
        frozen = row['published_definition'] or validate_definition(service, row['definition'])
        c.update('campaigns', Q(id=row['id']), {'published_definition': frozen, 'state': 'PUBLISHED', 'updated_at': service.clock()})
    elif action == 'campaign.pause':
        if row['state'] not in ('PUBLISHED', 'PAUSED'):
            raise Fault('CAMPAIGN_STATE', '只有已发布活动可以暂停', 409)
        c.update('campaigns', Q(id=row['id']), {'state': 'PAUSED', 'updated_at': service.clock()})
    elif action == 'campaign.end':
        if not row['published_definition']:
            raise Fault('CAMPAIGN_STATE', '草稿不能结束或发奖', 409)
        c.update('campaigns', Q(id=row['id']), {'state': 'ENDED', 'ended_at': row['ended_at'] or service.clock(), 'updated_at': service.clock()})
    elif action == 'leaderboard.finalize':
        return _finalize(service, c, row)
    elif action == 'campaign.checkin':
        if not _open(service, row):
            raise Fault('CAMPAIGN_CLOSED', '签到活动当前未开放', 409)
        day = _day(service, row)
        exists = c.select('campaign_days', Q(campaign_id=row['id'], account_id=actor['id'], kind='CHECKIN', local_day=day)).fetchone()
        if not exists:
            c.insert('campaign_days', {'campaign_id': row['id'], 'account_id': actor['id'], 'kind': 'CHECKIN', 'local_day': day, 'created_at': service.clock()})
        return {'campaign_id': row['id'], 'local_day': day, 'already_checked_in': bool(exists), 'progress': _progress(service, c, row, actor['id'])}
    elif action == 'campaign.claim':
        if row['state'] not in ('PUBLISHED', 'ENDED') or not row['published_definition'] or service.clock() < _definition(row)['starts_at']:
            raise Fault('CAMPAIGN_CLOSED', '奖励当前不可领取', 409)
        reward_key = text_field(data.get('reward_key'), 'reward_key', 64)
        rule = next((r for r in _definition(row)['rewards'] if r['key'] == reward_key), None)
        if not rule:
            raise Fault('NOT_FOUND', '奖励不存在', 404)
        if rule['type'] == 'LEADERBOARD':
            raise Fault('AUTOMATIC_REWARD', '排行榜奖励封榜后自动发放，请查看到账状态', 409)
        claim_key = 'leaderboard' if rule['type'] == 'LEADERBOARD' else 'rule:' + reward_key
        previous = c.select('campaign_claims', Q(campaign_id=row['id'], account_id=actor['id'], claim_key=claim_key)).fetchone()
        if previous:
            return {'campaign_id': row['id'], 'reward_key': previous['reward_key'], 'operation_id': previous['operation_id'], 'claimed': True}
        eligible = next(r for r in _eligibility(service, c, row, actor['id'], _progress(service, c, row, actor['id'])) if r['key'] == reward_key)
        if not eligible['eligible']:
            raise Fault('REWARD_INELIGIBLE', '尚未达到奖励条件或尚未完成封榜', 409)
        # 资格、账本、领取唯一键和通用审计同事务；容量不足等异常回滚全部，不会吞掉领奖机会。
        result = mutate(service, c, {'id': actor['id'], 'role': 'player'}, actor['id'], rule['changes'],
                        '开服活动领取：' + row['title'] + ' / ' + reward_key, 'CAMPAIGN:' + row['id'])
        operation_id = result['operation_id']
        c.insert('campaign_claims', {'id': identifier(), 'campaign_id': row['id'], 'account_id': actor['id'],
                 'claim_key': claim_key, 'reward_key': reward_key, 'operation_id': operation_id, 'created_at': service.clock()})
        return {'campaign_id': row['id'], 'reward_key': reward_key, 'operation_id': operation_id, 'claimed': True}
    return _base(_campaign(service, c, row['id']))


def view(service, c, actor, name, query):
    """返回活动、个人资格或一张分榜；正式与合成分区始终由服务环境决定而非查询参数。"""
    if name == 'campaign-admin':
        _manage(actor, read=True)
        rows = []
        for row in c.select('campaigns', Q(test_only=service.test_profile), order=('-created_at', 'id')):
            value = _base(row)
            frozen = _freeze(c, row['id'])
            awards = frozen['awards'].values() if frozen else []
            statuses = [award.get('status', 'PENDING') for award in awards]
            value['payouts'] = {state.lower(): statuses.count(state) for state in ('PENDING', 'FAILED', 'SUCCEEDED')}
            rows.append(value)
        return rows
    if name == 'campaigns':
        if actor.get('kind') == 'player':
            _player(service, c, actor)
        rows = c.select('campaigns', Q(test_only=service.test_profile, published_definition__isnull=False), order=('-created_at', 'id'))
        result = []
        for row in rows:
            value = _base(row)
            if actor.get('kind') == 'player':
                value['progress'] = _progress(service, c, row, actor['id'])
                value['rewards'] = _eligibility(service, c, row, actor['id'], value['progress'])
            result.append(value)
        return result
    if name != 'leaderboards':
        raise Fault('NOT_FOUND', '未知活动视图', 404)
    row = _campaign(service, c, query.get('campaign_id'))
    if not row['published_definition'] and not (actor.get('kind') == 'admin' and actor.get('role') == 'owner'):
        raise Fault('NOT_FOUND', '活动尚未发布', 404)
    map_id = integer(query.get('map_id'), 1, 65535, 'map_id')
    if map_id not in MAPS:
        raise Fault('INVALID', '地图不属于本期榜单')
    difficulty = integer(query.get('difficulty'), 4097, 4102, 'difficulty')
    count = integer(query.get('player_count'), 1, 8 if map_id in HUNT_MAPS else 4, 'player_count')
    frozen = _freeze(c, row['id'])
    boards = frozen['boards'] if frozen else _boards(service, c, row)
    board = next((b for b in boards if b['map_id'] == map_id and b['difficulty'] == difficulty and b['player_count'] == count), None)
    return {'entries': board['entries'][:100] if board else [], 'finalized': frozen is not None,
            'test_only': service.test_profile, 'native_results_enabled': False}


def ingest(service, node_token, data):
    """认证单局节点后接收合成验收结果；正式原生结果尚未实测，始终拒绝正式入口。

    此函数不接受玩家会话，也不开放浏览器自报成绩。测试使用同一合同验证分榜、
    Boss/结算和冻结名单；未来启用正式来源必须经过独立原生数据与多人验收。
    """
    if not service.test_profile:
        raise Fault('COMPATIBILITY_BLOCKED', '正式游戏可信结算来源尚未验收', 409)
    required = {'campaign_id', 'match_id', 'instance_id', 'epoch', 'event_id', 'map_id', 'difficulty',
                'participant_ids', 'duration_ms', 'settled_at', 'outcome', 'boss_killed', 'settled'}
    if not isinstance(data, dict) or set(data) != required:
        raise Fault('INVALID', '可信结算字段不正确')
    for flag in ('boss_killed', 'settled'):
        if type(data[flag]) is not bool:
            raise Fault('INVALID', '结算标志必须是布尔值')
    if data['outcome'] not in ('VICTORY', 'FAILURE'):
        raise Fault('INVALID', '未知结算结果')
    event_id = text_field(data['event_id'], 'event_id', 80)
    map_id = integer(data['map_id'], 1, 65535, 'map_id')
    if map_id not in MAPS:
        raise Fault('INVALID', '地图不属于本期榜单')
    difficulty = integer(data['difficulty'], 4097, 4102, 'difficulty')
    duration = integer(data['duration_ms'], 1, 30 * 86400000, 'duration_ms')
    settled_at = integer(data['settled_at'], 0, service.clock(), 'settled_at')
    reported = data['participant_ids']
    if not isinstance(reported, list) or not reported or len(reported) > 32 or any(not isinstance(aid, str) for aid in reported) or len(set(reported)) != len(reported):
        raise Fault('INVALID', '实际参战名单格式不正确')
    from .rooms import node_identity
    with service.db.transaction() as c:
        match = node_identity(service, c, node_token, data['match_id'], data['epoch'])
        if data['instance_id'] != match['instance_id']:
            raise Fault('STALE_INSTANCE', '结果不属于此节点实例', 409)
        row = _campaign(service, c, data['campaign_id'])
        digest = token_hash(canonical(data))
        previous = c.select('campaign_results', Q(campaign_id=row['id'], match_id=match['id'])).fetchone()
        if previous:
            if previous['payload_hash'] != digest:
                raise Fault('RESULT_CONFLICT', '同局已记录不同结算内容', 409)
            return _result(previous)
        if _freeze(c, row['id']):
            raise Fault('LEADERBOARD_FINALIZED', '此活动已经封榜', 409)
        definition = _definition(row)
        end = min(definition['ends_at'], row['ended_at'] or definition['ends_at'])
        if not row['published_definition'] or row['state'] not in ('PUBLISHED', 'ENDED') or not definition['starts_at'] <= settled_at < end:
            raise Fault('CAMPAIGN_CLOSED', '结算不属于有效活动期', 409)
        if match['state'] != 'ACTIVE':
            raise Fault('MATCH_STATE', '只有已就绪活动实例能够上报结算', 409)
        snapshot = json.loads(match['snapshot'])
        settings = snapshot.get('native_settings', {})
        frozen_map = settings.get('MapId')
        if frozen_map is None and snapshot.get('map_key', '').startswith('cn2020:'):
            frozen_map = int(snapshot['map_key'].split(':')[1])
        if frozen_map != map_id or settings.get('SubModeId') != difficulty:
            raise Fault('RESULT_MAP_CONFLICT', '地图或难度不匹配开始时快照', 409)
        # allocation created_at 早于实际开局；上报的游玩时长不得超过整个实例存在时间。
        if settled_at < match['created_at'] or duration > (settled_at - match['created_at'] + 1) * 1000:
            raise Fault('RESULT_TIME_CONFLICT', '游玩时长超出实例生命周期', 409)
        members = sorted(player['account_id'] for player in snapshot['players'] if not player.get('observer', False))
        count = len(members)
        if not 1 <= count <= (8 if map_id in HUNT_MAPS else 4) or len(set(members)) != count:
            raise Fault('RESULT_ROSTER', '冻结参战人数不符合地图规则', 409)
        valid, reason = True, ''
        if sorted(reported) != members:
            valid, reason = False, 'ROSTER_CHANGED'
        elif not data['settled']:
            valid, reason = False, 'SETTLEMENT_PENDING'
        else:
            for player in snapshot['players']:
                if player.get('observer', False):
                    continue
                account = service._account(c, player['account_id'])
                enrolled = c.select('match_players', Q(match_id=match['id'], account_id=account['id'], eligible=1)).fetchone()
                if not enrolled or account['security_version'] != player['security_version'] or account['status'] != 'ACTIVE':
                    valid, reason = False, 'ROSTER_REVOKED'
                    break
                service._active(c, account)
        rank_eligible = valid and data['outcome'] == 'VICTORY' and (map_id in HUNT_MAPS or data['boss_killed'])
        if valid and not rank_eligible:
            reason = 'MISSION_FAILED' if data['outcome'] != 'VICTORY' else 'BOSS_NOT_KILLED'
        result = {'id': identifier(), 'campaign_id': row['id'], 'match_id': match['id'], 'event_id': event_id,
                  'payload_hash': digest, 'map_id': map_id, 'difficulty': difficulty, 'player_count': count,
                  'team_key': token_hash(canonical(members)), 'member_ids': members, 'duration_ms': duration,
                  'settled_at': settled_at, 'outcome': data['outcome'], 'boss_killed': data['boss_killed'],
                  'valid': valid, 'rank_eligible': rank_eligible, 'rejection_code': reason,
                  'test_only': True, 'created_at': service.clock()}
        if c.select('campaign_results', Q(campaign_id=row['id'], event_id=event_id)).fetchone():
            raise Fault('RESULT_CONFLICT', '结算事件已绑定另一局', 409)
        c.insert('campaign_results', result)
        return _result(result)


def _result(row):
    """节点回执只包含资格结论，不携带账户资料、令牌或客户端可以重新发放的奖励金额。"""
    return {'result_id': row['id'], 'valid': row['valid'], 'rank_eligible': row['rank_eligible'],
            'reason': row['rejection_code'], 'test_only': row['test_only']}


def drain_awards(service, limit=20):
    """维护器逐目标发榜奖；资产、唯一领取事实及目标状态同事务，失败只回滚该用户并退避重试。

    使用已冻结 awards 字典保存批次状态，不能重新读取实时排名扩大名单。即使进程在
    成功响应前崩溃，下一次也会先读取 CampaignClaim，已成功用户不会再次获得道具。
    """
    with service.db.transaction() as c:
        ids = list(c.table('campaign_board_freezes').filter(campaign__test_only=service.test_profile)
            .order_by('created_at').values_list('campaign_id', flat=True))
    counts = {'processed': 0, 'succeeded': 0, 'failed': 0}
    for campaign_id in ids:
        with service.db.transaction() as c:
            frozen = _freeze(c, campaign_id)
            candidates = [aid for aid, award in frozen['awards'].items() if award.get('status') != 'SUCCEEDED'
                and award.get('retry_at', 0) <= service.clock()][:limit-counts['processed']]
        for account_id in candidates:
            with service.db.transaction() as c:
                frozen = _freeze(c, campaign_id)
                awards = frozen['awards']
                award = awards[account_id]
                if award.get('status') == 'SUCCEEDED' or award.get('retry_at', 0) > service.clock():
                    continue
                counts['processed'] += 1
                attempts = award.get('attempts', 0)+1
                try:
                    with transaction.atomic(using=c.alias):
                        previous = c.select('campaign_claims', Q(campaign_id=campaign_id, account_id=account_id, claim_key='leaderboard')).fetchone()
                        if previous:
                            operation_id = previous['operation_id']
                        else:
                            account = service._account(c, account_id)
                            service._active(c, account)
                            actor = {'id': 'system:campaign:'+campaign_id, 'kind': 'system', 'role': 'owner'}
                            result = mutate(service, c, actor, account_id, award['changes'],
                                '排行榜封榜自动奖励：'+award['reward_key'], 'CAMPAIGN:'+campaign_id)
                            operation_id = result['operation_id']
                            c.insert('campaign_claims', {'id': identifier(), 'campaign_id': campaign_id,
                                'account_id': account_id, 'claim_key': 'leaderboard', 'reward_key': award['reward_key'],
                                'operation_id': operation_id, 'created_at': service.clock()})
                            service._audit(c, actor['id'], 'leaderboard.reward', account_id, identifier(),
                                '封榜最高档奖励自动发放', {'campaign_id': campaign_id, 'operation_id': operation_id})
                        award.update(status='SUCCEEDED', operation_id=operation_id, attempts=attempts, retry_at=0, last_error='')
                        c.update('campaign_board_freezes', Q(campaign_id=campaign_id), {'awards': awards})
                    counts['succeeded'] += 1
                except (Fault, DatabaseError) as error:
                    # 只保存有界业务码，不持久化可能包含数据库/玩家资料的异常文本。
                    award.update(status='FAILED', attempts=attempts, last_error=error.code if isinstance(error, Fault) else 'DATABASE_ERROR',
                        retry_at=service.clock()+min(300, 5*2**min(attempts, 6)))
                    c.update('campaign_board_freezes', Q(campaign_id=campaign_id), {'awards': awards})
                    counts['failed'] += 1
        if counts['processed'] >= limit:
            break
    return counts
