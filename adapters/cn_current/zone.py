"""当前 Zone 请求解析与固定研究登录响应；不输出密钥、不进行网络操作或写入玩家资产。"""
import struct

ZONE_HEADER_PROFILE = 'nz-current-zone-header-observation-v1'
ZONE_NETBAR_KEY_PROFILE = 'nz-current-zone-netbar-key-notification-v1'
ZONE_LOGIN_REQUEST_PROFILE = 'nz-current-zone-login-request-v1'
ZONE_PLAYER_DEFINE_REQUEST_PROFILE = 'nz-current-player-define-request-v1'
ZONE_EMPTY_PLAYER_DEFINE_PROFILE = 'cn2020-player-define-empty-research-v1'
ZONE_EMPTY_ASSET_PLAYER_INFO_PROFILE = 'cn2020-player-info-empty-assets-research-v1'
ZONE_EMPTY_PLAYER_PROPS_PROFILE = 'cn2020-player-props-empty-research-v1'
ZONE_PLAYER_PROPS_CODEC_PROFILE = 'cn2020-player-props-codec-research-v1'
ZONE_DETAILED_CHANNEL_PROFILE = 'cn2020-detailed-channel-loopback-research-v1'
ZONE_OPAQUE_PLAYER_DEFINE_PROFILE = 'cn2020-player-define-opaque-v1'
ZONE_NO_HINTS_PROFILE = 'cn2020-zone-no-hints-research-v1'
ZONE_LOGIN_RESPONSE_PROFILE = 'cn2020-zone-login-success-research-v1'
ZONE_CLOCK_COMPAT_PROFILE = 'cn2020-clock-unix-seconds-compat-v1'
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


def build_clock_login_response(*, server_time, profile):
    """显式兼容登录时间：以UTC Unix秒提供当前日历，保留研究响应的零钱包和单服字段。

    当前消费者把64位输入转换为tm并加1900/月份加1；本合同明确采用Unix秒，
    不以某一原版服务器时区规则自居。不得通过回拨系统时钟改变商品可见性。
    """
    if profile != ZONE_CLOCK_COMPAT_PROFILE:
        raise ZoneProtocolError('UNSUPPORTED_CLOCK_COMPAT_PROFILE')
    if type(server_time) is not int or not 1577836800 <= server_time < 4102444800:
        raise ZoneProtocolError('INVALID_COMPAT_SERVER_TIME')
    wire = bytearray(build_research_login_response(profile=ZONE_LOGIN_RESPONSE_PROFILE))
    struct.pack_into('>Q',wire,18,server_time)
    return bytes(wire)


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


def parse_set_player_define_request(payload):
    """按当前A202的DataLen+Data读取不透明设置；长度严格匹配且不超过3200，不解释内部版本。"""
    header = parse_zone_header(payload)
    if header['command'] != 0xA202 or header['body_length'] < 4:
        raise ZoneProtocolError('INVALID_SET_PLAYER_DEFINE_REQUEST')
    length = struct.unpack_from('>I', payload, 8)[0]
    if length > 3200 or length != len(payload) - 12:
        raise ZoneProtocolError('INVALID_SET_PLAYER_DEFINE_DATA_LENGTH')
    return payload[12:]


def build_player_define_data_response(data, *, profile):
    """将同账号、同客户端和TDR版本取回的不透明正文封装为A205；不生成或改写内部设置结构。"""
    if profile != ZONE_OPAQUE_PLAYER_DEFINE_PROFILE or type(profile) is not str:
        raise ZoneProtocolError('UNSUPPORTED_OPAQUE_PLAYER_DEFINE_PROFILE')
    if not isinstance(data, bytes) or len(data) > 3200:
        raise ZoneProtocolError('INVALID_PLAYER_DEFINE_DATA')
    body = struct.pack('>HI', 0x8100, len(data)) + data
    return _HEADER.pack(_MAGIC, 0xA205, _HEADER.size, len(body)) + body


def build_set_player_define_committed_response(*, profile):
    """调用方已确认MySQL提交后才可发送A203；Result来自当前成功宏，不能以此代替持久化。"""
    if profile != ZONE_OPAQUE_PLAYER_DEFINE_PROFILE or type(profile) is not str:
        raise ZoneProtocolError('UNSUPPORTED_OPAQUE_PLAYER_DEFINE_PROFILE')
    body = struct.pack('>H', 0x8100)
    return _HEADER.pack(_MAGIC, 0xA203, _HEADER.size, len(body)) + body


def build_research_player_info(*, game_id, nickname_bytes, profile):
    """编码当前78字段资料的空资产研究夹具；昵称必须由调用方按已验证编码提供，不改业务资料。"""
    if type(profile) is not str or profile != ZONE_EMPTY_ASSET_PLAYER_INFO_PROFILE:
        raise ZoneProtocolError('UNSUPPORTED_PLAYER_INFO_RESEARCH_PROFILE')
    if type(game_id) is not int or not 1 <= game_id <= 0x7FFFFFFF:
        raise ZoneProtocolError('INVALID_PLAYER_INFO_GAME_ID')
    if not isinstance(nickname_bytes, bytes) or not 1 <= len(nickname_bytes) <= 31 or b'\0' in nickname_bytes:
        raise ZoneProtocolError('INVALID_PLAYER_INFO_NICKNAME_BYTES')
    # 当前固定TDR的PlayerInfo host为1410B，昵称占32B；线格式用BE32含NUL长度替代固定容量。
    # 两个不同ID/昵称长度的当前DLL完整host/正文/全包往返精确验证此布局，不复用PH短结构。
    remaining = bytearray(1366)
    struct.pack_into('>I', remaining, 116, 1)  # Level=1是显式研究值，其他未知统计/角色/权益为0。
    body = (struct.pack('>HQII', 0x8100, game_id, 0, len(nickname_bytes) + 1)
            + nickname_bytes + b'\0' + remaining)
    return _HEADER.pack(_MAGIC, 0xA005, _HEADER.size, len(body)) + body


def encode_current_player_nickname(nickname, *, code_page):
    """按已核验的当前CP_ACP=936严格编码；不可表示、NUL或超过31字节时拒绝，不替换/截断/改名。"""
    if type(code_page) is not int or code_page != 936:
        raise ZoneProtocolError('UNVERIFIED_PLAYER_NICKNAME_CODE_PAGE')
    if not isinstance(nickname, str) or not nickname or '\0' in nickname:
        raise ZoneProtocolError('INVALID_PLAYER_INFO_NICKNAME')
    try:
        encoded = nickname.encode('cp936', errors='strict')
        if encoded.decode('cp936', errors='strict') != nickname:
            raise UnicodeError('Lossy nickname conversion')
    except UnicodeError:
        raise ZoneProtocolError('UNREPRESENTABLE_PLAYER_NICKNAME') from None
    if len(encoded) > 31:
        raise ZoneProtocolError('PLAYER_NICKNAME_BYTE_LIMIT')
    return encoded


def build_research_empty_player_props(*, profile):
    """编码当前A006的零道具终包夹具；只验证当前布局，不授予角色或改写业务库存。"""
    if type(profile) is not str or profile != ZONE_EMPTY_PLAYER_PROPS_PROFILE:
        raise ZoneProtocolError('UNSUPPORTED_EMPTY_PLAYER_PROPS_PROFILE')
    body=struct.pack('>HBH',0x8100,1,0)
    return _HEADER.pack(_MAGIC,0xA006,_HEADER.size,len(body))+body


def build_research_no_zone_hints(*, profile):
    """当前FF13只含一个16位hint；零是明确无提示研究值，不创建原版活动或宣称大厅完成。

    完整host/独立正文/全包往返由当前DLL核对；仅在本次临时资料/装备链之后显式发送一次。
    该消息没有Result字段，不能给它添加其他消息的8100返回码或解释为认证成功。
    """
    if type(profile) is not str or profile != ZONE_NO_HINTS_PROFILE:
        raise ZoneProtocolError('UNSUPPORTED_ZONE_HINTS_PROFILE')
    body = struct.pack('>H', 0)
    return _HEADER.pack(_MAGIC, 0xff13, _HEADER.size, len(body)) + body


def build_research_player_props(*, game_id, props, is_last, profile):
    """编码当前每批最多5件的完整70B道具，调用方必须提供每一字段；不生成GID或授予业务资产。

    这里只验证当前TDR字段/数值宽度与连接身份绑定，不判断有效期、耐久、装备或角色规则。
    不采用PH结构，不把序列化夹具中的值当作可用角色默认值；尚未接入生产网络入口。
    """
    if type(profile) is not str or profile != ZONE_PLAYER_PROPS_CODEC_PROFILE:
        raise ZoneProtocolError('UNSUPPORTED_PLAYER_PROPS_CODEC_PROFILE')
    if type(game_id) is not int or not 1 <= game_id <= 0x7fffffff:
        raise ZoneProtocolError('INVALID_PLAYER_PROPS_GAME_ID')
    if type(is_last) is not bool or not isinstance(props, (tuple, list)) or len(props) > 5:
        raise ZoneProtocolError('INVALID_PLAYER_PROPS_CHUNK')
    names = ('Uin','GID','ItemId','ResFlag','StackNum','ObtainTime','Validity','Durability',
             'DurabilityMax','OwnerGID','Location','AvailbilityHour','UseHour','GainType')
    codec = struct.Struct('>QQiQiqiiiQbiiB')
    encoded, gids = [], set()
    for prop in props:
        if not isinstance(prop, dict) or set(prop) != set(names) or any(type(prop[key]) is not int for key in names):
            raise ZoneProtocolError('INVALID_PLAYER_PROP_FIELDS')
        if prop['Uin'] != game_id or prop['GID'] <= 0 or prop['ItemId'] <= 0 or prop['StackNum'] <= 0:
            raise ZoneProtocolError('INVALID_PLAYER_PROP_OWNERSHIP_OR_ID')
        if prop['GID'] in gids:
            raise ZoneProtocolError('DUPLICATE_PLAYER_PROP_GID')
        try:
            encoded.append(codec.pack(*(prop[key] for key in names)))
        except struct.error:
            raise ZoneProtocolError('PLAYER_PROP_FIELD_OUT_OF_RANGE') from None
        gids.add(prop['GID'])
    body = struct.pack('>HBH', 0x8100, int(is_last), len(encoded)) + b''.join(encoded)
    return _HEADER.pack(_MAGIC, 0xA006, _HEADER.size, len(body)) + body


def build_research_main_channel(*, profile):
    """按当前MainChnlInfo返回一个兼容主频道；三项DsaIPs为回环研究地址，不声明房间可用。

    名称采用ASCII，当前Normal枚举为2。布局与A358不同：本消息无Zone数组，
    而在人数后含三个固定IPv4字段，必须独立经当前DLL往返核对。
    """
    if profile != ZONE_DETAILED_CHANNEL_PROFILE:
        raise ZoneProtocolError('UNSUPPORTED_DETAILED_CHANNEL_PROFILE')
    name = b'NZ Online\0'
    body = (struct.pack('>HHHHI', 0x8100, 1, 1, 2, len(name)) + name
            + struct.pack('>IIIII', 100, 1, 0x0100007f, 0x0100007f, 0x0100007f))
    return _HEADER.pack(_MAGIC, 0xA356, 8, len(body)) + body


def build_current_heartbeat_response(request):
    """只回应当前A004的单字节零Padding请求；FF10同样仅含Padding，无Result或时间戳。"""
    header = parse_zone_header(request)
    if header['command'] != 0xA004 or request[8:] != b'\0':
        raise ZoneProtocolError('UNSUPPORTED_HEARTBEAT_REQUEST')
    return _HEADER.pack(_MAGIC, 0xFF10, 8, 1) + b'\0'


def build_research_zone_channels(request, *, port, profile):
    """当前A132指定主频道1，A133无Result，返回单Zone/单Channel；仅作为回环房间入口研究。

    采用当前ZoneChannelListInfo完整布局，首末包均1，主频道与端口绑定本探针。
    人数与容量沿兼容目录示例，不代表原生战斗人数上限。
    """
    if type(profile) is not str or profile != ZONE_DETAILED_CHANNEL_PROFILE:
        raise ZoneProtocolError('UNSUPPORTED_DETAILED_CHANNEL_PROFILE')
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ZoneProtocolError('INVALID_DETAILED_CHANNEL_LOOPBACK_PORT')
    header = parse_zone_header(request)
    if header['command'] != 0xA132 or request[8:] != struct.pack('>I', 1):
        raise ZoneProtocolError('UNSUPPORTED_ZONE_CHANNEL_REQUEST')
    title = b'NZ-1\0'
    channel = struct.pack('>II', 1, len(title)) + title + struct.pack('>6I', 100, 1, 0, 0, 1, 0)
    zone = struct.pack('>III', 1, 0x0100007f, 1) + b'\0' + struct.pack('>Hi', port, 1) + channel + struct.pack('>i', 0)
    body = struct.pack('>BBh', 1, 1, 1) + zone
    return _HEADER.pack(_MAGIC, 0xA133, 8, len(body)) + body


def parse_detailed_main_channel_request(payload):
    """仅核验当前实收A357的4B Padding；不据此判断客户端已进入大厅。"""
    header = parse_zone_header(payload)
    if header['command'] != 0xA357 or header['body_length'] != 4:
        raise ZoneProtocolError('INVALID_DETAILED_CHANNEL_REQUEST')
    return struct.unpack('>I', payload[8:])[0]


def build_research_detailed_main_channel(*, port, profile):
    """构造当前A358的一区服一频道回环研究目录，Normal=2来自当前宏表，不沿用PH短结构。

    两个端口的当前DLL完整host/独立正文/全包往返验证61B格式；100容量、1人数、
    NZ Online/NZ和显示序号1为显式研究选择，不能当作战斗人数上限或真实在线人数。
    """
    if type(profile) is not str or profile != ZONE_DETAILED_CHANNEL_PROFILE:
        raise ZoneProtocolError('UNSUPPORTED_DETAILED_CHANNEL_PROFILE')
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ZoneProtocolError('INVALID_DETAILED_CHANNEL_LOOPBACK_PORT')
    title, prefix = b'NZ Online\0', b'NZ\0'
    zone = struct.pack('>IHHHI', 0x0100007f, port, 1, 1, len(prefix)) + prefix + struct.pack('>I', 1)
    detail = (struct.pack('>HHI', 1, 2, len(title)) + title
              + struct.pack('>IIH', 100, 1, 1) + zone)
    body = struct.pack('>Hh', 0x8100, 1) + detail
    return _HEADER.pack(_MAGIC, 0xA358, _HEADER.size, len(body)) + body


def player_info_research_compatibility():
    """列出空资产资料夹具差异；不把序列化成功或临时资料通知当作角色、库存或大厅验收。"""
    return {'profile': ZONE_EMPTY_ASSET_PLAYER_INFO_PROFILE,
            'scope': 'PLAYER_INFO_EMPTY_ASSETS_DIAGNOSTIC_ONLY',
            'metadata_sha256': ZONE_METADATA_SHA256,
            'mapped_fields': ['Uin', 'NickName'], 'nickname_encoding': 'CALLER_MUST_VERIFY_CURRENT_CONSUMER',
            'research_values': {'Result': 0x8100, 'VerifyKey': 0, 'Level': 1,
                                'CurRoleGID': 0, 'RoleType': 0, 'remaining_fields': 0},
            'real_wallet_or_inventory_projected': False, 'business_profile_written': False,
            'role_resources_verified': False, 'lobby_verified': False}
