"""公共封面跨模块回归：真实回环HTTP+发行客户端+控制器，不连接远端或借用私有会话。"""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest
from unittest.mock import Mock, patch

from django.test import RequestFactory

from tools import native_launcher as launcher
from tools.email_launcher import Client, LoginError
from tools.launcher_runtime import PACKAGE_VERSION, PublicApiClient


class MediaHandler(BaseHTTPRequestHandler):
    """仅测试固定公共媒体响应；记录请求路径和头用于断言，不输出到终端或磁盘。"""
    def do_GET(self):
        """用本例指定的响应模拟网关；302只提供Location，不创建第二个目标。"""
        self.server.observed.append((self.path, self.headers.get('Authorization'), self.headers.get('Host')))
        status, mime, payload = self.server.media_response
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(payload)))
        if status == 302:
            self.send_header('Location', 'https://never-contact.example.invalid/private')
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        """测试不记录HTTP路径或任何请求头；失败由unittest断言报告。"""


class CampaignCoverBridgeTests(unittest.TestCase):
    """公共封面本机仍需能力及登录；远端固定匿名GET，大小/MIME/TLS边界与私图核心一致。"""
    @classmethod
    def setUpClass(cls):
        """允许纯工具测试运行，不读取项目配置或数据库环境。"""
        from django.conf import settings
        if not settings.configured:
            settings.configure(SECRET_KEY='cover-unit-only', DEFAULT_CHARSET='utf-8', DATA_UPLOAD_MAX_MEMORY_SIZE=7*1024*1024)

    def setUp(self):
        """独立临时端口只提供内存测试图片；公开Host覆盖与实际TCP目标分别验证。"""
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), MediaHandler)
        self.server.observed = []
        self.server.media_response = (200, 'image/webp', b'RIFF\x04\0\0\0WEBP')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)
        self.client = PublicApiClient({'schema_version': PACKAGE_VERSION,
            'api_base_url': 'http://127.0.0.1:' + str(self.server.server_port), 'loopback_host_port': 18765})
        self.controller = launcher.LauncherController(self.client)
        self.controller.session = 'private-player-session-never-send'
        self.factory = RequestFactory()
        self.media_id = 'a' * 32

    def shutdown(self):
        """等待当前测试网关线程退出，避免后续用例复用残留监听或连接。"""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, **headers):
        """经过真实本机endpoint而非直接绕过Host/Origin/cap能力校验。"""
        options = {'HTTP_HOST': f'127.0.0.1:{launcher.PORT}', 'HTTP_X_NZ_LAUNCHER': self.controller.capability}
        options.update(headers)
        request = self.factory.post('/launcher/api/campaign-cover-image', json.dumps({'media_id': self.media_id}),
            content_type='application/json', **options)
        with patch.object(launcher, 'CONTROLLER', self.controller):
            return launcher.endpoint(request, 'api/campaign-cover-image')

    def test_public_client_controller_and_endpoint_read_fixed_cover_without_bearer(self):
        """真实发行客户端连回环网关；WebView只拿WebP字节，公共请求无任何远端Bearer。"""
        response = self.request()
        self.assertEqual(response.status_code, 200, response.content)
        value = json.loads(response.content)
        self.assertEqual(base64.b64decode(value['content_base64']), self.server.media_response[2])
        self.assertEqual(value['content_type'], 'image/webp')
        self.assertNotIn(self.controller.session, response.content.decode())
        self.assertEqual(self.server.observed, [('/api/media/' + self.media_id, None, '127.0.0.1:18765')])
        # 研究模式也必须用相同固定公共路径，不能因无Bearer而退回JSON读取或私图接口。
        research = Client('http://127.0.0.1:' + str(self.server.server_port))
        self.assertEqual(research.public_cover_image(self.media_id)[1], 'image/webp')
        self.assertIsNone(self.server.observed[-1][1])

    def test_local_capability_login_and_origin_still_gate_public_media(self):
        """公共远端资源不能使本机代理变匿名：无cap/跨源/未登录都在联网前拒绝。"""
        for headers in ({'HTTP_X_NZ_LAUNCHER': ''}, {'HTTP_ORIGIN': 'https://other.example.invalid'}, {'HTTP_HOST': 'other.invalid'}):
            self.assertEqual(self.request(**headers).status_code, 403)
        self.controller.session = None
        response = self.request()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(json.loads(response.content)['error'], 'LOGIN_REQUIRED')
        self.assertEqual(self.server.observed, [])

    def test_url_paths_and_extra_fields_never_reach_public_gateway(self):
        """只收媒体ID；任意URL、私图路径、大小写/长度错误和附带字段均不触发HTTP。"""
        for value in ('https://other.invalid/x', '/api/feedback/attachments/' + self.media_id, '../secret', 'A'*32, 'a'*31, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.controller.feedback_media('campaign-cover-image', {'media_id': value})
            with self.assertRaises(LoginError):
                self.client.public_cover_image(value)
        with self.assertRaises(ValueError):
            self.controller.feedback_media('campaign-cover-image', {'media_id': self.media_id, 'url': '/api/media/x'})
        self.assertEqual(self.server.observed, [])

    def test_binary_mime_size_and_redirect_are_rejected_by_real_public_transport(self):
        """实际回环响应验证WebP、5MiB和单次请求；302不能跟随Location。"""
        cases = [(200, 'text/html', b'<html/>', 'INVALID_ATTACHMENT'),
                 (200, 'image/webp', b'x' * (5*1024*1024+1), 'RESPONSE_TOO_LARGE'),
                 (302, 'application/json', b'{}', 'REQUEST_REJECTED')]
        for status, mime, payload, code in cases:
            self.server.media_response = (status, mime, payload)
            before = len(self.server.observed)
            with self.assertRaisesRegex(LoginError, '^' + code + '$'):
                self.client.public_cover_image(self.media_id)
            self.assertEqual(len(self.server.observed), before+1)
            self.assertIsNone(self.server.observed[-1][1])

    def test_https_cover_uses_verified_factory_without_authorization(self):
        """发行HTTPS媒体继续使用系统证书验证，不因公共图片而降级TLS或泄露当前会话。"""
        client = PublicApiClient({'schema_version': PACKAGE_VERSION, 'api_base_url': 'https://api.example.invalid'})
        connection = Mock()
        response = connection.getresponse.return_value
        response.status, response.read.return_value = 200, self.server.media_response[2]
        response.getheader.return_value = 'image/webp'
        with patch('tools.launcher_runtime.ssl.create_default_context', return_value='verified-context') as context, \
                patch('tools.launcher_runtime.http.client.HTTPSConnection', return_value=connection) as factory:
            self.assertEqual(client.public_cover_image(self.media_id)[1], 'image/webp')
        context.assert_called_once_with()
        self.assertEqual(factory.call_args.kwargs['context'], 'verified-context')
        self.assertNotIn('Authorization', connection.request.call_args.args[3])
        self.assertEqual(connection.request.call_args.args[:2], ('GET', '/api/media/' + self.media_id))
        connection.close.assert_called_once()
