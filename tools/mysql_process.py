"""规避 Windows 中文 argv[0] 路径解析问题，只启动调用者明确指定的独立 MySQL。"""
import argparse
from pathlib import Path
import subprocess


def main():
    """使用明确 executable、ASCII argv[0] 及相对配置；后台模式隐藏窗口并释放终端。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init', 'start'])
    parser.add_argument('binary', type=Path)
    parser.add_argument('instance', type=Path)
    args = parser.parse_args()
    binary, instance = args.binary.resolve(strict=True), args.instance.resolve(strict=True)
    if binary.name.lower() != 'mysqld.exe' or not (instance / 'my.ini').is_file():
        raise ValueError('Expected an explicit MySQL executable and initialized instance configuration')
    command = ['mysqld', '--defaults-file=my.ini']
    if args.action == 'init':
        return subprocess.call([*command, '--initialize-insecure'], executable=str(binary), cwd=instance)
    subprocess.Popen(command, executable=str(binary), cwd=instance, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
