"""统一 WSGI 服务工厂；开发命令与真实 HTTP 测试均执行相同监听约束。"""
import json
from http import HTTPStatus
from waitress import create_server
from waitress.channel import HTTPChannel
from waitress.parser import HTTPRequestParser, ParsingError
from waitress.utilities import Error


class JsonTransportError(Error):
    """WSGI 之前的体积/传输错误也沿用 JSON 合同，避免默认文本错误页破坏客户端解析。"""
    def __init__(self, code, error, message):
        """保存 HTTP 状态及旧格式错误载荷；这里只构造响应数据，不执行数据库或业务操作。"""
        self.code=code
        self.reason=HTTPStatus(code).phrase
        self.payload={'error':error,'message':message}

    def to_response(self, ident=None):
        """传输层拒绝还没有进入 Django 中间件，因此这里补齐一致的安全响应头。"""
        headers=[('Content-Type','application/json; charset=utf-8'),('Cache-Control','no-store'),
                 ('X-Content-Type-Options','nosniff'),('Referrer-Policy','no-referrer'),
                 ('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")]
        return f'{self.code} {self.reason}',headers,json.dumps(self.payload).encode('utf-8')


class StrictRequestParser(HTTPRequestParser):
    """Waitress 会移除 Transfer-Encoding 头；必须在解码前拒绝，不能只依赖 Django。"""
    def parse_header(self, header_plus):
        """先执行 Waitress 标准头解析，再拒绝分块传输，防止解码后移除头绕过中间件。"""
        super().parse_header(header_plus)
        if self.chunked or self.headers.get('TRANSFER_ENCODING'):
            raise ParsingError('Transfer encoding is not accepted')

    def received(self, data):
        """超限正文最多排空 128 KiB 再返回错误，避免 Windows 在未读正文上发送 TCP RST。"""
        # 排空只计数字节、不保存正文，也不会进入 WSGI。超大请求仍立即关闭；
        # 上限和服务的连接超时共同限制恶意客户端占用资源的时间与流量。
        if getattr(self,'discard_remaining',0):
            consumed=min(len(data),self.discard_remaining)
            self.discard_remaining-=consumed
            self.completed=self.discard_remaining==0
            return consumed
        consumed=super().received(data)
        if self.error and not isinstance(self.error,JsonTransportError):
            if self.error.code==413:
                self.error=JsonTransportError(413,'BODY_SIZE','Request body limit is 64 KiB')
                if self.headers_finished and 65536 < self.content_length <= 131072:
                    self.discard_remaining=self.content_length
                    self.completed=False
            else:
                message='Transfer encoding is not accepted' if self.error.body=='Transfer encoding is not accepted' else 'Invalid HTTP request'
                self.error=JsonTransportError(self.error.code,'INVALID',message)
        return consumed


class StrictChannel(HTTPChannel):
    """服务与真实 HTTP 测试共用严格解析器，拒绝的请求不进入业务授权或命令分派。"""
    parser_class=StrictRequestParser


def local_server(application, host='127.0.0.1', port=8765):
    """只允许回环监听，16 个工作线程；独立 socket map 避免测试实例互相干扰。"""
    if host not in ('127.0.0.1', '::1'):
        raise ValueError('Research server can only bind loopback')
    # Waitress 在长度 >= max_body 时拒绝，设为 65537 才与 API 的 <= 65536 边界一致。
    server=create_server(application, host=host, port=port, threads=16, expose_tracebacks=False,
                         max_request_body_size=65537, channel_timeout=10, connection_limit=16, map={})
    server.channel_class=StrictChannel
    return server
