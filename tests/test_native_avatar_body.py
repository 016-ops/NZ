"""真实 MySQL 验证角色隐藏部件修复：初装、旧账号、续费、隔离和事务尾故障。"""
import struct
from unittest.mock import patch

from django.db import DatabaseError
from django.db.models import Q
from django.test import override_settings

from .test_control_plane import Base
from . import test_native_features
from revival import launcher
from revival.catalogs import with_native_inventory
from revival.native_actions import apply, item_rewards
from revival.native_avatar_body import attach, parts, slot, VERSION
from revival.native_features_catalog import FEATURE_VERSION
from revival.security import new_token, token_hash


class NativeAvatarBodyTests(Base):
    """独立空账号经过正式鉴权接口，数据库和原生包内容分别核对；不使用真实玩家。"""
    player_game = test_native_features.NativeFeatureTests.player_game
    call = test_native_features.NativeFeatureTests.call
    view = test_native_features.NativeFeatureTests.view
    bridge = test_native_features.NativeFeatureTests.bridge

    def setUp(self):
        """隔离测试目录和会话，默认不开启初装，避免旧夹具掩盖缺项场景。"""
        super().setUp()
        self.s.catalog = with_native_inventory(self.s.catalog)
        self.account, self.player_token, self.game, self.base = self.player_game()
        self.base['feature_version'] = FEATURE_VERSION

    def grant_roles(self, *ids, duration=None):
        """通过真实资产账本准备角色，返回独立实例；不直接插入库存或 GID。"""
        with self.s.db.transaction() as c:
            operations = apply(self.s, c, self.account,
                item_rewards(self.s, [dict(item_id=i, duration_seconds=duration) for i in ids]), 'TEST_FIXTURE')
            return list(c.table('asset_entries').filter(operation_id__in=operations,
                instance_id__isnull=False).values_list('instance_id', flat=True))

    def test_default_parts_enter_native_character_tree_and_reject_wrong_parent(self):
        """首装实际有手臂/身体四项，原生仍发 -1；错家族拒绝后合法武器操作继续成功。"""
        with override_settings(NZ_STARTER_EQUIPMENT_ENABLED=True):
            self.account, self.player_token, self.game, self.base = self.player_game('body-starter')
        self.base['feature_version'] = FEATURE_VERSION
        features = self.bridge()
        inventory = features.inventory
        inventory.features = features
        rows = {r['item_id']: r for r in inventory.items}
        for parent_id in (100216, 100217):
            parent = rows[parent_id]
            for definition in parts(parent_id):
                child = rows[definition['item_id']]
                self.assertEqual(child['equipment_slot'], slot(child['item_id'], parent['gid']))
                prop = features.prop(child)
                self.assertEqual((prop['OwnerGID'], prop['Location']), (parent['gid'], -1))
        self.assertTrue(inventory.packets())
        from tools.native_inventory_bridge import NativeEquipmentUnsupported
        packet = lambda child, parent: struct.pack('>HHHHhQQb', 0x3243, 0xa008, 8, 19, 0, child, parent, -1)
        with self.assertRaises(NativeEquipmentUnsupported):
            inventory.equip(packet(rows[300062]['gid'], rows[100217]['gid']))
        inventory.equip(packet(rows[100292]['gid'], rows[100063]['gid']))
        self.assertEqual(next(r for r in inventory.items if r['item_id'] == 100292)['equipment_slot'],
                         f"native:{rows[100063]['gid']}:0")

    def test_old_account_redemption_reuses_owned_parts_and_keeps_old_receipt(self):
        """旧账号启动时补挂本人既有部件，重复兑换不发新资产，旧初装回执版本保持原样。"""
        ids = self.grant_roles(100216, 300062, 300063)
        with self.s.db.transaction() as c:
            role = c.select('inventory', Q(account_id=self.account, item_key='cn2020:100216')).fetchone()
            c.equip(self.account, 'native:1:10', role['id'])
            c.insert('native_receipts', dict(id='legacy-body-receipt', account_id=self.account,
                kind='starter.loadout', result=dict(rule_version='cn2020-starter-loadout-v2'),
                created_at=self.s.clock(), confirmed=True))
        for _ in range(2):
            verifier = new_token()
            ticket = launcher.issue(self.s, self.player_token, self.meta(**self.base, verifier_sha256=token_hash(verifier)))
            launcher.redeem(self.s, dict(self.base, ticket=ticket['ticket'], verifier=verifier))
        with self.s.db.transaction() as c:
            self.assertEqual(set(c.table('inventory').filter(account_id=self.account).values_list('id', flat=True)), set(ids))
            self.assertEqual(c.table('equipment').filter(account_id=self.account).count(), 3)
            self.assertEqual(c.select('native_receipts', Q(id='legacy-body-receipt')).fetchone()['result']['rule_version'],
                             'cn2020-starter-loadout-v2')

    def test_two_role_instances_keep_independent_parts_and_renew_atomically(self):
        """相同角色两实例不共享身体 GID，续费同步部件期限；再次准备不新增流水或绑定。"""
        ids = self.grant_roles(100216, 100216, duration=3600)
        with self.s.db.transaction() as c:
            changed = attach(self.s, c, self.account, ids)
            self.assertEqual(len(changed), 4)
            self.assertEqual(len({r['gid'] for r in changed}), 4)
            role = c.select('inventory', Q(id=ids[0])).fetchone()
            apply(self.s, c, self.account, [dict(type='renew', instance_id=role['id'], duration_seconds=7200)], 'TEST_FIXTURE')
            changed = attach(self.s, c, self.account, [role['id']])
            self.assertEqual(len(changed), 2)
            expires = c.select('inventory', Q(id=role['id'])).fetchone()['expires_at']
            self.assertTrue(all(c.select('inventory', Q(id=r['instance_id'])).fetchone()['expires_at'] == expires for r in changed))
            count = c.count('asset_operations').fetchone()[0]
            self.assertEqual(attach(self.s, c, self.account, ids), [])
            self.assertEqual(count, c.count('asset_operations').fetchone()[0])

    def test_body_tail_failure_rolls_back_new_parts_and_relationships(self):
        """最后部件事件异常时，原角色保留，新资源/GID/关系和流水全回滚，重试恢复完整结果。"""
        ids = self.grant_roles(100216)
        before = self.view('inventory')
        original = __import__('revival.native_actions', fromlist=['event']).event
        def fail(service, c, account, kind, payload):
            """只在部件真实挂接后注入故障，不以输入拒绝代替事务测试。"""
            if kind == 'native.avatar.body':
                raise DatabaseError('injected body event tail')
            return original(service, c, account, kind, payload)
        with patch('revival.native_actions.event', side_effect=fail):
            with self.assertRaises(DatabaseError):
                with self.s.db.transaction() as c:
                    attach(self.s, c, self.account, ids)
        self.assertEqual(before, self.view('inventory'))
        with self.s.db.transaction() as c:
            self.assertEqual(len(attach(self.s, c, self.account, ids)), 2)
        self.assertEqual(len(self.view('inventory')['items']), 3)

    def test_slow_transaction_keeps_parent_exact_expiry_and_public_grant_cannot_override(self):
        """跨秒补齐仍与父角色到期秒完全一致；普通管理发放不能使用内部绝对期限字段。"""
        ids=self.grant_roles(100216,duration=3600)
        def advancing_clock():
            """每次读取时间前进一秒，模拟远程MySQL慢事务中的真实跨秒。"""
            self.now+=1
            return self.now
        self.s.clock=advancing_clock
        with self.s.db.transaction() as c:
            role=c.select('inventory',Q(id=ids[0])).fetchone()
            changed=attach(self.s,c,self.account,ids)
            self.assertEqual(len(changed),2)
            self.assertTrue(all(c.select('inventory',Q(id=r['instance_id'])).fetchone()['expires_at']==role['expires_at'] for r in changed))
        from revival.service import Fault
        with self.assertRaises(Fault):
            self.grant(self.account,[dict(type='item',item_key='cn2020:300062',quantity=1,
                duration_seconds=3600,expires_at_exact=self.now+99999)])
        self.assertEqual(len(self.view('inventory')['items']),3)
