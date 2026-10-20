"""意见反馈的本人归属、四态流转与管理员权限；命令事务/审计/幂等由Service统一托管。"""
from django.db.models import Q

from .service import Fault, identifier, integer, text_field

COMMANDS = {'feedback.create', 'feedback.reply', 'feedback.status'}
VIEWS = {'feedback', 'feedback-admin', 'feedback-detail'}
STATES = {'OPEN', 'IN_PROGRESS', 'RESOLVED', 'CLOSED'}


def content(value, name='body', limit=5000):
    """正文允许换行，但拒绝空白、NUL及其他控制字符；只保存纯文本，不解释HTML。"""
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(
            ord(ch) < 32 and ch not in '\n\r\t' for ch in value):
        raise Fault('INVALID', f'{name} 必须为1至{limit}字的纯文本')
    return value.strip().replace('\r\n', '\n').replace('\r', '\n')


def permitted(actor, *, write=False):
    """仅用户管理员/Owner处理反馈，auditor只读，resources没有反馈读取权。"""
    roles = {'owner', 'users'} if write else {'owner', 'users', 'auditor'}
    if actor.get('kind') != 'admin' or actor.get('role') not in roles:
        raise Fault('FORBIDDEN', '没有意见反馈管理权限', 403)


def ticket(c, actor, value, *, write=False):
    """先验证身份再按本人过滤；越权工单统一404，避免枚举其他玩家反馈。"""
    condition = Q(id=text_field(value, 'ticket_id', 32))
    if actor.get('kind') == 'player':
        condition &= Q(account_id=actor['id'])
    else:
        permitted(actor, write=write)
    row = c.select('feedback_tickets', condition).fetchone()
    if not row:
        raise Fault('NOT_FOUND', '反馈工单不存在', 404)
    if write and row['state'] == 'CLOSED':
        raise Fault('FEEDBACK_CLOSED', '此反馈已经关闭，不能继续回复或上传', 409)
    return row


def _summary(c, row):
    """返回工单摘要及计数，不输出账号邮箱、管理员登录名或存储路径。"""
    return {key: row[key] for key in ('id', 'account_id', 'title', 'state', 'revision', 'created_at', 'updated_at')} | {
        'message_count': c.table('feedback_messages').filter(ticket_id=row['id']).count(),
        'attachment_count': c.table('feedback_attachments').filter(ticket_id=row['id']).count()}


def attachment_metadata(row):
    """私有图片仅返回鉴权读取地址；不暴露磁盘键、原始文件名与上传者标识。"""
    return {key: row[key] for key in ('id', 'mime', 'width', 'height', 'bytes', 'created_at')} | {
        'url': '/api/feedback/attachments/' + row['id']}


def command(service, c, actor, action, data):
    """创建、回复和状态写入均由统一Service命令进入；成功重试不重复追加消息。"""
    if action == 'feedback.create':
        if actor.get('kind') != 'player':
            raise Fault('FORBIDDEN', '只能由玩家本人创建反馈', 403)
        service._active(c, service._account(c, actor['id']))
        title, body = text_field(data.get('title'), 'title', 120), content(data.get('body'))
        ticket_id = identifier()
        c.insert('feedback_tickets', {'id': ticket_id, 'account_id': actor['id'], 'title': title,
                 'state': 'OPEN', 'revision': 1, 'created_at': service.clock(), 'updated_at': service.clock()})
        c.insert('feedback_messages', {'id': identifier(), 'ticket_id': ticket_id, 'author_kind': 'player',
                 'author_id': actor['id'], 'sequence': 1, 'body': body, 'created_at': service.clock()})
        return {'ticket_id': ticket_id, 'revision': 1, 'state': 'OPEN'}
    row = ticket(c, actor, data.get('ticket_id'), write=action != 'feedback.status')
    expected = integer(data.get('expected_revision'), 1, 2147483647, 'expected_revision')
    if expected != row['revision']:
        raise Fault('VERSION_CONFLICT', '反馈已更新，请刷新后再提交', 409)
    state, message_id = row['state'], None
    if action == 'feedback.reply':
        body = content(data.get('body'))
        message_id = identifier()
        c.insert('feedback_messages', {'id': message_id, 'ticket_id': row['id'], 'author_kind': actor['kind'],
                 'author_id': actor['id'], 'sequence': row['revision'] + 1, 'body': body, 'created_at': service.clock()})
        if state == 'RESOLVED':
            state = 'OPEN'
    elif action == 'feedback.status':
        permitted(actor, write=True)
        requested = data.get('state')
        if not isinstance(requested, str) or requested not in STATES:
            raise Fault('INVALID', '反馈状态不正确')
        if state == 'CLOSED':
            raise Fault('FEEDBACK_CLOSED', '关闭的工单只读，不能重新流转', 409)
        state = requested
    else:
        raise Fault('NOT_FOUND', '未知反馈命令', 404)
    c.update('feedback_tickets', Q(id=row['id']), {'revision': row['revision'] + 1,
             'state': state, 'updated_at': service.clock()})
    result = {'ticket_id': row['id'], 'state': state, 'revision': row['revision'] + 1}
    if message_id:
        result['message_id'] = message_id
    return result


def view(service, c, actor, name, query):
    """本人列表/管理员列表/工单对话；列表有界分页，详细读取保留附件授权。"""
    if name == 'feedback-detail':
        row = ticket(c, actor, query.get('ticket_id'))
        return _summary(c, row) | {
            'messages': [{key: message[key] for key in ('id', 'body', 'author_kind', 'created_at')}
                         for message in c.select('feedback_messages', Q(ticket_id=row['id']), order=('sequence', 'id'))],
            'attachments': [attachment_metadata(item) for item in c.select('feedback_attachments', Q(ticket_id=row['id']), order=('created_at', 'id'))]}
    if name == 'feedback':
        if actor.get('kind') != 'player':
            raise Fault('FORBIDDEN', '本人反馈列表仅限玩家', 403)
        condition = Q(account_id=actor['id'])
    elif name == 'feedback-admin':
        permitted(actor)
        condition = Q()
    else:
        raise Fault('NOT_FOUND', '未知反馈视图', 404)
    if query.get('state'):
        if not isinstance(query['state'], str) or query['state'] not in STATES:
            raise Fault('INVALID', '反馈状态不正确')
        condition &= Q(state=query['state'])
    offset = integer(query.get('offset', 0), 0, 1000000, 'offset')
    limit = integer(query.get('limit', 25), 1, 100, 'limit')
    rows = c.table('feedback_tickets').filter(condition).order_by('-updated_at', 'id')
    total = rows.count()
    return {'tickets': [_summary(c, row) for row in rows.values()[offset:offset + limit]],
            'total': total, 'offset': offset, 'limit': limit}
