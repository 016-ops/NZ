"""邮箱启动器传输边界与当前 IPC 编码回归；普通工具测试不依赖游戏文件或数据库。"""
import http.server
from contextlib import redirect_stderr
import io
import json
import struct
import sys
import threading
from unittest import TestCase
from unittest.mock import Mock, patch
from adapters.cn_current.email_ipc import research_mapping
from tools.email_launcher import Client, LoginError


class EmailToolTests(TestCase):
    """防止把邮箱密码发送到 URL 中的外部主机、跳转目标或非本项目原生组件。"""
    def test_zone_reply_requires_explicit_capture_and_verified_email_before_launch(self):
        """研究登录响应不能由缺失前置模式或匿名 CLI 启用，参数拒绝发生在读凭据与启动游戏之前。"""
        from tools import native_email_boot
        base = ['native_email_boot', '--workspace', 'not-used', '--output', 'not-used']
        capture = ['--verify-native-challenge', '--capture-application', '--serve-geo-ping',
                   '--serve-geo-zone', '--capture-zone-sequence']
        for options in (['--serve-zone-login'], ['--serve-player-define'], capture + ['--serve-player-define'], capture + ['--serve-zone-login'],
                        ['--dev-email-state', '--serve-zone-login']):
            with self.subTest(options=options), patch.object(sys, 'argv', base + options), \
                    patch.object(native_email_boot, 'probe') as start, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    native_email_boot.main()
                self.assertEqual(result.exception.code, 2)
                start.assert_not_called()

    def test_native_capture_retains_rejected_header_and_has_total_deadline(self):
        """失败头仍有私人证据；每次分片使用剩余总期限，不能反复重置超时而无限等待。"""
        from tools.native_email_boot import CapturedFrameStream
        connection=Mock()
        connection.recv.side_effect=[b'header',b'body']
        with patch('tools.native_email_boot.time.monotonic',side_effect=[100,101,104,106]):
            stream=CapturedFrameStream(connection,seconds=5)
            self.assertEqual(stream.recv(12),b'header')
            self.assertEqual(stream.recv(16),b'body')
            with self.assertRaises(TimeoutError):
                stream.recv(1)
        self.assertEqual(bytes(stream.content),b'headerbody')
        self.assertEqual([call.args for call in connection.settimeout.call_args_list],[(4,),(1,)])
        self.assertEqual(connection.recv.call_count,2)

    def test_native_capture_rejects_over_budget_before_socket_read(self):
        """任何超出 64KiB 的后续读取在触碰 socket 前失败，不能扩大诊断缓存。"""
        from tools.native_email_boot import CapturedFrameStream
        connection=Mock()
        connection.recv.return_value=b'x'*65536
        stream=CapturedFrameStream(connection)
        self.assertEqual(len(stream.recv(65536)),65536)
        with self.assertRaises(ValueError):
            stream.recv(1)
        self.assertEqual(connection.recv.call_count,1)

    def test_remote_urls_credentials_paths_and_implicit_ports_are_rejected(self):
        """启动器只使用明确端口的本机/SSH 隧道，不接受外部 URL、URL 密码或路径。"""
        self.assertEqual(Client('http://127.0.0.1:18765').port, 18765)
        for url in ('http://example.test:80', 'https://127.0.0.1:123', 'http://user:password@127.0.0.1:123',
                    'http://127.0.0.1', 'http://127.0.0.1:123/path', 'http://127.0.0.1:123/?token=secret'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                Client(url)

    def test_http_redirect_is_not_followed_and_error_message_does_not_echo_password(self):
        """真实 HTTP 返回跳转时不转发登录正文，服务端错误 message 也不能回显密码到 CLI。"""
        received = []
        class Redirect(http.server.BaseHTTPRequestHandler):
            """记录本机请求数量，模拟携带敏感错误说明和外部 Location 的错误服务。"""
            def do_POST(self):
                """读取本次测试正文后返回 302，不创建任何真实外部连接。"""
                self.rfile.read(int(self.headers['Content-Length']))
                received.append(self.path)
                body = json.dumps({'error': 'REDIRECT_REFUSED', 'message': 'sensitive-password-marker'}).encode()
                self.send_response(302)
                self.send_header('Location', '/should-never-be-requested')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, format, *args):
                """禁止 HTTP 基类输出请求信息，测试不会把模拟密码写入控制台。"""
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Redirect)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(LoginError) as error:
                Client(f'http://127.0.0.1:{server.server_port}').request('/api/player/login', {'password': 'sensitive-password-marker'})
            self.assertEqual(str(error.exception), 'REDIRECT_REFUSED')
            self.assertEqual(received, ['/api/player/login'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_ipc_fields_are_bounded_and_do_not_include_network_server_records(self):
        """编码的本地 ID/令牌和偏移均受界限约束；研究格式的服务器列表始终为空。"""
        token = 'independent_token_123'
        encoded = research_mapping(10002, token)
        self.assertLess(len(encoded), 65536)
        self.assertEqual(struct.unpack_from('<I', encoded)[0], 32)
        values = {}
        for tag in range(1, 33):
            offset = struct.unpack_from('<I', encoded, tag * 4)[0]
            self.assertGreaterEqual(offset, 132)
            length = struct.unpack_from('<I', encoded, offset)[0]
            self.assertLessEqual(offset + 4 + length, len(encoded))
            values[tag] = encoded[offset + 4:offset + 4 + length]
        # 当前 TGame 消费代码已证实 tag2 才是账号 ID；tag1 仅保留明确标记的研究 ServiceId。
        self.assertEqual(struct.unpack('<I', values[1])[0], 1)
        self.assertEqual(struct.unpack('<I', values[2])[0], 10002)
        self.assertEqual(values[4], token.encode())
        from adapters.cn_current.email_ipc import local_session_id
        self.assertEqual(values[5], local_session_id(token))
        self.assertNotEqual(values[5], local_session_id(token + '_other'))
        self.assertEqual(values[3], bytes([len(token)]))
        self.assertEqual(struct.unpack('<I', values[17])[0], 0)
        for game_id, bad_token in ((True, token), (0, token), (2147483648, token), (1, 'x' * 129), (1, 'x\0y')):
            with self.assertRaises(ValueError):
                research_mapping(game_id, bad_token)

    def test_loopback_server_primary_endpoint_uses_current_consumed_subfields(self):
        """当前游戏消费 sub2/sub3 作为主端点；嵌套表必须有界并把回环地址放在已证位置。"""
        port = 41237
        encoded = research_mapping(10002, 'independent_token_123', loopback_port=port,
                                   client_path='C:/fixture/nz')
        outer = struct.unpack_from('<I', encoded, 20 * 4)[0]
        table = struct.unpack_from('<I', encoded, outer)[0]
        self.assertLessEqual(table + 8 * 4, len(encoded))
        fields = {}
        for subfield in range(1, 9):
            offset = struct.unpack_from('<I', encoded, table + (subfield - 1) * 4)[0]
            self.assertGreaterEqual(offset, table + 8 * 4)
            length = struct.unpack_from('<I', encoded, offset)[0]
            self.assertLessEqual(offset + 4 + length, len(encoded))
            fields[subfield] = encoded[offset + 4:offset + 4 + length]
        self.assertEqual(fields[2], '127.0.0.1\0'.encode('utf-16-le'))
        self.assertEqual(struct.unpack('<I', fields[3])[0], port)
        # 第二组端点含义未证实；保持回环约束，不能把工具断言当作该字段业务语义已验收。
        self.assertEqual(fields[4], '127.0.0.1\0'.encode('utf-16-le'))
        self.assertEqual(struct.unpack('<I', fields[5])[0], port)
        for invalid_port in (True, 0, 1023, 65536):
            with self.subTest(port=invalid_port), self.assertRaises(ValueError):
                research_mapping(10002, 'independent_token_123', loopback_port=invalid_port)

    def test_loopback_client_path_obeys_utf16_byte_and_character_limits(self):
        """包含非 BMP 字符的路径按含终止符的 UTF16 字节数验界，仍保留既有 250 字符限制。"""
        boundary = '\U0001f600' * 129 + 'a'
        encoded = research_mapping(10002, 'independent_token_123', loopback_port=41237,
                                   client_path=boundary)
        offset = struct.unpack_from('<I', encoded, 19 * 4)[0]
        length = struct.unpack_from('<I', encoded, offset)[0]
        self.assertEqual(length, 0x208)
        self.assertEqual(encoded[offset + 4:offset + 4 + length], (boundary + '\0').encode('utf-16-le'))
        for invalid_path in ('\U0001f600' * 130, '\U0001f600' * 250, 'a' * 251):
            with self.subTest(characters=len(invalid_path)), self.assertRaises(ValueError):
                research_mapping(10002, 'independent_token_123', loopback_port=41237,
                                 client_path=invalid_path)
