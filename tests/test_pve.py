"""真实 MySQL 上的合成 PvE 规则、节点契约及奖励测试；不计为原客户端联机证据。"""
import json
from django.db.models import Q
from revival.assets import settle
from revival.nodes import ready, pending, acknowledge
from revival.pve import events, recover_timeouts
from revival.rooms import heartbeat
from revival.service import Fault, Service, identifier
from .support import DatabasePool
from .test_control_plane import Base


class PveTests(Base):
    """所有数值和地图来自独立 test-pve-v1；两个账号共同作用于一份持久状态。"""
    pve_profile = True

    def start(self, mode='survival'):
        """通过实际领域入口创建两名玩家、冻结装备、分配并认证一个合成节点。"""
        self.a, self.ta = self.player('pve-a')
        self.b, self.tb = self.player('pve-b')
        for account, token in [(self.a, self.ta), (self.b, self.tb)]:
            self.grant(account, [{'type': 'item', 'item_key': 'test-rifle', 'quantity': 1}, {'type': 'item', 'item_key': 'test-token', 'quantity': 1}])
            inventory = self.s.query(token, 'me')['inventory']
            item = next(row for row in inventory if row['item_key'] == 'test-rifle')
            self.command('inventory.equip', {'catalog_version': self.s.catalog['id'], 'instance_id': item['id'], 'slot': 'primary'}, token)
        room = self.command('room.create', {'map_key': 'test-' + mode}, self.ta)
        self.room_id = room['room_id']
        self.command('room.join', {'room_id': self.room_id}, self.tb)
        for token in (self.ta, self.tb):
            self.command('room.ready', {'room_id': self.room_id, 'ready': True}, token)
        self.now += 30
        revision = self.s.query(self.ta, 'rooms')[0]['revision']
        allocation = self.command('room.start', {'room_id': self.room_id, 'expected_revision': revision}, self.ta)
        self.node = self.command('node.enroll', {'match_id': allocation['match_id'], 'current_password': self.password})
        request = {key: self.node[key] for key in ('match_id', 'instance_id', 'epoch', 'snapshot_hash', 'map_key')}
        request['health'] = {'map_loaded': True, 'world_ready': True, 'network_ready': True}
        self.ready_data = request
        self.state = ready(self.s, self.node['node_token'], request)['pve']
        self.sequence = 0

    def envelope(self, kind, payload=None):
        """构造当前阶段的下一个事件，测试可以保留原请求模拟断线后重放。"""
        return {'match_id': self.node['match_id'], 'epoch': self.node['epoch'], 'event_id': identifier(),
                'sequence': self.sequence + 1, 'stage_id': self.state['stage_id'], 'attempt_id': self.state['attempt_id'],
                'kind': kind, 'payload': payload or {}}

    def emit(self, kind, payload=None):
        """每个事件前续租，只有服务真正提交成功后更新本地序号和观察到的状态。"""
        self.now += 1
        heartbeat(self.s, self.node['node_token'], self.node['match_id'], self.node['epoch'])
        data = self.envelope(kind, payload)
        result = events(self.s, self.node['node_token'], data)
        self.sequence += 1
        self.state = result['state']
        return result, data

    def clear(self):
        """通过攻击和真实规则推进合成全图，禁止直接发送胜利、奖励或 GM 结束事件。"""
        for _ in range(60):
            if self.state['phase'] == 'VICTORY':
                return self.result
            if self.state['enemies']:
                alive = [key for key, value in self.state['players'].items() if value['eligible'] and value['hp'] > 0]
                result, data = self.emit('attack', {'account_id': alive[self.sequence % len(alive)], 'enemy_id': next(iter(self.state['enemies']))})
            elif self.state['pending']:
                result, data = self.emit('tick')
            else:
                result, data = self.emit('objective.activate', {'account_id': self.a, 'objective_id': 'gate'})
            self.result, self.last_data = result, data
        self.fail('Synthetic rule director did not finish')

    def wipe(self):
        """让服务端 AI 按时钟攻击直到团灭/目标损坏，以验证失败而非手改状态。"""
        for _ in range(30):
            self.emit('tick')
            if self.state['phase'] == 'FAILED':
                return
        self.fail('Expected authoritative failure')

    def revive_request(self):
        """选取付款玩家自己的最后一个复活道具并使用当前阶段标识。"""
        item = next(row for row in self.s.query(self.ta, 'me')['inventory'] if row['item_key'] == 'test-token')
        return self.meta(match_id=self.node['match_id'], stage_id=self.state['stage_id'], attempt_id=self.state['attempt_id'],
                         instance_id=item['id'], catalog_version=self.s.catalog['id'])

    def reload(self):
        """从新服务实例重新读取已提交状态，模拟服务进程重启。"""
        self.s = Service(test_profile=True, test_pve=True, clock=lambda: self.now)
        self.state = self.s.query(self.owner, 'pve', match_id=self.node['match_id'])[0]['state']

    def test_survival_two_players_one_enemy_boss_and_exactly_once_rewards(self):
        """两名玩家共同扣减同一敌人生命，自动通过波次/Boss，结算重放只到账一次。"""
        self.start()
        self.emit('tick')
        enemy = next(iter(self.state['enemies']))
        self.emit('attack', {'account_id': self.a, 'enemy_id': enemy})
        self.assertEqual(self.state['enemies'][enemy]['hp'], 25)
        self.emit('attack', {'account_id': self.b, 'enemy_id': enemy})
        self.assertNotIn(enemy, self.state['enemies'])
        result = self.clear()
        self.now += 100
        self.reload()
        replay = events(self.s, self.node['node_token'], self.last_data)
        self.assertEqual(replay, result)
        self.assertEqual((self.balance(self.a), self.balance(self.b)), (100, 100))
        self.assertEqual(self.s.query(self.ta, 'rooms')[0]['state'], 'OPEN')

    def test_encirclement_gate_advances_once_and_rejects_old_stage(self):
        """机关在清空队列后解锁；重复触发原事件返回原结果，旧阶段新事件拒绝。"""
        self.start('encirclement')
        self.emit('tick')
        enemy = next(iter(self.state['enemies']))
        for account in (self.a, self.b):
            self.emit('attack', {'account_id': account, 'enemy_id': enemy})
        old = self.envelope('objective.activate', {'account_id': self.a, 'objective_id': 'gate'})
        result, data = self.emit('objective.activate', {'account_id': self.a, 'objective_id': 'gate'})
        self.assertEqual(self.state['stage_id'], 'boss-stage')
        self.assertEqual(events(self.s, self.node['node_token'], data), result)
        old.update(event_id=identifier(), sequence=self.sequence + 1)
        with self.assertRaises(Fault) as error:
            events(self.s, self.node['node_token'], old)
        self.assertEqual(error.exception.code, 'STALE_STAGE')
        self.clear()

    def test_defense_facility_failure_and_vehicle_driver_isolation(self):
        """载具席位只属于一人，非驾驶员不能移动；目标归零会失败且不发胜利奖励。"""
        self.start('defense')
        self.emit('vehicle.enter', {'account_id': self.a, 'vehicle_id': 'test-turret'})
        with self.assertRaises(Fault):
            self.emit('vehicle.enter', {'account_id': self.b, 'vehicle_id': 'test-turret'})
        with self.assertRaises(Fault):
            self.emit('vehicle.move', {'account_id': self.b, 'vehicle_id': 'test-turret', 'position': [1, 0]})
        self.emit('vehicle.move', {'account_id': self.a, 'vehicle_id': 'test-turret', 'position': [1, 0]})
        self.wipe()
        self.assertEqual(self.state['target_hp'], 0)
        while self.state['phase'] != 'DEFEAT':
            self.emit('tick')
        self.assertEqual((self.balance(self.a), self.balance(self.b)), (0, 0))

    def test_failure_revive_race_and_failed_restart_refund_once(self):
        """两次并发续局只扣最后一个道具一次，节点重开失败后只补偿一次。"""
        self.start()
        self.wipe()
        requests = [self.revive_request(), self.revive_request()]
        def submit(data):
            """并发请求使用独立 MySQL 连接，保留真实业务拒绝。"""
            try:
                return self.s.command(self.ta, 'pve.revive', data)
            except Fault as error:
                return error.code
        with DatabasePool(max_workers=2) as pool:
            results = list(pool.map(submit, requests))
        committed = [value for value in results if isinstance(value, dict)]
        self.assertEqual(len(committed), 1)
        self.reload()
        result, data = self.emit('restart.failed', {'revive_operation_id': committed[0]['revive_operation_id']})
        self.assertEqual(self.state['revive']['status'], 'REFUNDED')
        self.assertEqual(events(self.s, self.node['node_token'], data), result)
        items = self.s.query(self.ta, 'me')['inventory']
        self.assertEqual(sum(row['quantity'] for row in items if row['item_key'] == 'test-token'), 1)

    def test_successful_restart_gets_new_attempt_and_rejects_stale_packets(self):
        """成功续局建立新 attempt；旧攻击包不影响新敌人，最终奖励仍只有一份。"""
        self.start()
        self.wipe()
        old_attempt = self.state['attempt_id']
        operation = self.s.command(self.ta, 'pve.revive', self.revive_request())
        self.reload()
        self.emit('restart.ready', {'revive_operation_id': operation['revive_operation_id']})
        self.assertNotEqual(self.state['attempt_id'], old_attempt)
        stale = self.envelope('tick')
        stale['attempt_id'] = old_attempt
        with self.assertRaises(Fault):
            events(self.s, self.node['node_token'], stale)
        self.clear()
        self.assertEqual(self.balance(self.a), 100)

    def test_lost_node_restart_timeout_refunds_after_service_restart(self):
        """扣道具后节点失联，维护进程在重启后仍能补偿，重复维护不再发道具。"""
        self.start()
        self.wipe()
        self.s.command(self.ta, 'pve.revive', self.revive_request())
        self.now += 11
        self.reload()
        self.assertEqual(recover_timeouts(self.s)['refunded_or_reviewed'], 1)
        self.assertEqual(recover_timeouts(self.s)['refunded_or_reviewed'], 0)

    def test_node_auth_ordering_and_forged_result_are_rejected(self):
        """玩家 token、旧 epoch、乱序和直接胜利都不能驱动 PvE 或奖励。"""
        self.start()
        data = self.envelope('tick')
        for token, body in [(self.ta, data), (self.node['node_token'], dict(data, epoch='old')),
                            (self.node['node_token'], dict(data, sequence=2))]:
            with self.assertRaises(Fault):
                events(self.s, token, body)
        with self.assertRaises(Fault):
            settle(self.s, self.node['node_token'], {'match_id': self.node['match_id'], 'epoch': self.node['epoch'], 'outcome': 'VICTORY'})
        result, original = self.emit('tick')
        with self.assertRaises(Fault):
            events(self.s, self.node['node_token'], dict(original, kind='attack'))
        self.assertEqual(events(self.s, self.node['node_token'], original), result)

    def test_repeated_moves_cannot_reuse_the_same_server_time_budget(self):
        """同一时刻换事件 ID 不能多领位移额度；拒绝事件不占用下一个连续序号。"""
        self.start()
        self.emit('move', {'account_id': self.a, 'position': [1, 0]})
        with self.assertRaises(Fault) as error:
            events(self.s, self.node['node_token'], self.envelope('move', {'account_id': self.a, 'position': [2, 0]}))
        self.assertEqual(error.exception.code, 'MOVEMENT')
        self.emit('move', {'account_id': self.a, 'position': [2, 0]})
        self.assertEqual(self.state['players'][self.a]['position'], [2, 0])

    def test_node_delivery_checks_scope_version_and_ban_removes_combat_authority(self):
        """资产与制裁逐实例确认；错误版本不算同步，封禁后旧战斗名单不再有效。"""
        self.start()
        operation = self.grant(self.a, [{'type': 'currency', 'code': 'GP', 'delta': 3}])
        identity = {key: self.node[key] for key in ('match_id', 'epoch')}
        event = next(row for row in pending(self.s, self.node['node_token'], identity) if row['type'] == 'assets.changed')
        with self.assertRaises(Fault):
            acknowledge(self.s, self.ta, dict(identity, event_key=event['event_key'], operation_id=operation['operation_id']))
        with self.assertRaises(Fault):
            acknowledge(self.s, self.node['node_token'], dict(identity, event_key=event['event_key'], operation_id='wrong'))
        acknowledge(self.s, self.node['node_token'], dict(identity, event_key=event['event_key'], operation_id=operation['operation_id']))
        self.command('sanction.create', {'target_account_id': self.a, 'expires_at': None})
        event = next(row for row in pending(self.s, self.node['node_token'], identity) if row['type'] == 'security.revoked')
        acknowledge(self.s, self.node['node_token'], dict(identity, event_key=event['event_key'], security_version=event['payload']['security_version']))
        self.emit('tick')
        self.assertFalse(self.state['players'][self.a]['eligible'])

    def test_current_client_profile_never_accepts_synthetic_node_events(self):
        """当前客户端模式在没有原生证据时始终拒绝节点就绪和事件。"""
        self.start()
        self.s.test_profile = False
        with self.assertRaises(Fault) as error:
            events(self.s, self.node['node_token'], self.envelope('tick'))
        self.assertEqual(error.exception.code, 'COMPATIBILITY_BLOCKED')

    def test_disconnect_during_boss_keeps_other_player_and_no_reward_for_leaver(self):
        """Boss 战中一人掉线不会终止另一人对局，退出者不获得最终奖励。"""
        self.start()
        self.emit('tick')
        while self.state['enemies']:
            self.emit('attack', {'account_id': self.a, 'enemy_id': next(iter(self.state['enemies']))})
        self.emit('tick')
        boss = next(iter(self.state['enemies']))
        self.emit('attack', {'account_id': self.a, 'enemy_id': boss})
        self.emit('disconnect', {'account_id': self.a})
        self.clear()
        self.assertEqual((self.balance(self.a), self.balance(self.b)), (0, 100))

    def test_ten_synthetic_rounds_have_new_entities_events_and_unique_settlements(self):
        """同一对账号连续十局，上一局状态不进入新局；这是合成回归，不是实机十局。"""
        self.start()
        seen_matches, seen_attempts = set(), set()
        for index in range(10):
            self.assertNotIn(self.node['match_id'], seen_matches)
            self.assertNotIn(self.state['attempt_id'], seen_attempts)
            seen_matches.add(self.node['match_id'])
            seen_attempts.add(self.state['attempt_id'])
            self.clear()
            if index == 9:
                break
            self.now += 30
            for token in (self.ta, self.tb):
                self.command('room.ready', {'room_id': self.room_id, 'ready': True}, token)
            revision = self.s.query(self.ta, 'rooms')[0]['revision']
            allocation = self.command('room.start', {'room_id': self.room_id, 'expected_revision': revision}, self.ta)
            self.node = self.command('node.enroll', {'match_id': allocation['match_id'], 'current_password': self.password})
            body = {key: self.node[key] for key in ('match_id', 'instance_id', 'epoch', 'snapshot_hash', 'map_key')}
            body['health'] = {'map_loaded': True, 'world_ready': True, 'network_ready': True}
            self.state = ready(self.s, self.node['node_token'], body)['pve']
            self.sequence = 0
        self.assertEqual((self.balance(self.a), self.balance(self.b)), (1000, 1000))
        with self.s.db.transaction() as c:
            self.assertEqual(c.count('settlements').fetchone()[0], 10)

    def test_two_live_pve_rooms_keep_entities_tokens_progress_and_rewards_separate(self):
        """两份活跃合成 PvE 同时存在；节点不能跨局写入，首房结算不改次房状态或余额。"""
        self.start()
        first_accounts = (self.a, self.b)
        second_accounts = [self.player('other-' + name) for name in ('a', 'b')]
        for account, token in second_accounts:
            self.grant(account, [{'type': 'item', 'item_key': 'test-rifle', 'quantity': 1}])
            item = next(row for row in self.s.query(token, 'me')['inventory'] if row['item_key'] == 'test-rifle')
            self.command('inventory.equip', {'catalog_version': self.s.catalog['id'], 'instance_id': item['id'], 'slot': 'primary'}, token)
        second_room = self.command('room.create', {'map_key': 'test-survival'}, second_accounts[0][1])['room_id']
        self.command('room.join', {'room_id': second_room}, second_accounts[1][1])
        for _, token in second_accounts:
            self.command('room.ready', {'room_id': second_room, 'ready': True}, token)
        revision = next(row for row in self.s.query(second_accounts[0][1], 'rooms') if row['id'] == second_room)['revision']
        match = self.command('room.start', {'room_id': second_room, 'expected_revision': revision}, second_accounts[0][1])
        # 通过真实操作绑定邮件码确认，不推进对局时钟导致另一份节点租约过期。
        second = self.command('node.enroll', {'match_id': match['match_id'], 'current_password': self.password})
        body = {key: second[key] for key in ('match_id', 'instance_id', 'epoch', 'snapshot_hash', 'map_key')}
        body['health'] = {'map_loaded': True, 'world_ready': True, 'network_ready': True}
        second_state = ready(self.s, second['node_token'], body)['pve']
        forged = dict(self.envelope('tick'), match_id=second['match_id'], epoch=second['epoch'],
                      stage_id=second_state['stage_id'], attempt_id=second_state['attempt_id'])
        with self.assertRaises(Fault) as error:
            events(self.s, self.node['node_token'], forged)
        self.assertEqual(error.exception.code, 'FORBIDDEN')
        self.clear()
        self.assertEqual([self.balance(account) for account in first_accounts], [100, 100])
        self.assertEqual([self.balance(account) for account, _ in second_accounts], [0, 0])
        with self.s.db.transaction() as c:
            self.assertEqual(json.loads(c.select('pve_runs', Q(match_id=second['match_id'])).fetchone()['state']), second_state)
        self.node, self.state, self.sequence = second, second_state, 0
        self.a, self.b = [account for account, _ in second_accounts]
        self.clear()
        self.assertEqual([self.balance(account) for account in (*first_accounts, self.a, self.b)], [100] * 4)
