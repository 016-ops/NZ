"""当前构建的自有D3D9帧显示桥：固定摘要、受控安装和退出恢复。

只允许仓库编译并验收的组件进入客户端工作副本；不下载DLL、不更改系统或原始客户端。
大厅交互已经实测，战斗、性能和其他客户端构建不在该版本的验收结论内。
"""
import argparse
import hashlib
from pathlib import Path

PROFILE = 'cn2020-frame-copy-v1-lobby-verified'
SHA256 = '12592afcafdb0e76581a48ce142f3b152fcda55288ce1156855c639a393fd051'
COMPONENT = Path(__file__).resolve().parents[1]/'launcher/bin/components/d3d9-frame-copy.dll'
TARGET = 'Binaries/Win32/d3d9.dll'


def bundle(path=None):
    """完整摘要匹配后返回字节；缺失、损坏或替换的组件在任何客户端写入前拒绝。"""
    path = COMPONENT if path is None else Path(path)
    if not path.is_file():
        raise RuntimeError('FRAME_BRIDGE_COMPONENT_MISSING')
    # 固定组件远小于4MiB；先限制大小，避免错误文件导致无界读内存。
    if path.stat().st_size > 4 * 1024 * 1024:
        raise RuntimeError('FRAME_BRIDGE_COMPONENT_MISMATCH')
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != SHA256:
        raise RuntimeError('FRAME_BRIDGE_COMPONENT_MISMATCH')
    return content


def install(guard):
    """拒绝覆盖已有d3d9.dll；恢复计划先落盘，再原子添加本轮可移除组件。"""
    from tools.client_workspace import checked_path
    content = bundle()
    if checked_path(guard.root, TARGET).exists():
        raise RuntimeError('FRAME_BRIDGE_FILE_CONFLICT')
    guard.add_new(TARGET, content)
    return dict(profile=PROFILE, files={TARGET: SHA256}, system_changed=False,
        original_client_writes=0, presentation='d3d11-flip-on-game-window')


def main():
    """构建脚本专用打包入口；固定摘要校验后原子替换Launcher组件，不触碰游戏目录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', required=True, type=Path)
    args = parser.parse_args()
    content = bundle(args.package)
    COMPONENT.parent.mkdir(parents=True, exist_ok=True)
    temporary = COMPONENT.with_suffix('.dll.tmp')
    temporary.write_bytes(content)
    temporary.replace(COMPONENT)
    print(PROFILE + ' ' + SHA256)


if __name__ == '__main__':
    main()
