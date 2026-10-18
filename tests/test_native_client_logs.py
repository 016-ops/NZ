"""用真实临时文件区分原生运行日志的追加、轮转和未变更情形，不执行游戏或读取用户日志。"""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from tools.native_client_logs import capture_logs, snapshot_logs


class NativeClientLogTests(TestCase):
    """确保旧错误不混入新片段，无法区分的重写日志显式标记。"""
    def setUp(self):
        """每例建立独立临时工作根与私人输出替身，测试完成后回收自己的文件。"""
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'client'
        self.output = Path(self.temporary.name) / 'private'
        self.root.mkdir()
        self.output.mkdir()
        self.log = self.root / 'debug.log'
        self.log.write_bytes(b'old FATAL\n')
        self.baseline = snapshot_logs(self.root, [self.log])

    def test_appended_log_excludes_old_fatal_and_preserves_full_evidence(self):
        """旧前缀匹配时只把新内容归入本轮，完整原始日志另存且客户端保持原样。"""
        self.log.write_bytes(b'old FATAL\nnew INFO\n')
        records = capture_logs(self.root, self.output, self.baseline)
        self.assertEqual(records[0]['classification'], 'VERIFIED_APPEND')
        self.assertEqual(records[0]['start_offset'], 10)
        self.assertEqual((self.output/'client-output-current/debug.log').read_bytes(), b'new INFO\n')
        self.assertEqual((self.output/'client-output/debug.log').read_bytes(), self.log.read_bytes())

    def test_rewritten_or_truncated_log_does_not_claim_verified_append(self):
        """相同长度改写和截断都不能按旧文件长度切片或声称已分离本轮。"""
        for content in (b'new ERROR\n', b'new'):
            self.log.write_bytes(content)
            record = capture_logs(self.root, self.output, self.baseline)[0]
            self.assertEqual(record['classification'], 'REPLACED_OR_REWRITTEN_UNSEPARATED')
            self.assertEqual(record['start_offset'], 0)
            self.assertEqual((self.output/'client-output-current/debug.log').read_bytes(), content)

    def test_unchanged_log_does_not_create_new_error_evidence(self):
        """完全未变的旧错误日志不能出现在本轮记录中。"""
        self.assertEqual(capture_logs(self.root, self.output, self.baseline), [])
        self.assertFalse((self.output/'client-output-current').exists())

    def test_new_log_is_saved_but_non_log_payload_is_excluded(self):
        """新日志完整记录，二进制及超限文件不进入日志来源报告。"""
        (self.root/'new.txt').write_bytes(b'new diagnostic')
        (self.root/'auth.bin').write_bytes(b'private-payload')
        records = capture_logs(self.root, self.output, self.baseline)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['path'], 'new.txt')
        self.assertEqual(records[0]['classification'], 'NEW_FILE')
