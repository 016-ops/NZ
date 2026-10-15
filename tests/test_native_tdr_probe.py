"""离线 TDR 工具的边界测试：非法模式和输出必须在任何原生进程启动前拒绝。"""
from contextlib import redirect_stderr
from itertools import combinations
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from tools import native_tdr_probe


class NativeTdrProbeBoundaryTests(unittest.TestCase):
    """不加载 DLL，仅验证命令入口不能绕过受限模式与私有证据目录。"""

    def test_zone_notification_cannot_enable_geo_responses(self):
        """通知模式不能同时构造任一种 GEO 响应，且拒绝发生在启动子进程前。"""
        for response in ("--verify-research-response", "--verify-zone-research-response"):
            with self.subTest(response=response), patch.object(sys, "argv", [
                "native_tdr_probe", "--output", "not-used", "--verify-zone-notification", response
            ]), patch.object(native_tdr_probe.subprocess, "Popen") as launch, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    native_tdr_probe.main()
                self.assertEqual(result.exception.code, 2)
                launch.assert_not_called()

    def test_zone_login_cannot_enable_geo_responses(self):
        """真实登录请求往返模式不能夹带GEO响应构造，互斥校验必须早于任何原生进程。"""
        for response in ("--verify-research-response", "--verify-zone-research-response"):
            with self.subTest(response=response), patch.object(sys, "argv", [
                "native_tdr_probe", "--output", "not-used", "--verify-zone-login", response
            ]), patch.object(native_tdr_probe.subprocess, "Popen") as launch, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    native_tdr_probe.main()
                self.assertEqual(result.exception.code, 2)
                launch.assert_not_called()

    def test_zone_captured_input_modes_are_mutually_exclusive(self):
        """Zone通知、登录请求、登录响应三种限定模式互斥，不让不同样本或响应构造相互覆盖。"""
        modes = ('--verify-zone-notification', '--verify-zone-login', '--verify-zone-login-response')
        for pair in combinations(modes, 2):
            with self.subTest(modes=pair), patch.object(sys, "argv", [
                "native_tdr_probe", "--output", "not-used", *pair
            ]), patch.object(native_tdr_probe.subprocess, "Popen") as launch, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    native_tdr_probe.main()
                self.assertEqual(result.exception.code, 2)
                launch.assert_not_called()

    def test_zone_login_response_cannot_enable_geo_responses(self):
        """固定A001响应profile不能同时执行GEO响应构造，防止单次结果混淆不同协议验收。"""
        for response in ('--verify-research-response', '--verify-zone-research-response'):
            with self.subTest(response=response), patch.object(sys, "argv", [
                "native_tdr_probe", "--output", "not-used", '--verify-zone-login-response', response
            ]), patch.object(native_tdr_probe.subprocess, "Popen") as launch, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    native_tdr_probe.main()
                self.assertEqual(result.exception.code, 2)
                launch.assert_not_called()

    def test_output_outside_private_evidence_is_rejected(self):
        """即使路径在仓库内，也不能把含主机结构的原生输出写到公开证据目录。"""
        destination = Path(native_tdr_probe.__file__).resolve().parents[1] / "evidence" / "forbidden-public-probe"
        with patch.object(sys, "argv", ["native_tdr_probe", "--output", str(destination)]), \
                patch.object(native_tdr_probe.subprocess, "Popen") as launch:
            with self.assertRaises(ValueError):
                native_tdr_probe.main()
            launch.assert_not_called()
        self.assertFalse(destination.exists())

    def test_arbitrary_dll_option_is_rejected(self):
        """工具没有任意 DLL 参数，外部路径不得进入加载流程。"""
        with patch.object(sys, "argv", [
            "native_tdr_probe", "--output", "not-used", "--dll", "untrusted.dll"
        ]), patch.object(native_tdr_probe.subprocess, "Popen") as launch, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                native_tdr_probe.main()
            self.assertEqual(result.exception.code, 2)
            launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
