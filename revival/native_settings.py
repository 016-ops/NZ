"""原生设置的MySQL持久化；保留不透明正文，按当前客户端/TDR隔离并以CAS保护重试和并发。"""
import base64
import binascii
import hashlib

from django.db.models import Q
from adapters.cn_current.zone import ZONE_METADATA_SHA256
from .launcher import authorize_game_session
from .service import Fault, identifier


def scope(data):
    """只接受当前已验证的TDR版本；客户端指纹由游戏会话授权检查，不能将旧格式覆盖新格式。"""
    if data.get('metadata_sha256') != ZONE_METADATA_SHA256:
        raise Fault('SETTINGS_FORMAT', 'Unsupported native settings metadata', 409)
    return ZONE_METADATA_SHA256


def view(row):
    """只在本账号游戏凭据授权后编码正文；审计和outbox不能调用此函数来复制不透明设置。"""
    payload = bytes(row['payload']) if row else b''
    return {'revision': row['revision'] if row else 0,
            'payload_base64': base64.b64encode(payload).decode('ascii'),
            'payload_sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload),
            'metadata_sha256': ZONE_METADATA_SHA256}


def load(service, game_token, data):
    """读取当前账号、当前客户端和当前TDR的设置；未保存返回revision0，不读取其他版本。"""
    metadata = scope(data)
    with service.db.transaction() as c:
        game, account = authorize_game_session(service, c, game_token, data)
        row = c.select('native_player_settings', Q(account_id=account['id'],
            client_sha256=game['client_sha256'], metadata_sha256=metadata)).fetchone()
        return view(row)


def save(service, game_token, data):
    """同一事务完成授权、CAS、设置、审计和outbox；提交成功后调用方才可应答原生A203。

    相同正文的旧revision重试返回当前结果且不增版；不同正文的旧/未来版本一律冲突。
    本接口不解释客户端内部设置格式，不改昵称、角色、库存或钱包，也不授权可信节点事件。
    """
    metadata = scope(data)
    encoded, expected = data.get('payload_base64'), data.get('expected_revision')
    if type(expected) is not int or not 0 <= expected < (1 << 63) - 1:
        raise Fault('INVALID', 'Invalid settings revision')
    if not isinstance(encoded, str) or len(encoded) > 4268:
        raise Fault('INVALID', 'Native settings exceeds 3200 bytes')
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise Fault('INVALID', 'Invalid native settings encoding') from None
    if len(payload) > 3200 or base64.b64encode(payload).decode('ascii') != encoded:
        raise Fault('INVALID', 'Noncanonical or oversized native settings')
    digest = hashlib.sha256(payload).hexdigest()
    with service.db.transaction() as c:
        game, account = authorize_game_session(service, c, game_token, data)
        condition = Q(account_id=account['id'], client_sha256=game['client_sha256'], metadata_sha256=metadata)
        row = c.select('native_player_settings', condition).fetchone()
        revision = row['revision'] if row else 0
        identical = row is not None and bytes(row['payload']) == payload
        if expected > revision or (expected != revision and not identical):
            raise Fault('SETTINGS_REVISION_CONFLICT', 'Reload current native settings before retrying', 409)
        if identical:
            return dict(view(row), status='UNCHANGED')
        revision += 1
        values = dict(payload=payload, payload_sha256=digest, byte_count=len(payload),
                      revision=revision, updated_at=service.clock())
        if row:
            record_id = row['id']
            c.update('native_player_settings', Q(id=record_id), values)
        else:
            record_id = identifier()
            c.insert('native_player_settings', dict(values, id=record_id, account_id=account['id'],
                client_sha256=game['client_sha256'], metadata_sha256=metadata))
        summary = {'revision': revision, 'payload_sha256': digest, 'bytes': len(payload),
                   'client_sha256': game['client_sha256'], 'metadata_sha256': metadata}
        # 记录摘要即可对账；不透明正文可能包含用户偏好，不能复制到公开事件或审计详情中。
        service._audit(c, account['id'], 'native.settings.save', record_id, identifier(),
                       'native client settings save', summary)
        service._outbox(c, f'native-settings:{record_id}:{revision}', 'native.settings.changed', account['id'], summary)
        return dict(view(dict(values)), status='SAVED')
