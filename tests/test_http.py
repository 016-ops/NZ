"""真实回环 HTTP 验收；请求通过与交付服务相同的 Waitress 和 Django 边界。"""
import http.client
import json
import threading
from unittest import TestCase
from revival.middleware import Limits
from revival.server import local_server
from django.core.wsgi import get_wsgi_application
from django.test import override_settings
from .test_control_plane import Base


class HttpTests(Base):
    """通过真实回环 Waitress 连接验证 HTTP 契约、请求边界及权限拒绝。"""
    def setUp(self):
        """建立本例独立业务环境和依赖；所有业务数据使用真实 MySQL 测试库。"""
        super().setUp()
        self.server=local_server(get_wsgi_application(),port=0)
        self.setting_override=override_settings(NZ_PROFILE='synthetic',NZ_PORT=int(self.server.effective_port))
        self.setting_override.enable()
        self.thread=threading.Thread(target=self.server.run,daemon=True);self.thread.start()

    def tearDown(self):
        """释放本例服务、连接和临时资源；清理由测试生命周期完成，不影响当前业务库。"""
        self.server.task_dispatcher.shutdown()
        # 关闭动作在Waitress事件线程执行，避免主线程关闭仍被select监视的fd造成EBADF。
        self.server.trigger.pull_trigger(self.server.close)
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive(), 'HTTP测试服务必须在清理后退出')
        self.setting_override.disable()
        super().tearDown()

    def request(self,path,body=None,headers=None,method=None):
        """通过真实 HTTP 连接发送正文和头，返回状态、原始响应与头；网络错误向测试传播。"""
        c=http.client.HTTPConnection('127.0.0.1',int(self.server.effective_port),timeout=10)
        request_headers={'Content-Type':'application/json','X-NZ-Request':'1'}
        request_headers.update(headers or {})
        c.request(method or ('POST' if body is not None else 'GET'),path,
                  body=json.dumps(body) if body is not None and not isinstance(body,str) else body,headers=request_headers)
        result=c.getresponse();data=result.read();status=result.status;response_headers=dict(result.getheaders());c.close()
        return status,data,response_headers

    def test_health_security_headers_and_static_page(self):
        """验证场景：健康接口及前端静态页真实可访问，响应携带安全头。"""
        status,data,headers=self.request('/health')
        self.assertEqual(status,200);self.assertFalse(json.loads(data)['retail_compatible'])
        self.assertEqual(headers['Cache-Control'],'no-store');self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
        status,data,headers=self.request('/')
        # 首页已升级为公共门户；仍检查同一React入口和产品标识，不固定旧管理页标题。
        self.assertEqual(status,200);self.assertIn('NZ Revival'.encode(),data)
        self.assertEqual(self.request('/../revival/schema.sql')[0],404)

    def test_csrf_origin_dns_rebinding_and_bad_body(self):
        """验证场景：跨 Origin、错误 Host、重复 JSON 字段、非有限数字和超限体被拒绝。"""
        self.assertEqual(self.request('/api/login',{},headers={'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.request('/api/login',{},headers={'X-NZ-Request':''})[0],403)
        self.assertEqual(self.request('/health',headers={'Host':'evil.example'})[0],403)
        self.assertEqual(self.request('/api/login','{"login":"a","login":"b"}')[0],400)
        self.assertEqual(self.request('/api/login','{"x":NaN}')[0],400)
        self.assertEqual(self.request('/api/login','a'*65537)[0],413)

    def test_real_http_auth_permissions_command_and_logout(self):
        """验证场景：真实 HTTP 下玩家不能越权发放资产，退出后会话失效。"""
        a,ta=self.player()
        code,data,_=self.request('/api/views/me',headers={'Authorization':'Bearer '+ta})
        self.assertEqual(code,200);self.assertEqual(json.loads(data)['id'],a)
        code,data,_=self.request('/api/command',self.meta(action='asset.grant',target_account_id=a,catalog_version=self.s.catalog['id'],changes=[{'type':'currency','code':'GP','delta':1}]),headers={'Authorization':'Bearer '+ta})
        self.assertEqual(code,403);self.assertEqual(self.balance(a),0)
        code,data,_=self.request('/api/logout',{},headers={'Authorization':'Bearer '+ta})
        self.assertEqual(code,200);self.assertEqual(self.request('/api/views/me',headers={'Authorization':'Bearer '+ta})[0],401)
        self.assertTrue(any(x['result']=='REJECTED'for x in self.s.query(self.owner,'audit')))

    def test_public_bind_and_untrusted_node_results_rejected(self):
        """验证场景：公共监听地址及非可信节点结算均被拒绝。"""
        with self.assertRaises(ValueError):local_server(get_wsgi_application(),host='0.0.0.0',port=0)
        a,ta=self.player()
        self.assertEqual(self.request('/api/node/settlement',{'match_id':'unknown','epoch':'old','outcome':'VICTORY'},headers={'Authorization':'Bearer '+ta})[0],403)

    def test_bounded_rate_limiter(self):
        """验证场景：限流窗口拒绝超额请求，客户端桶数量始终有界。"""
        limits=Limits()
        self.assertTrue(limits.allow('one',maximum=1));self.assertFalse(limits.allow('one',maximum=1))
        for i in range(1100):limits.allow(i)
        self.assertEqual(len(limits.buckets),1024)

    def test_transfer_encoding_is_rejected_before_wsgi_and_size_boundary_is_exact(self):
        """实际发出分块传输，防止 Waitress 解码并删除头后绕过中间件；64 KiB 边界保持一致。"""
        connection=http.client.HTTPConnection('127.0.0.1',int(self.server.effective_port),timeout=10)
        connection.putrequest('POST','/api/login')
        connection.putheader('Content-Type','application/json')
        connection.putheader('X-NZ-Request','1')
        connection.putheader('Transfer-Encoding','chunked')
        connection.endheaders(b'2\r\n{}\r\n0\r\n\r\n')
        result=connection.getresponse()
        self.assertEqual(result.status,400)
        self.assertEqual(json.loads(result.read())['error'],'INVALID')
        connection.close()
        self.assertEqual(self.request('/api/login','{}'+' '*65534)[0],401)
        status,body,_=self.request('/api/login','{}'+' '*65535)
        self.assertEqual(status,413);self.assertEqual(json.loads(body)['error'],'BODY_SIZE')
