"""固定昵称代码采样的边界测试；用假的内存API验证所有权、页面权限与读取上限。"""
import ctypes
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.native_login_probe import sample_nickname_thunks


class NicknameCodeBoundaryTests(unittest.TestCase):
    """跳板只能来自两个固定槽，每个只读128字节，不跟随直接跳转读取更多内存。"""

    def kernel(self, protection=0x20, partial=False):
        """模拟固定槽与两段可执行代码，不接触任何真实进程或系统句柄。"""
        kernel = Mock()

        def read(process, address, buffer, count, received):
            """只允许预定8字节槽与两个128字节代码目标，其他读取立即使测试失败。"""
            if address == 0x400000 + 0x1a3e1a0 and count == 8:
                data = struct.pack('<II', 0x500100, 0x500200)
            elif address in (0x500100, 0x500200) and count == 128:
                data = b'\xe9' + struct.pack('<i', 123) + b'\x90' * 123
            else:
                raise AssertionError('Unexpected process memory read')
            ctypes.memmove(buffer, data, count)
            received._obj.value = count - 1 if partial else count
            return True

        def query(process, address, result, size):
            """模拟已提交私有页；保护属性由测试选择，以验证非代码页不能被采样。"""
            region = result._obj
            region.base = 0x500000
            region.size = 4096
            region.state = 0x1000
            region.protect = protection
            return size

        kernel.ReadProcessMemory.side_effect = read
        kernel.VirtualQueryEx.side_effect = query
        return kernel

    def test_owned_code_reads_two_fixed_targets_without_following_relative_jump(self):
        """直接E9只换算模块归属；每个固定槽最多128字节且不会进一步读取跳转目标。"""
        kernel = self.kernel()
        group = Mock()
        group.pids.return_value = [10]
        # 这是纯内存API夹具；非Windows的ctypes没有WinDLL属性，测试临时创建后自动恢复。
        with tempfile.TemporaryDirectory() as directory, patch('tools.native_login_probe.ctypes.WinDLL', return_value=kernel, create=True):
            locate = Mock(return_value={'module': 'fixture.dll', 'rva': '0x10'})
            result = sample_nickname_thunks(group, 10, 20, 0x400000, Path(directory), locate)
            self.assertEqual(len(result), 2)
            self.assertEqual(kernel.ReadProcessMemory.call_count, 3)
            self.assertEqual(locate.call_count, 2)
            for row in result:
                self.assertFalse(row['recursive_follow'])
                self.assertEqual((Path(directory) / row['file']).stat().st_size, 128)

    def test_non_owned_process_never_reads_slots(self):
        """进程不属于当前Job时不能读取固定槽，更不能通过外部PID采集代码。"""
        kernel = self.kernel()
        group = Mock()
        group.pids.return_value = []
        with patch('tools.native_login_probe.ctypes.WinDLL', return_value=kernel, create=True):
            self.assertEqual(sample_nickname_thunks(group, 10, 20, 0x400000, Path('.'), Mock()), [])
            kernel.ReadProcessMemory.assert_not_called()

    def test_data_guard_and_noaccess_pages_are_not_read(self):
        """可读数据页、guard代码页和noaccess均拒绝；只允许先读取8字节固定函数槽。"""
        group = Mock()
        group.pids.return_value = [10]
        for protection in (0x04, 0x120, 0x01):
            kernel = self.kernel(protection)
            with self.subTest(protection=protection), patch('tools.native_login_probe.ctypes.WinDLL', return_value=kernel, create=True):
                result = sample_nickname_thunks(group, 10, 20, 0x400000, Path('.'), Mock())
                self.assertEqual(kernel.ReadProcessMemory.call_count, 1)
                self.assertTrue(all(row['executable'] is False for row in result))

    def test_partial_slot_read_cannot_create_evidence(self):
        """固定槽只读到部分字节时不解码残缺指针，不查询页面或创建代码文件。"""
        kernel = self.kernel(partial=True)
        group = Mock()
        group.pids.return_value = [10]
        with patch('tools.native_login_probe.ctypes.WinDLL', return_value=kernel, create=True):
            self.assertEqual(sample_nickname_thunks(group, 10, 20, 0x400000, Path('.'), Mock()), [])
            kernel.VirtualQueryEx.assert_not_called()


if __name__ == '__main__':
    unittest.main()
