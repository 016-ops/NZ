"""当前 GEO 请求严格解析及单条回环研究目录；布局经当前 DLL 核对，兼容数值不宣称原版规则。"""
import struct

GEO_PING_PROFILE = 'nz-current-geo-ping-request-v1'
GEO_PING_RESPONSE_PROFILE = 'nz-current-geo-ping-loopback-research-v1'
GEO_ZONE_REQUEST_PROFILE = 'nz-current-geo-zone-request-v1'
GEO_ZONE_RESPONSE_PROFILE = 'nz-current-geo-zone-loopback-research-v1'
GEO_METADATA_SHA256 = '6d75e846532bb506a858323b8a1c91fa67d3ed0c8766f1d1682f9a8d8afbc8e1'
_HEADER = struct.Struct('>HHHH')
_MAGIC = 0x8202
_REQUEST_PING_LIST = 0x1001
_RESPONSE_PING_LIST = 0x2001
_REQUEST_ZONE_LIST = 0x1000
_RESPONSE_ZONE_LIST = 0x2000
_MAX_PING_SERVERS = 192
_MAX_DOMAIN_BYTES = 128


class GeoProtocolError(ValueError):
    """当前 GEO 请求或固定研究 profile 合同之外的输入；只用固定错误码，不回显业务原文。"""


def parse_ping_request(payload):
    """只解析精确 9 字节 PingREQ，返回结构摘要和原始单字节 IsForceLogin，不执行任何操作。"""
    if not isinstance(payload, bytes) or len(payload) != 9:
        raise GeoProtocolError('INVALID_GEO_PING_LENGTH')
    magic, command, head_length, body_length = _HEADER.unpack_from(payload)
    # 当前 TDR 确定字段与命令，实际首包进一步确认大端和精确线长；不能沿用其他版本的布局。
    if magic != _MAGIC or command != _REQUEST_PING_LIST:
        raise GeoProtocolError('UNSUPPORTED_GEO_MESSAGE')
    if head_length != _HEADER.size or body_length != 1:
        raise GeoProtocolError('INVALID_GEO_PING_HEADER_LENGTHS')
    # 已证语法仅为一个字节；不臆造布尔取值范围，更不把非零值当成允许强制登录的授权。
    return {'geo_profile': GEO_PING_PROFILE, 'message': 'C2GEO_ReqPingList', 'magic': magic,
            'command': command, 'head_length': head_length, 'body_length': body_length,
            'is_force_login': payload[_HEADER.size]}


def parse_zone_request(payload):
    """严格解析当前 ZoneList 请求的有界数组和 ASCII Domain；只保留测量值，不选择端点或授权登录。"""
    # metadata 的数组最多 192 条，每条 wire 为 Group/Ipv4/字符串长度/Ping 共16字节加最多128字节字符串。
    # 当前实包是单条40字节，但零条和其他有界长度依然合法，不能把一个采样长度变成全部协议的限制。
    maximum = _HEADER.size + 2 + _MAX_PING_SERVERS * (16 + _MAX_DOMAIN_BYTES) + 4
    if not isinstance(payload, bytes) or not _HEADER.size + 6 <= len(payload) <= maximum:
        raise GeoProtocolError('INVALID_GEO_ZONE_LENGTH')
    magic, command, head_length, body_length = _HEADER.unpack_from(payload)
    if magic != _MAGIC or command != _REQUEST_ZONE_LIST:
        raise GeoProtocolError('UNSUPPORTED_GEO_MESSAGE')
    if head_length != _HEADER.size or body_length != len(payload) - _HEADER.size:
        raise GeoProtocolError('INVALID_GEO_ZONE_HEADER_LENGTHS')
    count = struct.unpack_from('>H', payload, _HEADER.size)[0]
    if count > _MAX_PING_SERVERS:
        raise GeoProtocolError('INVALID_GEO_ZONE_SERVER_COUNT')
    if not _HEADER.size + 6 + count * 17 <= len(payload) <= _HEADER.size + 6 + count * 144:
        raise GeoProtocolError('INVALID_GEO_ZONE_LENGTH')
    cursor = _HEADER.size + 2

    def take(length):
        """每次取字段前核对边界；允许读到恰好包尾，最终再单独检查 MaxDelay 与尾随字节。"""
        nonlocal cursor
        if cursor + length > len(payload):
            raise GeoProtocolError('TRUNCATED_GEO_ZONE_FIELD')
        value = payload[cursor:cursor + length]
        cursor += length
        return value

    servers = []
    for _ in range(count):
        group = struct.unpack('>I', take(4))[0]
        ipv4 = take(4)
        domain_length = struct.unpack('>I', take(4))[0]
        if not 1 <= domain_length <= _MAX_DOMAIN_BYTES:
            raise GeoProtocolError('INVALID_GEO_ZONE_DOMAIN_LENGTH')
        domain_bytes = take(domain_length)
        if domain_bytes[-1:] != b'\0' or b'\0' in domain_bytes[:-1]:
            raise GeoProtocolError('INVALID_GEO_ZONE_DOMAIN_TERMINATION')
        # 只确认当前 ASCII 回环样本；明确拒绝未知编码和控制符，不用宽松解码隐藏不受支持的输入。
        if any(value < 32 or value > 126 for value in domain_bytes[:-1]):
            raise GeoProtocolError('UNSUPPORTED_GEO_ZONE_DOMAIN_ENCODING')
        servers.append({'group': group, 'ipv4_wire_bytes': ipv4.hex(),
                        'ipv4_host_dword': struct.unpack('>I', ipv4)[0],
                        'domain': domain_bytes[:-1].decode('ascii'),
                        'ping_in_ms': struct.unpack('>I', take(4))[0]})
    max_delay = struct.unpack('>I', take(4))[0]
    if cursor != len(payload):
        raise GeoProtocolError('TRAILING_GEO_ZONE_DATA')
    return {'geo_profile': GEO_ZONE_REQUEST_PROFILE, 'message': 'C2GEO_ReqZoneList',
            'magic': magic, 'command': command, 'head_length': head_length, 'body_length': body_length,
            'server_count': count, 'servers': servers, 'max_delay_in_ms': max_delay}


def ping_response_compatibility():
    """返回可公开的固定研究规则副本；调用方修改返回值不会改变编码，也不能配置其他端点。"""
    return {
        'profile': GEO_PING_RESPONSE_PROFILE,
        'scope': 'ONE_LOOPBACK_PING_DIRECTORY_RESEARCH_ONLY',
        'metadata_sha256': GEO_METADATA_SHA256,
        'serializer_evidence': 'evidence/aliyun-native-tdr-offline.json',
        'message': 'GEO2C_ResPingList',
        'native_serializer_verified': True,
        'original_business_rules_verified': False,
        'client_address_consumption_verified': False,
        'zone_response_in_this_profile': False,
        # Result 是当前文件的已证成功宏；其余业务数值是显式研究参数，不来源于原版平衡或容量规则。
        'result_macro': {'name': 'GEO_ERR_SUCC', 'value': 0x8B00},
        'research_values': {
            'server_count': 1, 'group': 0, 'ipv4_host_bytes': '7f000001',
            'ipv4_wire_bytes': '0100007f', 'domain': '127.0.0.1', 'ping_in_ms': 0,
            'max_delay_in_ms': 1000, 'rank': 0, 'next_query_gap': 0, 'close_conn': 0,
        },
    }


def build_research_ping_response(*, profile):
    """仅编码显式选中的单条回环研究目录，不进行网络操作、身份授权或创建房间/区服。"""
    if type(profile) is not str or profile != GEO_PING_RESPONSE_PROFILE:
        raise GeoProtocolError('UNSUPPORTED_GEO_PING_RESPONSE_PROFILE')
    # 当前恢复 TDR 与固定指纹 DLL 离线 pack/unpack 互证：Domain 为 BE32 长度，计入尾部 NUL。
    # type19 的当前 host 内存是 7F000001，作为 x86 DWORD 交给原生 pack 后 wire 为 0100007F；
    # 这里精确复现该 golden，不替换成凭常规网络字节序猜出的 7F000001，客户端使用语义仍待实机确认。
    domain = b'127.0.0.1\0'
    body = (struct.pack('>HHII', 0x8B00, 1, 0, 0x0100007F)
            + struct.pack('>I', len(domain)) + domain
            + struct.pack('>IIIIH', 0, 1000, 0, 0, 0))
    # 尾部 18 字节依次是 PingInMs、MaxDelayInMs 和当前版本新增的完整 RankInfo，不能照旧样本省略。
    # 返回的 52 字节与当前 DLL 生成并完整回解的独立 golden 一致；这仍不代表游戏已接受研究业务值。
    return _HEADER.pack(_MAGIC, _RESPONSE_PING_LIST, _HEADER.size, len(body)) + body


def _research_zone_port(port):
    """仅接受本次自有监听器的非特权整数端口；布尔、自动转换或范围外数值均拒绝。"""
    if type(port) is not int or not 1024 <= port <= 65535:
        raise GeoProtocolError('INVALID_GEO_RESEARCH_ZONE_PORT')
    return port


def zone_response_compatibility(*, port):
    """公开单区服研究参数的独立副本；只可选择本次回环监听端口，不声称房间实例已可用。"""
    port = _research_zone_port(port)
    return {
        'profile': GEO_ZONE_RESPONSE_PROFILE,
        'scope': 'ONE_LOOPBACK_ZONE_DIRECTORY_RESEARCH_ONLY',
        'metadata_sha256': GEO_METADATA_SHA256,
        'serializer_evidence': 'evidence/aliyun-native-tdr-offline.json',
        'message': 'GEO2C_ResZoneList',
        'native_serializer_verified': True,
        'original_business_rules_verified': False,
        'zone_login_verified': False,
        'room_node_available': False,
        'same_node_multiple_rooms_retained': True,
        'result_macro': {'name': 'GEO_ERR_SUCC', 'value': 0x8B00},
        'research_values': {
            'zone_count': 1, 'ipv4_host_bytes': '7f000001', 'ipv4_wire_bytes': '0100007f',
            'domain': '127.0.0.1', 'port': port, 'online_num': 1, 'ping_in_ms': 1,
            'main_channel_id': 1, 'load_exts': [0, 0, 0, 0, 0, 0], 'decision_method': 1,
        },
    }


def build_research_zone_response(*, port, profile):
    """编码当前 DLL 已核验的单区服研究目录；仅端口可变化，主机固定回环，不建立网络或房间。"""
    if type(profile) is not str or profile != GEO_ZONE_RESPONSE_PROFILE:
        raise GeoProtocolError('UNSUPPORTED_GEO_ZONE_RESPONSE_PROFILE')
    port = _research_zone_port(port)
    domain = b'127.0.0.1\0'
    # 当前 DLL probe10 完整包/独立 body/host 缓冲区互证；这里保留相同 IPv4 DWORD 与含NUL字符串。
    # OnlineNum、PingInMs、MainChannelId=1，LoadExts=六个0，DecisionMethod=1 均为显式研究值。
    body = (struct.pack('>HHI', 0x8B00, 1, 0x0100007F)
            + struct.pack('>I', len(domain)) + domain
            + struct.pack('>HIII6IH', port, 1, 1, 1, 0, 0, 0, 0, 0, 0, 1))
    # 一个目录入口不等于只允许一间房，也不表示指向的节点已具备实例或游戏能力。
    return _HEADER.pack(_MAGIC, _RESPONSE_ZONE_LIST, _HEADER.size, len(body)) + body
