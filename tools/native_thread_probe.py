"""当前启动探针的线程取样：只读取所属 Job 的 x86 指令位置与帧链，不改代码或寄存器。"""
import ctypes
from ctypes import wintypes
import hashlib
import struct
import time


def sample_owned_threads(group, code_output=None, discover_login_references=False, expected_identity=None):
    """短暂暂停本 Job 的高 CPU/主线程，取样后立即恢复；拒绝采样其他进程，错误显式记录。

    帧链在编译器省略 EBP 时可能不完整，因此结果只是初始化定位线索，不能判定业务成功。
    不请求调试特权、不读取任意数据区、不保存原始栈或凭据、不生成转储文件。
    """
    class Module(ctypes.Structure):
        """Toolhelp 模块布局，用基址把取样地址转换成可公开的模块名和 RVA。"""
        _fields_=[('size',wintypes.DWORD),('module_id',wintypes.DWORD),('pid',wintypes.DWORD),
                  ('global_count',wintypes.DWORD),('process_count',wintypes.DWORD),('base',ctypes.c_void_p),
                  ('length',wintypes.DWORD),('handle',wintypes.HMODULE),('name',wintypes.WCHAR*256),('path',wintypes.WCHAR*260)]
    class Thread(ctypes.Structure):
        """THREADENTRY32 只用于筛选本 Job 线程，不输出其他程序的线程列表。"""
        _fields_=[('size',wintypes.DWORD),('usage',wintypes.DWORD),('tid',wintypes.DWORD),
                  ('pid',wintypes.DWORD),('priority',wintypes.LONG),('delta',wintypes.LONG),('flags',wintypes.DWORD)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
    for name in ('Module32FirstW','Module32NextW'):
        getattr(kernel,name).argtypes=[wintypes.HANDLE,ctypes.POINTER(Module)]
    for name in ('Thread32First','Thread32Next'):
        getattr(kernel,name).argtypes=[wintypes.HANDLE,ctypes.POINTER(Thread)]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.OpenThread.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenThread.restype=wintypes.HANDLE
    kernel.GetProcessIdOfThread.argtypes=[wintypes.HANDLE]
    kernel.GetProcessIdOfThread.restype=wintypes.DWORD
    kernel.GetThreadTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(ctypes.c_uint64)]*4)]
    kernel.Wow64SuspendThread.argtypes=kernel.ResumeThread.argtypes=[wintypes.HANDLE]
    kernel.Wow64SuspendThread.restype=kernel.ResumeThread.restype=wintypes.DWORD
    kernel.Wow64GetThreadContext.argtypes=[wintypes.HANDLE,ctypes.c_void_p]
    kernel.ReadProcessMemory.argtypes=[wintypes.HANDLE,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.POINTER(ctypes.c_size_t)]
    active=set(group.pids())
    result={pid:{'threads':[]} for pid in active}
    snapshot=kernel.CreateToolhelp32Snapshot(4,0)
    if snapshot==ctypes.c_void_p(-1).value:
        return {'winerror':ctypes.get_last_error()}
    threads={pid:[] for pid in active}
    try:
        entry=Thread(); entry.size=ctypes.sizeof(entry)
        valid=kernel.Thread32First(snapshot,ctypes.byref(entry))
        while valid:
            if entry.pid in active:
                handle=kernel.OpenThread(0x800,False,entry.tid)
                if handle:
                    try:
                        created,exited,cpu_system,cpu_user=(ctypes.c_uint64() for _ in range(4))
                        if kernel.GetThreadTimes(handle,*map(ctypes.byref,(created,exited,cpu_system,cpu_user))):
                            threads[entry.pid].append((entry.tid,created.value,cpu_system.value+cpu_user.value))
                    finally:
                        kernel.CloseHandle(handle)
            valid=kernel.Thread32Next(snapshot,ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    for pid,rows in threads.items():
        if pid not in group.pids() or not rows:
            continue
        modules=[]
        snapshot=kernel.CreateToolhelp32Snapshot(0x18,pid)
        if snapshot!=ctypes.c_void_p(-1).value:
            try:
                entry=Module(); entry.size=ctypes.sizeof(entry)
                valid=kernel.Module32FirstW(snapshot,ctypes.byref(entry))
                while valid:
                    modules.append((entry.base,entry.length,entry.name))
                    valid=kernel.Module32NextW(snapshot,ctypes.byref(entry))
            finally:
                kernel.CloseHandle(snapshot)

        def location(address):
            """只输出模块内偏移，无法归属的堆/栈地址不公开。"""
            for base,length,name in modules:
                if base <= address < base+length:
                    return {'module':name,'rva':hex(address-base)}
            return {'module':'UNMAPPED'}

        # PROCESS_VM_READ 只供有界 EBP 帧链读取；不能打开时仅保留错误，不绕过进程保护。
        process=kernel.OpenProcess(0x1010,False,pid)
        if not process:
            result[pid]['winerror']=ctypes.get_last_error(); continue
        try:
            if code_output is not None:
                # 仅采集当前样本已观察到的调用点指令；不读取 MessageBox 参数、窗口文本或任意数据区。
                # 代码 RVA 来自本次主线程帧链，不能将它用于其他客户端构建。
                for base,length,name in modules:
                    if name.lower()=='tgame.exe' and base<0x100000000 and length>0xf83f00:
                        result[pid]['code_samples']=[]
                        # 后三个固定范围来自非沙箱 08/09 主线程 Sleep 调用帧及其直接被调函数。
                        # 用有界指令取样区分帧率控制与业务等待，不接受任意内存地址输入。
                        # 三段 14AB/14A5 范围由第 11 次登录常量引用定位，补接口调用和建连分支。
                        # 14A/14B 范围由第17至20次Geo引用定位，核对收包接口及其调用链。
                        # 155F 两段由第20次当前登录常量引用定位，补齐Result分支；不读账号或窗口数据。
                        for rva,size in ((0xf83d00,0x200),(0xfb5b00,0x500),(0xfb2600,0x600),(0x12d180,0x80),
                                         (0x14ab780,0x100),(0x14a5100,0x300),(0x14a551f,0x200),
                                         (0x14a1c20,0x60),(0x14a1180,0x100),(0x14a60a0,0x180),(0x14bb4d0,0x180),
                                         (0x14bae80,0x200),(0x155f39c,0x500),(0x155f851,0x4a0),(0x155e540,0x200),
                                         (0x14f42c0,0x400),(0x14f58bd,0x900)):
                            code=ctypes.create_string_buffer(size); read=ctypes.c_size_t()
                            if kernel.ReadProcessMemory(process,base+rva,code,size,ctypes.byref(read)) and read.value==size:
                                target=code_output/f'tgame-{pid}-{rva:x}.bin'
                                with target.open('xb') as stream:
                                    stream.write(code.raw)
                                result[pid]['code_samples'].append({'image_base':hex(base),'rva':hex(rva),'bytes':size,
                                    'sha256':hashlib.sha256(code.raw).hexdigest(),'file':target.name})
                        # 固定当前构建的公开登录/Geo诊断常量，只在代码页寻找引用，不读取 UI 或账号结构。
                        if discover_login_references:
                            from tools.native_login_probe import sample_login_code
                            result[pid]['login_code_refs']=sample_login_code(group,pid,process,base,length,code_output)
                        if expected_identity is not None:
                            from tools.native_login_probe import compare_owned_login_state
                            result[pid]['login_state_comparison']=compare_owned_login_state(
                                group,pid,process,base,*expected_identity)
                        break
            oldest=min(rows,key=lambda row:row[1])
            selected={row[0]:row for row in [oldest,*sorted(rows,key=lambda row:row[2],reverse=True)[:4]]}
            for tid,created,cpu in selected.values():
                report={'tid':tid,'oldest_thread':tid==oldest[0],'cpu_seconds':cpu/10_000_000}
                result[pid]['threads'].append(report)
                handle=kernel.OpenThread(0x84a,False,tid)  # 查询、取上下文、暂停恢复；没有设置上下文的权限。
                if not handle:
                    report['winerror']=ctypes.get_last_error(); continue
                suspended=False
                try:
                    # 再核对实际线程所属进程，避免枚举后线程 ID 被其他进程复用。
                    if kernel.GetProcessIdOfThread(handle)!=pid or pid not in group.pids():
                        report['owner_changed']=True; continue
                    if kernel.Wow64SuspendThread(handle)==0xffffffff:
                        report['suspend_winerror']=ctypes.get_last_error(); continue
                    suspended=True
                    deadline=time.monotonic()+0.1
                    context=ctypes.create_string_buffer(716)
                    struct.pack_into('<I',context,0,0x10001)  # WOW64_CONTEXT_CONTROL，只读 EIP/ESP/EBP。
                    if not kernel.Wow64GetThreadContext(handle,context):
                        report['context_winerror']=ctypes.get_last_error(); continue
                    ebp,eip=struct.unpack_from('<II',context,180)
                    esp=struct.unpack_from('<I',context,196)[0]
                    report['instruction']=location(eip)
                    report['frame_chain']=[]
                    for _ in range(16):
                        if time.monotonic()>deadline or not esp <= ebp <= esp+262144:
                            break
                        pair=ctypes.create_string_buffer(8); read=ctypes.c_size_t()
                        if not kernel.ReadProcessMemory(process,ebp,pair,8,ctypes.byref(read)) or read.value!=8:
                            break
                        following,returned=struct.unpack('<II',pair.raw)
                        report['frame_chain'].append(location(returned))
                        if following<=ebp:
                            break
                        ebp=following
                finally:
                    if suspended:
                        report['resumed']=kernel.ResumeThread(handle)!=0xffffffff
                    kernel.CloseHandle(handle)
        finally:
            kernel.CloseHandle(process)
    return result
