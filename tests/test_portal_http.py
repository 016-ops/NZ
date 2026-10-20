"""门户真实 Django/MySQL 请求合同：公开读模型、运营权限与显式部署来源边界。"""
from unittest.mock import patch
from django.conf import settings
from django.test import Client, override_settings
from .test_control_plane import Base


class PortalHttpTests(Base):
    """只使用隔离测试账号和库，经真实中间件验证新 HTTP 路由。"""
    def setUp(self):
        """固定 HTTP Service 与本例时钟；不把测试身份写入实际玩家库。"""
        super().setUp()
        self.client = Client(HTTP_HOST=f'127.0.0.1:{settings.NZ_PORT}')
        service = patch('revival.views.Service', return_value=self.s)
        service.start()
        self.addCleanup(service.stop)

    def post(self, path, data, token=None, **headers):
        """通过标准 JSON 与防跨站头发送真实 Django 请求。"""
        if token:
            headers['HTTP_AUTHORIZATION'] = 'Bearer '+token
        return self.client.post(path, data=data, content_type='application/json', HTTP_X_NZ_REQUEST='1', **headers)

    def test_public_portal_contains_exact_seven_maps_without_admin_or_player_data(self):
        """匿名可读首发七图与空活动，联盟变体160关闭；不能借公开路由读取监控。"""
        result = self.client.get('/api/public/portal')
        self.assertEqual(result.status_code, 200)
        body = result.json()
        self.assertEqual({r['map_id'] for r in body['maps']}, {113,164,191,141,140,168,96})
        self.assertEqual(body['campaigns'], [])
        self.assertFalse(body['capabilities']['native_gameplay'])
        self.assertNotIn('email', str(body))
        self.assertEqual(self.client.get('/api/views/monitoring').status_code, 401)
        self.assertEqual(self.client.get('/api/public/monitoring').status_code, 404)

    def test_player_envelope_rejects_admin_view_and_only_reads_own_inventory(self):
        """对象信封给桌面用，仍需玩家身份；不能用 view 参数越权读取用户和主机。"""
        aid, token = self.player()
        result = self.post('/api/player/view', {'view':'me'}, token)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['data']['id'], aid)
        self.assertIsNone(result.json()['data']['level'])
        for view in ('users', 'monitoring', 'campaign-admin'):
            self.assertEqual(self.post('/api/player/view', {'view':view}, token).status_code, 403)
        self.assertEqual(self.post('/api/player/view', {'view':'me'}, self.owner).status_code, 403)

    def test_configured_https_origin_is_exact_and_cross_origin_remains_rejected(self):
        """仅部署配置中的HTTPS Host和Origin被接受，其他子域或HTTP来源仍拒绝。"""
        with override_settings(NZ_PUBLIC_ORIGIN='https://portal.example.test', NZ_PUBLIC_HOST='portal.example.test'):
            for origin, status in (('https://portal.example.test',200),('http://portal.example.test',403),('https://other.example.test',403)):
                result = self.client.get('/api/public/portal', HTTP_HOST='portal.example.test', HTTP_ORIGIN=origin)
                self.assertEqual(result.status_code, status)
        self.assertEqual(self.client.get('/api/public/portal', HTTP_HOST='portal.example.test').status_code, 403)
