"""第二轮本机桥接安全测试：注册/onboarding/私图固定传输，不借模拟网络宣称Windows实机通过。"""
import base64
import json
import unittest
from unittest.mock import Mock, patch
from django.test import RequestFactory, override_settings
from tools import native_launcher as launcher
from tools.email_launcher import Client, LoginError


class LauncherPortalV2Tests(unittest.TestCase):
    """本地能力、远端令牌隔离与上传尺寸门禁均在真实控制器上验证。"""
    @classmethod
    def setUpClass(cls):
        """独立unittest模式无需数据库；Django runner已配置时保持其MySQL设置。"""
        from django.conf import settings
        if not settings.configured:
            settings.configure(SECRET_KEY='unit-only', DEFAULT_CHARSET='utf-8', DATA_UPLOAD_MAX_MEMORY_SIZE=7*1024*1024)

    def setUp(self):
        """每例采用全新内存客户端；没有凭据写盘或真实发信。"""
        self.client = Mock()
        self.controller = launcher.LauncherController(self.client)

    def test_registration_proxy_is_fixed_and_clears_credentials(self):
        """未登录注册仅走指定路径；结束即销毁控制器接收的密码/验证码字典。"""
        payload = {'email': 'new@example.invalid', 'password': 'fake-password', 'nickname': '测试角色', 'code': '123456'}
        self.client.request.return_value = {'account_id': 'new', 'status': 'ACTIVE'}
        result = self.controller.account_access({'operation': 'register', 'payload': payload})
        self.assertEqual(self.client.request.call_args.args[0], '/api/player/register')
        self.assertEqual(payload, {})
        self.assertNotIn('token', result)
        self.assertIsNone(self.controller.session)
        for bad in [[], 'request', 'https://other.invalid']:
            with self.assertRaises(ValueError):
                self.controller.account_access({'operation': bad, 'payload': {}})
        with self.assertRaises(ValueError):
            self.controller.account_access({'operation': 'register', 'payload': {'path': '/api/login'}})

    def test_verified_legacy_account_stays_logged_in_for_onboarding(self):
        """邮箱已验证的正常无角色账号保留登录，启动被阻止，补建角色后门槛解除。"""
        self.client.request.side_effect = [{'token': 'private-source'}, {'email_verified': True, 'id': 'a', 'game_id': 10001, 'character': None}]
        self.controller._execute('login', {'email': 'old@example.invalid', 'password': 'fake'})
        self.assertEqual(self.controller.phase, 'LOGGED_IN')
        self.assertTrue(self.controller.status()['character_required'])
        self.client.request.side_effect = [{ 'character_id': 'c' }, {'id': 'a', 'game_id': 10001, 'character': {'id': 'c', 'nickname': '新角色'}}]
        self.controller.portal('command', {'action': 'character.create', 'payload': {'nickname': '新角色'}, 'metadata': {'request_id': 'r', 'idempotency_key': 'k', 'reason': '初始化'}})
        self.assertFalse(self.controller.status()['character_required'])
        self.assertNotIn('private-source', json.dumps(self.controller.status()))

    def test_shop_and_admin_commands_cannot_cross_player_proxy(self):
        """新旧商城命令均无法到达远端，管理员功能也不能复用玩家桥。"""
        self.controller.session = 'source'
        with self.assertRaises(ValueError):
            self.controller.portal('view', {'view': 'player-shop'})
        for action in ('shop.purchase', 'player.shop.quote', 'player.shop.purchase', 'feedback.status', 'asset.grant'):
            with self.assertRaises(ValueError):
                self.controller.portal('command', {'action': action, 'payload': {}, 'metadata': {}})
        self.client.request.assert_not_called()

    def test_private_image_proxy_uses_fixed_client_and_no_bearer_output(self):
        """渲染层只拿图片字节，远端Bearer只作为Python客户端参数。"""
        self.controller.session = 'private-source'
        image = b'fake-validated-webp'
        self.client.feedback_image.return_value = (image, 'image/webp')
        value = self.controller.feedback_media('feedback-image', {'attachment_id': 'a'*32})
        self.assertEqual(base64.b64decode(value['content_base64']), image)
        self.assertNotIn('private-source', json.dumps(value))
        self.client.feedback_upload.return_value = {'id': 'b'*32}
        self.controller.feedback_media('feedback-upload', {'ticket_id': 'c'*32, 'file_name': '../../ignored.png', 'content_type': 'image/png', 'content_base64': base64.b64encode(b'png').decode(), 'upload_key': 'upload-key'})
        self.client.feedback_upload.assert_called_once_with('c'*32, b'png', 'image/png', 'upload-key', 'private-source')
        with self.assertRaises(ValueError):
            self.controller.feedback_media('feedback-upload', {'ticket_id':'c'*32, 'file_name':'x', 'content_type':'image/png','content_base64':'%%%','upload_key':'upload-key'})

    def test_command_text_and_image_have_separate_body_budgets(self):
        """5000字中文反馈可通过64KiB命令预算，启动参数仍维持8KiB，且始终需要本机能力。"""
        factory = RequestFactory()
        body = json.dumps({'text': '反馈'*2500})
        request = factory.post('/launcher/api/command', body, content_type='application/json', HTTP_HOST=f'127.0.0.1:{launcher.PORT}', HTTP_X_NZ_LAUNCHER=self.controller.capability)
        with patch.object(launcher, 'CONTROLLER', self.controller), patch.object(self.controller, 'portal', return_value={'ok': True}), override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=7*1024*1024):
            self.assertEqual(launcher.endpoint(request, 'api/command').status_code, 200)
        request = factory.post('/launcher/api/start', body, content_type='application/json', HTTP_HOST=f'127.0.0.1:{launcher.PORT}', HTTP_X_NZ_LAUNCHER=self.controller.capability)
        with patch.object(launcher, 'CONTROLLER', self.controller):
            self.assertEqual(launcher.endpoint(request, 'api/start').status_code, 409)


class FixedFeedbackTransportTests(unittest.TestCase):
    """真实HTTP封装仅接受固定附件ID/MIME/大小，且不信任远端返回的任意二进制类型。"""
    def test_fixed_multipart_and_binary_validation(self):
        """上传保留X-NZ-Request与Bearer，响应只解合法JSON；私图必须是WebP。"""
        client = Client('http://127.0.0.1:18765')
        connection = Mock()
        response = connection.getresponse.return_value
        response.status = 200
        response.read.return_value = b'{"id":"safe"}'
        with patch('tools.email_launcher.http.client.HTTPConnection', return_value=connection):
            self.assertEqual(client.feedback_upload('a'*32, b'png', 'image/png', 'upload-key', 'private'), {'id':'safe'})
            args = connection.request.call_args.args
            self.assertEqual(args[1], '/api/feedback/'+'a'*32+'/attachments')
            self.assertEqual(args[3]['Authorization'], 'Bearer private')
            response.getheader.return_value = 'text/html'
            with self.assertRaises(LoginError):
                client.feedback_image('a'*32, 'private')
        for value in ('../secret', None, []):
            with self.assertRaises(LoginError):
                client.feedback_image(value, 'private')
