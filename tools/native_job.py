"""Windows 探针进程组：父进程退出后仍跟踪 GameLoader 子进程，关闭句柄统一清理。"""
import ctypes
from ctypes import wintypes
import os
import time


class JobGroup:
    """只关联调用方刚创建并持有句柄的进程，不按游戏名称匹配其他进程。"""
    def __init__(self):
        """创建无 UI/内存/CPU 限额的匿名 Job，仅配置退出清理，不请求系统调试权限。"""
        if os.name != 'nt':
            raise OSError('Native job groups require Windows')
        class Basic(ctypes.Structure):
            """对应 JOBOBJECT_BASIC_LIMIT_INFORMATION 的原生字段顺序及指针对齐。"""
            _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64), ('flags', wintypes.DWORD),
                        ('min_working', ctypes.c_size_t), ('max_working', ctypes.c_size_t), ('active_limit', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]
        class Extended(ctypes.Structure):
            """包含 IO_COUNTERS 与四个 SIZE_T，避免硬编码 x64 结构字节偏移。"""
            _fields_ = [('basic', Basic), ('io', ctypes.c_uint64 * 6), ('process_memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        self.kernel.QueryInformationJobObject.restype = wintypes.BOOL
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.TerminateJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.GetExitCodeProcess.restype = wintypes.BOOL
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        # 保留首次观察到的子进程句柄，退出后仍可查询状态，且不会误读复用后的同号 PID。
        self.process_handles, self.exit_codes, self.query_errors = {}, {}, {}
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE，不设置 breakaway 或保护绕过。
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def attach(self, process):
        """关联刚创建的 Popen 句柄；失败立即终止该进程，禁止静默退回无限制启动。"""
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            error = ctypes.get_last_error()
            process.terminate()
            process.wait(timeout=5)
            raise ctypes.WinError(error)
        # Popen 仍持有父进程句柄；即使它随后先退出，也必须登记，不能只等活跃 PID 枚举。
        self.watch_pid(process.pid)

    def watch_pid(self, pid):
        """为本次已关联进程保存只读句柄；失败保留错误，不伪造退出状态。"""
        if pid not in self.process_handles:
            handle = self.kernel.OpenProcess(0x101000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION。
            if handle:
                self.process_handles[pid] = handle
                self.query_errors.pop(pid, None)
            else:
                self.query_errors[pid] = ctypes.get_last_error()

    def pids(self):
        """枚举 Job 内实际仍活跃的 PID；超过 1024 个进程则报错并由上下文清理。"""
        buffer = ctypes.create_string_buffer(8 + 1024 * ctypes.sizeof(ctypes.c_size_t))
        if not self.kernel.QueryInformationJobObject(self.handle, 3, buffer, len(buffer), None):
            raise ctypes.WinError(ctypes.get_last_error())
        count = ctypes.c_uint32.from_buffer(buffer, 4).value
        active = list((ctypes.c_size_t * count).from_buffer(buffer, 8))
        for pid in active:
            self.watch_pid(pid)
        self.collect_exits()
        return active

    def collect_exits(self, timeout_ms=0):
        """只记录已结束进程；清理时可全局限时等句柄触发，Job 移除 PID 可能略早于此信号。"""
        deadline = time.monotonic() + timeout_ms / 1000
        for pid, handle in self.process_handles.items():
            wait_ms = max(0, int((deadline - time.monotonic()) * 1000))
            # 259 本身可能是真实退出码，须先核对句柄已触发，不能仅依据 STILL_ACTIVE 数字判断。
            if pid not in self.exit_codes and self.kernel.WaitForSingleObject(handle, wait_ms) == 0:
                code = wintypes.DWORD()
                if self.kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                    self.exit_codes[pid] = code.value
                else:
                    self.query_errors[pid] = ctypes.get_last_error()
        return dict(self.exit_codes)

    def terminate(self):
        """只结束此 Job 中的全部实例，包含已退出父进程留下的 GameLoader 子树。"""
        if not self.kernel.TerminateJobObject(self.handle, 0xE0000001):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        """异常退出也关闭最后一个 Job 句柄，由 Windows 清理关联进程。"""
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        for handle in self.process_handles.values():
            self.kernel.CloseHandle(handle)
        self.process_handles.clear()

    def __enter__(self):
        """将实例用于 with，在观察流程出错时仍保证执行 close。"""
        return self

    def __exit__(self, *_):
        """清理本次 Job，不修改其他应用、系统服务或驱动。"""
        self.close()
