"""只读采样当前 Job 的 IPv4 TCP/UDP端点，保存端点状态而不抓取报文或其他程序连接。"""
import ctypes
from ctypes import wintypes
import socket
import struct


def sample_owned_tcp(group):
    """查询 Windows OWNER_PID 表后立即筛选本次 Job；失败显式报告，不把缺失样本解释为无网络。

    API 合同：https://learn.microsoft.com/en-us/windows/win32/api/iphlpapi/nf-iphlpapi-getextendedtcptable
    只覆盖采样时尚存的 IPv4 TCP；短连接、UDP、IPv6 和已退出进程不在这份证据范围。
    """
    owned = set(group.pids())
    if not owned:
        return {'connections': [], 'verified': True}
    library = ctypes.WinDLL('iphlpapi', use_last_error=True)
    query = library.GetExtendedTcpTable
    query.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
                      wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    query.restype = wintypes.DWORD
    size = wintypes.DWORD(0)
    for _ in range(4):
        if size.value > 8 * 1024 * 1024:
            return {'connections': [], 'verified': False, 'error': 'TABLE_SIZE_LIMIT'}
        buffer = ctypes.create_string_buffer(size.value) if size.value else None
        status = query(buffer, ctypes.byref(size), False, 2, 5, 0)  # AF_INET、TCP_TABLE_OWNER_PID_ALL。
        if status == 122:
            continue
        if status:
            return {'connections': [], 'verified': False, 'winerror': status}
        if buffer is None or len(buffer) < 4:
            break
        count = struct.unpack_from('<I', buffer)[0]
        if count > (len(buffer) - 4) // 24:
            break
        # 再次核对 Job 活跃集合，防止枚举结束前退出或 PID 复用把其他进程纳入报告。
        owned.intersection_update(group.pids())
        rows = []
        for index in range(count):
            state, local, local_port, remote, remote_port, pid = struct.unpack_from('<6I', buffer, 4 + index * 24)
            if pid in owned:
                rows.append({'pid': pid, 'state': state, 'local_address': socket.inet_ntoa(struct.pack('<I', local)),
                    'local_port': socket.ntohs(local_port & 0xffff),
                    'remote_address': socket.inet_ntoa(struct.pack('<I', remote)),
                    'remote_port': socket.ntohs(remote_port & 0xffff)})
        return {'connections': rows, 'verified': True}
    return {'connections': [], 'verified': False, 'error': 'TABLE_CHANGED_OR_INVALID'}


def sample_owned_udp(group):
    """按微软OWNER_PID表合同查询本Job的IPv4 UDP绑定；存在端口不等于原生握手或战斗就绪。

    API及12字节行格式：GetExtendedUdpTable、MIB_UDPROW_OWNER_PID。只返回本Job两次活跃
    集合的交集，不收集其他程序的地址，也不发送数据报。IPv6和采样间的短绑定不在覆盖范围。
    """
    owned=set(group.pids())
    if not owned:
        return {'endpoints':[], 'verified':True}
    query=ctypes.WinDLL('iphlpapi',use_last_error=True).GetExtendedUdpTable
    query.argtypes=[ctypes.c_void_p,ctypes.POINTER(wintypes.DWORD),wintypes.BOOL,
                    wintypes.ULONG,ctypes.c_int,wintypes.ULONG]
    query.restype=wintypes.DWORD
    size=wintypes.DWORD(0)
    for _ in range(4):
        if size.value>8*1024*1024:
            return {'endpoints':[], 'verified':False, 'error':'TABLE_SIZE_LIMIT'}
        buffer=ctypes.create_string_buffer(size.value) if size.value else None
        status=query(buffer,ctypes.byref(size),False,2,1,0)  # AF_INET、UDP_TABLE_OWNER_PID。
        if status==122:
            continue
        if status:
            return {'endpoints':[], 'verified':False, 'winerror':status}
        if buffer is None or len(buffer)<4:
            break
        count=struct.unpack_from('<I',buffer)[0]
        if count>(len(buffer)-4)//12:
            break
        owned.intersection_update(group.pids())
        rows=[]
        for index in range(count):
            address,port,pid=struct.unpack_from('<III',buffer,4+index*12)
            if pid in owned:
                rows.append({'pid':pid,'local_address':socket.inet_ntoa(struct.pack('<I',address)),
                             'local_port':socket.ntohs(port & 0xffff)})
        return {'endpoints':rows, 'verified':True}
    return {'endpoints':[], 'verified':False, 'error':'TABLE_CHANGED_OR_INVALID'}
