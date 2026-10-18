"""图形组件包与工作副本安装边界，不以模拟测试代替GPU/真实大厅验收。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import native_graphics_compat as graphics
from tools.native_config_guard import NativeConfigGuard


class GraphicsCompatibilityTests(unittest.TestCase):
    """错误包、已存在组件和部分安装失败不得覆盖玩家或其他工具的文件。"""

    def test_invalid_archive_is_rejected_before_reading_or_installing(self):
        """缺失及同名错误文件不能作为可执行兼容库；不信任下载文件名。"""
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory)/'dx9.games.zip'
            with self.assertRaisesRegex(RuntimeError, 'PACKAGE_MISSING'):
                graphics.bundle(archive)
            archive.write_bytes(bytes(3642528))
            with self.assertRaisesRegex(RuntimeError, 'PACKAGE_MISMATCH'):
                graphics.bundle(archive)

    def test_foreign_file_rejected_before_any_install(self):
        """即使第一项空闲而后项冲突，也应在写第一项前停止。"""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); root = base/'client'; output = base/'evidence'
            (root/'Binaries/Win32').mkdir(parents=True); output.mkdir()
            existing = root/'Binaries/Win32/dxwrapper.dll'; existing.write_bytes(b'external')
            guard = NativeConfigGuard(root, output)
            with patch.object(graphics, 'bundle', return_value={'d3d9.dll': b'owned', 'dxwrapper.dll': b'owned'}):
                with self.assertRaisesRegex(RuntimeError, 'FILE_CONFLICT'):
                    graphics.install(guard)
            self.assertFalse((root/'Binaries/Win32/d3d9.dll').exists())
            self.assertEqual(existing.read_bytes(), b'external')
            self.assertEqual(guard.records, [])

    def test_partial_install_failure_removes_only_owned_files_on_recovery(self):
        """第二项写入失败后保留计划；重新创建守卫可恢复已添加的第一项及未动原件。"""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); root = base/'client'; output = base/'evidence'
            (root/'Binaries/Win32').mkdir(parents=True); output.mkdir()
            guard = NativeConfigGuard(root, output); original = guard.add_new
            def second_failure(relative, content):
                """模拟磁盘在第一份DLL完成后失败，不动真实客户端。"""
                if relative.endswith('dxwrapper.dll'):
                    raise OSError('injected')
                return original(relative, content)
            with patch.object(graphics, 'bundle', return_value={'d3d9.dll': b'owned', 'dxwrapper.dll': b'owned'}), \
                    patch.object(guard, 'add_new', side_effect=second_failure):
                with self.assertRaises(OSError):
                    graphics.install(guard)
            self.assertTrue(NativeConfigGuard.from_plan(root, output).restore()['configuration_restored'])
            self.assertEqual(list((root/'Binaries/Win32').iterdir()), [])
