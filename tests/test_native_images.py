"""PE 截断检查的纯文件夹具回归，防止把虚拟补零和缺失磁盘节数据混为一谈。"""
import struct
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest import TestCase, skipUnless
import zlib
from tools.native_image_audit import layout


def fixture(raw_size=128, virtual_size=4096):
    """生成一个最小 x86 节表夹具；不是可执行游戏文件，不会被运行。"""
    data = bytearray(512)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 0x3c, 64)
    data[64:68] = b'PE\0\0'
    struct.pack_into('<HH', data, 68, 0x14c, 1)
    struct.pack_into('<H', data, 84, 96)
    struct.pack_into('<H', data, 88, 0x10b)
    data[184:189] = b'.text'
    struct.pack_into('<IIII', data, 192, virtual_size, 4096, raw_size, 384)
    return data


class ImageTests(TestCase):
    """覆盖截断、合法虚拟补零、架构冲突和不可信节表长度。"""

    def test_missing_disk_section_is_rejected_but_virtual_zero_fill_is_allowed(self):
        """合法 BSS 变大不要求文件增长，原始磁盘数据越界则必须报告缺失字节。"""
        self.assertEqual(layout(fixture())['issues'], [])
        broken = layout(fixture(raw_size=200))
        self.assertEqual(broken['sections'][0]['bytes_beyond_eof'], 72)
        self.assertEqual(broken['issues'], ['SECTION_RAW_DATA_BEYOND_EOF:.text'])

    def test_architecture_conflict_zero_image_and_unbounded_headers(self):
        """不能把不匹配的 PE32+、全零文件或越界节表显示为可运行 x86。"""
        data = fixture()
        struct.pack_into('<H', data, 88, 0x20b)
        self.assertIn('MACHINE_OPTIONAL_HEADER_MISMATCH', layout(data)['issues'])
        for bad in (bytes(512), b'MZ', bytes(data[:200])):
            with self.assertRaises(ValueError):
                layout(bad)

    def test_archive_paths_and_crc_reject_corruption_and_escape(self):
        """归档只接纳独立 nz 树和普通文件，解包后同大小内容损坏也必须由 CRC 检出。"""
        from tools.archive_client_workspace import checked_members, verify_file
        def member(name, link=False):
            """模拟仅含 RAR 目录头属性的成员，测试不调用任何外部解包程序。"""
            return SimpleNamespace(filename=name, file_size=8, CRC=zlib.crc32(b'baseline'),
                                   is_dir=lambda: False, is_symlink=lambda: link, file_redir=None)
        for name in ('../escape', 'nz/../escape', 'nz/file:stream', '/nz/file', 'nz/.. /escape', 'nz/NUL.txt', 'nz/a.'):
            with self.assertRaises(ValueError):
                checked_members([member(name)])
        with self.assertRaises(ValueError):
            checked_members([member('nz/a', True)])
        with self.assertRaises(ValueError):
            checked_members([member('nz/a'), member('nz/A')])
        record = checked_members([member('nz/a')])[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'a'
            path.write_bytes(b'baseline')
            self.assertEqual(len(verify_file(path, record)['sha256']), 64)
            path.write_bytes(b'tampered')
            with self.assertRaises(ValueError):
                verify_file(path, record)

    def test_recovery_receipt_detects_changed_target_and_backup(self):
        """恢复凭据只允许固定入口文件，修改目标或备份后不能再获准启动。"""
        import json
        from tools.client_workspace import digest
        from tools.native_recovery import validate
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            source, work = base / 'original', base / 'work'
            relative = 'Binaries/Win32/TGameBase.dll'
            for root, content in ((source, b'bad'), (work, b'good')):
                path = root / relative
                path.parent.mkdir(parents=True)
                path.write_bytes(content)
            backup = base / 'backup'
            backup.write_bytes(b'bad')
            receipt = base / 'receipt.json'
            receipt.write_text(json.dumps({'state': 'APPLIED', 'workspace': str(work), 'original': str(source),
                'files': [{'path': relative, 'before_sha256': digest(backup), 'after_sha256': digest(work / relative), 'backup': str(backup)}]}))
            metadata = {'status': 'RECOVERY_OVERLAY', 'source': str(source), 'workspace': str(work),
                        'recovery_receipt': str(receipt), 'recovery_receipt_sha256': digest(receipt)}
            (base / 'work.workspace.json').write_text(json.dumps(metadata))
            self.assertIsNotNone(validate(work)[1])
            (work / relative).write_bytes(b'changed')
            with self.assertRaises(ValueError):
                validate(work)
            (work / relative).write_bytes(b'good')
            backup.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                validate(work)

    @skipUnless(os.name == 'nt', 'Windows Job Object integration')
    def test_job_keeps_and_cleans_child_after_root_exits(self):
        """真实父进程退出 0 后子进程仍在 Job 内，到期统一结束，不误判原生实例已退出。"""
        from tools.native_job import JobGroup
        code = "import subprocess,sys,time;time.sleep(.3);subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)"
        with JobGroup() as group:
            process = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            group.attach(process)
            self.assertEqual(process.wait(timeout=5), 0)
            self.assertTrue(group.pids())
            group.terminate()
            deadline = time.monotonic() + 5
            while group.pids() and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertEqual(group.pids(), [])
            # 父进程正常退出不掩盖子进程被清理的不同退出码；PID 句柄在观察期内保留。
            exits = group.collect_exits(timeout_ms=1000)
            self.assertEqual(exits[process.pid], 0)
            self.assertIn(0xE0000001, exits.values())
