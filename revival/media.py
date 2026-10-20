"""图片真解码、去元数据重编码与分区存储；反馈图片始终通过工单授权读取。"""
from io import BytesIO
import hashlib
import os
from pathlib import Path
import re
import warnings

from django.conf import settings
from django.db.models import Q

from . import feedback
from .service import Fault, identifier, text_field

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_EDGE = 8192


def normalize_image(uploaded):
    """最多读取5MiB，验证PNG/JPEG/WebP及单帧/像素限制，再重建像素图去除EXIF等元数据。

    不相信扩展名或浏览器MIME。原图只用于验证/幂等摘要，不作为永久图片落盘。
    输出统一WebP，保证公共与私有读取的内容类型固定，禁止SVG/脚本与动画。
    """
    from PIL import Image, ImageOps, UnidentifiedImageError
    if isinstance(uploaded, (bytes, bytearray)):
        raw = bytes(uploaded)
    elif hasattr(uploaded, 'read'):
        uploaded.seek(0)
        raw = uploaded.read(MAX_UPLOAD_BYTES + 1)
    else:
        raise Fault('INVALID_IMAGE', '请选择PNG、JPEG或WebP图片')
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise Fault('IMAGE_SIZE', '每张图片不得超过5MiB', 413)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as source:
                if source.format not in ('PNG', 'JPEG', 'WEBP') or getattr(source, 'n_frames', 1) != 1:
                    raise Fault('INVALID_IMAGE', '只接受单帧PNG、JPEG或WebP图片')
                width, height = source.size
                if min(width, height) < 1 or max(width, height) > MAX_EDGE or width * height > MAX_PIXELS:
                    raise Fault('IMAGE_DIMENSIONS', '图片边长或像素总量超过限制', 413)
                source.verify()
            with Image.open(BytesIO(raw)) as source:
                source.load()
                oriented = ImageOps.exif_transpose(source)
                pixels = oriented.convert('RGBA' if 'A' in oriented.getbands() or 'transparency' in oriented.info else 'RGB')
                clean = Image.new(pixels.mode, pixels.size)
                clean.paste(pixels)
                target = BytesIO()
                clean.save(target, format='WEBP', quality=90, method=4)
                payload = target.getvalue()
                width, height = clean.size
    except Fault:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        raise Fault('INVALID_IMAGE', '图片文件损坏或格式不受支持') from None
    if len(payload) > MAX_UPLOAD_BYTES:
        raise Fault('IMAGE_SIZE', '重编码后的图片超过5MiB，请缩小图片后重试', 413)
    return {'payload': payload, 'input_sha256': hashlib.sha256(raw).hexdigest(),
            'sha256': hashlib.sha256(payload).hexdigest(), 'mime': 'image/webp',
            'bytes': len(payload), 'width': width, 'height': height}


def _root():
    """使用独立可写媒体根；生产须配置NZ_MEDIA_ROOT，默认仅落本机runtime中。"""
    value = getattr(settings, 'NZ_MEDIA_ROOT', None) or os.environ.get('NZ_MEDIA_ROOT')
    root = Path(value) if value else Path(settings.BASE_DIR) / 'runtime/media'
    if not root.is_absolute():
        raise Fault('MEDIA_STORAGE', '媒体存储路径必须为绝对路径', 503)
    return root


def _path(namespace, key):
    """磁盘键仅使用服务端随机ID，拒绝路径分隔符；两类图片使用完全不同目录。"""
    if namespace not in ('private_feedback', 'public_covers') or not re.fullmatch(r'[a-f0-9]{32}\.webp', key):
        raise Fault('MEDIA_STORAGE', '媒体存储标识无效', 503)
    root = _root()
    directory = root / namespace
    if root.is_symlink() or directory.is_symlink():
        raise Fault('MEDIA_STORAGE', '媒体目录配置无效', 503)
    return directory / key


def _store(namespace, key, payload):
    """独占创建600权限文件；出现写失败时清理半文件，不覆盖其他已提交图片。"""
    target = _path(namespace, key)
    created = False
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with target.open('xb') as stream:
            created = True
            os.chmod(target, 0o600)
            stream.write(payload)
    except OSError:
        if created:
            target.unlink(missing_ok=True)
        raise Fault('MEDIA_STORAGE', '图片保存失败，请稍后重试', 503) from None
    return target


def _upload_key(value):
    """上传幂等键不能含路径或控制字符，与页面提交随机键保持一一对应。"""
    key = text_field(value, 'upload_key', 128)
    if not re.fullmatch(r'[A-Za-z0-9._:-]+', key):
        raise Fault('INVALID', '上传重试标识不正确')
    return key


def _cleanup_uncommitted(service, table, stored):
    """提交应答丢失时先回读引用；数据库无法确认则保留不可直接访问的文件，防止删掉已提交图。

    普通验证/审计回滚后没有记录，清理本轮文件；已有记录时重试仍能通过同键读到它。
    孤立文件可由离线运维对账清理，不能以牺牲已提交附件可用性处理提交结果未知。
    """
    if stored is None:
        return
    try:
        with service.db.transaction() as c:
            referenced = c.table(table).filter(storage_key=stored.name).exists()
    except Exception:
        return
    if not referenced:
        stored.unlink(missing_ok=True)


def public_metadata(row):
    """公共封面只返回稳定地址和尺寸，不返回内部路径或上传管理员信息。"""
    return {key: row[key] for key in ('id', 'mime', 'width', 'height', 'bytes')} | {'url': '/api/media/' + row['id']}


def upload_feedback(service, token, ticket_id, uploaded, upload_key):
    """顶层HTTP调用：先认证本人/工单管理员，再解码；最终事务重查权限、关闭态和3张总限。

    相同操作者/工单/upload_key与相同原图返回既有附件，换图同键409。文件失败不删除
    已提交正文；事务失败清理本次新图片。调用方不应把本函数包在其他业务事务中。
    """
    key = _upload_key(upload_key)
    with service.db.transaction() as c:
        actor = service._actor(c, token)
        feedback.ticket(c, actor, ticket_id, write=True)
    image = normalize_image(uploaded)
    stored = None
    try:
        with service.db.transaction() as c:
            actor = service._actor(c, token)
            row = feedback.ticket(c, actor, ticket_id, write=True)
            condition = Q(ticket_id=row['id'], author_kind=actor['kind'], author_id=actor['id'], upload_key=key)
            previous = c.select('feedback_attachments', condition).fetchone()
            if previous:
                if previous['input_sha256'] != image['input_sha256']:
                    raise Fault('IDEMPOTENCY_CONFLICT', '同一上传标识不能用于不同图片', 409)
                return feedback.attachment_metadata(previous) | {'ticket_revision': row['revision']}
            if c.table('feedback_attachments').filter(ticket_id=row['id']).count() >= 3:
                raise Fault('ATTACHMENT_LIMIT', '每份反馈最多上传3张图片', 409)
            media_id = identifier()
            values = {k: v for k, v in image.items() if k != 'payload'}
            values.update(id=media_id, ticket_id=row['id'], author_kind=actor['kind'], author_id=actor['id'],
                          upload_key=key, storage_key=media_id + '.webp', created_at=service.clock())
            stored = _store('private_feedback', values['storage_key'], image['payload'])
            c.insert('feedback_attachments', values)
            c.update('feedback_tickets', Q(id=row['id']), {'revision': row['revision'] + 1, 'updated_at': service.clock()})
            service._audit(c, actor['id'], 'feedback.attachment.upload', row['id'], key,
                           '上传意见反馈截图', {'attachment_id': media_id})
            return feedback.attachment_metadata(values) | {'ticket_revision': row['revision'] + 1}
    except Exception:
        _cleanup_uncommitted(service, 'feedback_attachments', stored)
        raise


def upload_public_cover(service, token, uploaded, upload_key):
    """公共封面只供Owner/resources上传；与私有反馈无共享路径或可切换的scope参数。"""
    key = _upload_key(upload_key)
    with service.db.transaction() as c:
        actor = service._actor(c, token)
        _cover_permission(actor)
    image = normalize_image(uploaded)
    stored = None
    try:
        with service.db.transaction() as c:
            actor = service._actor(c, token)
            _cover_permission(actor)
            previous = c.select('public_media', Q(author_id=actor['id'], upload_key=key)).fetchone()
            if previous:
                if previous['input_sha256'] != image['input_sha256']:
                    raise Fault('IDEMPOTENCY_CONFLICT', '同一上传标识不能用于不同图片', 409)
                return public_metadata(previous)
            media_id = identifier()
            values = {k: v for k, v in image.items() if k != 'payload'}
            values.update(id=media_id, author_id=actor['id'], upload_key=key,
                          storage_key=media_id + '.webp', created_at=service.clock())
            stored = _store('public_covers', values['storage_key'], image['payload'])
            c.insert('public_media', values)
            service._audit(c, actor['id'], 'campaign.cover.upload', media_id, key, '上传活动公共封面', {'media_id': media_id})
            return public_metadata(values)
    except Exception:
        _cleanup_uncommitted(service, 'public_media', stored)
        raise


def _cover_permission(actor):
    """封面编辑角色与活动运营保持一致，不向普通用户或只读审计开放。"""
    if actor.get('kind') != 'admin' or actor.get('role') not in ('owner', 'resources'):
        raise Fault('FORBIDDEN', '没有活动封面上传权限', 403)


def _read(namespace, row):
    """鉴权后读取已验证的重编码内容；缺失或被篡改统一404，不暴露本机路径。"""
    try:
        with _path(namespace, row['storage_key']).open('rb') as stream:
            payload = stream.read(MAX_UPLOAD_BYTES + 1)
    except OSError:
        raise Fault('NOT_FOUND', '图片不存在', 404) from None
    if len(payload) != row['bytes'] or hashlib.sha256(payload).hexdigest() != row['sha256']:
        raise Fault('NOT_FOUND', '图片不存在', 404)
    return payload, row['mime']


def read_feedback_attachment(service, token, attachment_id):
    """私有GET每次认证会话和工单归属，随机附件ID本身不能当作访问凭证。"""
    with service.db.transaction() as c:
        actor = service._actor(c, token)
        if actor['kind'] != 'player':
            feedback.permitted(actor)
        row = c.select('feedback_attachments', Q(id=text_field(attachment_id, 'attachment_id', 32))).fetchone()
        if not row:
            raise Fault('NOT_FOUND', '图片不存在', 404)
        feedback.ticket(c, actor, row['ticket_id'])
    return _read('private_feedback', row)


def read_public_cover(service, media_id):
    """公共封面GET只查public_media表，不能通过同一个ID访问任何反馈截图。"""
    with service.db.transaction() as c:
        row = c.select('public_media', Q(id=text_field(media_id, 'media_id', 32))).fetchone()
        if not row:
            raise Fault('NOT_FOUND', '图片不存在', 404)
    return _read('public_covers', row)
