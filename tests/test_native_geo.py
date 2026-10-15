"""当前 GEO 请求解析与研究响应边界；用当前 DLL 独立 golden 校验，不运行游戏或发送数据。"""
import hashlib
import struct
import unittest

from adapters.cn_current.geo import (
    GEO_PING_PROFILE, GEO_PING_RESPONSE_PROFILE, GEO_ZONE_REQUEST_PROFILE, GEO_ZONE_RESPONSE_PROFILE,
    GeoProtocolError, build_research_ping_response, build_research_zone_response,
    parse_ping_request, parse_zone_request, ping_response_compatibility, zone_response_compatibility,
)


class NativeGeoTests(unittest.TestCase):
    """验证首包严格解析、研究响应独立 golden、显式启用及展示层不能改变编码的边界。"""

    def test_current_observed_ping_request_matches_published_digest(self):
        """固定 9 字节夹具匹配两次当前实机首包公开摘要，解析结果只包含已证请求字段。"""
        payload = bytes.fromhex('820210010008000100')
        self.assertEqual(hashlib.sha256(payload).hexdigest(),
                         '62c85c113786fadf47bfcb66180e9ba50eb1a443f051fbb02d2681618cda5324')
        self.assertEqual(parse_ping_request(payload), {
            'geo_profile': GEO_PING_PROFILE, 'message': 'C2GEO_ReqPingList', 'magic': 0x8202,
            'command': 0x1001, 'head_length': 8, 'body_length': 1, 'is_force_login': 0,
        })

    def test_truncated_extra_and_wrong_input_types_are_rejected(self):
        """必须是单个完整 bytes 请求；截断、尾随数据、拼接帧或其他类型都不能部分解析。"""
        payload = bytes.fromhex('820210010008000100')
        for invalid in ([payload[:length] for length in range(9)] +
                        [payload + b'\0', payload + payload, bytearray(payload), payload.hex(), None]):
            with self.subTest(input_type=type(invalid).__name__), self.assertRaisesRegex(
                    GeoProtocolError, 'INVALID_GEO_PING_LENGTH'):
                parse_ping_request(invalid)

    def test_unknown_magic_commands_and_little_endian_are_rejected(self):
        """拒绝不同协议、响应、ZoneList及小端变体；仅承认当前已证的大端 PingREQ。"""
        cases = [struct.pack('>HHHHB', magic, command, 8, 1, 0)
                 for magic, command in ((0, 0x1001), (0x8203, 0x1001), (0x8202, 0x1000),
                                        (0x8202, 0x2001), (0x8202, 0xFFFF))]
        cases.append(struct.pack('<HHHHB', 0x8202, 0x1001, 8, 1, 0))
        for payload in cases:
            with self.assertRaisesRegex(GeoProtocolError, 'UNSUPPORTED_GEO_MESSAGE'):
                parse_ping_request(payload)

    def test_declared_lengths_must_match_observed_header_and_body(self):
        """即使实收9字节，声明长度不符也必须拒绝，不按总长度相同或剩余字节猜结构。"""
        for head_length, body_length in ((0, 1), (7, 2), (9, 0), (8, 0), (8, 2), (65535, 65535)):
            with self.subTest(head=head_length, body=body_length), self.assertRaisesRegex(
                    GeoProtocolError, 'INVALID_GEO_PING_HEADER_LENGTHS'):
                parse_ping_request(struct.pack('>HHHHB', 0x8202, 0x1001, head_length, body_length, 0))

    def test_force_login_byte_is_preserved_without_boolean_or_authorization_inference(self):
        """已证单字节语法的边界值原样保留；没有把未知非零值变为强制登录许可的分支。"""
        for value in (0, 1, 2, 127, 255):
            parsed = parse_ping_request(bytes.fromhex('8202100100080001') + bytes([value]))
            self.assertIs(type(parsed['is_force_login']), int)
            self.assertEqual(parsed['is_force_login'], value)
            self.assertNotIn('authorized', parsed)

    def test_research_response_matches_current_native_dll_golden(self):
        """独立当前 DLL 的 pack/unpack 样本固定 52 字节；捕获字符串、IPv4与 RankInfo 跨版本差异。"""
        # 来源：当前 DLL 离线探针 09；先独立打包 body 后回填长度，完整包回解和再打包均通过。
        # 这些是固定回环研究字段，不含账号、令牌或私有网络地址，不由被测 Python builder 生成夹具。
        golden = bytes.fromhex(
            '820220010008002c8b000001000000000100007f0000000a3132372e302e302e3100'
            '00000000000003e800000000000000000000')
        response = build_research_ping_response(profile=GEO_PING_RESPONSE_PROFILE)
        self.assertEqual(response, golden)
        self.assertEqual(hashlib.sha256(response).hexdigest(),
                         'b67c020800b28039cd7ef19d31f7fa71a003e4f6ae16c6085b46ccf7944da8c2')
        self.assertEqual(struct.unpack_from('>HHHH', response), (0x8202, 0x2001, 8, 44))

    def test_research_response_requires_explicit_exact_profile(self):
        """研究响应不能默认启用或冒充已验证生产协议；未知 profile 和非字符串全部固定错误拒绝。"""
        with self.assertRaises(TypeError):
            build_research_ping_response()
        for profile in (None, True, 1, b'nz-current-geo-ping-loopback-research-v1', '',
                        GEO_PING_PROFILE, 'cn-current-unverified', GEO_PING_RESPONSE_PROFILE + '-unknown'):
            with self.subTest(profile_type=type(profile).__name__), self.assertRaisesRegex(
                    GeoProtocolError, '^UNSUPPORTED_GEO_PING_RESPONSE_PROFILE$'):
                build_research_ping_response(profile=profile)

    def test_public_compatibility_values_cannot_change_future_encoding(self):
        """外部展示层修改兼容说明不会改变下一次目录响应或伪造运行验收状态，固定端点始终为本机。"""
        before = build_research_ping_response(profile=GEO_PING_RESPONSE_PROFILE)
        description = ping_response_compatibility()
        self.assertFalse(description['original_business_rules_verified'])
        self.assertFalse(description['client_address_consumption_verified'])
        self.assertFalse(description['zone_response_in_this_profile'])
        self.assertEqual(description['research_values']['server_count'], 1)
        self.assertEqual(description['research_values']['domain'], '127.0.0.1')
        description['research_values']['server_count'] = 192
        description['research_values']['domain'] = 'untrusted.invalid'
        description['original_business_rules_verified'] = True
        after = ping_response_compatibility()
        self.assertEqual(after['research_values']['server_count'], 1)
        self.assertEqual(after['research_values']['domain'], '127.0.0.1')
        self.assertFalse(after['original_business_rules_verified'])
        self.assertEqual(before, build_research_ping_response(profile=GEO_PING_RESPONSE_PROFILE))


class NativeGeoZoneRequestTests(unittest.TestCase):
    """验证实际 ZoneREQ 摘要及元数据数组/字符串边界，防止按单条抓包硬编码或越界取字段。"""

    @staticmethod
    def packet(domains, *, count=None, delay=1000):
        """构造纯测试输入；端点字段不参与连接，Domain 参数含 NUL 以便覆盖畸形和最大值。"""
        body = struct.pack('>H', len(domains) if count is None else count)
        for domain in domains:
            body += struct.pack('>III', 0, 0x0100007F, len(domain)) + domain + struct.pack('>I', 1)
        body += struct.pack('>I', delay)
        return struct.pack('>HHHH', 0x8202, 0x1000, 8, len(body)) + body

    def test_current_zone_request_matches_independent_observed_digest(self):
        """第18次客户端消费 PingRES 后发出40字节 ZoneREQ；独立摘要验证字段与计时测量未被改写。"""
        payload = self.packet([b'127.0.0.1\0'])
        self.assertEqual(len(payload), 40)
        self.assertEqual(hashlib.sha256(payload).hexdigest(),
                         '4e9b0f9151539016d31e14d3b0957340f1e2ecd60cb5708530a0ab8165ad44a3')
        parsed = parse_zone_request(payload)
        self.assertEqual(parsed['geo_profile'], GEO_ZONE_REQUEST_PROFILE)
        self.assertEqual(parsed['server_count'], 1)
        self.assertEqual(parsed['servers'], [{'group': 0, 'ipv4_wire_bytes': '0100007f',
                         'ipv4_host_dword': 0x0100007F, 'domain': '127.0.0.1', 'ping_in_ms': 1}])
        self.assertEqual(parsed['max_delay_in_ms'], 1000)

    def test_zero_multiple_and_maximum_metadata_counts_are_not_fixed_to_capture_length(self):
        """合法0/2/192条及每条最大128字节Domain均可解析；数值只保留，不推断授权或容量配置。"""
        for domains in ([], [b'\0', b'127.0.0.1\0'], [b'a' * 127 + b'\0'] * 192):
            payload = self.packet(domains, delay=0xFFFFFFFF)
            parsed = parse_zone_request(payload)
            self.assertEqual(parsed['server_count'], len(domains))
            self.assertEqual(len(parsed['servers']), len(domains))
            self.assertEqual(parsed['max_delay_in_ms'], 0xFFFFFFFF)
        for count in (193, 65535):
            with self.assertRaisesRegex(GeoProtocolError, '^INVALID_GEO_ZONE_SERVER_COUNT$'):
                parse_zone_request(self.packet([], count=count))

    def test_zone_request_rejects_wrong_type_headers_truncation_and_trailing_bytes(self):
        """声明长度、命令、实收长度和消费终点必须一致；截断任意位置不能冒充完整请求。"""
        payload = self.packet([b'127.0.0.1\0'])
        for malformed in (None, payload.hex(), bytearray(payload), payload + payload,
                          *(payload[:length] for length in range(len(payload)))):
            with self.assertRaises(GeoProtocolError):
                parse_zone_request(malformed)
        for magic, command, head, body in ((0, 0x1000, 8, 32), (0x8202, 0x1001, 8, 32),
                                           (0x8202, 0x2000, 8, 32), (0x8202, 0x1000, 7, 33),
                                           (0x8202, 0x1000, 8, 31)):
            with self.assertRaises(GeoProtocolError):
                parse_zone_request(struct.pack('>HHHH', magic, command, head, body) + payload[8:])
        extra = payload + b'\0'
        extra = extra[:6] + struct.pack('>H', len(extra) - 8) + extra[8:]
        with self.assertRaisesRegex(GeoProtocolError, '^TRAILING_GEO_ZONE_DATA$'):
            parse_zone_request(extra)

    def test_zone_domain_rejects_unproven_lengths_termination_and_encoding(self):
        """Domain长度须含唯一末尾NUL且不超过metadata容量；拒绝未知编码和控制符，不宽松截断。"""
        for domain in (b'', b'a' * 128 + b'\0', b'no-null', b'a\0b\0', b'\xff\0', b'\n\0'):
            with self.subTest(size=len(domain)), self.assertRaises(GeoProtocolError):
                parse_zone_request(self.packet([domain]))
        payload = self.packet([b'127.0.0.1\0'])
        for length in (0, 128, 129, 0xFFFFFFFF):
            malformed = payload[:18] + struct.pack('>I', length) + payload[22:]
            with self.assertRaises(GeoProtocolError):
                parse_zone_request(malformed)

    def test_zone_request_rejects_count_that_disagrees_with_serialized_entries(self):
        """数组计数不足会留下尾随数据，计数过大则无法完整读取；不扫描猜测真实条目数。"""
        for payload in (self.packet([b'127.0.0.1\0'], count=0),
                        self.packet([b'\0'], count=2),
                        self.packet([b'\0', b'\0'], count=1)):
            with self.assertRaises(GeoProtocolError):
                parse_zone_request(payload)


class NativeGeoZoneResponseTests(unittest.TestCase):
    """验证当前 DLL 的独立 ZoneRES golden、固定回环拓扑及可变监听端口的严格范围。"""

    def test_zone_response_matches_independent_current_native_golden(self):
        """当前DLL probe10独立生成70字节/62字节body；完整字段顺序及六个LoadExts必须精确保持。"""
        golden = bytes.fromhex(
            '820220000008003e8b0000010100007f0000000a3132372e302e302e3100b26e'
            '0000000100000001000000010000000000000000000000000000000000000000000000000001')
        response = build_research_zone_response(port=45678, profile=GEO_ZONE_RESPONSE_PROFILE)
        self.assertEqual(response, golden)
        self.assertEqual(hashlib.sha256(response).hexdigest(),
                         '520b80a6dcf77b4ab830dee4f7b11b5defdaa226c28735db3c7cbf591d0905d8')
        self.assertEqual(struct.unpack_from('>HHHH', response), (0x8202, 0x2000, 8, 62))

    def test_zone_response_can_only_change_verified_port_field(self):
        """边界监听端口仍为BE16；改变端口不允许附带更改地址、目录数、研究业务值或包长度。"""
        reference = build_research_zone_response(port=45678, profile=GEO_ZONE_RESPONSE_PROFILE)
        for port in (1024, 18767, 65535):
            response = build_research_zone_response(port=port, profile=GEO_ZONE_RESPONSE_PROFILE)
            self.assertEqual(response[:30], reference[:30])
            self.assertEqual(response[32:], reference[32:])
            self.assertEqual(response[30:32], struct.pack('>H', port))

    def test_zone_response_rejects_implicit_profiles_and_invalid_port_types_or_ranges(self):
        """不隐式启用研究回复，拒绝bool、字符串、浮点和特权/溢出端口，避免端口被自动转换。"""
        with self.assertRaises(TypeError):
            build_research_zone_response(port=45678)
        for profile in (None, True, GEO_PING_RESPONSE_PROFILE, GEO_ZONE_REQUEST_PROFILE,
                        GEO_ZONE_RESPONSE_PROFILE.encode(), GEO_ZONE_RESPONSE_PROFILE + '-other'):
            with self.assertRaisesRegex(GeoProtocolError, '^UNSUPPORTED_GEO_ZONE_RESPONSE_PROFILE$'):
                build_research_zone_response(port=45678, profile=profile)
        for port in (None, False, True, '45678', 45678.0, -1, 0, 1023, 65536, 2**32):
            with self.subTest(port_type=type(port).__name__), self.assertRaisesRegex(
                    GeoProtocolError, '^INVALID_GEO_RESEARCH_ZONE_PORT$'):
                build_research_zone_response(port=port, profile=GEO_ZONE_RESPONSE_PROFILE)
            with self.assertRaisesRegex(GeoProtocolError, '^INVALID_GEO_RESEARCH_ZONE_PORT$'):
                zone_response_compatibility(port=port)

    def test_zone_compatibility_is_detached_and_does_not_claim_room_readiness(self):
        """一个区服条目保留多房能力且不宣称实例就绪；修改公开说明不能污染后续固定回复。"""
        before = build_research_zone_response(port=45678, profile=GEO_ZONE_RESPONSE_PROFILE)
        description = zone_response_compatibility(port=45678)
        self.assertFalse(description['room_node_available'])
        self.assertFalse(description['zone_login_verified'])
        self.assertTrue(description['same_node_multiple_rooms_retained'])
        self.assertEqual(description['research_values']['zone_count'], 1)
        self.assertEqual(description['research_values']['domain'], '127.0.0.1')
        description['research_values']['load_exts'][0] = 900
        description['research_values']['zone_count'] = 16
        fresh = zone_response_compatibility(port=45678)
        self.assertEqual(fresh['research_values']['load_exts'], [0] * 6)
        self.assertEqual(fresh['research_values']['zone_count'], 1)
        self.assertEqual(before, build_research_zone_response(port=45678, profile=GEO_ZONE_RESPONSE_PROFILE))
