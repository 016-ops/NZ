"""真实临时文件验证原生配置恢复；注入磁盘、计划与外部改写故障，不运行客户端或访问数据库。"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.native_config_guard import NativeConfigGuard, _stage_bytes


class NativeConfigGuardTests(unittest.TestCase):
    """每例使用独立临时工作副本及证据目录，所有故障只影响本例自建的普通文件。"""

    def setUp(self):
        """建立两个配置及外部证据目录；绝不读取真实客户端或实际凭据。"""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root, self.output = self.base / 'client', self.base / 'evidence'
        self.root.mkdir()
        self.output.mkdir()
        self.originals = {'first.ini': b'Host=example.test\r\nPort=1234\r\n', 'second.xml': b'<test original="yes"/>'}
        for relative, content in self.originals.items():
            (self.root / relative).write_bytes(content)
        self.guard = NativeConfigGuard(self.root, self.output)

    def test_durable_plan_precedes_mutation_and_fresh_instance_restores(self):
        """每次客户端替换前已有可核验的原件和 PREPARED 计划；丢弃对象后仍能从磁盘计划恢复。"""
        replace = os.replace

        def inspect_replace(source, destination):
            """只观察本例配置替换边界，确认恢复资料已先持久化，不阻止正常原子写入。"""
            if Path(destination).parent == self.root:
                plan = json.loads(self.guard.plan_path.read_text(encoding='utf-8'))
                row = next(row for row in plan['records'] if row['relative'] == Path(destination).name)
                self.assertEqual(row['status'], 'PREPARED')
                self.assertEqual((self.guard.store / row['backup']).read_bytes(), self.originals[row['relative']])
            return replace(source, destination)

        with patch('tools.native_config_guard.os.replace', side_effect=inspect_replace):
            for relative in self.originals:
                self.guard.apply(relative, b'variant-' + relative.encode())
        restored = NativeConfigGuard.from_plan(self.root, self.output).restore()
        self.assertTrue(restored['configuration_restored'])
        for relative, original in self.originals.items():
            self.assertEqual((self.root / relative).read_bytes(), original)
        self.assertTrue(NativeConfigGuard.from_plan(self.root, self.output).restore()['configuration_restored'])

    def test_plan_write_failure_never_mutates_target(self):
        """恢复计划无法持久化时原配置不动，已保存备份不会被误当成已安装的变体。"""
        with patch.object(self.guard, '_persist', side_effect=OSError('injected plan write failure')):
            with self.assertRaises(OSError):
                self.guard.apply('first.ini', b'variant')
        self.assertEqual((self.root / 'first.ini').read_bytes(), self.originals['first.ini'])

    def test_prepared_disk_plan_recovers_when_post_write_recording_fails(self):
        """模拟替换已完成但后续状态落盘全部失败，新的进程仍凭 PREPARED 和备份恢复原件。"""
        persist = self.guard._persist
        calls = 0

        def fail_after_prepared():
            """仅让 PREPARED 首次落盘成功，其余状态写入故障不影响磁盘上的原始计划。"""
            nonlocal calls
            calls += 1
            if calls > 1:
                raise OSError('injected post-replacement plan failure')
            return persist()

        with patch.object(self.guard, '_persist', side_effect=fail_after_prepared):
            with self.assertRaises(OSError):
                self.guard.apply('first.ini', b'variant')
        self.assertEqual((self.root / 'first.ini').read_bytes(), b'variant')
        plan = json.loads(self.guard.plan_path.read_text(encoding='utf-8'))
        self.assertEqual(plan['records'][0]['status'], 'PREPARED')
        self.assertTrue(NativeConfigGuard.from_plan(self.root, self.output).restore()['configuration_restored'])
        self.assertEqual((self.root / 'first.ini').read_bytes(), self.originals['first.ini'])

    def test_variant_flush_failure_never_replaces_original(self):
        """变体临时文件同步失败时不进入 replace，临时文件清理且已有计划可恢复。"""
        fsync = os.fsync
        calls = 0

        def fail_variant_flush(descriptor):
            """备份和 PREPARED 计划同步成功后，让第三次即变体临时文件的同步失败。"""
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError('injected variant flush failure')
            return fsync(descriptor)

        with patch('tools.native_config_guard.os.fsync', side_effect=fail_variant_flush):
            with self.assertRaises(OSError):
                self.guard.apply('first.ini', b'variant')
        self.assertEqual((self.root / 'first.ini').read_bytes(), self.originals['first.ini'])
        self.assertFalse(list(self.root.glob('.nz-config-*')))
        self.assertTrue(NativeConfigGuard.from_plan(self.root, self.output).restore()['configuration_restored'])

    def test_atomic_replace_failure_leaves_original_and_cleans_temporary_file(self):
        """替换调用失败不能留下半截配置；PREPARED/APPLY_FAILED 计划仍可安全恢复原状态。"""
        replace = os.replace

        def fail_target(source, destination):
            """只让客户端替换失败，计划自身仍能原子持久化供后续恢复。"""
            if Path(destination) == self.root / 'first.ini':
                raise OSError('injected replace failure')
            return replace(source, destination)

        with patch('tools.native_config_guard.os.replace', side_effect=fail_target):
            with self.assertRaises(OSError):
                self.guard.apply('first.ini', b'variant')
        self.assertEqual((self.root / 'first.ini').read_bytes(), self.originals['first.ini'])
        self.assertFalse(list(self.root.glob('.nz-config-*')))
        self.assertTrue(NativeConfigGuard.from_plan(self.root, self.output).restore()['configuration_restored'])

    def test_external_change_is_preserved_while_other_files_restore(self):
        """外部新内容不能被旧备份覆盖，但其他登记文件仍独立恢复并提供明确失败状态。"""
        for relative in self.originals:
            self.guard.apply(relative, b'variant')
        (self.root / 'second.xml').write_bytes(b'new external content')
        result = self.guard.restore()
        self.assertFalse(result['configuration_restored'])
        self.assertEqual((self.root / 'second.xml').read_bytes(), b'new external content')
        self.assertEqual((self.root / 'first.ini').read_bytes(), self.originals['first.ini'])
        self.assertEqual(result['configurations'][1]['status'], 'EXTERNAL_CHANGE_PRESERVED')

    def test_one_restore_failure_does_not_stop_other_files_or_report(self):
        """某个恢复替换失败后继续其余文件，错误持久化且 restore 始终返回逐项结果。"""
        for relative in self.originals:
            self.guard.apply(relative, b'variant')
        replace = os.replace

        def fail_second(source, destination):
            """让逆序恢复中的第一个文件失败，以验证后续恢复不会因异常被跳过。"""
            if Path(destination) == self.root / 'second.xml':
                raise OSError('injected restore failure')
            return replace(source, destination)

        with patch('tools.native_config_guard.os.replace', side_effect=fail_second):
            result = self.guard.restore()
        self.assertFalse(result['configuration_restored'])
        self.assertEqual(result['configurations'][1]['status'], 'RESTORE_ERROR')
        self.assertEqual((self.root / 'first.ini').read_bytes(), self.originals['first.ini'])
        self.assertTrue(NativeConfigGuard.from_plan(self.root, self.output).restore()['configuration_restored'])

    def test_corrupt_backup_and_missing_target_are_reported_independently(self):
        """备份损坏或目标被外部删除时不猜测恢复内容，两项错误都记录而不相互掩盖。"""
        for relative in self.originals:
            self.guard.apply(relative, b'variant')
        (self.guard.store / self.guard.records[0]['backup']).write_bytes(b'corrupt backup')
        (self.root / 'second.xml').unlink()
        result = self.guard.restore()
        self.assertFalse(result['configuration_restored'])
        self.assertEqual([row['status'] for row in result['configurations']], ['RESTORE_ERROR', 'RESTORE_ERROR'])
        self.assertEqual((self.root / 'first.ini').read_bytes(), b'variant')
        self.assertFalse((self.root / 'second.xml').exists())

    def test_external_write_during_staging_is_rechecked_before_replace(self):
        """初次检查后临时文件写入期间出现外部新内容时，再次摘要检查必须阻止覆盖。"""
        self.guard.apply('first.ini', b'variant')

        def change_during_stage(target, content):
            """真实写完本次临时文件后模拟外部编辑，保留父方法的正常临时文件清理路径。"""
            temporary = _stage_bytes(target, content)
            if target == self.root / 'first.ini':
                target.write_bytes(b'external edit while staging')
            return temporary

        with patch('tools.native_config_guard._stage_bytes', side_effect=change_during_stage):
            result = self.guard.restore()
        self.assertFalse(result['configuration_restored'])
        self.assertEqual(result['configurations'][0]['status'], 'EXTERNAL_CHANGE_PRESERVED')
        self.assertEqual((self.root / 'first.ini').read_bytes(), b'external edit while staging')
        self.assertFalse(list(self.root.glob('.nz-config-*')))

    def test_post_restore_digest_mismatch_is_reported_and_external_bytes_kept(self):
        """恢复替换后又发生外部编辑时，最终摘要核验不能假报成功或再次覆盖新内容。"""
        self.guard.apply('first.ini', b'variant')
        replace = os.replace

        def change_after_replace(source, destination):
            """正常原子替换后立刻模拟另一个编辑器写入，验证恢复结果必须以最终内容为准。"""
            result = replace(source, destination)
            if Path(destination) == self.root / 'first.ini':
                Path(destination).write_bytes(b'external after restore')
            return result

        with patch('tools.native_config_guard.os.replace', side_effect=change_after_replace):
            result = self.guard.restore()
        self.assertFalse(result['configuration_restored'])
        self.assertEqual(result['configurations'][0]['status'], 'EXTERNAL_CHANGE_PRESERVED')
        self.assertEqual((self.root / 'first.ini').read_bytes(), b'external after restore')

    def test_plan_rejects_path_escape_backup_escape_and_wrong_root(self):
        """篡改计划不能写出工作根或读取任意备份路径，工作根绑定错误也必须拒绝。"""
        self.guard.apply('first.ini', b'variant')
        baseline = json.loads(self.guard.plan_path.read_text(encoding='utf-8'))
        for field, value in (('relative', '../outside.ini'), ('backup', '../outside.bin'),
                             ('relative', './first.ini'), ('relative', 'first.ini:stream'),
                             ('relative', 'first.ini.')):
            changed = json.loads(json.dumps(baseline))
            changed['records'][0][field] = value
            self.guard.plan_path.write_text(json.dumps(changed), encoding='utf-8')
            with self.assertRaises(ValueError):
                NativeConfigGuard.from_plan(self.root, self.output)
        baseline['workspace'] = str(self.base / 'other-client')
        self.guard.plan_path.write_text(json.dumps(baseline), encoding='utf-8')
        with self.assertRaises(ValueError):
            NativeConfigGuard.from_plan(self.root, self.output)

    def test_hardlinked_target_is_rejected_before_backup_or_mutation(self):
        """真实硬链接不能作为独立工作配置，防止同一次替换触及另一个来源路径。"""
        linked = self.root / 'linked.ini'
        os.link(self.root / 'first.ini', linked)
        with self.assertRaises(ValueError):
            self.guard.apply('linked.ini', b'variant')
        self.assertEqual((self.root / 'first.ini').read_bytes(), self.originals['first.ini'])
        self.assertEqual(linked.read_bytes(), self.originals['first.ini'])

    def test_plan_persistence_failure_during_restore_still_restores_all_files(self):
        """恢复状态写盘失败单独记录，所有原配置仍继续恢复并可从旧计划幂等复核。"""
        for relative in self.originals:
            self.guard.apply(relative, b'variant')
        with patch.object(self.guard, '_persist', side_effect=OSError('injected plan failure')):
            result = self.guard.restore()
        self.assertTrue(result['configuration_restored'])
        self.assertEqual(len(result['plan_errors']), 2)
        for relative, original in self.originals.items():
            self.assertEqual((self.root / relative).read_bytes(), original)
        self.assertTrue(NativeConfigGuard.from_plan(self.root, self.output).restore()['configuration_restored'])

    def test_directory_reparse_point_in_plan_is_rejected(self):
        """用可用的目录链接能力验证恢复计划拒绝重解析点；不修改已有路径或要求管理员权限。"""
        self.guard.apply('first.ini', b'variant')
        outside = self.base / 'outside'
        outside.mkdir()
        linked = self.root / 'link'
        try:
            linked.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            if os.name != 'nt':
                raise
            # Windows 普通账号通常不能创建符号链接，但可在本例临时目录内建立目录联接。
            created = subprocess.run(['cmd', '/c', 'mklink', '/J', str(linked), str(outside)],
                                     capture_output=True, timeout=10)
            if created.returncode:
                self.skipTest('Operating system cannot create a directory reparse point: ' + type(error).__name__)
        self.addCleanup(linked.rmdir)
        plan = json.loads(self.guard.plan_path.read_text(encoding='utf-8'))
        plan['records'][0]['relative'] = 'link/outside.ini'
        self.guard.plan_path.write_text(json.dumps(plan), encoding='utf-8')
        with self.assertRaises(ValueError):
            NativeConfigGuard.from_plan(self.root, self.output)
