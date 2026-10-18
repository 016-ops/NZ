"""原生设置到独立游戏会话API的桥接；只在MySQL保存响应完整核对后允许调用方发送A203。"""
import base64
import binascii
import hashlib
import threading

from adapters.cn_current.zone import ZONE_METADATA_SHA256
from adapters.email_protocol import PROTOCOL


def decode_settings_response(result):
    """验证服务端返回的版本、长度、摘要和规范base64；不返回原始响应或在错误中带正文。"""
    if not isinstance(result, dict) or result.get('metadata_sha256') != ZONE_METADATA_SHA256:
        raise ValueError('INVALID_NATIVE_SETTINGS_RESPONSE')
    revision, count, encoded = result.get('revision'), result.get('bytes'), result.get('payload_base64')
    if (type(revision) is not int or not 0 <= revision < (1 << 63) or type(count) is not int
            or not 0 <= count <= 3200 or not isinstance(encoded, str) or len(encoded) > 4268):
        raise ValueError('INVALID_NATIVE_SETTINGS_RESPONSE')
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError('INVALID_NATIVE_SETTINGS_RESPONSE') from None
    if (len(payload) != count or base64.b64encode(payload).decode('ascii') != encoded
            or hashlib.sha256(payload).hexdigest() != result.get('payload_sha256')
            or (revision == 0 and payload)):
        raise ValueError('INVALID_NATIVE_SETTINGS_RESPONSE')
    return revision, payload


class NativeSettingsBridge:
    """每个已验证游戏会话独立持有设置快照；凭据只在本对象内存，正文不会进入公开审计摘要。"""
    def __init__(self, client, game_token, client_sha256):
        """启动游戏前读取同客户端/TDR快照；API不可用或返回不一致立即拒绝，不用空数据覆盖。"""
        self.client, self.game_token = client, game_token
        self.scope = dict(protocol=PROTOCOL, client_sha256=client_sha256, metadata_sha256=ZONE_METADATA_SHA256)
        self.lock = threading.Lock()
        result = client.request('/api/game/settings/get', self.scope, game_token)
        self.revision, self.data = decode_settings_response(result)

    def save(self, payload):
        """带当前revision保存，核对返回确为同一正文后更新快照；任何超时/冲突/错误均不返回成功摘要。"""
        if not isinstance(payload, bytes) or len(payload) > 3200:
            raise ValueError('INVALID_NATIVE_SETTINGS_DATA')
        with self.lock:
            result = self.client.request('/api/game/settings/set', dict(self.scope,
                expected_revision=self.revision, payload_base64=base64.b64encode(payload).decode('ascii')), self.game_token)
            revision, saved = decode_settings_response(result)
            status = result.get('status')
            if (saved != payload or (status == 'SAVED' and revision != self.revision + 1)
                    or (status == 'UNCHANGED' and (revision <= 0 or revision < self.revision))
                    or status not in ('SAVED', 'UNCHANGED')):
                raise ValueError('UNCONFIRMED_NATIVE_SETTINGS_COMMIT')
            self.revision, self.data = revision, saved
            return {'revision': revision, 'bytes': len(saved), 'payload_sha256': hashlib.sha256(saved).hexdigest()}
