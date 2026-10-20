"""门户运营的真实 MySQL 回归；覆盖规则竞争、价格事务、本人资产边界与原删除保护。

每例沿用项目可销毁测试库，不连接现有玩家库或游戏节点。商城 quote/commit 测试同时
验证原生领域接入运营 helper 后，网页会话能够安全复用原购物车，而不是生成模拟到账。
"""
from unittest.mock import patch

from django.db import DatabaseError
from django.db.models import Q

from revival import portal_operations as operations
from revival.assets import mutate
from revival.catalogs import with_native_inventory
from revival.native_rooms import catalog as room_catalog
from revival.service import Fault

from .support import DatabasePool
from .test_control_plane import Base


class PortalOperationsTests(Base):
    """用网页玩家 Session 与独立管理员验证门户领域，不借用游戏 Bearer。"""

    def setUp(self):
        """准备当前真实物品定义及两个空仓玩家，所有数据仅存在本例测试库。"""
        super().setUp()
        self.s.catalog = with_native_inventory(self.s.catalog)
        self.account, self.player_token = self.player('portal-player')
        self.other_account, self.other_token = self.player('portal-other')

    def call(self, action, *, player=False, **data):
        """模拟 Service 已鉴权并持有部署事务锁的调用边界；统一元数据由原夹具生成。"""
        payload = self.meta(**data)
        with self.s.db.transaction() as c:
            actor = self.s._actor(c, self.player_token if player else self.owner,
                                  'player' if player else '*')
            return operations.command(self.s, c, actor, action, payload)

    def read(self, view, *, player=False, **filters):
        """以真实网页会话读取管理视图或本人资产，不直接构造玩家身份。"""
        with self.s.db.transaction() as c:
            actor = self.s._actor(c, self.player_token if player else self.owner,
                                  'player' if player else '*')
            return operations.query(self.s, c, actor, view, filters)

    def grant(self, item_id, quantity=1):
        """通过统一资产入口构建真实库存实例，返回归属、版本与自动分配的稳定GID。"""
        with self.s.db.transaction() as c:
            mutate(self.s, c, {'id': 'test-fixture', 'role': 'owner'}, self.account,
                   [{'type': 'item', 'item_key': 'cn2020:' + str(item_id), 'quantity': quantity}],
                   'portal test fixture', 'TEST_FIXTURE')
        return next(row for row in self.read('player-inventory', player=True)['items']
                    if row['item_id'] == item_id)

    def fund(self):
        """给本人钱包添加有限测试余额，保留账本与资产版本，不直接改余额列。"""
        with self.s.db.transaction() as c:
            mutate(self.s, c, {'id': 'test-fixture', 'role': 'owner'}, self.account,
                   [{'type': 'currency', 'code': 'cn2020:GP', 'delta': 50000}],
                   'portal test funds', 'TEST_FIXTURE')

    def test_offer_cas_hides_restored_offers_and_does_not_mutate_fixed_catalog(self):
        """价格覆盖和下架保存不可变版本；同修订竞争失败，不能改写原目录缓存。"""
        from revival.native_features_catalog import feature_catalog
        original = list(feature_catalog()['offers']['200001']['nPriceArray'])
        saved = self.call('shop.offer.save', commodity_id=200001, expected_revision=0,
                          enabled=True, sort_order=1, prices=[123], period_hours=[24])
        self.assertEqual(saved['revision'], 1)
        with self.s.db.transaction() as c:
            effective = operations.resolve_shop_offer(self.s, c, 200001)
            self.assertEqual(effective['nPriceArray'], [123])
            self.assertEqual(effective['nAvailPreiodArray'], [24])
        self.assertEqual(feature_catalog()['offers']['200001']['nPriceArray'], original)
        with self.assertRaises(Fault) as error:
            self.call('shop.offer.save', commodity_id=200001, expected_revision=0,
                      enabled=False, sort_order=1)
        self.assertEqual(error.exception.code, 'RULE_CONFLICT')
        self.call('shop.offer.save', commodity_id=200001, expected_revision=1,
                  enabled=False, sort_order=1)
        with self.s.db.transaction() as c:
            self.assertIsNone(operations.resolve_shop_offer(self.s, c, 200001, allow_hidden=True))
        self.assertNotIn(200001, [row['commodity_id'] for row in self.read('player-shop', player=True)['items']])

    def test_two_admins_cannot_overwrite_the_same_revision(self):
        """两个独立 MySQL 连接同时保存同修订，只能一项成功，历史快照不丢失。"""
        def save(index):
            """每个竞争者重新鉴权；业务冲突作为预期结果，其他异常不能吞掉。"""
            try:
                return self.call('shop.offer.save', commodity_id=200001, expected_revision=0,
                                 enabled=True, sort_order=index, prices=[100 + index])
            except Fault as error:
                return error.code
        with DatabasePool(max_workers=2) as pool:
            results = list(pool.map(save, [1, 2]))
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertIn('RULE_CONFLICT', results)
        with self.s.db.transaction() as c:
            self.assertEqual(c.count('native_rule_revisions', Q(namespace=operations.SHOP_NAMESPACE)).fetchone()[0], 1)

    def test_shop_snapshot_is_read_once_per_transaction_and_updated_after_publish(self):
        """多行报价只加载一次规则，事务内发布后立即看到新价格，下一事务不会复用旧缓存。"""
        with self.s.db.transaction() as c:
            actor = self.s._actor(c, self.owner, '*')
            with self.assertNumQueries(1):
                for _ in range(20):
                    operations.resolve_shop_offer(self.s, c, 200001)
            operations.command(self.s, c, actor, 'shop.offer.save', self.meta(commodity_id=200001,
                expected_revision=0, enabled=True, sort_order=1, prices=[123]))
            with self.assertNumQueries(0):
                self.assertEqual(operations.resolve_shop_offer(self.s, c, 200001)['nPriceArray'], [123])
        with self.s.db.transaction() as c:
            with self.assertNumQueries(1):
                self.assertEqual(operations.shop_revision(c), 1)

    def test_period_and_price_shape_rejected_before_revision_is_written(self):
        """不能变更价格档位个数、使用布尔价格或为计数材料设置小时期限。"""
        bad = [{'prices': [1, 2]}, {'prices': [True]}, {'period_hours': [0]}]
        for extra in bad:
            with self.subTest(extra=extra), self.assertRaises(Fault):
                self.call('shop.offer.save', commodity_id=200001, expected_revision=0,
                          enabled=True, sort_order=1, **extra)
        with self.assertRaises(Fault) as error:
            self.call('shop.offer.save', commodity_id=200062, expected_revision=0,
                      enabled=True, sort_order=1, period_hours=[1, 2, 3, 4])
        self.assertEqual(error.exception.code, 'OFFER_PERIOD')
        with self.s.db.transaction() as c:
            self.assertEqual(operations.shop_revision(c), 0)

    def test_default_policy_and_revision_restrict_maps_modes_and_submodes(self):
        """默认仅七图且排除160；全量白名单保存后，旧图和未开放难度均服务端拒绝。"""
        view = self.read('play-policy')
        self.assertEqual({row['map_id'] for row in view['maps'] if row['enabled']},
                         set(operations.DEFAULT_MAP_IDS))
        with self.s.db.transaction() as c:
            with self.assertRaises(Fault):
                operations.check_play_policy(self.s, c, 160, 8197, 4097)
            result = operations.filter_play_catalog(self.s, c, room_catalog())
            self.assertEqual(set(map(int, result['maps'])), set(operations.DEFAULT_MAP_IDS))
        self.call('play-policy.save', expected_revision=0,
                  maps=[{'map_id': 113, 'enabled': True, 'submodes': [4097]}])
        with self.s.db.transaction() as c:
            self.assertEqual(operations.check_play_policy(self.s, c, 113, 260, 4097)['revision'], 1)
            for values in [(113, 260, 4099), (113, 8197, 4097), (164, 260, 4097)]:
                with self.subTest(values=values), self.assertRaises(Fault):
                    operations.check_play_policy(self.s, c, *values)
        self.call('play-policy.save', expected_revision=1, maps=[])
        self.assertFalse(any(row['enabled'] for row in self.read('play-policy')['maps']))
        self.assertEqual(room_catalog()['maps']['113']['submodes'], [4097, 4101, 4099, 4102])

    def test_web_purchase_uses_server_price_and_old_quote_is_rejected(self):
        """网页 Session 正常报价购买；改价后旧报价拒绝，客户端指定他人目标也不转移资产。"""
        self.fund()
        lines = [{'commodity_id': 200001, 'price_index': 0, 'quantity': 1}]
        old = self.call('player.shop.quote', player=True, lines=lines)
        self.call('shop.offer.save', commodity_id=200001, expected_revision=0,
                  enabled=True, sort_order=1, prices=[123])
        with self.assertRaises(Fault) as error:
            self.call('player.shop.purchase', player=True, lines=lines, quote=old)
        self.assertEqual(error.exception.code, 'QUOTE_CHANGED')
        quote = self.call('player.shop.quote', player=True, lines=lines)
        self.assertEqual(quote['totals'], {'cn2020:GP': 123})
        result = self.call('player.shop.purchase', player=True, lines=lines, quote=quote,
                           target_account_id=self.other_account)
        self.assertEqual(result['recipient_account_id'], self.account)
        self.assertEqual(len(self.read('player-inventory', player=True)['items']), 1)
        with self.s.db.transaction() as c:
            self.assertEqual(c.count('inventory', Q(account_id=self.other_account)).fetchone()[0], 0)
        for invalid in ([None], [{'commodity_id': 200001, 'price': 1}]):
            with self.subTest(lines=invalid), self.assertRaises(Fault):
                self.call('player.shop.quote', player=True, lines=invalid)

    def test_purchase_event_failure_rolls_back_wallet_inventory_and_receipt(self):
        """沿用原购物车原子事务，最终事件失败不得留下扣款、订单或GID。"""
        self.fund()
        lines = [{'commodity_id': 200001}]
        quote = self.call('player.shop.quote', player=True, lines=lines)
        with patch('revival.native_actions.event', side_effect=DatabaseError('injected')):
            with self.assertRaises(DatabaseError):
                self.call('player.shop.purchase', player=True, lines=lines, quote=quote)
        with self.s.db.transaction() as c:
            self.assertEqual(c.count('inventory', Q(account_id=self.account)).fetchone()[0], 0)
            self.assertEqual(c.count('native_receipts').fetchone()[0], 0)
            self.assertEqual(c.select('wallets', Q(account_id=self.account, currency='cn2020:GP')).fetchone()['balance'], 50000)

    def test_administrator_remove_checks_owner_version_and_preserves_dependency_protection(self):
        """删除按目标/版本定位，玩家不能调用管理入口，已有子装备关系阻止父道具删除。"""
        item = self.grant(100001)
        with self.assertRaises(Fault):
            self.call('asset.remove', player=True, target_account_id=self.account,
                      instance_id=item['instance_id'], quantity=1, expected_version=item['version'])
        with self.assertRaises(Fault):
            self.call('asset.remove', target_account_id=self.other_account,
                      instance_id=item['instance_id'], quantity=1, expected_version=item['version'])
        with self.assertRaises(Fault) as error:
            self.call('asset.remove', target_account_id=self.account, instance_id=item['instance_id'],
                      quantity=1, expected_version=item['version'] + 1)
        self.assertEqual(error.exception.code, 'REVISION_CONFLICT')
        child = self.grant(100003)
        with self.s.db.transaction() as c:
            c.insert('equipment', {'account_id': self.account, 'slot': 'native:' + str(item['gid']) + ':0',
                                   'inventory_id': child['instance_id']})
        with self.assertRaises(Fault) as error:
            self.call('asset.remove', target_account_id=self.account, instance_id=item['instance_id'],
                      quantity=1, expected_version=item['version'])
        self.assertEqual(error.exception.code, 'ITEM_IN_USE')
        with self.s.db.transaction() as c:
            c.delete('equipment', Q(inventory_id=child['instance_id']))
        result = self.call('asset.remove', target_account_id=self.account, instance_id=item['instance_id'],
                           quantity=1, expected_version=item['version'])
        self.assertEqual(result['status'], 'COMMITTED')
        with self.s.db.transaction() as c:
            self.assertIsNone(c.select('inventory', Q(id=item['instance_id'])).fetchone())
            self.assertIsNone(c.select('native_inventory_bindings', Q(inventory_id=item['instance_id'])).fetchone())
            self.assertEqual(c.select('asset_entries', Q(operation_id=result['operation_id'])).fetchone()['delta'], -1)

    def test_player_inventory_ignores_other_account_filter(self):
        """伪造 account_id 不能读取别人仓库，原生分页版本和稳定GID结构保持不变。"""
        item = self.grant(100001)
        view = self.read('player-inventory', player=True, account_id=self.other_account)
        self.assertEqual([row['instance_id'] for row in view['items']], [item['instance_id']])
        self.assertIn('wallet', view)
        with self.assertRaises(Fault):
            self.read('shop-admin', player=True)
        for value in (True, 1.5, '1.5'):
            with self.subTest(limit=value), self.assertRaises(Fault):
                self.read('player-shop', player=True, limit=value)

    def test_administrator_can_remove_expired_instance_without_relaxing_player_rules(self):
        """后台可清理本人目标的过期库存；玩家删除过期实例仍失败，历史流水保持追加。"""
        item = self.grant(100002)
        with self.s.db.transaction() as c:
            c.update('inventory', Q(id=item['instance_id']), {'expires_at': self.now - 1})
        with self.assertRaises(Fault):
            with self.s.db.transaction() as c:
                mutate(self.s, c, {'id': self.account, 'kind': 'player', 'role': 'player'}, self.account,
                       [{'type': 'remove', 'instance_id': item['instance_id'], 'quantity': 1}],
                       'player cannot clean expired inventory', 'ADMIN_REMOVE')
        result = self.call('asset.remove', target_account_id=self.account, instance_id=item['instance_id'],
                           quantity=1, expected_version=item['version'])
        self.assertEqual(result['status'], 'COMMITTED')
        with self.s.db.transaction() as c:
            self.assertIsNone(c.select('inventory', Q(id=item['instance_id'])).fetchone())
            self.assertEqual(c.select('asset_entries', Q(operation_id=result['operation_id'])).fetchone()['delta'], -1)
