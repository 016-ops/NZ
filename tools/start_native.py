"""本机在线手动测试入口：防止重复会话、按需连接既有DEV、保留游戏到用户退出。"""
import argparse
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / 'runtime/native-recovery/coherent-2020/nz'


def pending_recovery(root=ROOT):
    """发现尚未完成的原生配置恢复即阻止再次兑换票据；不结束旧进程或覆盖其配置。"""
    pending = []
    for path in (root/'evidence/private').glob('*/native-config-guard/plan.json'):
        plan = json.loads(path.read_text(encoding='utf-8'))
        if Path(plan.get('workspace', '')).resolve() != (root/'runtime/native-recovery/coherent-2020/nz').resolve():
            continue
        if any(row.get('status') not in ('RESTORED', 'ALREADY_ORIGINAL') for row in plan.get('records', [])):
            pending.append(path.parents[1].name)
    return sorted(pending)


@contextmanager
def single_launcher():
    """整个登录和游戏生命周期持有Windows文件锁；并发双击在创建会话前退出。"""
    import msvcrt
    with (ROOT/'runtime/native-start.lock').open('a+b') as stream:
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError('ANOTHER_LAUNCHER_IS_RUNNING') from None
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def current_health():
    """只接受当前环境健康响应，避免把合成PvE端口误当真实账号服务。"""
    from tools.email_launcher import Client
    result = Client('http://127.0.0.1:18765').request('/health')
    if result.get('status') != 'ok' or result.get('profile') != 'cn-current-unverified':
        raise RuntimeError('CURRENT_SERVICE_NOT_READY')
    return result


@contextmanager
def launcher_connection():
    """桌面启动器始终创建到已登记 DEV 的专属 SSH 通道，不复用来源不明的本机业务端口。"""
    from tools.dev_remote import connect
    from tools.dev_tunnel import ForwardServer, handler
    from tools.email_launcher import Client
    ssh = connect(ROOT.parent/'DEV环境服务器.txt', ROOT/'runtime/dev-known-hosts')
    server, thread = None, None
    try:
        server = ForwardServer(('127.0.0.1', 0), handler(ssh.get_transport(), 18765))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = Client(f'http://127.0.0.1:{server.server_address[1]}', host_port=18765)
        health = client.request('/health')
        if health.get('status') != 'ok' or health.get('profile') != 'cn-current-unverified':
            raise RuntimeError('CURRENT_SERVICE_NOT_READY')
        yield client
    finally:
        if server:
            if thread:
                server.shutdown(); thread.join(timeout=3)
            server.server_close()
        ssh.close()


@contextmanager
def online_connection():
    """复用健康的既有隧道；端口空闲时创建本次专属隧道，退出只清理自己创建的监听。"""
    with socket.socket() as probe:
        probe.settimeout(2)
        existing = probe.connect_ex(('127.0.0.1', 18765)) == 0
    if existing:
        current_health()
        yield
        return
    from tools.dev_remote import connect
    from tools.dev_tunnel import ForwardServer, handler
    client = connect(ROOT.parent/'DEV环境服务器.txt', ROOT/'runtime/dev-known-hosts')
    server = None
    thread = None
    try:
        server = ForwardServer(('127.0.0.1', 18765), handler(client.get_transport(), 18765))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        current_health()
        yield
    finally:
        if server:
            if thread:
                server.shutdown()
                thread.join(timeout=3)
            server.server_close()
        client.close()


def main():
    """默认启动既有GM到大厅供手测；--check只核对入口和未清理会话，不登录、不启动游戏。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--features',action='store_true',help='启用本批功能候选，要求DEV已经发布新迁移和接口')
    args = parser.parse_args()
    os.chdir(ROOT)
    if not (ROOT/'runtime/mail-acceptance.json').is_file() or not WORKSPACE.is_dir():
        print('缺少本机既有账号记录或恢复客户端，无法启动。')
        return 1
    pending = pending_recovery()
    if pending:
        print('上一轮启动尚未完成退出和配置恢复，暂不重复登录：' + ', '.join(pending))
        print('请先在游戏内退出，保留启动窗口等待清理完成；若只剩启动器卡住，请告知我处理。')
        return 2
    if args.check:
        print('入口文件齐全，未发现待恢复的启动会话；未登录或启动游戏。')
        return 0
    with single_launcher():
        # 获得锁后再次检查，避免两个入口交错运行到票据兑换。
        if pending_recovery():
            raise RuntimeError('PREVIOUS_SESSION_CLEANUP_PENDING')
        with online_connection():
            name = 'manual-online-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            output = ROOT/'evidence/private'/name
            log = ROOT/'runtime'/(name+'.log')
            print('正在连接既有GM账号并启动游戏，请等待大厅出现。', flush=True)
            print('请保留此启动窗口。没有自动退出计时；从游戏内退出后会恢复临时配置。', flush=True)
            print('当前可测试大厅、仓库和换装；开局和抽奖仍在开发。', flush=True)
            from tools.native_email_boot import main as boot
            previous = sys.argv
            sys.argv = ['native_email_boot', '--workspace', str(WORKSPACE), '--output', str(output),
                '--seconds', '300', '--manual-test', '--redirect-game-network', '--verify-native-challenge',
                '--capture-application', '--serve-geo-ping', '--serve-geo-zone', '--capture-zone-sequence',
                '--serve-zone-login', '--serve-player-define', '--serve-player-info', '--serve-player-props',
                '--dev-email-state', '--windowed', '--serve-detailed-channel', '--native-settings',
                '--serve-zone-hints', '--defer-player-info-after-version', '--continuous-zone', '--native-inventory']
            if args.features:
                sys.argv.append('--native-features')
            try:
                with log.open('x', encoding='utf-8') as stream, redirect_stdout(stream), redirect_stderr(stream):
                    boot()
            finally:
                sys.argv = previous
            if pending_recovery():
                raise RuntimeError('CONFIGURATION_RECOVERY_PENDING')
            print('游戏已退出，本次会话清理完成。')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('启动已中断；请等待会话清理完成后再重开。')
        raise SystemExit(130)
    except Exception as error:
        # 底层异常可能包含服务地址或私人材料，只向控制台显示异常类别。
        print('启动未完成（' + type(error).__name__ + '）。请提供此提示，保留runtime中的启动日志。')
        raise SystemExit(1)
