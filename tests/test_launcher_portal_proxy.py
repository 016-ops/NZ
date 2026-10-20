"""桌面玩家代理隔离回归；页面不获得令牌，也不能把代理变为通用远端请求。"""
from unittest import TestCase
from unittest.mock import Mock
from tools.native_launcher import LauncherController


class LauncherPortalProxyTests(TestCase):
    """以伪网络客户端验证权限边界；不宣称桌面安装或真实原生启动通过。"""
    def setUp(self):
        """内存中放置仅本例使用的源会话，退出后没有持久凭据。"""
        self.client = Mock()
        self.controller = LauncherController(self.client)
        self.controller.session = 'unit-private-token'

    def test_read_uses_fixed_envelope_and_never_returns_token(self):
        """库存读取由本机附加会话；返回给渲染层的只有业务数据。"""
        self.client.request.return_value = {'data': {'items':[]}}
        result = self.controller.portal('view', {'view':'player-inventory','query':{'limit':20}})
        self.assertEqual(result, {'items':[]})
        self.client.request.assert_called_once_with('/api/player/view',
            {'view':'player-inventory','query':{'limit':20}}, 'unit-private-token')
        self.assertNotIn('unit-private-token', str(result))

    def test_admin_actions_paths_and_query_injection_are_rejected(self):
        """只允许枚举玩家视图/动作，任意管理命令、路径和服务器地址都不能被转发。"""
        invalid = [
            ('view',{'view':'users'}), ('view',{'view':'player-inventory','query':{'account_id':'other'}}),
            ('view',{'view':'me','path':'https://other.invalid'}),
            ('command',{'action':'asset.grant','payload':{},'metadata':{}}),
        ]
        for operation, data in invalid:
            with self.assertRaises(ValueError):
                self.controller.portal(operation,data)
        self.client.request.assert_not_called()
        self.controller.session = None
        with self.assertRaises(RuntimeError):
            self.controller.portal('view',{'view':'me'})
