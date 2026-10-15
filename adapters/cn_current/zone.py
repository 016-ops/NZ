"""当前 Zone 请求解析与固定研究登录响应；不输出密钥、不进行网络操作或写入玩家资产。"""
import struct

ZONE_HEADER_PROFILE = 'nz-current-zone-header-observation-v1'
ZONE_NETBAR_KEY_PROFILE = 'nz-current-zone-netbar-key-notification-v1'
ZONE_LOGIN_REQUEST_PROFILE = 'nz-current-zone-login-request-v1'
ZONE_PLAYER_DEFINE_REQUEST_PROFILE = 'nz-current-player-define-request-v1'
ZONE_EMPTY_PLAYER_DEFINE_PROFILE = 'cn2020-player-define-empty-research-v1'
ZONE_LOGIN_RESPONSE_PROFILE = 'cn2020-zone-login-success-research-v1'
ZONE_METADATA_SHA256 = 'c7050a5d78edb68eb66b2b1e00e68640a762398b1d7c7f9b9072e4c74f915dba'
MAX_ZONE_PACKET_BYTES = 65536
_HEADER = struct.Struct('>HHHH')
_MAGIC = 0x3243
_NOTIFY_NETBAR_KEY = 0x1250
_REQUEST_LOGIN = 0xA000
_RESPONSE_LOGIN = 0xA001
_MAX_NETBAR_KEY_BYTES = 512


class ZoneProtocolError(ValueError):
    """拒绝当前有界合同之外的输入；异常只用固定错误码，不包含包体或密钥原文。"""


def parse_zone_header(payload):
    """检查完整 Zone 应用包的头和长度，只返回观察摘要；未知命令的包体始终标记未验证。"""
    if not isinstance(payload, bytes) or not _HEADER.size <= len(payload) <= MAX_ZONE_PACKET_BYTES:
        raise ZoneProtocolError('INVALID_ZONE_PACKET_LENGTH')
    magic, command, head_length, body_length = _HEADER.unpack_from(payload)
    if magic != _MAGIC:
        raise ZoneProtocolError('UNSUPPORTED_ZONE_MAGIC')
    if head_length != _HEADER.size or body_length != len(payload) - _HEADER.size:
        raise ZoneProtocolError('INVALID_ZONE_HEADER_LENGTHS')
    # 仅有包头正确不能证明业务已解析；调用方不得把本摘要当作登录成功或未知命令的认可。
    return {'zone_profile': ZONE_HEADER_PROFILE, 'metadata_sha256': ZONE_METADATA_SHA256,
            'magic': magic, 'command': command, 'head_length': head_length,
            'body_length': body_length, 'body_validation': 'UNVALIDATED'}


def parse_netbar_key_notification(payload):
    """解析当前 NetBarKey 的计数字段，验证数组精确边界；只返回密钥长度，绝不返回密钥或其摘要。"""
    result = parse_zone_header(payload)
    if result['command'] != _NOTIFY_NETBAR_KEY:
        raise ZoneProtocolError('UNSUPPORTED_ZONE_MESSAGE')
    if result['body_length'] < 2:
        raise ZoneProtocolError('INVALID_ZONE_NETBAR_LENGTH')
    # 当前 metadata 的 KeyLen/KeyData 双向引用、512上限，与 boot19 的11B包和 DLL probe11回环互证。
    # KeyData 是计数控制的字节数组，不是C字符串；不搜索NUL、不打印内容，也不把它当作邮箱凭据。
    key_length = struct.unpack_from('>H', payload, _HEADER.size)[0]
    if key_length > _MAX_NETBAR_KEY_BYTES:
        raise ZoneProtocolError('INVALID_ZONE_NETBAR_KEY_LENGTH')
    if result['body_length'] != 2 + key_length:
        raise ZoneProtocolError('INVALID_ZONE_NETBAR_LENGTH')
    result.update({'zone_profile': ZONE_NETBAR_KEY_PROFILE, 'message': 'C2ZN_NtfNetBarKey',
                   'body_validation': 'VALIDATED_STRUCTURE_ONLY', 'key_length': key_length})
    return result


def parse_login_request(payload):
    """解析当前26字节登录请求的频道、CRC、IP原始值和平台；身份必须由外层可信连接提供。"""
    result = parse_zone_header(payload)
    if result['command'] != _REQUEST_LOGIN:
        raise ZoneProtocolError('UNSUPPORTED_ZONE_MESSAGE')
    if result['body_length'] != 18:
        raise ZoneProtocolError('INVALID_ZONE_LOGIN_LENGTH')
    # boot20真实请求经当前DLL逐字段/整包往返：4个BE32后接BE16，type19也保留其DWORD线表示。
    # SubChannelId的FFFFFFFF及平台未知值均只作为观测值；不把它们解释为自动选服或绕过鉴权的许可。
    sub_channel, preference_crc, settings_crc, zone_ip, platform = struct.unpack_from('>IIIIH', payload, 8)
    result.update({'zone_profile': ZONE_LOGIN_REQUEST_PROFILE, 'message': 'C2ZN_ReqLogin',
                   'body_validation': 'VALIDATED_STRUCTURE_ONLY', 'sub_channel_id': sub_channel,
                   'preference_crc_value': preference_crc, 'system_setting_crc_value': settings_crc,
                   'current_zone_host_ip_wire_bytes': payload[20:24].hex(),
                   'current_zone_host_ip_dword': zone_ip, 'login_platform': platform})
    return result


def login_response_compatibility():
    """返回固定研究登录响应的独立说明；当前成功分支与线格式已证，角色和资产投影仍明确未完成。"""
    return {
        'profile': ZONE_LOGIN_RESPONSE_PROFILE,
        'scope': 'FIXED_ZONE_LOGIN_INITIALIZATION_RESEARCH_ONLY',
        'metadata_sha256': ZONE_METADATA_SHA256,
        'serializer_evidence': 'evidence/aliyun-native-zone-login-response-offline.json',
        'message': 'ZN2C_ResLogin', 'native_serializer_verified': True,
        'current_success_branch_verified': True, 'original_business_rules_verified': False,
        'character_state_projected': False, 'wallet_or_inventory_projected': False,
        'lobby_verified': False, 'server_time_calibration': 'SKIPPED_BY_VERIFIED_ZERO_BRANCH',
        'result_macro': {'name': 'ZONE_ERR_SUCC', 'value': 0x8100},
        'research_values': {'main_channel_id': 1, 'sub_channel_id': 1, 'server_time': 0,
                            'tgame_point': 0, 'gold_point': 0, 'free_prop_count': 0,
                            'is_newbie': 0, 'area_id': 0},
    }


def build_research_login_response(*, profile):
    """显式启用后生成当前DLL验证的41字节固定响应；不读取账号、授予道具或更改数据库中的余额。"""
    if type(profile) is not str or profile != ZONE_LOGIN_RESPONSE_PROFILE:
        raise ZoneProtocolError('UNSUPPORTED_ZONE_LOGIN_RESPONSE_PROFILE')
    # 当前consumer Result=8100进入成功初始化，ServerTime=0明确跳过校准；其余零值为研究选择。
    # 当前DLL以FreePropCount=0省略数组，独立body pack得到33B，完整包/host/repack严格互证。
    body = struct.pack('>HIIQIIHBI', 0x8100, 1, 1, 0, 0, 0, 0, 0, 0)
    return _HEADER.pack(_MAGIC, _RESPONSE_LOGIN, _HEADER.size, len(body)) + body


def parse_player_define_request(payload):
    """只接收当前实机的8字节A204空请求；元数据无正文分支，不创造字段或推断用户已有设置。"""
    result = parse_zone_header(payload)
    if result['command'] != 0xA204:
        raise ZoneProtocolError('UNSUPPORTED_ZONE_MESSAGE')
    if result['body_length'] != 0:
        raise ZoneProtocolError('INVALID_PLAYER_DEFINE_REQUEST_LENGTH')
    result.update({'zone_profile': ZONE_PLAYER_DEFINE_REQUEST_PROFILE,
                   'message': 'ID_C2ZN_REQ_GET_PLAYERDEFINEINFO',
                   'body_validation': 'VERIFIED_EMPTY_REQUEST'})
    return result


def build_research_empty_player_define_response(*, profile):
    """只构造显式无自定义数据的A205研究响应；不读取、清空或持久化真实玩家设置。"""
    if type(profile) is not str or profile != ZONE_EMPTY_PLAYER_DEFINE_PROFILE:
        raise ZoneProtocolError('UNSUPPORTED_EMPTY_PLAYER_DEFINE_PROFILE')
    # 当前consumer已证Result=8100/DataLen=0跳过版本内容解析并执行完成回调。
    # 当前DLL独立bodypack/fullhost/repack均一致，14B golden摘要见对应离线证据。
    # 此处不构造未知非空Data，也不把该回调当作角色或大厅完成；仅限独立研究启动。
    body = struct.pack('>HI', 0x8100, 0)
    return _HEADER.pack(_MAGIC, 0xA205, _HEADER.size, len(body)) + body
