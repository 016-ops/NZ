"""邮箱启动器传输边界与当前 IPC 编码回归；普通工具测试不依赖游戏文件或数据库。"""
import http.server
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import struct
import sys
import threading
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch
from adapters.cn_current.email_ipc import research_mapping
from tools.email_launcher import Client, LoginError


class EmailToolTests(TestCase):
    """防止把邮箱密码发送到 URL 中的外部主机、跳转目标或非本项目原生组件。"""
    def test_large_inventory_reads_do_not_expand_authentication_response_limit(self):
        """只有本人库存读入口放宽到8MiB，超大库存及普通认证响应仍拒绝，避免无界内存读取。"""
        client=Client('http://127.0.0.1:18765')
        def transport(payload):
            """模拟有界HTTP读取，返回真实JSON字节，不执行网络或保存玩家信息。"""
            response=Mock(status=200)
            response.read.side_effect=lambda count:payload[:count]
            connection=Mock();connection.getresponse.return_value=response
            return connection
        payload=json.dumps({'items':['x'*100000]}).encode()
        for path in ('/api/views/me','/api/game/inventory/get'):
            with patch('http.client.HTTPConnection',return_value=transport(payload)):
                self.assertEqual(len(client.request(path)['items'][0]),100000)
        with patch('http.client.HTTPConnection',return_value=transport(payload)):
            with self.assertRaisesRegex(LoginError,'RESPONSE_TOO_LARGE'):
                client.request('/api/player/login',{})
        with patch('http.client.HTTPConnection',return_value=transport(b'x'*(8*1024*1024+1))):
            with self.assertRaisesRegex(LoginError,'RESPONSE_TOO_LARGE'):
                client.request('/api/game/inventory/get',{})

    def test_native_observation_limit_is_rejected_before_credentials_and_launch(self):
        """用户授权的最长观察为300秒；越界时先拒绝，不读验收凭据、不登录也不启动进程。"""
        from tools import native_email_boot
        for seconds in ('0','-1','301'):
            args=['native_email_boot','--workspace','not-used','--output','not-used',
                  '--dev-email-state','--seconds',seconds]
            with self.subTest(seconds=seconds), patch.object(sys,'argv',args), \
                    patch.object(Path,'read_text') as read_state, patch.object(native_email_boot,'probe') as launch, \
                    redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    native_email_boot.main()
                self.assertEqual(result.exception.code,2)
                read_state.assert_not_called()
                launch.assert_not_called()

    def test_manual_test_cannot_bypass_owned_session_requirements(self):
        """手动保留模式缺少真实库存、DEV身份或持续区服任一前置时，在读凭据和启动前拒绝。"""
        from tools import native_email_boot
        required=['--dev-email-state','--native-inventory','--continuous-zone']
        for missing in required:
            args=['native_email_boot','--workspace','not-used','--output','not-used','--manual-test']
            args.extend(flag for flag in required if flag!=missing)
            with patch.object(sys,'argv',args),patch.object(Path,'read_text') as read_state, \
                    patch.object(native_email_boot,'probe') as launch,redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    native_email_boot.main()
                self.assertEqual(result.exception.code,2)
                read_state.assert_not_called();launch.assert_not_called()

    def test_player_info_uses_same_fresh_identity_and_original_character_nickname(self):
        """同一次me身份/角色绑定通过后编码原昵称；不改写视图、不读取历史昵称或再次发邮件。"""
        from tools.native_email_boot import current_dev_player_info
        client=Mock()
        nickname='邮箱DEV_0123456789'
        view={'id':'owned','game_id':10001,'email_verified':True,
              'character':{'id':'character','account_id':'owned','nickname':nickname}}
        client.request.return_value=view
        proof,encoded=current_dev_player_info(client,'session',
            {'account_id':'owned','game_id':10001,'character_id':'character'},
            {'account_id':'owned','game_id':10001},code_page=936)
        self.assertEqual(encoded.decode('cp936'),nickname)
        self.assertEqual(view['character']['nickname'],nickname)
        self.assertTrue(proof['email_verified'])
        client.request.assert_called_once_with('/api/views/me',token='session')

    def test_player_info_rejects_unbound_character_before_encoding(self):
        """角色不存在、角色ID或所属账号不符时拒绝，不能借另一角色昵称绕过独立身份。"""
        from tools.native_email_boot import current_dev_player_info
        state={'account_id':'owned','game_id':10001,'character_id':'character'}
        identity={'account_id':'owned','game_id':10001}
        client=Mock()
        for character in (None,{'id':'other','account_id':'owned','nickname':'中文'},
                          {'id':'character','account_id':'other','nickname':'中文'}):
            client.request.return_value={'id':'owned','game_id':10001,'email_verified':True,'character':character}
            with self.subTest(character=character), self.assertRaisesRegex(ValueError,'CURRENT_DEV_CHARACTER_MISMATCH'):
                current_dev_player_info(client,'session',state,identity,code_page=936)

    def test_empty_props_requires_current_inventory_to_be_explicitly_empty(self):
        """存在任何真实库存或缺少读取字段时拒绝空终包；不能用该研究功能清空玩家资产显示。"""
        from tools.native_email_boot import current_dev_player_info
        client=Mock()
        view={'id':'owned','game_id':10001,'email_verified':True,
              'character':{'id':'character','account_id':'owned','nickname':'中文'}}
        for inventory in (None,{},[{'id':'real-item'}]):
            client.request.return_value=dict(view,inventory=inventory)
            with self.subTest(inventory=inventory), self.assertRaisesRegex(ValueError,'CURRENT_DEV_INVENTORY_NOT_EMPTY'):
                current_dev_player_info(client,'session',{'account_id':'owned','game_id':10001,'character_id':'character'},
                    {'account_id':'owned','game_id':10001},code_page=936,require_empty_inventory=True)
        client.request.return_value=dict(view,inventory=[])
        proof,_=current_dev_player_info(client,'session',{'account_id':'owned','game_id':10001,'character_id':'character'},
            {'account_id':'owned','game_id':10001},code_page=936,require_empty_inventory=True)
        self.assertTrue(proof['email_verified'])

    def _run_owned_dev_cli(self, current_view, identity=None):
        """只用内存夹具执行完整 CLI 分支；捕获启动与注销调用，不读真实凭据、不发请求或启动游戏。"""
        from tools import native_email_boot, email_launcher
        state = {'registered': True, 'email_verified': True, 'reset_verified': True,
                 'account_id': 'owned-account', 'game_id': 10001,
                 'email': 'fixture@example.test', 'password': 'fixture-only-password'}
        game_identity = identity or {'account_id': 'owned-account', 'game_id': 10001}
        client = Mock()
        revoked = False

        def request(path, data=None, token=None):
            """按当前 HTTP 路径提供固定返回，实时读取失败也交给 CLI 的 finally 执行注销。"""
            nonlocal revoked
            if path == '/api/logout':
                revoked = True
                return {'logged_out': True}
            if revoked and path in ('/api/views/me', '/api/game/session'):
                raise LoginError('AUTH')
            if path == '/api/views/me':
                if isinstance(current_view, Exception):
                    raise current_view
                return current_view
            return {'/api/player/login': {'token': 'fixture-session'},
                    '/api/launcher/tickets': {'ticket': 'fixture-ticket'},
                    '/api/launcher/redeem': {'game_token': 'fixture-game-token'},
                    '/api/game/session': game_identity}[path]

        client.request.side_effect = request
        argv = ['native_email_boot', '--workspace', 'not-used', '--output', 'not-used',
                '--verify-native-challenge', '--capture-application', '--serve-geo-ping',
                '--serve-geo-zone', '--capture-zone-sequence', '--serve-zone-login',
                '--serve-player-define', '--dev-email-state']
        failure = None
        with patch.object(sys, 'argv', argv), patch.object(Path, 'read_text', return_value=json.dumps(state)), \
                patch.object(Path, 'write_text'), patch.object(email_launcher, 'Client', return_value=client), \
                patch.object(email_launcher, 'fingerprint', return_value=('a' * 64, {})), \
                patch.object(native_email_boot, 'probe', return_value={}) as launch, \
                redirect_stdout(io.StringIO()) as stdout:
            try:
                native_email_boot.main()
            except Exception as error:
                failure = error
        return client, launch, failure, stdout.getvalue()

    def test_native_dev_rebound_email_is_rejected_and_session_revoked(self):
        """历史验收为真不能覆盖当前换绑后的未验证邮箱，非布尔真和读取失败也必须拒绝并注销。"""
        for current in (False, None, 1, 'true', LoginError('AUTH')):
            view = current if isinstance(current, Exception) else {
                'id': 'owned-account', 'game_id': 10001, 'email_verified': current}
            with self.subTest(current=type(current).__name__):
                client, launch, failure, _ = self._run_owned_dev_cli(view)
                self.assertIsNotNone(failure)
                launch.assert_not_called()
                self.assertEqual(client.request.call_args_list[-3].args, ('/api/logout', {}, 'fixture-session'))
                self.assertEqual(client.request.call_args_list[-2].args, ('/api/views/me', None, 'fixture-session'))

    def test_native_dev_live_account_and_redeemed_identity_must_match(self):
        """实时账号、游戏账号与验收记录必须一致，不能用另一个已验证账号通过启动门禁。"""
        normal = {'id': 'owned-account', 'game_id': 10001, 'email_verified': True}
        cases = [(dict(normal, id='another-account'), None),
                 (normal, {'account_id': 'another-account', 'game_id': 10001}),
                 (dict(normal, game_id=10002), None),
                 (normal, {'account_id': 'owned-account', 'game_id': 10002})]
        for view, identity in cases:
            with self.subTest(view_id_matches=view['id'] == 'owned-account', identity=identity is not None):
                client, launch, failure, _ = self._run_owned_dev_cli(view, identity)
                self.assertIsInstance(failure, ValueError)
                launch.assert_not_called()
                self.assertEqual(client.request.call_args_list[-3].args, ('/api/logout', {}, 'fixture-session'))
                self.assertEqual(client.request.call_args_list[-2].args, ('/api/views/me', None, 'fixture-session'))

    def test_native_dev_fresh_verification_precedes_probe_and_is_reported(self):
        """成功路径必须实时读取 me、匹配兑换身份后才启动，报告保留核验范围并注销来源会话。"""
        client, launch, failure, output = self._run_owned_dev_cli({
            'id': 'owned-account', 'game_id': 10001, 'email_verified': True})
        self.assertIsNone(failure)
        launch.assert_called_once()
        self.assertEqual(launch.call_args.args[1:3], (10001, 'fixture-game-token'))
        client.request.assert_any_call('/api/views/me', token='fixture-session')
        self.assertEqual(client.request.call_args_list[-3].args, ('/api/logout', {}, 'fixture-session'))
        self.assertEqual(client.request.call_args_list[-2].args, ('/api/views/me', None, 'fixture-session'))
        self.assertEqual(client.request.call_args.args[0], '/api/game/session')
        report = json.loads(output)
        self.assertIs(report['server_email_identity_verified'], True)
        self.assertEqual(report['server_email_identity_verification'], {
            'source': 'CURRENT_API_VIEWS_ME_AND_REDEEMED_IDENTITY',
            'email_verified': True, 'acceptance_account_matches': True,
            'game_identity_account_matches': True, 'game_id_matches': True})

    def test_zone_reply_requires_explicit_capture_and_verified_email_before_launch(self):
        """研究登录响应不能由缺失前置模式或匿名 CLI 启用，参数拒绝发生在读凭据与启动游戏之前。"""
        from tools import native_email_boot
        base = ['native_email_boot', '--workspace', 'not-used', '--output', 'not-used']
        capture = ['--verify-native-challenge', '--capture-application', '--serve-geo-ping',
                   '--serve-geo-zone', '--capture-zone-sequence']
        for options in (['--defer-player-info-after-version'], ['--serve-zone-hints'], ['--server-map','SV-Maya_3_Main'], ['--refresh-role-probe'], ['--native-settings'], ['--serve-detailed-channel'], ['--serve-role-equip-probe'], ['--probe-role-resources'], ['--serve-player-props'], ['--serve-player-info'], capture+['--serve-zone-login','--serve-player-info','--dev-email-state'],
                        ['--serve-zone-login'], ['--serve-player-define'], capture + ['--serve-player-define'], capture + ['--serve-zone-login'],
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
