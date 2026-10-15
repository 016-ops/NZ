"""当前 Win32 TenProxy 的独立身份 IPC 研究格式；只供自测，不声明原生登录兼容。"""
import struct
import re
import hashlib

PROXY_SHA256 = '788bea4e34d6aaea8187df482ea2c859f7a6c8802b226b1ab7c2d9a6a10bc047'
IPC_PROFILE = 'nz-email-ipc-research-v2'
LOOPBACK_IPC_PROFILE = 'nz-email-ipc-loopback-research-v2'
RESEARCH_SERVICE_ID = 1  # 已确定字段身份，尚未验证该业务值；不能复用 PH 的 ServiceId。


def local_session_id(game_token):
    """从独立游戏令牌派生 16 字节初始传输密钥，避免账号共用固定值；函数名保留既有工具兼容。

    当前 ProtocalHandler 已证实 tag5 用作 AES bootstrap key，早期报告只把它称为会话标识。
    """
    return hashlib.sha256(b'nz-email-ipc-session-v1\0' + game_token.encode('ascii')).digest()[:16]


def research_mapping(game_id, game_token, *, loopback_port=None, client_path=''):
    """按当前 DLL 静态 getter 构造有界 TLV，自建账号与令牌采用本地命名空间。

    版本下限、32 位偏移和读取上限来自当前哈希；标量业务含义尚未全部验证。
    默认服务列表为空；显式回环端口仅用于收集当前客户端请求，不等于实现原生服务端。
    """
    if type(game_id) is not int or not 1 <= game_id <= 2147483647:
        raise ValueError('Local game ID is outside the proven control-plane range')
    if not isinstance(game_token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', game_token):
        raise ValueError('Independent token must fit the observed IPC byte field')
    entries = {tag: struct.pack('<I', 0) for tag in range(1, 33)}
    # 第 12 次当前游戏代码证实输出基址及消费偏移：tag1/+4 是 ServiceId，tag2/+0x10 才是 Uin。
    # v1 只核对 DLL getter，误把这两个字段互换；v2 修正自建账号 ID 所处的位置。
    entries.update({1: struct.pack('<I', RESEARCH_SERVICE_ID), 2: struct.pack('<I', game_id), 3: bytes([len(game_token)]),
                    4: game_token.encode('ascii'), 5: local_session_id(game_token), 7: b'', 14: b'\0\0', 15: b'\0\0',
                    16: b'\0\0', 17: struct.pack('<I', 0), 18: struct.pack('<I', 1), 19: b'\0\0',
                    26: b'\0', 27: b''})
    if loopback_port is not None:
        if type(loopback_port) is not int or not 1024 <= loopback_port <= 65535:
            raise ValueError('Research endpoint requires a non-privileged loopback port')
        if len(client_path) > 250 or '\0' in client_path:
            raise ValueError('Invalid working copy path')
        encoded_client_path = (client_path + '\0').encode('utf-16-le')
        # 当前 getter 的 tag19 上限按字节计；非 BMP 字符占两个 UTF16 码元，不能只数 Python 字符。
        if len(encoded_client_path) > 0x208:
            raise ValueError('Working copy path exceeds the observed UTF16 field')
        entries[17] = struct.pack('<I', 1)
        entries[19] = encoded_client_path
    header = bytearray(struct.pack('<I', 32) + bytes(32 * 4))
    for tag, value in entries.items():
        struct.pack_into('<I', header, tag * 4, len(header))
        header.extend(struct.pack('<I', len(value)) + value)
    if loopback_port is not None:
        # tag20 是特殊两层偏移表，不能套普通 TLV 的长度头。字段读取位置已在当前 DLL 验证；
        # 数值业务含义尚待实机验证，因此只允许本机监听器，绝不发往腾讯或任意外部地址。
        struct.pack_into('<I', header, 20*4, len(header))
        sub_table = len(header)
        header.extend(bytes(4))
        struct.pack_into('<I', header, sub_table, len(header))
        record_table = len(header)
        header.extend(bytes(8*4))
        # 当前 TGame 实际建连路径读取 record+0 的 UTF16 host 与 record+0x100 的端口：sub2/sub3。
        # sub4/sub5 的业务含义仍未证明，保留原回环研究值，不能再把它们称为主连接地址/端口。
        fields = {1:struct.pack('<I',1), 2:'127.0.0.1\0'.encode('utf-16-le'),
                  3:struct.pack('<I',loopback_port), 4:'127.0.0.1\0'.encode('utf-16-le'),
                  5:struct.pack('<I',loopback_port), 6:struct.pack('<I',0),
                  7:struct.pack('<I',0), 8:b''}
        for tag, value in fields.items():
            struct.pack_into('<I', header, record_table+(tag-1)*4, len(header))
            header.extend(struct.pack('<I',len(value))+value)
    return bytes(header)
