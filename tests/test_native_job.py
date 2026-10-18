"""Windows真实子进程故障回归：观察器抛错后等待文件占用释放，再按持久计划恢复配置。"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from tools.native_config_guard import NativeConfigGuard
from tools.native_job import JobGroup


@unittest.skipUnless(os.name=='nt','Windows Job and file-sharing semantics required')
class NativeJobCleanupTests(unittest.TestCase):
    """所有进程与文件由本例创建，不使用真实游戏、账户、系统设置或网络。"""
    def test_observer_error_waits_for_owned_file_holder_before_restore(self):
        """重现诊断pids错误；退出with后文件持有者已结束，首次配置恢复即可成功。"""
        class FaultingObservation(JobGroup):
            """故意模拟研究钩子错误；清理不得再次依赖该钩子。"""
            def pids(self):
                raise TypeError('injected observation failure')
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);root=base/'client';output=base/'evidence'
            root.mkdir();output.mkdir();guard=NativeConfigGuard(root,output)
            guard.add_new('owned-overlay.dll',b'isolated test overlay')
            ready=base/'ready';process=None
            script=("import pathlib,sys,time; "
                    "stream=open(sys.argv[1],'rb'); "
                    "pathlib.Path(sys.argv[2]).write_text('ready'); time.sleep(30)")
            with self.assertRaisesRegex(TypeError,'injected observation'):
                with FaultingObservation() as group:
                    process=subprocess.Popen([sys.executable,'-c',script,str(root/'owned-overlay.dll'),str(ready)],
                                             creationflags=subprocess.CREATE_NO_WINDOW)
                    group.attach(process)
                    deadline=time.monotonic()+5
                    while not ready.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.02)
                    self.assertTrue(ready.exists(),'isolated child must hold the overlay before fault injection')
                    group.pids()
            self.assertIsNotNone(process.poll())
            self.assertTrue(group.cleanup_verified,group.cleanup_errors)
            self.assertEqual(group.cleanup_errors,[])
            self.assertTrue(guard.restore()['configuration_restored'])
            self.assertFalse((root/'owned-overlay.dll').exists())
            group.close()  # 重复关闭不破坏已验证结果。
            self.assertTrue(group.cleanup_verified)

    def test_parent_exit_does_not_leave_unobserved_owned_child_running(self):
        """父进程先退出且没有调用pids；关闭Job仍枚举并等待后代，避免只等待父句柄。"""
        with tempfile.TemporaryDirectory() as temporary:
            ready=Path(temporary)/'child-ready'
            child_script="import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text('ready'); time.sleep(30)"
            parent_script="import subprocess,sys; sys.stdin.read(1); subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]],creationflags=subprocess.CREATE_NO_WINDOW)"
            with JobGroup() as group:
                parent=subprocess.Popen([sys.executable,'-c',parent_script,child_script,str(ready)],
                                        stdin=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
                group.attach(parent);parent.stdin.write(b'1');parent.stdin.close();parent.wait(timeout=5)
                deadline=time.monotonic()+5
                while not ready.exists() and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(ready.exists())
            self.assertTrue(group.cleanup_verified,group.cleanup_errors)
            self.assertGreaterEqual(len(group.exit_codes),2)
