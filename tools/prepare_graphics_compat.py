"""下载固定官方图形组件并核验发布摘要；只写仓库工具缓存，不安装到游戏或系统目录。"""
import json
import os
from pathlib import Path
import tempfile
from urllib.request import urlopen

from tools.native_graphics_compat import ARCHIVE_SHA256, RELEASE, RELEASE_URL, bundle


def main():
    """已有包必须通过摘要核验；新包限制长度、核验后无覆盖重命名，失败只删除自己的临时文件。"""
    root = Path(__file__).resolve().parents[1]/('runtime/tools/dxwrapper-' + RELEASE)
    root.mkdir(parents=True, exist_ok=True)
    destination = root/'dx9.games.zip'
    if not destination.exists():
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=root, prefix='download-', suffix='.zip', delete=False) as stream:
                temporary = Path(stream.name)
                with urlopen(RELEASE_URL, timeout=60) as response:
                    total = 0
                    while content := response.read(256*1024):
                        total += len(content)
                        if total > 3642528:
                            raise RuntimeError('GRAPHICS_COMPAT_DOWNLOAD_TOO_LARGE')
                        stream.write(content)
                stream.flush(); os.fsync(stream.fileno())
            bundle(temporary)
            if os.name != 'nt':
                raise RuntimeError('GRAPHICS_COMPAT_BUILD_REQUIRES_WINDOWS')
            os.rename(temporary, destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    bundle(destination)
    print(json.dumps(dict(release=RELEASE, sha256=ARCHIVE_SHA256, verified=True)))


if __name__ == '__main__':
    main()
