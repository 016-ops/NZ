"""仅在本探针自有 TGame 可执行页内寻找当前版本的公开登录诊断常量引用，不读取 UI 或凭据。"""
import ctypes
from ctypes import wintypes
import hashlib
import struct
import time


def compare_owned_login_state(group, pid, process, base, game_id, port):
    """只比较当前构建已证实的身份/端点字段与本探针预期值，不返回原值或读取令牌正文。

    这些固定偏移来自第 12 次 GetLoginInfo 输出实参与建连读取代码；只证明内存交接状态。
    两个初始化标量仅记录数值，不能当作窗口、登录结果或大厅成功标志。
    """
    if pid not in group.pids():
        return {'verified': False, 'reason': 'OWNER_EXITED'}
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]

    def read(rva, size):
        """仅由下方固定字段表调用，不接受外部地址，失败不输出部分缓冲区。"""
        data = ctypes.create_string_buffer(size); received = ctypes.c_size_t()
        if not kernel.ReadProcessMemory(process, base + rva, data, size, ctypes.byref(received)) or received.value != size:
            raise OSError('Bounded current login field read failed')
        return data.raw

    try:
        result = {'verified': True, 'account_matches': read(0x2240540, 4) == struct.pack('<I', game_id),
            'research_service_matches': read(0x2240534, 4) == struct.pack('<I', 1),
            'primary_host_matches_loopback': read(0x22419B0, 20) == '127.0.0.1\0'.encode('utf-16-le'),
            'primary_port_matches': read(0x2241AB0, 2) == struct.pack('<H', port),
            'login_key_length_nonzero': read(0x2240570, 1) != b'\0',
            'initialization_scalars': {hex(rva): struct.unpack('<I', read(rva, 4))[0] for rva in (0x2258D98, 0x2258D9C)},
            'credential_bytes_read': False, 'raw_identity_or_endpoint_returned': False}
    except OSError:
        return {'verified': False, 'reason': 'READ_FAILED'}
    if pid not in group.pids():
        return {'verified': False, 'reason': 'OWNER_EXITED_DURING_SAMPLE'}
    return result


def sample_login_code(group, pid, process, base, image_size, output):
    """有界搜索当前已核对的 PUSH 常量地址，只保存匹配前后小段指令；不暂停、注入或改写进程。

    调用方先验证客户端固定哈希，本函数再限制 Job 所属、模块范围、已提交可执行页及三秒预算。
    字节匹配仅是候选引用，仍需反汇编确认指令边界，不能据此宣布调用发生或登录成功。
    """
    class Region(ctypes.Structure):
        """本机 MEMORY_BASIC_INFORMATION 布局，依 ctypes 指针对齐查询页面权限。"""
        _fields_ = [('base', ctypes.c_void_p), ('allocation_base', ctypes.c_void_p),
                    ('allocation_protect', wintypes.DWORD), ('size', ctypes.c_size_t),
                    ('state', wintypes.DWORD), ('protect', wintypes.DWORD), ('kind', wintypes.DWORD)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(Region), ctypes.c_size_t]
    kernel.VirtualQueryEx.restype = ctypes.c_size_t
    kernel.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    end = base + min(image_size, 0x1A3D2EC)
    cursor = base + 0x1000
    deadline = time.monotonic() + 3
    # Zone 登录结果及 PlayerInfo 诊断常量用于定位返回码/资料消费；仍只读取可执行代码页。
    targets = {rva: b'\x68' + struct.pack('<I', base + rva) for rva in (
        0x1EFEB38, 0x1EFD1B0, 0x1EFD588, 0x1EF7398, 0x1EF71E8, 0x1EFCF30,
        0x1F0B870, 0x1F0B918, 0x1F0B328, 0x1F0CE08, 0x1F0D118, 0x1F0D258,
        0x1F3D630, 0x1F3D800, 0x1F3D910)}
    result = {'method': 'OWNED_EXECUTABLE_PAGES_FIXED_LITERAL_REFERENCES', 'candidate_references': [],
              'bytes_searched': 0, 'complete': False, 'ui_or_data_pages_read': False}
    counts = {rva: 0 for rva in targets}
    while cursor < end and time.monotonic() < deadline:
        if pid not in group.pids():
            result['owner_exited'] = True
            return result
        region = Region()
        if not kernel.VirtualQueryEx(process, cursor, ctypes.byref(region), ctypes.sizeof(region)) or not region.size:
            result['query_failed'] = True
            return result
        region_end = min(end, region.base + region.size)
        if region_end <= cursor:
            return result
        # 排除数据页、未提交页、guard/noaccess 及其他映像；绝不跨保护边界采样指令。
        if region.allocation_base == base and region.state == 0x1000 and not region.protect & 0x101 and region.protect & 0xF0:
            carry = b''
            while cursor < region_end and time.monotonic() < deadline:
                size = min(65536, region_end - cursor)
                buffer = ctypes.create_string_buffer(size); received = ctypes.c_size_t()
                if not kernel.ReadProcessMemory(process, cursor, buffer, size, ctypes.byref(received)) or received.value != size:
                    result['read_failed'] = True
                    return result
                data = carry + buffer.raw
                result['bytes_searched'] += size
                for literal, needle in targets.items():
                    index = data.find(needle)
                    while index >= 0 and counts[literal] < 8:
                        address = cursor - len(carry) + index
                        start = max(region.base, base + 0x1000, address - 96)
                        count = min(256, region_end - start)
                        code = ctypes.create_string_buffer(count)
                        if kernel.ReadProcessMemory(process, start, code, count, ctypes.byref(received)) and received.value == count:
                            name = f'tgame-{pid}-loginref-{address-base:x}.bin'
                            with (output / name).open('xb') as stream:
                                stream.write(code.raw)
                            result['candidate_references'].append({'literal_rva': hex(literal), 'match_rva': hex(address-base),
                                'sample_rva': hex(start-base), 'bytes': count, 'sha256': hashlib.sha256(code.raw).hexdigest(), 'file': name})
                            counts[literal] += 1
                        index = data.find(needle, index + 1)
                carry = data[-4:]
                cursor += size
            if cursor < region_end:
                return result
        cursor = region_end
    result['complete'] = cursor >= end
    return result
