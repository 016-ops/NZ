"""工作副本恢复和准备矩阵的真实文件夹具测试；不启动游戏或写原客户端。"""
import json
import os
from pathlib import Path
import tempfile
from unittest import TestCase
from tools.client_workspace import checked_path, copy_verified, digest
from tools.pve_readiness import build


class WorkspaceTests(TestCase):
    """验证原件只读、副本独立恢复及损坏来源不能覆盖已验收目标。"""

    def test_copy_restore_and_bad_source_preserve_original_and_good_target(self):
        """副本修改后从摘要基线恢复；来源失配时保留目标及原件失败证据。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / 'source.bin', root / 'work' / 'target.bin'
            source.write_bytes(b'baseline')
            record = {'path': 'source.bin', 'bytes': 8, 'sha256': digest(source)}
            copy_verified(source, target, record)
            self.assertNotEqual(source.stat().st_ino, target.stat().st_ino)
            target.write_bytes(b'modified')
            copy_verified(source, target, record)
            self.assertEqual(target.read_bytes(), source.read_bytes())
            source.write_bytes(b'tampered')
            with self.assertRaises(ValueError):
                copy_verified(source, target, record)
            self.assertEqual(target.read_bytes(), b'baseline')
            self.assertEqual(source.read_bytes(), b'tampered')
            self.assertFalse(target.with_name(target.name + '.nz-copy-part').exists())

    def test_traversal_and_hardlinks_are_rejected(self):
        """目录穿越和外部硬链接不能成为已验证工作副本，避免副本写入波及原件。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            original = root / 'original'
            original.write_text('baseline')
            os.link(original, root / 'linked')
            for relative in ('../escape', str(root.parent / 'escape'), 'linked'):
                with self.assertRaises(ValueError):
                    checked_path(root, relative)

    def test_matrix_retains_unknown_maps_and_never_enables_static_candidates(self):
        """主图存在只提供静态线索，未知图和难度继续进入分母且不能启用。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'json').mkdir()
            fixtures = {
                'source_files': [{'extension': '.udk', 'path': 'Maps/SV-Maya_3_Main.udk',
                                  'sha256': 'a' * 64, 'status': 'PRESENT'}],
                'maps': [{'logical_map_id': key, 'attributes': {'fields': [
                    {'key': 'MapName', 'value': name}, {'key': 'NickName', 'value': '夹具'}]}}
                         for key, name in [(47, 'SV-Maya_3_Main'), (9999, 'Unknown_Main')]],
                'modes': [{'prefix': 'SV', 'class_name': 'TGSVGame'}],
            }
            for name, data in fixtures.items():
                (root / 'json' / (name + '.json')).write_text(json.dumps(data), encoding='utf-8')
            matrix = build(root)
            self.assertEqual((matrix['all_logical_maps'], matrix['m5_candidates']), (2, 1))
            self.assertFalse(matrix['scope_confirmed_complete'])
            self.assertEqual(matrix['maps'][1]['scope_status'], 'UNCLASSIFIED_RETAINED')
            self.assertTrue(all(not row['enabled'] and row['difficulty'] is None for row in matrix['maps']))
