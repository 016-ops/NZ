"""通过本测试进程真实绑定UDP验证Windows端点表和所有权过滤，不发送网络数据包。"""
import os
import socket
import unittest

from tools.native_tcp_probe import sample_owned_udp


class SelfOnly:
    """测试夹具仅允许本测试Python进程；真实探针仍由Windows Job提供所属PID。"""

    def pids(self):
        """返回当前进程，不枚举或操作其他程序。"""
        return [os.getpid()]


class EmptyGroup:
    """模拟已清理Job，不应把全系统端点表当作本Job的记录。"""

    def pids(self):
        """显式空集合触发无所属端点路径。"""
        return []


class NativeUdpProbeTests(unittest.TestCase):
    """真实绑定应被正确解码，空所属集合始终为空；两者不证明任何游戏握手。"""

    def test_real_owned_loopback_udp_port_detected_without_sending(self):
        """使用临时回环端口，校验网络字节序和端点PID，finally由上下文关闭socket。"""
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as owned:
            owned.bind(('127.0.0.1',0))
            port=owned.getsockname()[1]
            result=sample_owned_udp(SelfOnly())
            self.assertTrue(result['verified'])
            self.assertIn({'pid':os.getpid(),'local_address':'127.0.0.1','local_port':port},result['endpoints'])
            self.assertTrue(all(row['pid']==os.getpid() for row in result['endpoints']))

    def test_empty_owner_set_never_includes_system_endpoints(self):
        """无自有PID时返回已验证空集合，不读取其他进程地址。"""
        self.assertEqual(sample_owned_udp(EmptyGroup()),{'endpoints':[], 'verified':True})
