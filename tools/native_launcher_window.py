"""同一 Windows 用户重开 Launcher 页面；DPAPI 文件仅含临时本机能力，不含游戏凭据。"""
import ctypes
from ctypes import wintypes
import http.client
import json
import os
import re
import webbrowser


def protect(content, *, decrypt=False):
    """使用当前 Windows 用户 DPAPI，无 UI、无机器级共享；缓冲区由系统释放。"""
    class Blob(ctypes.Structure):
        """DATA_BLOB 对齐按本机指针宽度，正文长度上限由调用方控制。"""
        _fields_ = [('length', wintypes.DWORD), ('data', ctypes.c_void_p)]
    if not isinstance(content, bytes) or not 1 <= len(content) <= 16384:
        raise ValueError('INVALID_WINDOW_RECORD')
    buffer = ctypes.create_string_buffer(content)
    source, target = Blob(len(content), ctypes.cast(buffer, ctypes.c_void_p)), Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    method = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    method.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    method.restype = wintypes.BOOL
    if not method(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise RuntimeError('WINDOW_RECORD_UNAVAILABLE')
    try:
        return ctypes.string_at(target.data, target.length)
    finally:
        kernel.LocalFree(target.data)


def save_window(path, capability):
    """单实例锁内原子替换临时能力；历史文件不参与游戏登录或配置恢复。"""
    payload = protect(json.dumps({'version': 1, 'capability': capability}).encode())
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def reopen_window(path, port):
    """只向固定回环端口验证旧能力，确认活跃同一服务后打开页面；不信任文件中的 URL。"""
    try:
        if not path.is_file() or path.stat().st_size > 16384:
            return False
        data = json.loads(protect(path.read_bytes(), decrypt=True))
        capability = data['capability']
        if data.get('version') != 1 or not re.fullmatch(r'[A-Za-z0-9_-]{43}', capability):
            return False
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        try:
            connection.request('GET', '/launcher/api/status', headers={'X-NZ-Launcher': capability})
            response = connection.getresponse()
            body = json.loads(response.read(16384))
            if response.status != 200 or body.get('version') != 'cn2020-launcher-v1':
                return False
        finally:
            connection.close()
        return webbrowser.open(f'http://127.0.0.1:{port}/launcher#access={capability}')
    except Exception:
        # 过期、他人用户的 DPAPI 文件或端口占用都不能退化成无认证入口。
        return False
