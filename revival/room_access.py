"""房间频道、难度、密码和指定账号邀请；仅代表控制面能力，不推断游戏协议。"""
from django.db.models import Q
from .security import new_token, token_hash, password_hash, verify_password
from .service import Fault, integer, text_field


def settings(c, room_id):
    """旧导入房间没有访问记录时保持原来的公开、默认难度语义。"""
    return c.select('room_settings', Q(room_id=room_id)).fetchone() or {
        'room_id': room_id, 'channel': 'local', 'difficulty': 'default', 'password_hash': '', 'invite_only': False}


def configure(c, room_id, definition, data):
    """只接受目录允许的难度；不提供新密码时保留原密码，显式空串才能清除。"""
    current = settings(c, room_id)
    difficulty = data.get('difficulty', current['difficulty'])
    if difficulty not in definition.get('difficulties', ['default']):
        raise Fault('UNVERIFIED_RESOURCE', 'Difficulty is not enabled in the active runtime catalog')
    channel = text_field(data.get('channel', current['channel']), 'channel', 64)
    if channel not in definition.get('channels', ['local']):
        raise Fault('UNVERIFIED_RESOURCE', 'Channel is not enabled in the active runtime catalog')
    invite_only = data.get('invite_only', current['invite_only'])
    if type(invite_only) is not bool:
        raise Fault('INVALID', 'invite_only must be boolean')
    hashed = current['password_hash']
    if 'password' in data:
        password = data['password']
        if password != '':
            try:
                hashed = password_hash(password)
            except ValueError as error:
                raise Fault('INVALID', str(error))
        else:
            hashed = ''
    values = {'channel': channel, 'difficulty': difficulty, 'password_hash': hashed, 'invite_only': invite_only}
    c.table('room_settings').update_or_create(room_id=room_id, defaults=values)
    return {key: value for key, value in values.items() if key != 'password_hash'}


def check_join(service, c, room_id, account_id, data):
    """有效定向邀请可进入受保护房间；其他玩家必须满足公开策略与密码校验。"""
    current = settings(c, room_id)
    token = data.get('invitation_token')
    invitation = c.select('room_invitations', Q(room_id=room_id, account_id=account_id, expires_at__gt=service.clock(),
                                                  digest=token_hash(token))).fetchone() if isinstance(token, str) else None
    if invitation:
        return
    if current['invite_only']:
        raise Fault('ROOM_INVITATION', 'An active invitation for this account is required', 403)
    if current['password_hash'] and not verify_password(data.get('password'), current['password_hash']):
        raise Fault('ROOM_PASSWORD', 'Room password is required or incorrect', 403)


def invite(service, c, actor, room, data):
    """房主发出一小时以内定向邀请；明文 token 只出现在首次命令响应。"""
    if room['owner'] != actor['id'] or room['state'] != 'OPEN':
        raise Fault('FORBIDDEN', 'Only the owner of an open room can invite', 403)
    account = service._account(c, data.get('target_account_id'))
    service._active(c, account)
    expiry = integer(data.get('expires_at'), service.clock() + 1, service.clock() + 3600, 'expires_at')
    c.delete('room_invitations', Q(room_id=room['id'], account_id=account['id']))
    token = new_token()
    c.insert('room_invitations', {'digest': token_hash(token), 'room_id': room['id'], 'account_id': account['id'], 'expires_at': expiry})
    return {'room_id': room['id'], 'account_id': account['id'], 'expires_at': expiry, 'invitation_token': token}


def list_rooms(service, c, filters):
    """数据库中按地图/频道/难度过滤并分页，返回脱敏访问规则和成员，避免泄漏口令摘要。"""
    query = c.table('rooms').exclude(state='CLOSED')
    for name in ('channel', 'difficulty'):
        if name in filters:
            value = text_field(filters[name], name, 64)
            ids = c.table('room_settings').filter(**{name: value}).values('room_id')
            if value == ('local' if name == 'channel' else 'default'):
                query = query.filter(Q(pk__in=ids) | ~Q(pk__in=c.table('room_settings').values('room_id')))
            else:
                query = query.filter(pk__in=ids)
    if 'map_key' in filters:
        query = query.filter(map_key=text_field(filters['map_key'], 'map_key', 100))
    limit = integer(filters.get('limit', 50), 1, 100, 'limit')
    offset = integer(filters.get('offset', 0), 0, 1000000, 'offset')
    result = []
    for room in query.order_by('id')[offset:offset + limit]:
        row = c.select('rooms', Q(id=room.id)).fetchone()
        access = settings(c, room.id)
        row.update({key: access[key] for key in ('channel', 'difficulty', 'invite_only')})
        row['password_required'] = bool(access['password_hash'])
        row['members'] = list(c.select('room_members', Q(room_id=room.id), columns=['account_id', 'slot', 'ready'], order=('slot',)))
        result.append(dict(row))
    return result
