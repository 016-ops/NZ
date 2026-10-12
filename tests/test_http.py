import http.client
import json
import threading
from unittest import TestCase
from revival.http import Limits,Server
from .test_control_plane import Base


class HttpTests(Base):
    def setUp(self):
        super().setUp()
        self.server=Server(('127.0.0.1',0),self.s)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=5)
        super().tearDown()

    def request(self,path,body=None,headers=None,method=None):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_address[1],timeout=10)
        request_headers={'Content-Type':'application/json','X-NZ-Request':'1'}
        request_headers.update(headers or {})
        c.request(method or ('POST' if body is not None else 'GET'),path,
                  body=json.dumps(body) if body is not None and not isinstance(body,str) else body,headers=request_headers)
        result=c.getresponse();data=result.read();status=result.status;response_headers=dict(result.getheaders());c.close()
        return status,data,response_headers

    def test_health_security_headers_and_static_page(self):
        status,data,headers=self.request('/health')
        self.assertEqual(status,200);self.assertFalse(json.loads(data)['retail_compatible'])
        self.assertEqual(headers['Cache-Control'],'no-store');self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
        status,data,headers=self.request('/')
        self.assertEqual(status,200);self.assertIn('本地控制台'.encode(),data)
        self.assertEqual(self.request('/../revival/schema.sql')[0],404)

    def test_csrf_origin_dns_rebinding_and_bad_body(self):
        self.assertEqual(self.request('/api/login',{},headers={'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.request('/api/login',{},headers={'X-NZ-Request':''})[0],403)
        self.assertEqual(self.request('/health',headers={'Host':'evil.example'})[0],403)
        self.assertEqual(self.request('/api/login','{"login":"a","login":"b"}')[0],400)
        self.assertEqual(self.request('/api/login','{"x":NaN}')[0],400)
        self.assertEqual(self.request('/api/login','a'*65537)[0],413)

    def test_real_http_auth_permissions_command_and_logout(self):
        a,ta=self.player()
        code,data,_=self.request('/api/views/me',headers={'Authorization':'Bearer '+ta})
        self.assertEqual(code,200);self.assertEqual(json.loads(data)['id'],a)
        code,data,_=self.request('/api/command',self.meta(action='asset.grant',target_account_id=a,catalog_version=self.s.catalog['id'],changes=[{'type':'currency','code':'GP','delta':1}]),headers={'Authorization':'Bearer '+ta})
        self.assertEqual(code,403);self.assertEqual(self.balance(a),0)
        code,data,_=self.request('/api/logout',{},headers={'Authorization':'Bearer '+ta})
        self.assertEqual(code,200);self.assertEqual(self.request('/api/views/me',headers={'Authorization':'Bearer '+ta})[0],401)
        self.assertTrue(any(x['result']=='REJECTED'for x in self.s.query(self.owner,'audit')))

    def test_public_bind_and_untrusted_node_results_rejected(self):
        with self.assertRaises(ValueError):Server(('0.0.0.0',0),self.s)
        a,ta=self.player()
        self.assertEqual(self.request('/api/node/settlement',{'match_id':'unknown','epoch':'old','outcome':'VICTORY'},headers={'Authorization':'Bearer '+ta})[0],403)

    def test_bounded_rate_limiter(self):
        limits=Limits()
        self.assertTrue(limits.allow('one',maximum=1));self.assertFalse(limits.allow('one',maximum=1))
        for i in range(1100):limits.allow(i)
        self.assertEqual(len(limits.buckets),1024)
