"""活动独立页面展示配置；slug/封面/文本与不可变奖励快照完全分离。"""
import re

from django.db.models import Q

from .service import Fault, integer, text_field
from .feedback import content

THEMES = {'default', 'tactical', 'anniversary'}
KINDS = {'text', 'rewards', 'leaderboards'}


def ensure(service, c, campaign):
    """旧活动按迁移回填的ID路径访问，新草稿也使用同一默认规则，不改变奖励定义。"""
    row = c.select('campaign_presentations', Q(campaign_id=campaign['id'])).fetchone()
    if not row:
        row = {'campaign_id': campaign['id'], 'slug': campaign['id'], 'title': campaign['title'],
               'summary': '', 'theme': 'default', 'cover_id': None, 'sections': [],
               'revision': 1, 'updated_at': service.clock()}
        c.insert('campaign_presentations', row)
    return row


def serialize(service, c, campaign):
    """展示层只返回公共封面地址及纯文本，不将管理员或磁盘信息送到公开页面。"""
    row = ensure(service, c, campaign)
    return {key: row[key] for key in ('slug', 'title', 'summary', 'theme', 'cover_id', 'sections', 'revision')} | {
        'cover_url': '/api/media/' + row['cover_id'] if row['cover_id'] else None}


def _sections(value):
    """限制可用区块和文本长度；奖励区块仅引用服务端冻结规则，不接受自定义发奖数据。"""
    if not isinstance(value, list) or len(value) > 12:
        raise Fault('INVALID', '活动专题最多12个区块')
    result, keys = [], set()
    for section in value:
        if not isinstance(section, dict) or set(section) - {'key', 'kind', 'title', 'body'}:
            raise Fault('INVALID', '活动区块字段不正确')
        key = text_field(section.get('key'), 'section key', 32)
        if (not re.fullmatch(r'[a-zA-Z0-9_-]+', key) or key in keys
                or not isinstance(section.get('kind'), str) or section['kind'] not in KINDS):
            raise Fault('INVALID', '活动区块类型或标识不正确')
        keys.add(key)
        row = {'key': key, 'kind': section['kind'], 'title': text_field(section.get('title'), 'section title', 120)}
        if 'body' in section:
            if not isinstance(section['body'], str):
                raise Fault('INVALID', '活动区块正文必须为纯文本')
            row['body'] = content(section['body'], 'section body', 5000) if section['body'].strip() else ''
        result.append(row)
    return result


def command(service, c, actor, data):
    """运营角色以独立修订CAS保存展示；首次发布以后slug永不改变，奖励快照不写入本表。"""
    from .campaigns import _manage, _campaign
    _manage(actor)
    campaign = _campaign(service, c, data.get('campaign_id'))
    row = ensure(service, c, campaign)
    if integer(data.get('expected_revision'), 1, 2147483647, 'expected_revision') != row['revision']:
        raise Fault('VERSION_CONFLICT', '活动页面已更新，请刷新后重试', 409)
    presentation = data.get('presentation')
    if not isinstance(presentation, dict) or set(presentation) - {'slug', 'title', 'summary', 'theme', 'cover_id', 'sections'}:
        raise Fault('INVALID', '活动页面展示字段不正确')
    slug = presentation.get('slug', row['slug'])
    if not isinstance(slug, str) or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,94}[a-z0-9])?', slug):
        raise Fault('INVALID', '活动地址只能使用小写字母、数字和中间连字符')
    if slug != row['slug'] and campaign['published_definition'] is not None:
        raise Fault('SLUG_LOCKED', '活动发布后不能修改独立地址', 409)
    if c.table('campaign_presentations').filter(slug=slug).exclude(campaign_id=campaign['id']).exists():
        raise Fault('SLUG_CONFLICT', '活动地址已被使用', 409)
    title = text_field(presentation.get('title', row['title']), 'presentation title', 120)
    summary = presentation.get('summary', row['summary'])
    if not isinstance(summary, str) or len(summary) > 500 or any(ord(ch) < 32 for ch in summary):
        raise Fault('INVALID', '活动摘要最多500字')
    theme = presentation.get('theme', row['theme'])
    if not isinstance(theme, str) or theme not in THEMES:
        raise Fault('INVALID', '活动主题不正确')
    cover_id = presentation.get('cover_id', row['cover_id'])
    if cover_id == '':
        cover_id = None
    if cover_id is not None and (not isinstance(cover_id, str) or not c.select('public_media', Q(id=cover_id)).fetchone()):
        raise Fault('INVALID', '活动封面必须是管理员上传的公共图片')
    sections = _sections(presentation.get('sections', row['sections']))
    c.update('campaign_presentations', Q(campaign_id=campaign['id']), {'slug': slug, 'title': title,
             'summary': summary.strip(), 'theme': theme, 'cover_id': cover_id, 'sections': sections,
             'revision': row['revision'] + 1, 'updated_at': service.clock()})
    return serialize(service, c, campaign)


def detail(service, c, actor, query):
    """按slug读取单个活动，未发布定义只对活动只读管理角色开放；玩家返回本人进度。"""
    from .campaigns import _manage, _player, _campaign, _base, _progress, _eligibility
    if query.get('slug'):
        row = c.select('campaign_presentations', Q(slug=text_field(query['slug'], 'slug', 96))).fetchone()
        if not row:
            raise Fault('NOT_FOUND', '活动不存在', 404)
        campaign = _campaign(service, c, row['campaign_id'])
    else:
        campaign = _campaign(service, c, query.get('campaign_id'))
    if campaign['published_definition'] is None:
        if actor.get('kind') != 'admin' or actor.get('role') not in ('owner', 'resources', 'auditor'):
            raise Fault('NOT_FOUND', '活动不存在', 404)
        _manage(actor, read=True)
    if actor.get('kind') == 'player':
        _player(service, c, actor)
    value = _base(campaign) | {'presentation': serialize(service, c, campaign)}
    if actor.get('kind') == 'player':
        value['progress'] = _progress(service, c, campaign, actor['id'])
        value['rewards'] = _eligibility(service, c, campaign, actor['id'], value['progress'])
    return value
