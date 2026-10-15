"""DEV 双控制台的 SSH 本机隧道；仅监听回环，固定远端端口，不输出连接凭据。"""
import argparse
from pathlib import Path
import select
import socketserver
import threading
import time
from tools.dev_remote import connect


class ForwardServer(socketserver.ThreadingTCPServer):
    """每连接一个线程，退出只关闭本次隧道，不影响服务器后台服务。"""
    daemon_threads = True
    allow_reuse_address = True


def handler(transport, port):
    """闭包固定 SSH 目标为服务器回环端口，浏览器数据原样传输而不代理/改写凭据。"""
    class Forward(socketserver.BaseRequestHandler):
        """在本机 socket 和 SSH direct-tcpip 通道之间转发直到任一端关闭。"""
        def handle(self):
            """每个通道随请求结束关闭；连接错误仅结束本连接，不记录原始正文。"""
            channel = None
            try:
                channel = transport.open_channel('direct-tcpip', ('127.0.0.1', port), self.request.getpeername(), timeout=10)
                if channel is None:
                    return
                while True:
                    ready, _, _ = select.select([self.request, channel], [], [], 30)
                    for source in ready:
                        data = source.recv(65536)
                        if not data:
                            return
                        (channel if source is self.request else self.request).sendall(data)
                    if not transport.is_active():
                        return
            except (OSError, EOFError):
                return
            finally:
                if channel is not None:
                    channel.close()
    return Forward


def main():
    """保持 18765/18767 同端口隧道，符合服务 Host/Origin 校验；Ctrl+C 只清理本机监听。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    client = connect(args.source, root / 'runtime/dev-known-hosts')
    servers = []
    try:
        transport = client.get_transport()
        transport.set_keepalive(30)
        for port in (18765, 18767):
            server = ForwardServer(('127.0.0.1', port), handler(transport, port))
            servers.append(server)
            threading.Thread(target=server.serve_forever, daemon=True).start()
        print('DEV SSH tunnels ready: http://127.0.0.1:18765 and http://127.0.0.1:18767', flush=True)
        while transport.is_active():
            time.sleep(1)
        raise ConnectionError('DEV SSH connection closed; restart the tunnel')
    except KeyboardInterrupt:
        pass
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        client.close()


if __name__ == '__main__':
    main()
