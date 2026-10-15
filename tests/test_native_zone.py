"""当前 Zone 包头、NetBarKey 和登录请求边界；使用公开摘要及测试字节，不运行 DLL、游戏或数据库。"""
import hashlib
import json
import struct
import unittest

from adapters.cn_current.zone import (
    ZONE_EMPTY_PLAYER_DEFINE_PROFILE, build_research_empty_player_define_response, parse_player_define_request,
    MAX_ZONE_PACKET_BYTES, ZONE_HEADER_PROFILE, ZONE_LOGIN_REQUEST_PROFILE, ZONE_LOGIN_RESPONSE_PROFILE,
    ZONE_METADATA_SHA256, ZONE_NETBAR_KEY_PROFILE,
    ZoneProtocolError, build_research_login_response, login_response_compatibility,
    parse_login_request, parse_netbar_key_notification, parse_zone_header,
)


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
