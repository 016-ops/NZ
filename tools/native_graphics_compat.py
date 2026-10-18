"""固定版本D3D9Ex Flip兼容配置：只安装到已验证工作副本，受持久恢复计划管理。

DxWrapper来自上游官方发布，保留原库及许可证，不启用插件、认证补丁或自定义进程。
此模块不自动下载、改动系统DLL或驱动；调用方必须先验证客户端、持有启动锁及清理自有Job。
"""
from pathlib import Path
import hashlib
from zipfile import ZipFile

PROFILE = 'cn2020-d3d9ex-flip-v3-experimental'
RELEASE = 'v1.9.8700.25'
ARCHIVE_SHA256 = '4680618ac5563d2c4042e344402eae76a55192634a3746e8fd672789089067e1'
RELEASE_URL = 'https://github.com/elishacloud/dxwrapper/releases/download/v1.9.8700.25/dx9.games.zip'
MEMBERS = {
    'd3d9.dll': (485888, 'b8b878e29c2931d6a8d064665c4c93edb28a0105152e7247f8f543c1603985c3'),
    'dxwrapper.dll': (8023552, '23a2889e0785a02f17f1ee764254bf22b937fc5bdc6ed6374201a9f50cc7b43d'),
    'License.txt': (24637, '8a586c8e0299cb3b141d589d9e933f58a5abb781e6c8775793ff21872ffee31c'),
}
# 只更改呈现模式。关闭插件、自定义DLL/进程、异常指令修复、系统API钩子与强制全屏。
# IncludeProcess每行追加一个进程名，上游不按逗号分割；写成一行会将真实进程全部排除。
# 仅匹配本构建的游戏及转交进程，已签名/原始EXE和认证接口保持原样。
CONFIG = b'''[General]
RealDllPath = AUTO
WrapperMode = AUTO
IncludeProcess = TGame.exe
IncludeProcess = GameLoader.exe
LoadCustomDllPath =
RunProcess =
DisableLogging = 1
[Plugins]
LoadPlugins = 0
[Compatibility]
EnableD3d9Wrapper = 1
D3d9to9Ex = 1
HandleExceptions = 0
[d3d9]
FlipEx = 1
AntiAliasing = 0
EnableWindowMode = 0
FullscreenWindowMode = 0
ForceExclusiveFullscreen = 0
DisableMaxWindowedMode = 0
D3d9HookSystem32 = 0
'''


def bundle(archive=None):
    """双重验证官方压缩包及逐文件摘要；只按固定成员名读取，不解压不可信路径。"""
    archive = Path(archive) if archive is not None else Path(__file__).resolve().parents[1]/(
        'runtime/tools/dxwrapper-' + RELEASE + '/dx9.games.zip')
    if not archive.is_file() or archive.stat().st_size != 3642528:
        raise RuntimeError('GRAPHICS_COMPAT_PACKAGE_MISSING')
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != ARCHIVE_SHA256:
            raise RuntimeError('GRAPHICS_COMPAT_PACKAGE_MISMATCH')
    result = {}
    with ZipFile(archive) as zipped:
        if len(zipped.infolist()) != 4 or set(zipped.namelist()) != {*MEMBERS, 'dxwrapper.ini'}:
            raise RuntimeError('GRAPHICS_COMPAT_PACKAGE_MISMATCH')
        for name, (size, expected) in MEMBERS.items():
            if zipped.getinfo(name).file_size != size:
                raise RuntimeError('GRAPHICS_COMPAT_PACKAGE_MISMATCH')
            content = zipped.read(name)
            if hashlib.sha256(content).hexdigest() != expected:
                raise RuntimeError('GRAPHICS_COMPAT_PACKAGE_MISMATCH')
            result[name if name != 'License.txt' else 'NZ-DxWrapper-License.txt'] = content
    result['dxwrapper.ini'] = CONFIG
    return result


def install(guard):
    """所有目标先检查不存在，再登记添加；部分安装失败由同一guard.finally完整恢复。"""
    from tools.client_workspace import checked_path
    files = bundle()
    targets = {'Binaries/Win32/' + name: content for name, content in files.items()}
    if any(checked_path(guard.root, relative).exists() for relative in targets):
        raise RuntimeError('GRAPHICS_COMPAT_FILE_CONFLICT')
    for relative, content in targets.items():
        guard.add_new(relative, content)
    return dict(profile=PROFILE, release=RELEASE, archive_sha256=ARCHIVE_SHA256,
        files={relative: hashlib.sha256(content).hexdigest() for relative, content in targets.items()},
        system_changed=False, original_client_writes=0)
