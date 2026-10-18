"""仅在本探针自有 TGame 可执行页内寻找当前版本的公开登录诊断常量引用，不读取 UI 或凭据。"""
import ctypes
from ctypes import wintypes
import hashlib
import struct
import time


def sample_nickname_resolvers(group, pid, process, base, output, locate):
    """核对当前TGameBase的四段固定指令、两个导入地址候选及其单层可执行跳板。

    偏移来自31轮直接跳转和同哈希磁盘指令；静态异或常数仅为候选，必须和运行时代码互证。
    函数不执行代码、不递归跟随、不读取UI/账号/任意结构；文件只进入当前私人证据目录。
    """
    class Region(ctypes.Structure):
        """检查候选地址所在页的权限，防止将解码失败后的数据地址当代码读取。"""
        _fields_ = [('base', ctypes.c_void_p), ('allocation_base', ctypes.c_void_p),
                    ('allocation_protect', wintypes.DWORD), ('size', ctypes.c_size_t),
                    ('state', wintypes.DWORD), ('protect', wintypes.DWORD), ('kind', wintypes.DWORD)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(Region), ctypes.c_size_t]
    kernel.VirtualQueryEx.restype = ctypes.c_size_t
    kernel.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]

    def read(address, size, executable=False):
        """仅由下方固定表和单层代码候选调用；每次重查Job所有权，代码必须在可执行页内。"""
        if pid not in group.pids():
            return None
        if executable:
            region = Region()
            if (not kernel.VirtualQueryEx(process, address, ctypes.byref(region), ctypes.sizeof(region))
                    or region.state != 0x1000 or region.protect & 0x101 or not region.protect & 0xF0
                    or address + size > region.base + region.size):
                return None
        content = ctypes.create_string_buffer(size)
        received = ctypes.c_size_t()
        if not kernel.ReadProcessMemory(process, address, content, size, ctypes.byref(received)) or received.value != size:
            return None
        return content.raw

    result = {'resolver_code': [], 'candidates': []}
    for rva in (0x166a52, 0x167847, 0x1bb2a9a, 0x1bb9fa4):
        code = read(base + rva, 64, executable=True)
        if code is not None:
            name = f'tgamebase-{pid}-nickname-{rva:x}.bin'
            with (output / name).open('xb') as stream:
                stream.write(code)
            result['resolver_code'].append({'rva': hex(rva), 'bytes': 64, 'private_image_base': hex(base),
                                            'file': name, 'sha256': hashlib.sha256(code).hexdigest()})
    for index, (slot, mask) in enumerate(((0x76e55c, 0x8bbc441e), (0x76e958, 0x6a70dd8e))):
        value = read(base + slot, 4)
        if value is None:
            continue
        address = struct.unpack('<I', value)[0] ^ mask
        row = {'slot_rva': hex(slot), 'bytes': 4, 'static_mask_candidate': hex(mask),
               'resolved_code_location': locate(address), 'private_code_pointer': hex(address)}
        result['candidates'].append(row)
        code = read(address, 16, executable=True)
        row['candidate_executable'] = code is not None
        if code is None:
            continue
        name = f'tgamebase-{pid}-nickname-candidate-{index}.bin'
        with (output / name).open('xb') as stream:
            stream.write(code)
        row.update(file=name, code_bytes=16, sha256=hashlib.sha256(code).hexdigest())
        if code[0] == 0xe9:
            row['direct_jump_location'] = locate(address + 5 + struct.unpack_from('<i', code, 1)[0])
    return result


def sample_nickname_thunks(group, pid, process, base, output, locate):
    """只采样91F0已定位的两个导入槽所指的128字节可执行跳板，不递归跟随或执行目标。

    当前壳把这两个函数槽放入私有可执行页，不能直接按模块导入名猜API。
    数据页/guard/进程退出即停止；所有绝对地址和字节只进入私人证据。
    """
    class Region(ctypes.Structure):
        """VirtualQueryEx布局仅用于确认两个固定目标确为已提交可执行页面。"""
        _fields_ = [('base', ctypes.c_void_p), ('allocation_base', ctypes.c_void_p),
                    ('allocation_protect', wintypes.DWORD), ('size', ctypes.c_size_t),
                    ('state', wintypes.DWORD), ('protect', wintypes.DWORD), ('kind', wintypes.DWORD)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(Region), ctypes.c_size_t]
    kernel.VirtualQueryEx.restype = ctypes.c_size_t
    kernel.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    result = []
    if pid not in group.pids():
        return result
    slots = ctypes.create_string_buffer(8)
    read = ctypes.c_size_t()
    if not kernel.ReadProcessMemory(process, base + 0x1a3e1a0, slots, 8, ctypes.byref(read)) or read.value != 8:
        return result
    for index, target in enumerate(struct.unpack('<II', slots.raw)):
        if pid not in group.pids():
            break
        region = Region()
        if (not kernel.VirtualQueryEx(process, target, ctypes.byref(region), ctypes.sizeof(region))
                or region.state != 0x1000 or region.protect & 0x101 or not region.protect & 0xF0
                or target + 128 > region.base + region.size):
            result.append({'slot_rva': hex(0x1a3e1a0 + index * 4), 'executable': False})
            continue
        code = ctypes.create_string_buffer(128)
        if not kernel.ReadProcessMemory(process, target, code, 128, ctypes.byref(read)) or read.value != 128:
            continue
        name = f'tgame-{pid}-nickname-thunk-{index}.bin'
        with (output / name).open('xb') as stream:
            stream.write(code.raw)
        result.append({'slot_rva': hex(0x1a3e1a0 + index * 4), 'private_code_pointer': hex(target),
                       'executable': True, 'bytes': 128, 'file': name,
                       'sha256': hashlib.sha256(code.raw).hexdigest(), 'recursive_follow': False})
        # E9 的有符号相对目标仅做模块归属，不再读取目标；跳板后随机/不可达字节不能线性解释。
        if code.raw[0] == 0xe9:
            destination = target + 5 + struct.unpack_from('<i', code.raw, 1)[0]
            result[-1]['direct_jump_location'] = locate(destination)
    return result


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
    """有界搜索已核对PUSH常量及固定初始化标志写入，只保存小段指令；不暂停、注入或改写进程。

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
        0x1F3D630, 0x1F3D800, 0x1F3D910, 0x1F3D520,
        # 当前磁盘公开诊断字符串的首地址：账号完成与角色GID自修复，非UI或账号数据。
        0x1F0B628, 0x1F0C570, 0x1F0C490, 0x1F0C6F0, 0x1F0C778,
        # 44轮临时角色引出真实A008装备请求；以下为当前原生A009响应诊断常量首地址。
        0x1F0A7D0, 0x1F0A8F8, 0x1F0A9A0, 0x1F0AAB0,
        # 当前磁盘独立UTF-16常量SERVER/EDITOR；仅定位引擎启动参数解析代码。
        # 不套用PH构建的GIsServer地址，不修改标志或越过初始化条件。
        0x1D49250, 0x1D45EE4)}
    # A005当前尾部已证OR local-player+314的bit2。只查同一固定偏移的
    # OR立即数字节模式，寻找其他资料完成分支；匹配仅为候选，不能推断bit语义或修改该值。
    for register in (0,1,2,3,5,6,7):
        for bit in (1,2,4,8,16):
            targets[f'playerdata_314_or_reg{register}_bit{bit}']=bytes((0x83,0x88+register))+struct.pack('<I',0x314)+bytes((bit,))
    # 46轮真实A357引出当前详细频道结构；寻找注册/请求代码，不能套用PH的A358短正文。
    for command in (0xA355,0xA356,0xA357,0xA358):
        targets[f'zone_command_push_{command:x}']=b'\x68'+struct.pack('<I',command)
    # 当前资源文件的两个私有标记及标准UE3标记。只查可执行页中的立即数候选，
    # 用于核对解码分支；磁盘壳内偶然字节匹配不能当成运行时代码地址。
    for marker in (0x3e27a8ff,0x3e27a900,0x9e2a83c1):
        targets[f'package_header_marker_{marker:x}']=struct.pack('<I',marker)
    # boot69当前启动代码实际写入的四个模式候选全局，仅读其可执行页引用；
    # 仅为候选语义，不能读写全局值或据此宣称服务器已运行。每项仍最多八段256字节。
    for rva in (0x2149ad0,0x2149ad4,0x2149ab4,0x2149abc):
        targets[f'current_startup_mode_reference_{rva:x}']=struct.pack('<I',base+rva)
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
                            result['candidate_references'].append({'literal_rva': hex(literal) if isinstance(literal,int) else None,
                                'pattern_id': literal if isinstance(literal,str) else None, 'match_rva': hex(address-base),
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
