"""可选 Windows 原生接口整合：真实 MySQL/HTTP 邮箱身份交给独立 32 位代理读取。"""
import json
import os
from pathlib import Path
import socket
import threading
from unittest import skipUnless
from django.core.wsgi import get_wsgi_application
from django.test import override_settings
from django.db.models import Q
from revival.server import local_server
from tools.email_launcher import Client, fingerprint, handshake
from adapters.cn_current.email_ipc import IPC_PROFILE, LOOPBACK_IPC_PROFILE, RESEARCH_SERVICE_ID
from .test_control_plane import Base

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / 'runtime/native-recovery/coherent-2020/nz'


@skipUnless(os.name == 'nt' and (CLIENT / 'Binaries/Win32/TenProxy.dll').is_file(),
            'Requires the user-provided validated Windows client; CI cannot claim native acceptance')
class EmailNativeTests(Base):
    """独立真实账号与短期游戏令牌的 IPC 自测；不运行 TGame，不把结果称为进大厅。"""
    def test_two_email_accounts_reach_native_proxy_without_shared_identity_or_qq(self):
        """两个邮箱分别通过完整 HTTP 交接，原生组件读到各自 ID/令牌，结束时注销源会话。"""
        from tools.native_email_probe import probe
        client_sha, _ = fingerprint(CLIENT)
        server = local_server(get_wsgi_application(), port=0)
        port = int(server.effective_port)
        thread = threading.Thread(target=server.run, daemon=True)
        reports = []
        try:
            with override_settings(NZ_PROFILE='synthetic', NZ_PORT=port):
                thread.start()
                for index in range(2):
                    email = f'native-{index}@example.test'
                    invite = self.invitation()
                    account = self.s.register(self.meta(email=email, password=self.password, invitation=invite['invitation_token']))
                    self.command('user.approve', {'target_account_id': account['account_id']})

                    def read_native(game_id, game_token):
                        """两个账号分别覆盖默认和回环列表；凭据只经 stdin，真实 DLL getter 不启动游戏。"""
                        if index == 0:
                            return probe(CLIENT, game_id, game_token)
                        # 为字段验证独占本机临时端口；只读 DLL 输出，不建立游戏连接或发送协议消息。
                        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as endpoint:
                            endpoint.bind(('127.0.0.1', 0))
                            return probe(CLIENT, game_id, game_token,
                                         loopback_port=endpoint.getsockname()[1], client_path=str(CLIENT))

                    report = handshake(Client(f'http://127.0.0.1:{port}'), email, self.password, client_sha,
                                       native_self_test=read_native)
                    observed = report['native_ipc_self_test']['observations']
                    self.assertTrue(report['native_ipc_self_test']['ipc_read_verified'])
                    self.assertEqual(len(observed), 1)
                    self.assertTrue(observed[0]['FactoryCreated'])
                    self.assertTrue(observed[0]['LoginInfoAccepted'])
                    self.assertTrue(observed[0]['LocalTokenMatches'])
                    self.assertTrue(observed[0]['LocalSessionIdMatches'])
                    self.assertTrue(observed[0]['OutputGuardIntact'])
                    self.assertTrue(observed[0]['ServerRecordVerified'])
                    self.assertEqual(observed[0]['ResearchServiceId'], RESEARCH_SERVICE_ID)
                    self.assertEqual(observed[0]['LocalGameId'], account['game_id'])
                    self.assertEqual(report['native_ipc_self_test']['ipc_profile'],
                                     IPC_PROFILE if index == 0 else LOOPBACK_IPC_PROFILE)
                    self.assertFalse(report['game_process_started'])
                    self.assertTrue(report['native_ipc_self_test']['process_tree_cleanup'])
                    with self.s.db.transaction() as c:
                        sessions = c.select('sessions', Q(subject_id=account['account_id'], kind='player'))
                        self.assertTrue(all(row['revoked'] for row in sessions))
                    reports.append(report)
                self.assertNotEqual(reports[0]['game_id'], reports[1]['game_id'])
        finally:
            server.task_dispatcher.shutdown()
            server.close()
            if thread.is_alive():
                thread.join(timeout=5)
        # 只发布无密码、邮箱、票据和令牌的结构结果；这不是双端游戏验收证据。
        # 批次复验可指定新的证据文件，避免覆盖此前原生桥验收结果。
        report_path = ROOT / os.environ.get('NZ_NATIVE_TEST_REPORT', 'evidence/email-login-native-bridge.json')
        report_path.write_text(json.dumps({
            'kind': 'MYSQL_HTTP_EMAIL_TO_NATIVE_IPC_NOT_GAME_ACCEPTANCE', 'accounts': reports,
            'native_game_started': False, 'lobby_verified': False, 'dual_client_verified': False,
        }, ensure_ascii=False, indent=2), encoding='utf-8')
