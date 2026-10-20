"""Launcher 控制和安全边界测试；使用伪服务验证失败清理，不冒充真实账号或原生界面验收。"""
import json
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from django.test import RequestFactory
from tools import native_launcher as launcher
from tools.email_launcher import LoginError
from tools.native_config_guard import NativeConfigGuard


class LauncherTests(unittest.TestCase):
    """覆盖跨站调用、重复启动、密码释放、未知兑换结果、退出重试与配置冲突保护。"""

    @classmethod
    def setUpClass(cls):
        """纯控制层测试不连接数据库；如果 Django runner 已配置则保留其真实 MySQL 设置。"""
        from django.conf import settings
        if not settings.configured:
            settings.configure(SECRET_KEY='unit-only', DEFAULT_CHARSET='utf-8', DATA_UPLOAD_MAX_MEMORY_SIZE=8192)

    def setUp(self):
        """每个用例拥有独立控制器、假凭据和请求工厂，不写玩家状态。"""
        self.controller = launcher.LauncherController(Mock())
        self.factory = RequestFactory()
        # 本组验证控制器的错误/清理逻辑，Win32代码页属于外部依赖；在非Windows主机
        # 显式模拟它，不能让缺少ctypes.WinDLL掩盖要验证的超时和图形失败路径。
        # 这不执行真实Windows API，也不构成安装、图形或游戏验收。
        if not hasattr(launcher.ctypes, 'WinDLL'):
            codepage = patch.object(launcher.ctypes, 'WinDLL', create=True)
            codepage.start().return_value.GetACP.return_value = 936
            self.addCleanup(codepage.stop)

    def call(self, action='status', method='get', **headers):
        """调用真实 Django 入口，默认仅提供正确 Host 和本机能力。"""
        headers = dict(HTTP_HOST=f'127.0.0.1:{launcher.PORT}', HTTP_X_NZ_LAUNCHER=self.controller.capability, **headers)
        request = getattr(self.factory, method)('/launcher/api/'+action, data='{}' if method == 'post' else None,
            content_type='application/json', **headers)
        with patch.object(launcher, 'CONTROLLER', self.controller):
            return launcher.endpoint(request, 'api/'+action)

    def test_origin_host_capability_and_method_are_required(self):
        """恶意网站、DNS 重绑定和无令牌均不能调用；GET 不能触发启动。"""
        self.assertEqual(self.call(HTTP_ORIGIN='https://other.invalid').status_code, 403)
        for field, value in [('HTTP_HOST', 'other.invalid'), ('HTTP_X_NZ_LAUNCHER', '')]:
            request = self.factory.get('/launcher/api/status', **{
                'HTTP_HOST': f'127.0.0.1:{launcher.PORT}', 'HTTP_X_NZ_LAUNCHER': self.controller.capability, field: value})
            with patch.object(launcher, 'CONTROLLER', self.controller):
                self.assertEqual(launcher.endpoint(request, 'api/status').status_code, 403)
        self.assertEqual(self.call('start').status_code, 405)
        self.assertEqual(self.call().status_code, 200)

    def test_static_traversal_cannot_read_private_files(self):
        """静态路由不能读取 runtime、配置、Python 或其他私人文件。"""
        request = self.factory.get('/launcher/assets/../../runtime/mail-acceptance.json', HTTP_HOST=f'127.0.0.1:{launcher.PORT}')
        self.assertEqual(launcher.endpoint(request, 'assets/../../runtime/mail-acceptance.json').status_code, 404)

    def test_oversized_non_json_and_unknown_arguments_fail(self):
        """边界请求在派发前拒绝，不能把 UI 变成任意路径/参数执行入口。"""
        self.assertEqual(self.call('start', 'post', CONTENT_LENGTH='9000').status_code, 409)
        with self.assertRaisesRegex(ValueError, 'UNEXPECTED_LAUNCHER_ARGUMENTS'):
            self.controller.submit('start', {'binary': 'untrusted.exe'})
        with self.assertRaisesRegex(RuntimeError, 'LOGIN_REQUIRED'):
            self.controller.submit('start', {})

    def test_login_discards_password_and_keeps_tokens_out_of_status(self):
        """正常登录后清空输入字典，公开状态只含角色名，不含密码、邮箱或来源令牌。"""
        data = {'email': 'player@example.invalid', 'password': 'test-secret'}
        self.controller.client.request.side_effect = [{'token': 'test-source'}, {
            'email_verified': True, 'id': 'account', 'game_id': 10001,
            'character': {'id': 'character', 'nickname': '玩家'}}]
        with patch.object(self.controller, '_connect'):
            self.controller._execute('login', data)
        self.assertEqual(data, {})
        self.assertEqual(self.controller.phase, 'LOGGED_IN')
        public = json.dumps(self.controller.status())
        for secret in ('test-source', 'test-secret', 'example.invalid', 'account'):
            self.assertNotIn(secret, public)

    def test_failed_identity_read_revokes_just_created_source_session(self):
        """登录后 me 失败仍撤销刚产生的来源凭据，不能遗留可用登录。"""
        self.controller.client.request.side_effect = [{'token': 'source'}, LoginError('AUTH')]
        with patch.object(self.controller, '_connect'), patch('tools.native_email_boot.revoke_owned_dev_session',
                return_value={'verified': True}) as revoke:
            self.controller._execute('login', {'email': 'p@example.invalid', 'password': 'test'})
        revoke.assert_called_once_with(self.controller.client, 'source', game_token=None, client_sha=None)
        self.assertIsNone(self.controller.session)
        self.assertEqual(self.controller.error, 'AUTH')

    def test_double_submit_does_not_create_two_workers(self):
        """第一个启动还未结束时第二次点击被拒绝；停止仅设置本轮取消标记。"""
        gate = threading.Event()
        self.controller.session = 'source'
        with patch.object(self.controller, '_run_game', side_effect=lambda: gate.wait(2)):
            self.controller.submit('start', {})
            with self.assertRaisesRegex(RuntimeError, 'LAUNCHER_BUSY'):
                self.controller.submit('start', {})
            self.controller.submit('stop', {})
            self.assertTrue(self.controller.stop.is_set())
            gate.set(); self.controller.worker.join(3)
        self.assertFalse(self.controller.busy)
        self.assertEqual(self.controller.graphics_mode, 'frame-copy')

    def test_graphics_mode_cannot_select_arbitrary_dll_or_change_during_run(self):
        """显示模式只接受固定枚举，不能传路径；运行期间第二次切换不会改变当前工作线程。"""
        self.controller.session = 'source'
        for data in ({'graphics_mode': '../arbitrary.dll'}, {'graphics_mode': True},
                     {'graphics_mode': 'd3d9ex-flip', 'dll': 'foreign'}):
            with self.assertRaises(ValueError):
                self.controller.submit('start', data)
        gate = threading.Event()
        with patch.object(self.controller, '_run_game', side_effect=lambda: gate.wait(2)):
            self.controller.submit('start', {'graphics_mode': 'd3d9ex-flip'})
            with self.assertRaisesRegex(RuntimeError, 'LAUNCHER_BUSY'):
                self.controller.submit('start', {'graphics_mode': 'system'})
            self.assertEqual(self.controller.graphics_mode, 'd3d9ex-flip')
            gate.set(); self.controller.worker.join(3)

    def test_cleanup_failure_is_retryable_without_losing_credentials(self):
        """断网不等于注销成功；保留内存凭据重试，确认成功后才释放。"""
        self.controller.session, self.controller.game_token = 'source', 'game'
        with patch('tools.native_email_boot.revoke_owned_dev_session', side_effect=[{'verified': False}, {'verified': True}]):
            self.controller._execute('logout', {})
            self.assertEqual(self.controller.session, 'source')
            self.assertTrue(self.controller.cleanup_pending)
            self.controller._execute('logout', {})
        self.assertIsNone(self.controller.session)
        self.assertIsNone(self.controller.game_token)
        self.assertFalse(self.controller.cleanup_pending)

    def test_uncertain_redeem_does_not_repeat_and_revokes_source(self):
        """票据兑换应答丢失不重放；撤销来源会话同时废止可能已生成的游戏会话。"""
        self.controller.session = 'source'
        self.controller.client.request.side_effect = [{'ticket': 'one-use-ticket'}, TimeoutError()]
        with patch.object(launcher, 'recover_configurations'), patch.object(launcher, 'fingerprint', return_value=('a'*64, {})), \
                patch('tools.native_email_boot.revoke_owned_dev_session', return_value={'verified': True}) as revoke:
            self.controller._execute('start', {})
        self.assertEqual(self.controller.client.request.call_count, 2)
        self.assertEqual(self.controller.error, 'TIMEOUTERROR')
        revoke.assert_called_once()
        self.assertIsNone(self.controller.session)

    def test_cancel_before_ticket_still_logs_out(self):
        """取消可在创建游戏前生效，且已经登录的来源会话仍会清理。"""
        self.controller.session = 'source'
        self.controller.stop.set()
        with patch.object(launcher, 'recover_configurations'), patch.object(launcher, 'fingerprint', return_value=('a'*64, {})), \
                patch('tools.native_email_boot.revoke_owned_dev_session', return_value={'verified': True}):
            self.controller._execute('start', {})
        self.controller.client.request.assert_not_called()
        self.assertEqual(self.controller.error, 'START_CANCELLED')

    def test_startup_timeout_is_not_success_and_revokes_both_sessions(self):
        """初始化超时即使已创建进程也必须报错；完成本地清理后撤销来源和游戏凭据。"""
        self.controller.session = 'source'
        self.controller.account = {'id': 'fixture', 'nickname': '测试'}
        self.controller.client.request.side_effect = [{'ticket': 'fixture'}, {'game_token': 'game'}, {'game_id': 10001}]
        with patch.object(launcher, 'recover_configurations'), patch.object(launcher, 'fingerprint', return_value=('a'*64, {})), \
                patch('tools.native_email_boot.current_dev_player_info', return_value=({}, b'fixture')), \
                patch('tools.native_settings_bridge.NativeSettingsBridge'), \
                patch('tools.native_inventory_bridge.NativeInventoryBridge'), \
                patch('tools.native_feature_bridge.NativeFeatureBridge'), \
                patch.object(launcher, 'SessionLease') as lease, \
                patch('tools.native_email_boot.probe', return_value={'game_process_started': True,
                    'cleanup_verified': True, 'configuration_restored': True, 'startup_error': 'CLIENT_STARTUP_TIMEOUT'}) as probe, \
                patch('tools.native_email_boot.revoke_owned_dev_session', return_value={'verified': True}) as revoke:
            lease.return_value.error = None
            self.controller._execute('start', {})
        self.assertEqual(self.controller.error, 'CLIENT_STARTUP_TIMEOUT')
        self.assertEqual(self.controller.phase, 'ERROR')
        self.assertEqual(probe.call_args.kwargs['startup_timeout'], 180)
        self.assertTrue(probe.call_args.kwargs['frame_copy'])
        self.assertFalse(probe.call_args.kwargs['graphics_compat'])
        self.assertEqual(revoke.call_args.kwargs['game_token'], 'game')
        self.assertIsNone(self.controller.session)
        self.assertIsNone(self.controller.game_token)
        lease.return_value.close.assert_called_once()

    def test_recovery_preserves_active_process_and_external_edits(self):
        """旧进程仍活跃时不恢复；进程退出后只恢复匹配变体，不覆盖其他工具写入的新内容。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root/'runtime/native-recovery/coherent-2020/nz'; workspace.mkdir(parents=True)
            output = root/'evidence/private/launcher-test'; output.mkdir(parents=True)
            target = workspace/'config.ini'; target.write_bytes(b'original')
            guard = NativeConfigGuard(workspace, output); guard.apply('config.ini', b'variant')
            (output/'owned-pids.json').write_text('[123]')
            with patch.object(launcher, 'process_still_exists', return_value=True):
                with self.assertRaisesRegex(RuntimeError, 'PREVIOUS_GAME_STILL_RUNNING'):
                    launcher.recover_configurations(root, workspace)
            self.assertEqual(target.read_bytes(), b'variant')
            target.write_bytes(b'external')
            with patch.object(launcher, 'process_still_exists', return_value=False):
                with self.assertRaisesRegex(RuntimeError, 'CONFIGURATION_CONFLICT'):
                    launcher.recover_configurations(root, workspace)
            self.assertEqual(target.read_bytes(), b'external')

    def test_graphics_preflight_failure_keeps_safe_reason_and_revokes(self):
        """已核实D3D9上下文失败保留诊断码，未知阻塞不回显；两种情况都撤销凭据。"""
        for blocker,expected in [('D3D9_UNAVAILABLE_IN_LAUNCH_CONTEXT','D3D9_UNAVAILABLE_IN_LAUNCH_CONTEXT'),
                                 ('password=not-a-public-error','CLIENT_DID_NOT_START')]:
            with self.subTest(blocker=blocker):
                controller=launcher.LauncherController(Mock());controller.session='source'
                controller.account={'id':'fixture','nickname':'测试'}
                controller.client.request.side_effect=[{'ticket':'fixture'},{'game_token':'game'},{'game_id':10001}]
                with patch.object(launcher,'recover_configurations'),patch.object(launcher,'fingerprint',return_value=('a'*64,{})), \
                     patch('tools.native_email_boot.current_dev_player_info',return_value=({},b'fixture')), \
                     patch('tools.native_settings_bridge.NativeSettingsBridge'),patch('tools.native_inventory_bridge.NativeInventoryBridge'), \
                     patch('tools.native_feature_bridge.NativeFeatureBridge'),patch.object(launcher,'SessionLease') as lease, \
                     patch('tools.native_email_boot.probe',return_value={'game_process_started':False,'blocker':blocker,
                         'configuration_restored':True,'cleanup_verified':True}), \
                     patch('tools.native_email_boot.revoke_owned_dev_session',return_value={'verified':True}) as revoke:
                    lease.return_value.error=None;controller._execute('start',{})
                self.assertEqual(controller.error,expected);self.assertEqual(controller.phase,'ERROR')
                self.assertIsNone(controller.session);self.assertIsNone(controller.game_token)
                self.assertEqual(revoke.call_args.kwargs['game_token'],'game');lease.return_value.close.assert_called_once()

    def test_error_text_never_echoes_credentials(self):
        """底层异常含请求正文时仅返回异常类别，固定业务错误码可供用户排查。"""
        self.assertEqual(launcher.safe_error(RuntimeError('password=secret')), 'RUNTIMEERROR')
        self.assertEqual(launcher.safe_error(LoginError('AUTH')), 'AUTH')

    def test_desktop_pipe_eof_requests_cleanup(self):
        """Tauri 正常关闭或崩溃使管道 EOF，均请求清理；不接受其他管道内容作为执行指令。"""
        requested, disconnected = threading.Event(), threading.Event()
        launcher.watch_desktop(io.StringIO('unknown\nclose\n'), requested, disconnected)
        self.assertTrue(requested.is_set())
        self.assertTrue(disconnected.is_set())
        requested.clear(); disconnected.clear()
        launcher.watch_desktop(io.StringIO(''), requested, disconnected)
        self.assertTrue(requested.is_set())
        self.assertTrue(disconnected.is_set())

    def test_invalid_lease_is_rejected_before_starting_thread(self):
        """旧服务或损坏租约不能让启动器假定可续期，布尔值也不能冒充整数时间。"""
        for response in ({}, {'expires_at': True, 'lease_max_expires_at': 500, 'session_lease': launcher.LEASE_VERSION},
                         {'expires_at': 600, 'lease_max_expires_at': 500, 'session_lease': launcher.LEASE_VERSION}):
            with self.assertRaisesRegex(RuntimeError, 'INVALID_SESSION_LEASE'):
                launcher.SessionLease(Mock(), 'test-game', {}, response, threading.Event())

    def test_revoked_lease_stops_game_without_retry(self):
        """有效期内服务端撤销也立即停止游戏，不能靠本地缓存继续续期。"""
        client, stop = Mock(), threading.Event()
        client.request.side_effect = LoginError('AUTH')
        lease = launcher.SessionLease(client, 'test-game', {}, {
            'session_lease': launcher.LEASE_VERSION, 'expires_at': 10000, 'lease_max_expires_at': 20000}, stop)
        with patch.object(launcher.time, 'time', return_value=9000):
            lease.run()
        self.assertEqual(lease.error, 'SESSION_REVOKED')
        self.assertTrue(stop.is_set())
        client.request.assert_called_once()


if __name__ == '__main__':
    unittest.main()
