"""采集器网络边界：精确端点、重定向拒绝和回环直连，避免系统代理吞掉本机采样。"""
import unittest
from unittest.mock import patch
import urllib.request
from types import SimpleNamespace

from tools.monitor_agent import endpoint, transport, sample, NoRedirect


class MonitorAgentTests(unittest.TestCase):
    """只检查传输构造和URL限制，不发出网络请求、不访问真实监控凭据。"""

    def test_loopback_transport_disables_environment_proxy_and_redirects(self):
        """本机HTTP隧道无论NO_PROXY是否存在均直连，同时继续禁止重定向。"""
        with patch('tools.monitor_agent.urllib.request.build_opener') as build:
            transport(endpoint('http://127.0.0.1:8875'))
        handlers = build.call_args.args
        self.assertTrue(any(isinstance(value, NoRedirect) for value in handlers))
        self.assertEqual(next(value.proxies for value in handlers
            if isinstance(value, urllib.request.ProxyHandler)), {})

    def test_external_https_preserves_operator_proxy_and_forbids_unsafe_url(self):
        """公网只允许HTTPS且不能夹带用户信息/参数；代理配置不被采集器静默覆盖。"""
        with patch('tools.monitor_agent.urllib.request.build_opener') as build:
            transport(endpoint('https://portal.example.test'))
        self.assertFalse(any(isinstance(value, urllib.request.ProxyHandler) for value in build.call_args.args))
        for value in ('http://external.example', 'https://user@example.test', 'https://example.test/?token=x'):
            with self.subTest(url=value), self.assertRaises(ValueError):
                endpoint(value)

    def test_missing_windows_service_does_not_interrupt_host_sample(self):
        """模拟Windows服务消失/拒绝访问的psutil异常，仍上报主机数据且仅该服务UNKNOWN。"""
        class ServiceReadError(Exception):
            """人工替身对应psutil.Error，不模拟真实Windows采集成功。"""

        fake = SimpleNamespace(Error=ServiceReadError,
            virtual_memory=lambda: SimpleNamespace(total=1024, available=512),
            disk_partitions=lambda all: [],
            net_io_counters=lambda pernic, nowrap: {'ethernet': SimpleNamespace(bytes_recv=100, bytes_sent=50)},
            net_if_stats=lambda: {'ethernet': SimpleNamespace(isup=True)},
            cpu_percent=lambda interval: 5,
            win_service_get=unittest.mock.Mock(side_effect=ServiceReadError))
        with patch('tools.monitor_agent.platform.system', return_value='Windows'):
            data = sample(fake, 'fixture', 'boot', 0, [], ['missing-fixture'])
        self.assertEqual(data['services'], {'missing-fixture': 'UNKNOWN'})
        self.assertEqual(data['memory_used'], 512)
