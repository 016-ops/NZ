"""独立 MySQL 主机监控回归；验证鉴权、去重、差分、失联及敏感字段隔离。"""
from revival.service import Fault
from revival.monitoring import ingest
from .test_control_plane import Base


class MonitoringTests(Base):
    """只建立本例监控节点，不连接任何真实主机或改变其配置。"""
    def enroll(self):
        """经正常管理员命令领取一次性采样凭据。"""
        return self.command('monitor.node.enroll', {'name': '测试主机', 'platform': 'linux'})

    def sample(self, node, sequence=0, **changes):
        """创建与受信 Agent 相同的累计字节采样，时间随测试时钟推进。"""
        return dict(node_id=node['node_id'], boot_id='boot-a', sequence=sequence, measured_at=self.now,
            cpu_percent=25, memory_total=1000, memory_used=500, rx_bytes=1000, tx_bytes=2000, **changes)

    def test_duplicate_conflict_and_bandwidth_difference(self):
        """响应丢失重试不重复聚合；同序号篡改拒绝，后样本按真实时间差计算。"""
        node = self.enroll()
        data = self.sample(node)
        ingest(self.s, node['node_token'], data)
        self.assertTrue(ingest(self.s, node['node_token'], data)['duplicate'])
        with self.assertRaises(Fault):
            ingest(self.s, node['node_token'], dict(data, cpu_percent=99))
        self.now += 15
        ingest(self.s, node['node_token'], dict(self.sample(node, 1), rx_bytes=2500, tx_bytes=5000))
        result = self.s.query(self.owner, 'monitoring', node_id=node['node_id'])
        self.assertEqual(result['nodes'][0]['rx_bps'], 100)
        self.assertEqual(result['nodes'][0]['tx_bps'], 200)
        self.assertEqual(len(result['samples']), 2)
        self.assertNotIn('token_digest', str(result))
        self.assertNotIn(node['node_token'], str(result))

    def test_stale_counter_reset_and_disabled_node(self):
        """首样本和计数器回退不显示零带宽；失联清空实时值，停用立即拒绝上报。"""
        node = self.enroll()
        ingest(self.s, node['node_token'], self.sample(node))
        self.assertIsNone(self.s.query(self.owner, 'monitoring')['nodes'][0]['rx_bps'])
        self.now += 15
        result = ingest(self.s, node['node_token'], dict(self.sample(node, 1), rx_bytes=10))
        self.assertEqual(result['network_state'], 'COUNTER_RESET')
        self.now += 60
        state = self.s.query(self.owner, 'monitoring')['nodes'][0]
        self.assertEqual(state['state'], 'STALE')
        self.assertIsNone(state['cpu_percent'])
        self.command('monitor.node.update', {'node_id': node['node_id'], 'expected_revision': 1, 'enabled': False})
        with self.assertRaises(Fault):
            ingest(self.s, node['node_token'], self.sample(node, 2))

    def test_player_cannot_read_or_enroll_and_player_token_cannot_ingest(self):
        """玩家不能读取主机状态、登记节点或冒用会话作为监控身份。"""
        _, token = self.player()
        with self.assertRaises(Fault):
            self.s.query(token, 'monitoring')
        with self.assertRaises(Fault):
            self.command('monitor.node.enroll', {'name': '攻击', 'platform': 'linux'}, token)
        node = self.enroll()
        with self.assertRaises(Fault):
            ingest(self.s, token, self.sample(node))

    def test_sequence_reversal_and_nan_are_rejected(self):
        """旧序号与非有限指标不能覆盖最新测量事实。"""
        node = self.enroll()
        ingest(self.s, node['node_token'], self.sample(node, 2))
        self.now += 15
        for data in (self.sample(node, 1), dict(self.sample(node, 3), cpu_percent=float('nan'))):
            with self.assertRaises((Fault, ValueError)):
                ingest(self.s, node['node_token'], data)

    def test_delayed_sample_is_stale_and_long_history_is_not_truncated(self):
        """刚接收的积压测量不是实时指标；48小时窗口包含前一日聚合，而不是只截最近24小时。"""
        node = self.enroll()
        ingest(self.s, node['node_token'], dict(self.sample(node), measured_at=self.now-90))
        self.assertEqual(self.s.query(self.owner, 'monitoring')['nodes'][0]['state'], 'STALE')
        with self.s.db.transaction() as c:
            c.insert('monitor_minutes', {'node_id':node['node_id'], 'minute':(self.now-30*3600)//60*60,
                'count':2, 'cpu_sum':80, 'memory_sum':100})
        result = self.s.query(self.owner, 'monitoring', node_id=node['node_id'], resolution='minute', hours=48)
        self.assertEqual(len(result['samples']), 2)
        self.assertEqual(result['samples'][0]['cpu_percent'], 40)
