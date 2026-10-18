"""当前 Zone 包头、NetBarKey 和登录请求边界；使用公开摘要及测试字节，不运行 DLL、游戏或数据库。"""
import hashlib
import json
import struct
import unittest

from adapters.cn_current.zone import (
    ZONE_EMPTY_ASSET_PLAYER_INFO_PROFILE, build_research_player_info, player_info_research_compatibility,
    encode_current_player_nickname,
    ZONE_EMPTY_PLAYER_PROPS_PROFILE, build_research_empty_player_props,
    ZONE_PLAYER_PROPS_CODEC_PROFILE, build_research_player_props,
    ZONE_DETAILED_CHANNEL_PROFILE, build_research_detailed_main_channel, parse_detailed_main_channel_request,
    build_research_main_channel, build_research_zone_channels, build_current_heartbeat_response,
    ZONE_EMPTY_PLAYER_DEFINE_PROFILE, build_research_empty_player_define_response, parse_player_define_request,
    MAX_ZONE_PACKET_BYTES, ZONE_HEADER_PROFILE, ZONE_LOGIN_REQUEST_PROFILE, ZONE_LOGIN_RESPONSE_PROFILE,
    ZONE_METADATA_SHA256, ZONE_NETBAR_KEY_PROFILE,
    ZoneProtocolError, build_research_login_response, login_response_compatibility,
    parse_login_request, parse_netbar_key_notification, parse_zone_header,
)


class NativeDetailedChannelTests(unittest.TestCase):
    """当前A358数组和单一回环入口边界，避免误发其他版本短结构或任意公网目标。"""
    def test_main_channel_and_zone_channel_current_native_goldens(self):
        """当前DLL的A356/A133逐字节摘要，防止向无Result的A133错误添加成功码。"""
        main = build_research_main_channel(profile=ZONE_DETAILED_CHANNEL_PROFILE)
        self.assertEqual(hashlib.sha256(main).hexdigest(), 'df2ba9dde48e9559ed3b37dc53e013c594bafb07a72f9fad08ee98e0cfc64930')
        request = struct.pack('>HHHHI',0x3243,0xA132,8,4,1)
        channel = build_research_zone_channels(request,port=19001,profile=ZONE_DETAILED_CHANNEL_PROFILE)
        self.assertEqual(hashlib.sha256(channel).hexdigest(),'fae3d7a5533ddc4347106502d1c21a9c4d583c39cd12a20f3a7df7b8d69ce62c')
        for invalid in (request[:-1],request[:-1]+b'\2',request+b'\0'):
            with self.assertRaises(ZoneProtocolError):
                build_research_zone_channels(invalid,port=19001,profile=ZONE_DETAILED_CHANNEL_PROFILE)

    def test_heartbeat_exact_padding_and_current_response(self):
        """仅已核验的A004零Padding得到FF10，未知填充和其他命令拒绝。"""
        request = struct.pack('>HHHHB',0x3243,0xA004,8,1,0)
        response = build_current_heartbeat_response(request)
        self.assertEqual(hashlib.sha256(response).hexdigest(),'62f0baa4dd3a5a95a6b1b315e7c624b871952b1f838c32d53db540e7de0ee1dd')
        for invalid in (request[:-1]+b'\1',response,request[:-1]):
            with self.assertRaises(ZoneProtocolError):
                build_current_heartbeat_response(invalid)
    def test_two_ports_match_current_native_full_host_goldens(self):
        """两个实际DLL61B夹具覆盖动态端口、字符串NUL和嵌套计数，不能只回放固定目录字节。"""
        for port, expected in ((19001,'0bb6ec868701e6a9c5dc090fee2dfe998adee5b483822c6dae10a385c729636e'),
                               (45678,'2af0fa2520715016020b14e876512a13ddbb3f024f774c4994cdb508a326946f')):
            wire=build_research_detailed_main_channel(port=port,profile=ZONE_DETAILED_CHANNEL_PROFILE)
            self.assertEqual(len(wire),61)
            self.assertEqual(hashlib.sha256(wire).hexdigest(),expected)

    def test_ports_profile_and_request_lengths_are_strict(self):
        """非普通端口、布尔值及其他profile拒绝；请求必须准确A357加4字节Padding。"""
        for port in (0,80,65536,True,'19001'):
            with self.assertRaisesRegex(ZoneProtocolError,'INVALID_DETAILED_CHANNEL_LOOPBACK_PORT'):
                build_research_detailed_main_channel(port=port,profile=ZONE_DETAILED_CHANNEL_PROFILE)
        with self.assertRaisesRegex(ZoneProtocolError,'UNSUPPORTED_DETAILED_CHANNEL_PROFILE'):
            build_research_detailed_main_channel(port=19001,profile='ph')
        self.assertEqual(parse_detailed_main_channel_request(bytes.fromhex('3243a3570008000400000000')),0)
        with self.assertRaisesRegex(ZoneProtocolError,'INVALID_DETAILED_CHANNEL_REQUEST'):
            parse_detailed_main_channel_request(bytes.fromhex('3243a35700080000'))


class NativePlayerPropsCodecTests(unittest.TestCase):
    """以两条实际当前DLL非空数组golden覆盖端序、64位、负值、槽步长及连接拥有关系。"""
    def fixture_props(self):
        """返回当前DLL独立host夹具的完整字段；这些是编码测试数值，不是有效角色默认值。"""
        return [dict(Uin=10001,GID=1001+i,ItemId=(100014,100016)[i],ResFlag=0x123456789abcdef0,
                     StackNum=1,ObtainTime=12345,Validity=-1,Durability=123,DurabilityMax=456,
                     OwnerGID=0,Location=10+i,AvailbilityHour=876000,UseHour=17,GainType=48)
                for i in range(2)]

    def test_nonempty_current_native_serializer_goldens_match_both_terminal_states(self):
        """两组153B全包来自当前DLL，而非Python自往返；一个末包字节的变化也必须精确一致。"""
        for terminal, expected in (
            (False,'f7b2c6bed19690cc6c48934db06ade5b100c03f15fb3c46c833ddb0e4761288a'),
            (True,'bdbe09ab1cdc6c7b02f0fcd750a9475750e8784c94bb353f00fd308f292c6500')):
            wire=build_research_player_props(game_id=10001,props=self.fixture_props(),is_last=terminal,
                                               profile=ZONE_PLAYER_PROPS_CODEC_PROFILE)
            self.assertEqual(len(wire),153)
            self.assertEqual(hashlib.sha256(wire).hexdigest(),expected)

    def test_foreign_identity_duplicate_gid_and_missing_fields_fail_closed(self):
        """不能把另一账号道具、重复实例或缺字段数据编码成看似有效的原生库存。"""
        for modify in ('foreign','duplicate','missing','bool'):
            props=self.fixture_props()
            if modify=='foreign': props[0]['Uin']=10002
            elif modify=='duplicate': props[1]['GID']=props[0]['GID']
            elif modify=='missing': del props[0]['OwnerGID']
            else: props[0]['GID']=True
            with self.subTest(case=modify), self.assertRaises(ZoneProtocolError):
                build_research_player_props(game_id=10001,props=props,is_last=True,profile=ZONE_PLAYER_PROPS_CODEC_PROFILE)

    def test_integer_width_overflows_and_invalid_chunks_are_rejected(self):
        """覆盖64位、带符号32位、带符号位置及无符号来源边界，避免struct异常或截断外泄。"""
        for field,value in (('GID',1<<64),('ItemId',1<<31),('Validity',-(1<<31)-1),
                            ('Location',128),('GainType',256),('ResFlag',-1)):
            props=self.fixture_props();props[0][field]=value
            with self.subTest(field=field), self.assertRaisesRegex(ZoneProtocolError,'PLAYER_PROP_FIELD_OUT_OF_RANGE'):
                build_research_player_props(game_id=10001,props=props,is_last=True,profile=ZONE_PLAYER_PROPS_CODEC_PROFILE)
        for props,terminal in ((self.fixture_props()*3,True),(self.fixture_props(),1)):
            with self.assertRaisesRegex(ZoneProtocolError,'INVALID_PLAYER_PROPS_CHUNK'):
                build_research_player_props(game_id=10001,props=props,is_last=terminal,profile=ZONE_PLAYER_PROPS_CODEC_PROFILE)

    def test_empty_codec_matches_verified_empty_terminal_but_profile_is_explicit(self):
        """同一布局的空终包必须与既有golden一致，其他功能profile不能误启用非空能力。"""
        wire=build_research_player_props(game_id=10001,props=[],is_last=True,profile=ZONE_PLAYER_PROPS_CODEC_PROFILE)
        self.assertEqual(wire,build_research_empty_player_props(profile=ZONE_EMPTY_PLAYER_PROPS_PROFILE))
        with self.assertRaisesRegex(ZoneProtocolError,'UNSUPPORTED_PLAYER_PROPS_CODEC_PROFILE'):
            build_research_player_props(game_id=10001,props=[],is_last=True,profile=ZONE_EMPTY_PLAYER_PROPS_PROFILE)


class NativePlayerInfoResearchTests(unittest.TestCase):
    """以当前DLL独立两组全包摘要验证当前长资料布局，拒绝身份错位和昵称截断。"""
    def test_empty_props_terminal_packet_matches_current_native_serializer(self):
        """零数组终包只含8100/IsLast=1/Count=0；不能夹带道具或由其他profile启用。"""
        wire=build_research_empty_player_props(profile=ZONE_EMPTY_PLAYER_PROPS_PROFILE)
        self.assertEqual(wire,bytes.fromhex('3243a006000800058100010000'))
        for profile in (None,True,'',ZONE_EMPTY_ASSET_PLAYER_INFO_PROFILE):
            with self.assertRaisesRegex(ZoneProtocolError,'UNSUPPORTED_EMPTY_PLAYER_PROPS_PROFILE'):
                build_research_empty_player_props(profile=profile)

    def test_two_current_native_full_host_roundtrips_match_exact_wire(self):
        """ID和字符串长度均不同的两套golden防止只固定一个字节串或误用PH短结构。"""
        for game_id, nickname, size, digest in (
            (10001, b'NZResearchA', 1404, 'ccac6e8e24102ed748c14611cc8653132c3dbea0d4baabc237e555f38d9267a3'),
            (10002, b'NZProfileFixtureB', 1410, 'e2e0975871ef846c65942dff1c689d293a5e1c10a6d172618a2cdc8bd4baa8de'),
        ):
            wire = build_research_player_info(game_id=game_id, nickname_bytes=nickname,
                                               profile=ZONE_EMPTY_ASSET_PLAYER_INFO_PROFILE)
            self.assertEqual(len(wire), size)
            self.assertEqual(hashlib.sha256(wire).hexdigest(), digest)

    def test_current_chinese_nickname_is_lossless_without_renaming(self):
        """当前CP936下中文与ASCII混合昵称逐字节往返；API接受原文，不作规范化或替换。"""
        encoded=encode_current_player_nickname('中文昵称',code_page=936)
        self.assertEqual(encoded,bytes.fromhex('d6d0cec4eac7b3c6'))
        nickname='邮箱DEV_0123456789'
        self.assertEqual(encode_current_player_nickname(nickname,code_page=936).decode('cp936'),nickname)

    def test_unverified_codepage_unrepresentable_and_byte_overflow_are_rejected(self):
        """不同ACP、emoji、NUL、空值和双字节溢出均拒绝，不能改名、静默问号替换或截断。"""
        for page in (65001,0,True,'936'):
            with self.assertRaisesRegex(ZoneProtocolError,'UNVERIFIED_PLAYER_NICKNAME_CODE_PAGE'):
                encode_current_player_nickname('中文',code_page=page)
        for name in (None,'','x\0y'):
            with self.assertRaisesRegex(ZoneProtocolError,'INVALID_PLAYER_INFO_NICKNAME'):
                encode_current_player_nickname(name,code_page=936)
        with self.assertRaisesRegex(ZoneProtocolError,'UNREPRESENTABLE_PLAYER_NICKNAME'):
            encode_current_player_nickname('玩家😀',code_page=936)
        with self.assertRaisesRegex(ZoneProtocolError,'PLAYER_NICKNAME_BYTE_LIMIT'):
            encode_current_player_nickname('中'*16,code_page=936)
        self.assertEqual(len(encode_current_player_nickname('中'*15+'x',code_page=936)),31)

    def test_identity_and_nickname_boundaries_do_not_convert_or_truncate(self):
        """拒绝bool/越界ID、隐式编码、NUL和超过含终止符32B容量的昵称；边界字节原样保留。"""
        for game_id in (True, 0, -1, 0x80000000, '10001'):
            with self.assertRaisesRegex(ZoneProtocolError, '^INVALID_PLAYER_INFO_GAME_ID$'):
                build_research_player_info(game_id=game_id, nickname_bytes=b'x', profile=ZONE_EMPTY_ASSET_PLAYER_INFO_PROFILE)
        for nickname in ('name', bytearray(b'x'), b'', b'\0', b'x\0y', b'x' * 32):
            with self.assertRaisesRegex(ZoneProtocolError, '^INVALID_PLAYER_INFO_NICKNAME_BYTES$'):
                build_research_player_info(game_id=10001, nickname_bytes=nickname, profile=ZONE_EMPTY_ASSET_PLAYER_INFO_PROFILE)
        wire = build_research_player_info(game_id=0x7FFFFFFF, nickname_bytes=b'x' * 31,
                                           profile=ZONE_EMPTY_ASSET_PLAYER_INFO_PROFILE)
        self.assertEqual(struct.unpack_from('>I', wire, 22)[0], 32)
        self.assertEqual(wire[26:58], b'x' * 31 + b'\0')

    def test_profile_is_explicit_and_compatibility_copy_does_not_enable_assets(self):
        """研究资料必须显式启用；修改说明副本不能悄悄启用真实资产或大厅完成标志。"""
        for profile in (None, True, '', ZONE_EMPTY_ASSET_PLAYER_INFO_PROFILE + '-next'):
            with self.assertRaisesRegex(ZoneProtocolError, '^UNSUPPORTED_PLAYER_INFO_RESEARCH_PROFILE$'):
                build_research_player_info(game_id=10001, nickname_bytes=b'x', profile=profile)
        details = player_info_research_compatibility()
        details['lobby_verified'] = True
        self.assertFalse(player_info_research_compatibility()['lobby_verified'])
        self.assertFalse(player_info_research_compatibility()['real_wallet_or_inventory_projected'])


class NativePlayerDefineTests(unittest.TestCase):
    """当前A204实包边界与显式空资料研究响应；不接受任意Data或对真实玩家设置进行读写。"""

    def test_actual_empty_request_digest_and_strict_body(self):
        """8字节请求匹配boot22实收摘要；无body分支不能偷偷接受扩展字段或其他空消息。"""
        request = bytes.fromhex('3243a20400080000')
        self.assertEqual(hashlib.sha256(request).hexdigest(),
                         '5a238cd4b51f13f21306b4c45b14bbdb33d1b40440ec0e64f354870eb0826e67')
        self.assertEqual(parse_player_define_request(request)['body_validation'], 'VERIFIED_EMPTY_REQUEST')
        for invalid in (request[:-1], request + b'\0', b'\x82\x02' + request[2:],
                        struct.pack('>HHHH', 0x3243, 0xA204, 8, 1) + b'\0',
                        struct.pack('>HHHH', 0x3243, 0xA205, 8, 0)):
            with self.assertRaises(ZoneProtocolError):
                parse_player_define_request(invalid)

    def test_empty_response_has_no_settings_payload_or_mutable_inputs(self):
        """响应匹配独立当前DLL黄金摘要，仅含8100和零计数，没有自由文本或持久化设置入口。"""
        response = build_research_empty_player_define_response(profile=ZONE_EMPTY_PLAYER_DEFINE_PROFILE)
        self.assertEqual(response, bytes.fromhex('3243a20500080006810000000000'))
        self.assertEqual(len(response), 14)
        self.assertEqual(hashlib.sha256(response).hexdigest(),
                         '5568769e481a4c956bc5376f80cbb7c4614c8a311f3175f15275fb288e113080')
        with self.assertRaises(TypeError):
            build_research_empty_player_define_response(profile=ZONE_EMPTY_PLAYER_DEFINE_PROFILE, data=b'x')

    def test_empty_response_requires_exact_explicit_profile(self):
        """默认、其他版本和隐式类型都不能启用空资料兼容行为。"""
        with self.assertRaises(TypeError):
            build_research_empty_player_define_response()
        for profile in (None, True, '', ZONE_EMPTY_PLAYER_DEFINE_PROFILE.encode(),
                        ZONE_EMPTY_PLAYER_DEFINE_PROFILE + '-other'):
            with self.assertRaisesRegex(ZoneProtocolError, '^UNSUPPORTED_EMPTY_PLAYER_DEFINE_PROFILE$'):
                build_research_empty_player_define_response(profile=profile)


class NativeZoneTests(unittest.TestCase):
    """验证真实通知的当前 DLL 回环摘要、有界计数、未知业务隔离和密钥内容不会出现在结果中。"""

    @staticmethod
    def packet(body, *, command=0x1250):
        """构造独立测试包，保持声明长度一致，以便分别测试包头和包体边界。"""
        return struct.pack('>HHHH', 0x3243, command, 8, len(body)) + body

    def test_observed_notification_matches_current_native_roundtrip_digest(self):
        """boot19第二连接实包经当前 DLL probe11严格往返，独立11B夹具须匹配其公开摘要。"""
        payload = bytes.fromhex('3243125000080003000100')
        self.assertEqual(hashlib.sha256(payload).hexdigest(),
                         '411481f88d63ea7ac96ed7db0d264d023d53243acd31f01e0149fb6af7b469ca')
        self.assertEqual(parse_netbar_key_notification(payload), {
            'zone_profile': ZONE_NETBAR_KEY_PROFILE, 'metadata_sha256': ZONE_METADATA_SHA256,
            'magic': 0x3243, 'command': 0x1250, 'head_length': 8, 'body_length': 3,
            'body_validation': 'VALIDATED_STRUCTURE_ONLY', 'message': 'C2ZN_NtfNetBarKey',
            'key_length': 1,
        })

    def test_zero_binary_and_maximum_key_arrays_use_metadata_capacity(self):
        """计数0、内含NUL和512字节最大值按数组解析，避免把抓包的1字节或C字符串当通用约束。"""
        for key in (b'', b'\0x\0', bytes(range(256)), bytes(range(256)) * 2):
            parsed = parse_netbar_key_notification(self.packet(struct.pack('>H', len(key)) + key))
            self.assertEqual(parsed['key_length'], len(key))
            self.assertEqual(parsed['body_length'], len(key) + 2)
        for key_length in (513, 65535):
            with self.assertRaisesRegex(ZoneProtocolError, '^INVALID_ZONE_NETBAR_KEY_LENGTH$'):
                parse_netbar_key_notification(self.packet(struct.pack('>H', key_length)))

    def test_unknown_commands_are_header_observations_not_validated_bodies(self):
        """通用头只服务有限抓包记录；未知命令、登录和响应均不能通过NetBarKey业务解析。"""
        for command in (0, 0x1251, 0xA000, 0xA001, 0xFFFF):
            payload = self.packet(b'\0\1\0', command=command)
            summary = parse_zone_header(payload)
            self.assertEqual(summary['zone_profile'], ZONE_HEADER_PROFILE)
            self.assertEqual(summary['body_validation'], 'UNVALIDATED')
            self.assertNotIn('message', summary)
            self.assertNotIn('key_length', summary)
            with self.assertRaisesRegex(ZoneProtocolError, '^UNSUPPORTED_ZONE_MESSAGE$'):
                parse_netbar_key_notification(payload)

    def test_bad_types_truncated_frames_extra_and_joined_packets_are_rejected(self):
        """只接收一个完整bytes包；截断、拼接、尾随以及隐式类型转换全部拒绝。"""
        payload = bytes.fromhex('3243125000080003000100')
        for invalid in (None, payload.hex(), bytearray(payload), memoryview(payload),
                        *(payload[:index] for index in range(len(payload))),
                        payload + b'\0', payload + payload):
            with self.subTest(input_type=type(invalid).__name__), self.assertRaises(ZoneProtocolError):
                parse_netbar_key_notification(invalid)

    def test_magic_endian_and_declared_header_lengths_are_strict(self):
        """不同协议或端序不能兼容猜测，声明头长必须8且体长须等于完整剩余字节。"""
        for payload in (
            struct.pack('<HHHH', 0x3243, 0x1250, 8, 3) + b'\0\1\0',
            struct.pack('>HHHH', 0x8202, 0x1250, 8, 3) + b'\0\1\0',
        ):
            with self.assertRaisesRegex(ZoneProtocolError, '^UNSUPPORTED_ZONE_MAGIC$'):
                parse_netbar_key_notification(payload)
        for head, body in ((0, 3), (7, 4), (9, 2), (8, 2), (8, 4), (65535, 65535)):
            with self.assertRaisesRegex(ZoneProtocolError, '^INVALID_ZONE_HEADER_LENGTHS$'):
                parse_netbar_key_notification(struct.pack('>HHHH', 0x3243, 0x1250, head, body) + b'\0\1\0')

    def test_key_count_must_explain_every_body_byte(self):
        """头部长度合法也不能掩盖密钥数组的缺失或尾随字节，不按实际余长修正不可信计数。"""
        for body in (b'', b'\0', b'\0\1', b'\0\0x', b'\0\1xy', b'\0\3xy'):
            with self.assertRaisesRegex(ZoneProtocolError, '^INVALID_ZONE_NETBAR_LENGTH$'):
                parse_netbar_key_notification(self.packet(body))

    def test_header_capture_has_an_exact_total_limit(self):
        """独立头摘要允许有限未知包体，边界等于64KiB可记录，超过一字节即拒绝。"""
        largest = self.packet(b'x' * (MAX_ZONE_PACKET_BYTES - 8), command=0xFFFF)
        self.assertEqual(parse_zone_header(largest)['body_length'], MAX_ZONE_PACKET_BYTES - 8)
        oversize = self.packet(b'x' * (MAX_ZONE_PACKET_BYTES - 7), command=0xFFFF)
        with self.assertRaisesRegex(ZoneProtocolError, '^INVALID_ZONE_PACKET_LENGTH$'):
            parse_zone_header(oversize)
        with self.assertRaisesRegex(ZoneProtocolError, '^UNSUPPORTED_ZONE_MESSAGE$'):
            parse_netbar_key_notification(largest)

    def test_outputs_and_errors_never_contain_key_material(self):
        """通知可能含敏感网吧密钥；公开结果只允许头、profile、固定消息名及长度，异常不带输入。"""
        key = b'TEST_ONLY_PRIVATE_NETBAR_KEY_7f3a'
        parsed = parse_netbar_key_notification(self.packet(struct.pack('>H', len(key)) + key))
        public = json.dumps(parsed, sort_keys=True)
        self.assertNotIn(key.decode('ascii'), public)
        self.assertNotIn(key.hex(), public)
        self.assertNotIn(hashlib.sha256(key).hexdigest(), public)
        self.assertEqual(set(parsed), {'zone_profile', 'metadata_sha256', 'magic', 'command',
                         'head_length', 'body_length', 'body_validation', 'message', 'key_length'})
        with self.assertRaisesRegex(ZoneProtocolError, '^INVALID_ZONE_NETBAR_LENGTH$') as caught:
            parse_netbar_key_notification(self.packet(struct.pack('>H', len(key) - 1) + key))
        self.assertNotIn(key.decode('ascii'), str(caught.exception))


class NativeZoneLoginTests(unittest.TestCase):
    """核对当前客户端真实登录请求的独立摘要，保留全部原始值但不推断账号身份或业务许可。"""

    @staticmethod
    def packet(sub_channel=0xFFFFFFFF, preference_crc=0, settings_crc=704643316,
               zone_ip=0x0100007F, platform=0):
        """生成当前已证布局的纯测试包；默认值来自boot20，摘要再独立绑定真实DLL往返证据。"""
        return struct.pack('>HHHHIIIIH', 0x3243, 0xA000, 8, 18,
                           sub_channel, preference_crc, settings_crc, zone_ip, platform)

    def test_captured_login_request_matches_current_dll_roundtrip_digest(self):
        """26B真实DEV身份登录请求经当前DLL往返一致；所有非身份字段与观察值一致。"""
        payload = self.packet()
        self.assertEqual(hashlib.sha256(payload).hexdigest(),
                         '4c00864169617cf28adfedffc566c8e6867471d1836e6546a44548679b106a38')
        self.assertEqual(parse_login_request(payload), {
            'zone_profile': ZONE_LOGIN_REQUEST_PROFILE, 'metadata_sha256': ZONE_METADATA_SHA256,
            'magic': 0x3243, 'command': 0xA000, 'head_length': 8, 'body_length': 18,
            'body_validation': 'VALIDATED_STRUCTURE_ONLY', 'message': 'C2ZN_ReqLogin',
            'sub_channel_id': 0xFFFFFFFF, 'preference_crc_value': 0,
            'system_setting_crc_value': 704643316, 'current_zone_host_ip_wire_bytes': '0100007f',
            'current_zone_host_ip_dword': 0x0100007F, 'login_platform': 0,
        })

    def test_numeric_boundaries_remain_raw_without_sentinel_or_platform_inference(self):
        """不把FFFFFFFF改写为-1，也不将未知平台过滤为授权选择；每个字段按已证宽度完整保留。"""
        for value, platform in ((0, 0), (0x80000000, 4), (0xFFFFFFFF, 65535)):
            parsed = parse_login_request(self.packet(value, value, value, value, platform))
            for key in ('sub_channel_id', 'preference_crc_value', 'system_setting_crc_value',
                        'current_zone_host_ip_dword'):
                self.assertEqual(parsed[key], value)
            self.assertEqual(parsed['current_zone_host_ip_wire_bytes'], struct.pack('>I', value).hex())
            self.assertEqual(parsed['login_platform'], platform)

    def test_login_body_must_be_exactly_eighteen_bytes(self):
        """即使通用头声明自洽，正文只接受18字节；不容忍截断、扩展或重复消息。"""
        payload = self.packet()
        for size in (0, 1, 17, 19, 36):
            malformed = struct.pack('>HHHH', 0x3243, 0xA000, 8, size) + b'\0' * size
            with self.assertRaisesRegex(ZoneProtocolError, '^INVALID_ZONE_LOGIN_LENGTH$'):
                parse_login_request(malformed)
        for malformed in (*(payload[:index] for index in range(len(payload))), payload + payload):
            with self.assertRaises(ZoneProtocolError):
                parse_login_request(malformed)

    def test_login_rejects_other_commands_types_and_protocol_headers(self):
        """拒绝通知、响应和未知消息，避免只凭18字节长度将其他命令解为登录。"""
        payload = self.packet()
        for command in (0x1250, 0xA001, 0xFFFF):
            with self.assertRaisesRegex(ZoneProtocolError, '^UNSUPPORTED_ZONE_MESSAGE$'):
                parse_login_request(payload[:2] + struct.pack('>H', command) + payload[4:])
        for malformed in (None, payload.hex(), bytearray(payload),
                          b'\x82\x02' + payload[2:], payload[:4] + b'\0\7' + payload[6:]):
            with self.assertRaises(ZoneProtocolError):
                parse_login_request(malformed)

    def test_login_parser_does_not_fabricate_identity_or_success(self):
        """请求中没有邮箱、密码或Uin；解析结果不能带认证成功、建角完成或大厅完成的推断。"""
        result = parse_login_request(self.packet())
        for key in ('email', 'password', 'uin', 'account_id', 'game_id', 'authorized', 'authenticated',
                    'success', 'login_success', 'character_ready', 'lobby_ready'):
            self.assertNotIn(key, result)
        self.assertEqual(result['body_validation'], 'VALIDATED_STRUCTURE_ONLY')


class NativeZoneLoginResponseTests(unittest.TestCase):
    """用当前DLL独立golden验证固定研究响应；显式profile及说明副本保护未验证业务边界。"""

    def test_fixed_response_matches_current_native_golden(self):
        """当前DLL对完整host和独立body互证，零FreeProps数组被省略后应精确为41B而非最大结构长度。"""
        golden = bytes.fromhex(
            '3243a00100080021810000000001000000010000000000000000000000000000000000000000000000')
        response = build_research_login_response(profile=ZONE_LOGIN_RESPONSE_PROFILE)
        self.assertEqual(response, golden)
        self.assertEqual(len(response), 41)
        self.assertEqual(hashlib.sha256(response).hexdigest(),
                         '71c6d9b507ea51733717213d306cc2d587960275fae747474683b89515aafc2c')
        self.assertEqual(struct.unpack_from('>HHHH', response), (0x3243, 0xA001, 8, 33))

    def test_explicit_compat_clock_preserves_other_fields_and_bounds(self):
        """兼容日历只替换64位时间；拒绝毫秒、零时刻及隐式profile，旧研究夹具保持不变。"""
        from adapters.cn_current.zone import build_clock_login_response,ZONE_CLOCK_COMPAT_PROFILE
        original=build_research_login_response(profile=ZONE_LOGIN_RESPONSE_PROFILE)
        wire=build_clock_login_response(server_time=2208988800,profile=ZONE_CLOCK_COMPAT_PROFILE)
        self.assertEqual(wire[:18]+wire[26:],original[:18]+original[26:])
        self.assertEqual(struct.unpack_from('>Q',wire,18)[0],2208988800)
        for value in (None,True,0,1577836799,4102444800,2208988800000):
            with self.assertRaises(ZoneProtocolError):
                build_clock_login_response(server_time=value,profile=ZONE_CLOCK_COMPAT_PROFILE)
        with self.assertRaises(ZoneProtocolError):
            build_clock_login_response(server_time=2208988800,profile=ZONE_LOGIN_RESPONSE_PROFILE)

    def test_response_requires_the_explicit_exact_research_profile(self):
        """生产或其他研究profile不能隐式启用该固定响应，调用方也不能传入任意业务字段。"""
        with self.assertRaises(TypeError):
            build_research_login_response()
        for profile in (None, True, 1, ZONE_LOGIN_RESPONSE_PROFILE.encode(), '', ZONE_LOGIN_REQUEST_PROFILE,
                        ZONE_HEADER_PROFILE, ZONE_NETBAR_KEY_PROFILE, ZONE_LOGIN_RESPONSE_PROFILE + '-other'):
            with self.assertRaisesRegex(ZoneProtocolError, '^UNSUPPORTED_ZONE_LOGIN_RESPONSE_PROFILE$'):
                build_research_login_response(profile=profile)
        with self.assertRaises(TypeError):
            build_research_login_response(profile=ZONE_LOGIN_RESPONSE_PROFILE, game_id=123)

    def test_compatibility_is_detached_and_does_not_claim_wallet_or_lobby(self):
        """修改展示说明不改变下一次编码；成功分支证据不扩张成角色、钱包和大厅验收。"""
        before = build_research_login_response(profile=ZONE_LOGIN_RESPONSE_PROFILE)
        details = login_response_compatibility()
        self.assertEqual(details['profile'], ZONE_LOGIN_RESPONSE_PROFILE)
        self.assertTrue(details['native_serializer_verified'])
        self.assertTrue(details['current_success_branch_verified'])
        for field in ('original_business_rules_verified', 'character_state_projected',
                      'wallet_or_inventory_projected', 'lobby_verified'):
            self.assertFalse(details[field])
        self.assertEqual(details['server_time_calibration'], 'SKIPPED_BY_VERIFIED_ZERO_BRANCH')
        details['research_values']['free_prop_count'] = 256
        details['result_macro']['value'] = 0
        details['lobby_verified'] = True
        fresh = login_response_compatibility()
        self.assertEqual(fresh['research_values']['free_prop_count'], 0)
        self.assertEqual(fresh['result_macro']['value'], 0x8100)
        self.assertFalse(fresh['lobby_verified'])
        self.assertEqual(before, build_research_login_response(profile=ZONE_LOGIN_RESPONSE_PROFILE))
