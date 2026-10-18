"""显示桥发布边界：拒绝未知二进制，恢复仅移除本轮组件，不把模拟测试作为图形验收。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import native_frame_bridge as bridge
from tools.native_config_guard import NativeConfigGuard


class FrameBridgeTests(unittest.TestCase):
    """覆盖损坏包、第三方文件冲突、持久恢复，以及与旧兼容模式互斥。"""

    def test_missing_or_changed_component_rejected(self):
        """部署不完整、同名DLL被替换时均拒绝安装，不能按文件名信任可执行代码。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'component.dll'
            with self.assertRaisesRegex(RuntimeError, 'COMPONENT_MISSING'):
                bridge.bundle(path)
            path.write_bytes(b'untrusted replacement')
            with self.assertRaisesRegex(RuntimeError, 'COMPONENT_MISMATCH'):
                bridge.bundle(path)

    def test_conflict_is_not_overwritten_and_restart_restores_owned_file(self):
        """外部组件不覆盖；本轮成功添加后即使守卫对象消失，也能从计划安全恢复。"""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); root = base/'client'; output = base/'evidence'
            target = root/bridge.TARGET
            target.parent.mkdir(parents=True); output.mkdir()
            target.write_bytes(b'external')
            guard = NativeConfigGuard(root, output)
            with patch.object(bridge, 'bundle', return_value=b'owned'):
                with self.assertRaisesRegex(RuntimeError, 'FILE_CONFLICT'):
                    bridge.install(guard)
                self.assertEqual(target.read_bytes(), b'external')
                self.assertEqual(guard.records, [])
                target.unlink()
                result = bridge.install(guard)
            self.assertEqual(result['profile'], bridge.PROFILE)
            self.assertEqual(target.read_bytes(), b'owned')
            recovered = NativeConfigGuard.from_plan(root, output).restore()
            self.assertTrue(recovered['configuration_restored'])
            self.assertFalse(target.exists())

    def test_modes_rejected_before_process_or_client_access(self):
        """互斥模式及错误类型在路径检查、写入和创建进程之前拒绝。"""
        from tools.native_email_boot import probe
        for arguments in ({'frame_copy': 'yes'}, {'graphics_compat': True, 'frame_copy': True}):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ValueError, 'INVALID_GRAPHICS_COMPAT_MODE'):
                probe(None, None, None, None, **arguments)
