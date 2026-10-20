"""真实 MySQL 活动/分榜回归；所有节点成绩都是隔离 test_profile 的合成证据。"""
import copy
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.db.models import Q

from revival import campaigns
from revival.security import token_hash
from revival.service import Fault, canonical, identifier
from .support import DatabasePool
from .test_control_plane import Base


class CampaignTests(Base):
    """通过真实服务命令、部署事务、节点凭据和不可改写账本验证活动与排行边界。"""

    def reward(self, key='days', kind='CHECKIN_DAYS', threshold=1, **values):
        """构造正数量测试道具奖励；排行榜使用独立名次范围，不混入客户端奖励数值。"""
        row = {'key': key, 'title': key, 'type': kind,
               'changes': [{'type': 'item', 'item_key': 'test-token', 'quantity': 1}]}
        row.update({'rank_from': 1, 'rank_to': 10, 'priority': 1} if kind == 'LEADERBOARD' else {'threshold': threshold})
        row.update(values)
        return row

    def campaign(self, rewards=None, *, publish=True, **definition):
        """先保存草稿再显式发布；默认覆盖当前时刻，不依赖仓库或真实 DEV 已有活动。"""
        data = {'starts_at': self.now - 60, 'ends_at': self.now + 40 * 86400,
                'timezone': 'Asia/Shanghai', 'rewards': rewards or [self.reward()]}
        data.update(definition)
        row = self.command('campaign.save', {'title': '合成开服活动', 'definition': data})
        if publish:
            row = self.command('campaign.publish', {'campaign_id': row['id']})
        return row

    def mine(self, token):
        """读取本人活动与奖励资格，不使用管理员视图替代玩家可见的结果。"""
        return self.s.query(token, 'campaigns')[0]

    def result(self, campaign, members, *, map_id=113, difficulty=4097, observers=(), **overrides):
        """创建显式合成节点及冻结名单；不启动游戏，不伪装为原生地图/网络就绪。"""
        room_id, match_id, instance, epoch, node = (identifier() for _ in range(5))
        with self.s.db.transaction() as c:
            roster = []
            for slot, aid in enumerate([*members, *observers]):
                account = self.s._account(c, aid)
                roster.append({'account_id': aid, 'security_version': account['security_version'],
                               'slot': slot, 'observer': aid in observers, 'equipment': []})
            c.insert('rooms', {'id': room_id, 'owner': members[0], 'map_key': 'cn2020:' + str(map_id),
                     'capacity': 8 if map_id in campaigns.HUNT_MAPS else 4, 'state': 'RUNNING'})
            snapshot = {'players': roster, 'map_key': 'cn2020:' + str(map_id),
                        'native_settings': {'MapId': map_id, 'SubModeId': difficulty}, 'reward': {}}
            c.insert('matches', {'id': match_id, 'room_id': room_id, 'instance_id': instance, 'epoch': epoch,
                     'state': 'ACTIVE', 'snapshot': canonical(snapshot), 'node_token_hash': token_hash(node),
                     'created_at': self.now - 120, 'lease_until': self.now + 30})
            for player in roster:
                c.insert('match_players', {'match_id': match_id, 'account_id': player['account_id'],
                         'security_version': player['security_version'], 'eligible': int(not player['observer'])})
        data = {'campaign_id': campaign['id'], 'match_id': match_id, 'instance_id': instance, 'epoch': epoch,
                'event_id': identifier(), 'map_id': map_id, 'difficulty': difficulty,
                'participant_ids': list(members), 'duration_ms': 60000, 'settled_at': self.now,
                'outcome': 'VICTORY', 'boss_killed': True, 'settled': True}
        data.update(overrides)
        return node, data

    def board(self, campaign, token=None, **values):
        """固定查询一张实际人数分榜；不同地图、难度及人数由独立调用验证互不污染。"""
        return self.s.query(token or self.owner, 'leaderboards', campaign_id=campaign['id'],
                            **dict({'map_id': 113, 'difficulty': 4097, 'player_count': 1}, **values))

    def counts(self):
        """比较事务前后账本、库存、资格、成绩与领取行，防止只验证最终显示而漏掉半份写入。"""
        with self.s.db.transaction() as c:
            return {name: c.table(name).count() for name in
                    ('campaign_claims', 'campaign_days', 'campaign_results', 'inventory', 'asset_operations', 'asset_entries')}

    def test_draft_does_not_grant_and_published_definition_is_immutable(self):
        """草稿不可见、不可签到；发布冻结定义，暂停阻止新增签到，恢复仍使用原规则。"""
        _, token = self.player()
        campaign = self.campaign(publish=False)
        self.assertEqual(self.s.query(token, 'campaigns'), [])
        with self.assertRaises(Fault):
            self.command('campaign.checkin', {'campaign_id': campaign['id']}, token)
        self.command('campaign.publish', {'campaign_id': campaign['id']})
        self.assertEqual(self.mine(token)['progress']['checkin_days'], 0)
        with self.assertRaises(Fault) as error:
            self.command('campaign.save', {'campaign_id': campaign['id'], 'title': '覆盖', 'definition': campaign['definition']})
        self.assertEqual(error.exception.code, 'CAMPAIGN_IMMUTABLE')
        self.command('campaign.pause', {'campaign_id': campaign['id']})
        with self.assertRaises(Fault):
            self.command('campaign.checkin', {'campaign_id': campaign['id']}, token)
        self.command('campaign.publish', {'campaign_id': campaign['id']})
        self.command('campaign.checkin', {'campaign_id': campaign['id']}, token)
        self.assertEqual(self.mine(token)['progress']['checkin_days'], 1)
        self.assertEqual(self.counts()['asset_operations'], 0)

    def test_checkin_cross_month_uses_activity_day_not_calendar_month(self):
        """北京时间月末跨入下月仍累计2天，同一天多次点击不增加天数或自动发道具。"""
        self.now = int(datetime(2026, 10, 31, 23, 59, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())
        _, token = self.player('day-player')
        campaign = self.campaign([self.reward(threshold=2)])
        first = self.command('campaign.checkin', {'campaign_id': campaign['id']}, token)
        self.assertEqual(first['local_day'], '2026-10-31')
        again = self.command('campaign.checkin', {'campaign_id': campaign['id']}, token)
        self.assertTrue(again['already_checked_in'])
        self.now += 120
        second = self.command('campaign.checkin', {'campaign_id': campaign['id']}, token)
        self.assertEqual(second['local_day'], '2026-11-01')
        self.assertEqual(second['progress']['checkin_days'], 2)
        self.assertTrue(self.mine(token)['rewards'][0]['eligible'])
        self.assertEqual(self.counts()['inventory'], 0)

    def test_launcher_login_days_are_only_recorded_by_internal_authorized_hook(self):
        """普通网页登录不产生Launcher登录日，真实授权钩子每日去重，跨日只增加一天。"""
        account, token = self.player('launch-player')
        self.campaign([self.reward(kind='LAUNCHER_LOGIN_DAYS', threshold=2)])
        self.s.login('launch-player', self.password)
        token = self.s.login('launch-player', self.password)['token']
        self.assertEqual(self.mine(token)['progress']['launcher_login_days'], 0)
        with self.s.db.transaction() as c:
            campaigns.record_launcher_login(self.s, c, account, 'accepted-first')
            campaigns.record_launcher_login(self.s, c, account, 'accepted-second')
        self.assertEqual(self.mine(token)['progress']['launcher_login_days'], 1)
        self.now += 86400
        token = self.s.login('launch-player', self.password)['token']
        with self.s.db.transaction() as c:
            campaigns.record_launcher_login(self.s, c, account, 'accepted-next-day')
        self.assertEqual(self.mine(token)['progress']['launcher_login_days'], 2)

    def test_concurrent_claims_with_different_request_keys_share_one_ledger_operation(self):
        """同时点击领取使用不同请求键仍只有一份资产操作，业务唯一键保护奖励本身。"""
        _, token = self.player()
        campaign = self.campaign()
        self.command('campaign.checkin', {'campaign_id': campaign['id']}, token)
        def claim(_):
            """并发线程各使用独立数据库连接和请求键，模拟两个同时打开的窗口。"""
            return self.s.command(token, 'campaign.claim', self.meta(campaign_id=campaign['id'], reward_key='days'))
        with DatabasePool(max_workers=2) as pool:
            results = list(pool.map(claim, range(2)))
        self.assertEqual(results[0]['operation_id'], results[1]['operation_id'])
        self.assertEqual(self.counts()['campaign_claims'], 1)
        self.assertEqual(self.counts()['asset_operations'], 1)
        self.assertEqual(self.counts()['inventory'], 1)

    def test_failure_after_asset_mutation_rolls_back_and_can_retry_same_request(self):
        """账本已写之后的故障仍回滚资产和领取状态，重试原请求仅发放一次。"""
        _, token = self.player()
        campaign = self.campaign()
        self.command('campaign.checkin', {'campaign_id': campaign['id']}, token)
        request = self.meta(campaign_id=campaign['id'], reward_key='days')
        original, before = campaigns.mutate, self.counts()
        def fail_after_mutation(*args, **kwargs):
            """在真实 mutate 完成后注入事务末尾异常，验证不是仅仅在写入之前失败。"""
            original(*args, **kwargs)
            raise Fault('AFTER_LEDGER_FAILURE', 'synthetic rollback')
        with patch('revival.campaigns.mutate', side_effect=fail_after_mutation):
            with self.assertRaises(Fault):
                self.s.command(token, 'campaign.claim', request)
        self.assertEqual(before, self.counts())
        claimed = self.s.command(token, 'campaign.claim', request)
        self.assertEqual(claimed, self.s.command(token, 'campaign.claim', request))
        self.assertEqual(self.counts()['asset_operations'], 1)

    def test_result_rejects_player_tokens_old_epoch_wrong_map_and_impossible_time(self):
        """受信结果必须来自绑定节点，且实例/epoch/地图/时长均与服务端快照一致。"""
        aid, token = self.player()
        campaign = self.campaign()
        node, data = self.result(campaign, [aid])
        for credential, changes, code in (
            (token, {}, 'FORBIDDEN'), (node, {'epoch': identifier()}, 'STALE_INSTANCE'),
            (node, {'map_id': 164}, 'RESULT_MAP_CONFLICT'), (node, {'duration_ms': 121001}, 'RESULT_TIME_CONFLICT')):
            with self.subTest(code=code):
                with self.assertRaises(Fault) as error:
                    campaigns.ingest(self.s, credential, dict(data, **changes))
                self.assertEqual(error.exception.code, code)
        self.assertEqual(self.counts()['campaign_results'], 0)

    def test_official_ingest_remains_disabled_and_test_boards_do_not_leak(self):
        """即使持有合成节点凭据，正式模式也不能写结果或读取合成活动分榜。"""
        aid, _ = self.player()
        campaign = self.campaign()
        node, data = self.result(campaign, [aid])
        formal = copy.copy(self.s)
        formal.test_profile = False
        with self.assertRaises(Fault) as error:
            campaigns.ingest(formal, node, data)
        self.assertEqual(error.exception.code, 'COMPATIBILITY_BLOCKED')
        campaigns.ingest(self.s, node, data)
        with formal.db.transaction() as c:
            public = campaigns.view(formal, c, {'kind': 'public'}, 'campaigns', {})
        self.assertEqual(public, [])

    def test_success_boss_and_settlement_gate_ranks_and_gameplay_progress(self):
        """失败局可计有效时长，通关计数及榜只接成功；塔防还必须击杀Boss且完成结算。"""
        aid, token = self.player()
        campaign = self.campaign([self.reward(kind='PLAYTIME_SECONDS', threshold=120), self.reward('clears', 'CLEAR_COUNT', 2)])
        cases = [dict(), dict(outcome='FAILURE'), dict(map_id=168, boss_killed=False),
                 dict(map_id=168, settled=False), dict(map_id=168)]
        receipts = []
        for values in cases:
            node, data = self.result(campaign, [aid], duration_ms=30000, **values)
            receipts.append(campaigns.ingest(self.s, node, data))
        self.assertEqual([r['rank_eligible'] for r in receipts], [True, False, False, False, True])
        progress = self.mine(token)['progress']
        self.assertEqual(progress['playtime_seconds'], 120)
        self.assertEqual(progress['clear_count'], 2)
        self.assertTrue(all(r['eligible'] for r in self.mine(token)['rewards']))
        self.assertEqual(len(self.board(campaign)['entries']), 1)
        self.assertEqual(len(self.board(campaign, map_id=168)['entries']), 1)

    def test_observers_are_excluded_and_additional_participants_disqualify(self):
        """观战者不占实际人数、不累计通关；结算擅自增加成员使整队成绩失格。"""
        aid, token = self.player('fighter')
        observer, observer_token = self.player('observer')
        campaign = self.campaign()
        node, data = self.result(campaign, [aid], observers=[observer])
        self.assertTrue(campaigns.ingest(self.s, node, data)['rank_eligible'])
        node, changed = self.result(campaign, [aid], participant_ids=[aid, observer], observers=[observer])
        self.assertEqual(campaigns.ingest(self.s, node, changed)['reason'], 'ROSTER_CHANGED')
        self.assertEqual(self.mine(token)['progress']['clear_count'], 1)
        self.assertEqual(self.mine(observer_token)['progress']['clear_count'], 0)
        self.assertEqual(self.board(campaign)['entries'][0]['member_ids'], [aid])

    def test_same_team_best_and_difficulty_count_partitions_and_stable_ties(self):
        """同队更快成绩覆盖旧纪录，成员组合/难度/人数分开，毫秒相同按结算时间与ID稳定排序。"""
        a, _ = self.player('team-a')
        b, _ = self.player('team-b')
        c, _ = self.player('team-c')
        campaign = self.campaign()
        entries = []
        for members, duration, difficulty in (([a, b], 60000, 4097), ([b, a], 50000, 4097),
                                              ([a, c], 50000, 4097), ([a], 40000, 4097), ([a, b], 30000, 4099)):
            node, data = self.result(campaign, members, duration_ms=duration, difficulty=difficulty)
            entries.append(campaigns.ingest(self.s, node, data))
        normal = self.board(campaign, player_count=2)['entries']
        self.assertEqual(len(normal), 2)
        self.assertEqual([r['duration_ms'] for r in normal], [50000, 50000])
        self.assertEqual([r['result_id'] for r in normal], sorted(r['result_id'] for r in normal))
        self.assertEqual(self.board(campaign)['entries'][0]['duration_ms'], 40000)
        self.assertEqual(self.board(campaign, difficulty=4099, player_count=2)['entries'][0]['duration_ms'], 30000)
        self.assertNotIn(entries[0]['result_id'], [r['result_id'] for r in normal])

    def test_result_replay_is_idempotent_but_changed_payload_is_conflict(self):
        """同局同事件重传只返回原结果，换时长或换事件不能产生第二次进度。"""
        aid, token = self.player()
        campaign = self.campaign()
        node, data = self.result(campaign, [aid])
        first = campaigns.ingest(self.s, node, data)
        self.assertEqual(first, campaigns.ingest(self.s, node, data))
        for changes in ({'duration_ms': 59000}, {'event_id': identifier()}):
            with self.assertRaises(Fault) as error:
                campaigns.ingest(self.s, node, dict(data, **changes))
            self.assertEqual(error.exception.code, 'RESULT_CONFLICT')
        self.assertEqual(self.mine(token)['progress']['clear_count'], 1)

    def test_finalization_freezes_boards_and_selects_only_highest_tier_per_player(self):
        """一人可在多张榜中获名次，但封榜只选最高priority奖励；领奖不会按多榜重复发放。"""
        aid, token = self.player()
        campaign = self.campaign([self.reward('low', 'LEADERBOARD', priority=1),
                                  self.reward('high', 'LEADERBOARD', priority=20, map_ids=[168])])
        for map_id in (113, 168):
            node, data = self.result(campaign, [aid], map_id=map_id)
            campaigns.ingest(self.s, node, data)
        with self.assertRaises(Fault):
            self.command('campaign.claim', {'campaign_id': campaign['id'], 'reward_key': 'high'}, token)
        with self.assertRaises(Fault):
            self.command('leaderboard.finalize', {'campaign_id': campaign['id']})
        self.command('campaign.end', {'campaign_id': campaign['id']})
        frozen = self.command('leaderboard.finalize', {'campaign_id': campaign['id']})
        self.assertEqual(frozen['winners'], 1)
        self.assertTrue(self.board(campaign)['finalized'])
        self.assertEqual(frozen, self.command('leaderboard.finalize', {'campaign_id': campaign['id']}))
        with self.assertRaises(Fault):
            self.command('campaign.claim', {'campaign_id': campaign['id'], 'reward_key': 'low'}, token)
        self.assertEqual(campaigns.drain_awards(self.s)['succeeded'], 1)
        self.assertEqual(campaigns.drain_awards(self.s)['processed'], 0)
        with self.assertRaises(Fault):
            self.command('campaign.claim', {'campaign_id': campaign['id'], 'reward_key': 'high'}, token)
        self.assertEqual(self.counts()['campaign_claims'], 1)
        node, late = self.result(campaign, [aid], duration_ms=1000)
        with self.assertRaises(Fault) as error:
            campaigns.ingest(self.s, node, late)
        self.assertEqual(error.exception.code, 'LEADERBOARD_FINALIZED')

    def test_automatic_payout_retries_only_failed_winner_without_partial_ledger(self):
        """榜奖逐目标提交；第二人入账后故障全部回滚，重试只补该人且不重发第一人。"""
        a, _ = self.player('winner-a')
        b, _ = self.player('winner-b')
        campaign = self.campaign([self.reward('rank', 'LEADERBOARD')])
        for account_id in (a, b):
            node, data = self.result(campaign, [account_id])
            campaigns.ingest(self.s, node, data)
        self.command('campaign.end', {'campaign_id': campaign['id']})
        self.command('leaderboard.finalize', {'campaign_id': campaign['id']})
        actual = campaigns.mutate
        def fault_after_second_winner_write(*args, **kwargs):
            """在真实资产写入后模拟第二人的尾部故障，验证保存点完整回滚。"""
            result = actual(*args, **kwargs)
            if args[3] == b:
                raise Fault('INJECTED', '测试尾部失败')
            return result
        with patch('revival.campaigns.mutate', side_effect=fault_after_second_winner_write):
            self.assertEqual(campaigns.drain_awards(self.s), {'processed':2,'succeeded':1,'failed':1})
        self.assertEqual(self.counts()['campaign_claims'], 1)
        self.now += 11
        self.assertEqual(campaigns.drain_awards(self.s), {'processed':1,'succeeded':1,'failed':0})
        self.assertEqual(self.counts()['campaign_claims'], 2)
        self.assertEqual(campaigns.drain_awards(self.s)['processed'], 0)

    def test_concurrent_draft_revision_cannot_overwrite_newer_edit(self):
        """草稿编辑要求当前revision，第二份旧页面不能覆盖已保存的标题和奖励。"""
        row = self.campaign(publish=False)
        data = {'campaign_id':row['id'],'expected_revision':row['revision'],
            'title':'已保存草稿','definition':row['definition']}
        self.command('campaign.save', data)
        with self.assertRaises(Fault) as error:
            self.command('campaign.save', dict(data,title='过期页面覆盖'))
        self.assertEqual(error.exception.code, 'REVISION_CONFLICT')
        self.assertEqual(self.s.query(self.owner,'campaign-admin')[0]['title'],'已保存草稿')

    def test_role_boundaries_and_public_view_do_not_disclose_personal_progress(self):
        """resources运营、auditor只读、Owner封榜；公开活动只有规则，不泄露玩家进度。"""
        resources, auditor, users = (self.extra_admin(role) for role in ('resources', 'auditor', 'users'))
        definition = {'starts_at': self.now - 1, 'ends_at': self.now + 100, 'rewards': [self.reward()]}
        row = self.command('campaign.save', {'title': '运营活动', 'definition': definition}, resources)
        self.command('campaign.publish', {'campaign_id': row['id']}, resources)
        self.assertEqual(len(self.s.query(auditor, 'campaign-admin')), 1)
        for role in (auditor, users):
            with self.assertRaises(Fault):
                self.command('campaign.pause', {'campaign_id': row['id']}, role)
        with self.assertRaises(Fault):
            self.command('leaderboard.finalize', {'campaign_id': row['id']}, resources)
        with self.s.db.transaction() as c:
            public = campaigns.view(self.s, c, {'kind': 'public'}, 'campaigns', {})
        self.assertNotIn('progress', public[0])
        self.assertNotIn('rewards', public[0])
        self.assertEqual(public[0]['definition']['timezone'], 'Asia/Shanghai')

    def test_invalid_rewards_and_same_name_map_variants_are_rejected(self):
        """发布不接受负数/货币/重复key，沙漠155与空间站219等同名变体不能混入本期榜。"""
        base = {'starts_at': self.now, 'ends_at': self.now + 10, 'rewards': [self.reward()]}
        for change in (
            {'rewards': [self.reward(changes=[{'type': 'currency', 'code': 'GP', 'delta': 1}])]},
            {'rewards': [self.reward(changes=[{'type': 'item', 'item_key': 'test-token', 'quantity': -1}])]},
            {'rewards': [self.reward(), self.reward()]}, {'timezone': 'Unknown/Zone'},
            {'rewards': [self.reward('rank', 'LEADERBOARD', map_ids=[155])]},
        ):
            with self.assertRaises(Fault):
                self.command('campaign.save', {'title': '不合法规则', 'definition': dict(base, **change)})
        campaign = self.campaign()
        for map_id in (155, 219, 160):
            with self.assertRaises(Fault):
                self.board(campaign, map_id=map_id)
