"""通过真实 Waitress/HTTP 验证新增节点路由、令牌范围及事件确认。"""
import json
import threading
from django.core.wsgi import get_wsgi_application
from django.test import override_settings
from revival.server import local_server
from .test_control_plane import Base
from . import test_http, test_pve


class NodeHttpTests(Base):
    """使用真实 MySQL 的独立合成规则环境，HTTP 仍经过同源和 JSON 边界。"""
    pve_profile = True

    def test_native_contract_routes_and_acknowledgement_scope(self):
        """节点可重放就绪/事件并确认自己的通知；玩家不能替代节点提交或确认。"""
        test_pve.PveTests.start(self)
        server = local_server(get_wsgi_application(), port=0)
        self.server = server
        override = override_settings(NZ_PROFILE='synthetic', NZ_PVE_TEST_PROFILE=True, NZ_PORT=int(server.effective_port))
        override.enable()
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            headers = {'Authorization': 'Bearer ' + self.node['node_token']}
            bad = {'Authorization': 'Bearer ' + self.ta}
            request = lambda path, data, h=headers: test_http.HttpTests.request(self, path, data, h)
            self.assertEqual(request('/api/node/ready', self.ready_data, bad)[0], 403)
            self.assertEqual(request('/api/node/ready', self.ready_data)[0], 200)
            invalid = dict(self.ready_data, health={'map_loaded': 1, 'world_ready': True, 'network_ready': True})
            self.assertEqual(request('/api/node/ready', invalid)[0], 409)
            body = test_pve.PveTests.envelope(self, 'tick')
            self.assertEqual(request('/api/node/events', body, bad)[0], 403)
            code, result, _ = request('/api/node/events', body)
            self.assertEqual(code, 200)
            self.assertFalse(json.loads(result)['retail_verified'])
            operation = self.grant(self.a, [{'type': 'currency', 'code': 'GP', 'delta': 1}])
            identity = {key: self.node[key] for key in ('match_id', 'epoch')}
            code, result, _ = request('/api/node/pending', identity)
            self.assertEqual(code, 200)
            event = next(row for row in json.loads(result) if row['type'] == 'assets.changed')
            ack = dict(identity, event_key=event['event_key'], operation_id=operation['operation_id'])
            self.assertEqual(request('/api/node/ack', ack, bad)[0], 403)
            self.assertEqual(request('/api/node/ack', ack)[0], 200)
        finally:
            server.task_dispatcher.shutdown()
            # 通过事件触发器在同一线程关socket，不在select等待期间跨线程关fd。
            server.trigger.pull_trigger(server.close)
            thread.join(timeout=5)
            override.disable()
