"""便携Launcher基础的独立测试；不连接DEV、不启动游戏、不执行Windows编译。"""
import json
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import launcher_runtime as runtime
from tools.email_launcher import LoginError
from tools.package_launcher import package_plan, verify_payload, smoke_service, smoke_failure_details


class LauncherRuntimeTests(unittest.TestCase):
    """验证包内资源/用户目录分离、公开传输限制及打包材料排除边界。"""

    def config(self, url='https://api.example.invalid'):
        """返回仅含公开URL的测试配置，使用保留域名避免误发真实登录请求。"""
        return {'schema_version': runtime.PACKAGE_VERSION, 'api_base_url': url}

    def test_packaged_feedback_uses_verified_tls_and_fixed_binary_contract(self):
        """真实发行客户端具备媒体方法；只使用预设HTTPS并拒绝跳转及错误图片类型。"""
        connection = Mock()
        response = connection.getresponse.return_value
        response.status = 200
        response.read.return_value = b'{"id":"safe"}'
        client = runtime.PublicApiClient(self.config())
        with patch.object(runtime.ssl, 'create_default_context', return_value='verified') as context, patch.object(runtime.http.client, 'HTTPSConnection', return_value=connection) as factory:
            self.assertEqual(client.feedback_upload('a'*32, b'png', 'image/png', 'upload-key', 'private'), {'id':'safe'})
            self.assertEqual(factory.call_args.kwargs['context'], 'verified')
            self.assertEqual(connection.request.call_args.args[1], '/api/feedback/'+'a'*32+'/attachments')
            self.assertEqual(connection.request.call_args.args[3]['Authorization'], 'Bearer private')
            response.read.return_value = b'webp'
            response.getheader.return_value = 'image/webp'
            self.assertEqual(client.feedback_image('b'*32, 'private'), (b'webp', 'image/webp'))
            response.status = 302; response.read.return_value = b'{}'
            with self.assertRaises(LoginError):
                client.feedback_image('b'*32, 'private')
            self.assertEqual(connection.request.call_count, 3)
            self.assertEqual(context.call_count, 3)

    def test_frozen_resources_and_state_are_separate_and_native_start_is_blocked(self):
        """发行资源不可充当运行数据目录，原生游戏缺少完整交付时明确停止。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(runtime.sys, 'frozen', True, create=True), \
                    patch.object(runtime.sys, '_MEIPASS', str(root / 'package/service/_internal'), create=True), \
                    patch.object(runtime.sys, 'executable', str(root / 'package/service/NZLauncherService.exe')), \
                    patch.dict(runtime.os.environ, {'LOCALAPPDATA': str(root / 'user')}):
                self.assertEqual(runtime.resource_root(), (root / 'package/service/_internal').resolve())
                self.assertEqual(runtime.distribution_root(), (root / 'package').resolve())
                state = runtime.state_root(create=True)
                self.assertTrue((state / 'runtime').is_dir())
                self.assertFalse((runtime.resource_root() / 'runtime').exists())
                self.assertFalse(runtime.native_start_status()['available'])
                with self.assertRaisesRegex(RuntimeError, 'PACKAGE_NATIVE_CLIENT_UNAVAILABLE'):
                    runtime.require_native_client()

    def test_repository_mode_preserves_existing_workspace(self):
        """源代码模式继续使用既有登记研究副本，不要求迁移当前客户端或秘密配置。"""
        with patch.object(runtime.sys, 'frozen', False, create=True):
            self.assertEqual(runtime.state_root(), runtime.resource_root())
            self.assertEqual(runtime.require_native_client(),
                             runtime.resource_root() / 'runtime/native-recovery/coherent-2020/nz')

    def test_public_config_rejects_cleartext_external_addresses_credentials_and_unknown_fields(self):
        """发行配置只接受HTTPS/明确IPv4回环，不允许URL凭据或未知字段携带SSH秘密。"""
        self.assertEqual(runtime.validate_public_config(self.config())['api_base_url'], 'https://api.example.invalid')
        loopback = dict(self.config('http://127.0.0.1:18765'), loopback_host_port=18765)
        self.assertEqual(runtime.validate_public_config(loopback), loopback)
        for url in ('http://api.example.invalid:80', 'http://localhost:18765',
                    'https://user:secret@api.example.invalid', 'https://api.example.invalid/?token=x',
                    'https://api.example.invalid/#fragment', 'https://api.example.invalid/base',
                    'https://api.example.invalid\n', 'ftp://127.0.0.1:20'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                runtime.validate_public_config(self.config(url))
        with self.assertRaises(ValueError):
            runtime.validate_public_config(dict(self.config(), ssh_password='never-package'))
        with self.assertRaises(ValueError):
            runtime.validate_public_config(dict(self.config(), loopback_host_port=18765))

    def test_https_uses_verified_context_and_keeps_dict_envelope(self):
        """HTTPS使用系统验证上下文；新玩家view的data信封原样保留，不传游戏凭据到页面。"""
        connection = Mock()
        response = connection.getresponse.return_value
        response.status = 200
        response.read.return_value = json.dumps({'data': [{'item_id': 100001}]}).encode()
        with patch.object(runtime.ssl, 'create_default_context', return_value='verified-context') as context, \
                patch.object(runtime.http.client, 'HTTPSConnection', return_value=connection) as factory:
            client = runtime.PublicApiClient(self.config())
            result = client.request('/api/player/view', {'view': 'player-inventory'}, token='memory-token')
        self.assertEqual(result, {'data': [{'item_id': 100001}]})
        context.assert_called_once_with()
        self.assertEqual(factory.call_args.kwargs['context'], 'verified-context')
        connection.close.assert_called_once()
        self.assertEqual(connection.request.call_args.args[3]['Authorization'], 'Bearer memory-token')

    def test_redirects_and_absolute_request_targets_are_never_followed(self):
        """302保持单次失败，登录信息不能发往Location；请求路径不能另选外部目标。"""
        connection = Mock()
        connection.getresponse.return_value.status = 302
        connection.getresponse.return_value.read.return_value = b'{}'
        client = runtime.PublicApiClient(self.config('http://127.0.0.1:18765'))
        with patch.object(runtime.http.client, 'HTTPConnection', return_value=connection):
            with self.assertRaises(LoginError):
                client.request('/api/player/login', {'password': 'memory-only'})
        self.assertEqual(connection.request.call_count, 1)
        for path in ('https://other.invalid/api/login', '//other.invalid/api/login', '/api/\nlogin'):
            with self.subTest(path=path), self.assertRaises(LoginError):
                client.request(path)

    def test_oversized_response_and_private_payload_are_rejected(self):
        """损坏网关响应有界，打包审查会拒绝私有.env文件；不写真实账户或配置。"""
        connection = Mock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.read.return_value = b'x' * (2 * 1024 * 1024 + 1)
        with patch.object(runtime.http.client, 'HTTPConnection', return_value=connection):
            with self.assertRaisesRegex(LoginError, 'RESPONSE_TOO_LARGE'):
                runtime.PublicApiClient(self.config('http://127.0.0.1:18765')).request('/health')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'public.txt').write_text('public')
            self.assertEqual(len(verify_payload(root)), 1)
            (root / '.env.local').write_text('not-a-real-secret')
            with self.assertRaisesRegex(ValueError, 'PRIVATE_PACKAGE_INPUT'):
                verify_payload(root)

    def test_plan_declares_blocked_native_mode_without_build_side_effects(self):
        """任何主机都可审查包合同，但计划本身不能声称已有Windows构建或客户端验收。"""
        plan = package_plan(self.config())
        self.assertFalse(plan['native_start_available'])
        self.assertEqual(plan['native_blocker'], 'PACKAGE_NATIVE_CLIENT_UNAVAILABLE')
        self.assertIn('service/NZLauncherService.exe', plan['layout'])

    def test_package_smoke_checks_private_pipe_and_never_contacts_api(self):
        """模拟冻结进程的退出握手以检查验收器边界；此测试本身不宣称Windows可执行已通过。"""
        message = {'version': 'cn2020-launcher-v1',
                   'url': 'http://127.0.0.1:18769/launcher#access=' + 'a' * 43}
        process = Mock(returncode=0, stdout=json.dumps(message).encode())
        with patch('tools.package_launcher.subprocess.run', return_value=process) as run:
            report = smoke_service(Path('/package'))
        self.assertTrue(report['bootstrap_close_passed'])
        self.assertFalse(report['api_login_executed'])
        self.assertEqual(run.call_args.kwargs['input'], b'close\n')
        process.stdout = json.dumps(dict(message, url=message['url'].replace('127.0.0.1', 'other.invalid'))).encode()
        with patch('tools.package_launcher.subprocess.run', return_value=process), \
                patch('tools.package_launcher.sys.stderr', io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'FROZEN_SERVICE_BOOTSTRAP_SMOKE_FAILED'):
                smoke_service(Path('/package'))

    def test_startup_diagnostic_keeps_only_safe_code_or_public_module_name(self):
        """导入失败的模块元数据可诊断；带路径/URL/能力的异常正文不能写入日志。"""
        error = ModuleNotFoundError('private exception text', name='django.db.backends.dummy')
        self.assertEqual(runtime.startup_error_code(error), 'IMPORT_MISSING_django.db.backends.dummy')
        self.assertEqual(runtime.startup_error_code(RuntimeError('PUBLIC_LAUNCHER_CONFIG_MISSING')),
                         'PUBLIC_LAUNCHER_CONFIG_MISSING')
        for error in (RuntimeError('https://private.invalid/?access=secret'),
                      ModuleNotFoundError('/private/user/secret', name='../private/secret')):
            code = runtime.startup_error_code(error)
            self.assertNotIn('secret', code)
            self.assertNotIn('/', code)

    def test_failed_smoke_never_echoes_bootstrap_or_full_stderr(self):
        """失败报告只返回固定四字段，管道能力、异常正文和临时路径均不可外泄。"""
        process = Mock(returncode=1, stdout=b'private-bootstrap-capability',
                       stderr=b"private-stderr-password\nModuleNotFoundError: No module named 'django.example'\n")
        console = io.StringIO()
        with patch('tools.package_launcher.subprocess.run', return_value=process), \
                patch('tools.package_launcher.sys.stderr', console):
            with self.assertRaisesRegex(RuntimeError, 'FROZEN_SERVICE_BOOTSTRAP_SMOKE_FAILED'):
                smoke_service(Path('/private/package'))
        details = json.loads(console.getvalue())['frozen_service_smoke']
        self.assertEqual(details, {'exit_code': 1, 'bootstrap_parsed': False,
                                  'startup_error_code': None, 'stderr_missing_module': 'django.example'})
        self.assertNotIn('private-', console.getvalue())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / 'NZRevivalLauncher/runtime/native-launcher-error.log'
            log.parent.mkdir(parents=True)
            log.write_text('IMPORT_MISSING_django.example', encoding='utf-8')
            self.assertEqual(smoke_failure_details(root, process, False)['startup_error_code'],
                             'IMPORT_MISSING_django.example')
            log.write_text('secret/path?access=private', encoding='utf-8')
            self.assertIsNone(smoke_failure_details(root, process, False)['startup_error_code'])
