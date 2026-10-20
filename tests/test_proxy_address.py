"""Web反代限流身份：显式可信回环链路区分玩家，外部请求不能伪造转发IP。"""
from django.test import SimpleTestCase, RequestFactory, override_settings

from revival.middleware import client_address
from revival.service import Fault


@override_settings(NZ_PUBLIC_HOST='portal.example.test', NZ_TRUST_LOOPBACK_PROXY=True)
class ProxyAddressTests(SimpleTestCase):
    """只检验请求地址解析，不连接MySQL或依赖真实反向代理。"""

    def request(self, **values):
        """创建精确公网Host和本机peer的请求；各用例只覆盖需要改变的信任条件。"""
        headers = {'REMOTE_ADDR': '127.0.0.1', 'HTTP_HOST': 'portal.example.test',
            'HTTP_X_REAL_IP': '203.0.113.7'}
        headers.update(values)
        return RequestFactory().post('/api/player/login', **headers)

    def test_trusted_proxy_distinguishes_clients(self):
        """模板中的本机反代明确覆盖头后，不同公网用户不再共享登录/邮件限流桶。"""
        self.assertEqual(client_address(self.request()), '203.0.113.7')
        self.assertEqual(client_address(self.request(HTTP_X_REAL_IP='203.0.113.8')), '203.0.113.8')

    def test_untrusted_peer_host_or_disabled_setting_ignores_forwarded_header(self):
        """外部peer、本机私有入口或未授权反代均以连接IP限流，不能自行换桶。"""
        self.assertEqual(client_address(self.request(REMOTE_ADDR='203.0.113.9')), '203.0.113.9')
        self.assertEqual(client_address(self.request(HTTP_HOST='127.0.0.1:8765')), '127.0.0.1')
        with override_settings(NZ_TRUST_LOOPBACK_PROXY=False):
            self.assertEqual(client_address(self.request()), '127.0.0.1')

    def test_invalid_proxy_address_is_rejected(self):
        """受信代理头必须是单个规范IP，不能使用任意字符串或逗号列表绕过桶限制。"""
        with self.assertRaises(Fault):
            client_address(self.request(HTTP_X_REAL_IP='203.0.113.7, 203.0.113.8'))
