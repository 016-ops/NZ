"""当前恢复副本的猎场盘点测试；使用真实临时文件，验证分类和损坏状态不会被旧记录掩盖。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.hunt_readiness import build


class HuntReadinessTests(unittest.TestCase):
    """地图分母、精确模式前缀、当前资源损坏和读取竞争均有独立拒绝或保留行为。"""

    def workspace(self, directory, extra=''):
        """创建两种猎场前缀及未知模式的临时副本，不加载或复制任何真实游戏文件。"""
        root = Path(directory)
        (root / 'TGame/Config').mkdir(parents=True)
        (root / 'TGame/CookedPC/Maps').mkdir(parents=True)
        text = ('[UTGame.UTGame]\n'
                '+DefaultMapPrefixes=(Prefix="AD",GameType="TGADGame.TGADGame")\n'
                '+DefaultMapPrefixes=(Prefix="AD10",GameType="TGAD10Game.TGAD10Game")\n'
                '+DefaultMapPrefixes=(Prefix="OTHER",GameType="Other.Game")\n'
                '+GameMapSettings=(MapId=113,MapName="AD-First",NickName="第一图")\n'
                '+GameMapSettings=(MapId=200,MapName="AD10-Second",NickName="第二图")\n'
                '+GameMapSettings=(MapId=999,MapName="AD100-Unknown",NickName="未知图")\n' + extra)
        (root / 'TGame/Config/DefaultGame.ini').write_text(text, encoding='utf-8')
        return root

    def test_all_maps_retained_and_exact_prefixes_do_not_enable_native_play(self):
        """AD10不误配AD、AD100不因前缀相似进入猎场；缺图继续计入分母且不能启用。"""
        with tempfile.TemporaryDirectory() as directory:
            root = self.workspace(directory)
            result = build(root)
            self.assertEqual((result['all_logical_maps'], result['m6_candidates']), (3, 2))
            first, second, unknown = result['maps']
            self.assertEqual(first['candidate_mode_classes'], ['TGADGame.TGADGame'])
            self.assertEqual(second['candidate_mode_classes'], ['TGAD10Game.TGAD10Game'])
            self.assertEqual(unknown['scope_status'], 'UNCLASSIFIED_RETAINED')
            self.assertEqual(result['candidate_resource_counts'], {'MISSING': 2})
            self.assertFalse(result['scope_confirmed_complete'])
            self.assertIsNone(result['first_map'])
            for row in result['maps']:
                self.assertFalse(row['enabled'] or row['native_loaded'] or row['multiplayer_verified'])
                self.assertIsNone(row['difficulty'])
                self.assertIsNone(row['player_limit'])

    def test_current_files_detect_zero_and_empty_resources_and_repair_changes_snapshot(self):
        """实际流式读取检测全零和空文件；恢复后摘要和状态更新，但不升级为依赖闭包通过。"""
        with tempfile.TemporaryDirectory() as directory:
            root = self.workspace(directory)
            maps = root / 'TGame/CookedPC/Maps'
            (maps / 'AD-First.udk').write_bytes(b'\0' * 8192)
            (maps / 'AD10-Second.udk').write_bytes(b'')
            before = build(root)
            self.assertEqual([r['main_resources'][0]['status'] for r in before['maps'][:2]], ['ZERO_FILLED', 'EMPTY'])
            (maps / 'AD-First.udk').write_bytes(b'\0' * 4096 + b'package-data')
            after = build(root)
            self.assertNotEqual(before['snapshot_sha256'], after['snapshot_sha256'])
            self.assertEqual(after['maps'][0]['main_resource_status'], 'PRESENT_UNVERIFIED')
            self.assertEqual(after['maps'][0]['dependency_closure'], 'UNVERIFIED')
            self.assertFalse(after['maps'][0]['native_loaded'])

    def test_duplicate_map_id_cannot_silently_drop_a_scope_entry(self):
        """相同MapId即使物理图名不同仍拒绝，不能通过字典覆盖减少分母。"""
        with tempfile.TemporaryDirectory() as directory:
            root = self.workspace(directory, '+GameMapSettings=(MapId=113,MapName="AD-Duplicate")\n')
            with self.assertRaisesRegex(ValueError, 'MISSING_OR_DUPLICATE_LOGICAL_MAP_ID'):
                build(root)

    def test_config_changed_during_scan_does_not_publish_a_mixed_snapshot(self):
        """配置前后摘要不同时拒绝发布，避免将前一版本模式和后一版本地图混在一起。"""
        with tempfile.TemporaryDirectory() as directory:
            root = self.workspace(directory)
            with patch('tools.hunt_readiness.digest', side_effect=['a' * 64, 'b' * 64]):
                with self.assertRaisesRegex(ValueError, 'EMPTY_OR_CHANGED_MAP_CONFIG'):
                    build(root)


if __name__ == '__main__':
    unittest.main()
