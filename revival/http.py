"""Loopback-only research HTTP API. This is not the retail game protocol."""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .assets import preview, settle
from .rooms import heartbeat, maintain
from .service import Fault, identifier

LOG = logging.getLogger('revival.http')
STATIC = Path(__file__).resolve().parents[1] / 'admin'


class Limits:
    def __init__(self):
        self.lock = threading.Lock()
        self.buckets = OrderedDict()

    def allow(self, key, maximum=30, window=300):
        now = time.monotonic()
        with self.lock:
            count, start = self.buckets.pop(key, (0, now))
            if now - start >= window:
                count, start = 0, now
            self.buckets[key] = (count + 1, start)
            while len(self.buckets) > 1024:
                self.buckets.popitem(last=False)
            return count < maximum


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, service):
        if address[0] not in ('127.0.0.1', '::1'):
            raise ValueError('Research server can only bind loopback')
        self.service = service
        self.limits = Limits()
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = 'NZResearch/0.1'

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format, *args):
        # Avoid logging request paths/queries, session tokens, or credentials.
        pass

    def _validate_host(self):
        port = self.server.server_address[1]
        valid = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if self.headers.get('Host') not in valid:
            raise Fault('HOST', 'Unexpected Host header', 403)
        origin = self.headers.get('Origin')
        if origin and origin not in {f'http://{host}' for host in valid}:
            raise Fault('ORIGIN', 'Cross-origin requests are rejected', 403)

    def _token(self):
        value = self.headers.get('Authorization', '')
        if not value.startswith('Bearer '):
            raise Fault('AUTH', 'Bearer session required', 401)
        return value[7:]

    def _send(self, status, value, content_type='application/json; charset=utf-8'):
        data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def do_GET(self):
        self._execute(self._get)

    def do_POST(self):
        self._execute(self._post)

    def _execute(self, operation):
        try:
            self._validate_host()
            operation()
        except Fault as error:
            if self.command == 'POST' and error.code not in ('RATE_LIMIT', 'BODY_SIZE'):
                try:
                    self.server.service.record_rejection(urlsplit(self.path).path, identifier(), error.code)
                except Exception:
                    LOG.error('Rejection audit failed')
            self._send(error.status, {'error': error.code, 'message': error.message})
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            self._send(400, {'error': 'INVALID', 'message': 'Invalid request structure'})
        except Exception:
            # Tracebacks are withheld because driver/transport exceptions may contain secrets.
            incident = identifier()
            LOG.error('Request failed: incident=%s', incident)
            self._send(500, {'error': 'INTERNAL', 'message': 'Request failed; assets remain transactional', 'incident_id': incident})

    def _get(self):
        parsed = urlsplit(self.path)
        if parsed.path in ('/', '/app.js', '/style.css'):
            names = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
            name, mime = names[parsed.path]
            self._send(200, (STATIC / name).read_bytes(), mime)
            return
        if parsed.path == '/health':
            self._send(200, {'status': 'ok', 'profile': self.server.service.catalog['id'], 'retail_compatible': False})
            return
        if parsed.path == '/api/status':
            self._send(200, {'profile': self.server.service.catalog['id'], 'evidence': self.server.service.catalog['evidence'],
                            'retail': {'login': 'BLOCKED', 'native_host': 'BLOCKED', 'inventory_sync': 'BLOCKED'},
                            'test_profile': self.server.service.test_profile})
            return
        if parsed.path.startswith('/api/views/'):
            filters = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            for name in ('limit', 'offset'):
                if name in filters:
                    filters[name] = int(filters[name])
            maintain(self.server.service)
            result = self.server.service.query(self._token(), parsed.path.removeprefix('/api/views/'), **filters)
            self._send(200, result)
            return
        raise Fault('NOT_FOUND', 'Route not found', 404)

    def _body(self):
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json' or self.headers.get('X-NZ-Request') != '1':
            raise Fault('CSRF', 'JSON content type and X-NZ-Request: 1 are required', 403)
        if self.headers.get('Transfer-Encoding'):
            raise Fault('INVALID', 'Transfer encoding is not accepted')
        length = int(self.headers.get('Content-Length', '0'))
        if not 1 <= length <= 65536:
            raise Fault('BODY_SIZE', 'Request body limit is 64 KiB', 413)
        def unique_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate JSON keys')
                result[key] = value
            return result
        data = json.loads(self.rfile.read(length), object_pairs_hook=unique_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite number')))
        if not isinstance(data, dict):
            raise Fault('INVALID', 'JSON object required')
        return data

    def _post(self):
        path = urlsplit(self.path).path
        if not self.server.limits.allow((self.client_address[0], path), 20 if path in ('/api/login', '/api/register') else 120, 300 if path in ('/api/login', '/api/register') else 60):
            raise Fault('RATE_LIMIT', 'Request limit reached; retry later', 429)
        data = self._body()
        service = self.server.service
        if path == '/api/login':
            result = service.login(data.get('login'), data.get('password'), kind=data.get('kind', 'player'), otp_code=data.get('otp_code'))
        elif path == '/api/register':
            result = service.register(data)
        elif path == '/api/command':
            maintain(service)
            result = service.command(self._token(), data.get('action'), data)
        elif path == '/api/asset-preview':
            result = preview(service, self._token(), data)
        elif path == '/api/node/heartbeat':
            result = heartbeat(service, self._token(), data.get('match_id'), data.get('epoch'))
        elif path == '/api/node/settlement':
            result = settle(service, self._token(), data)
        elif path == '/api/logout':
            token = self._token()
            from .security import token_hash
            with service.db.transaction() as c:
                actor = service._actor(c, token)
                c.execute('UPDATE sessions SET revoked=1 WHERE token_hash=?', (token_hash(token),))
                if actor['kind'] == 'player':
                    service._detach(c, actor['id'])
            result = {'logged_out': True}
        else:
            raise Fault('NOT_FOUND', 'Route not found', 404)
        self._send(200, result)
