"""Local authenticated JSON gateway to the existing, audited Python deploy engine.

The browser/web server does not run this. It is a *local child process* of the
Godot desktop app, bound only to 127.0.0.1, with a fresh session capability.
No credentials, GitHub tokens, or SSH responses are logged or written to disk.
"""
from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import threading
import time
from typing import Any

from . import deploy
from .models import Config, UserError
from .ssh_client import probe_host, pin_host

MAX_REQUEST_BYTES = 32768
MAX_RESPONSE_BYTES = 131072
TTL_SECONDS = 15 * 60


def config_from_dict(source: object) -> Config:
    if not isinstance(source, dict):
        raise UserError('Отсутствуют параметры подключения.')
    fields = {k: source.get(k, '') for k in Config.__dataclass_fields__}
    if not all(isinstance(v, (str, int)) for v in fields.values()):
        raise UserError('Некорректный формат параметров подключения.')
    try:
        fields['port'] = int(fields['port'])
    except (TypeError, ValueError) as exc:
        raise UserError('SSH-порт должен быть числом.') from exc
    return Config(**fields).validated()


def config_fingerprint(config: Config) -> str:
    # Never persist credentials or print the digest. Used for inspection binding.
    payload = {name: getattr(config, name) for name in Config.__dataclass_fields__}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Engine:
    def __init__(self):
        self.probed = None
        self.checked = None
        self.checked_until = 0.0
        self.lock = threading.Lock()

    def execute(self, action: str, raw_config: object, fingerprint: object = None) -> dict:
        config = config_from_dict(raw_config)
        if action == 'probe':
            self.checked = None
            details, key = probe_host(config)
            self.probed = (config.host, config.port, details['fingerprint'], key)
            return {'details': details}
        if action == 'pin':
            if not isinstance(fingerprint, str) or self.probed is None:
                raise UserError('Сначала проверьте SSH-сервер и его отпечаток.')
            host, port, original, key = self.probed
            if host != config.host or port != config.port or not hmac.compare_digest(original, fingerprint):
                raise UserError('Отпечаток не совпадает с проверенным SSH-ключом.')
            pin_host(config, key, fingerprint)
            self.probed = None
            return {'pinned': True}
        if action == 'inspect':
            self.checked = None
            result = deploy.inspect(config, lambda _: None)
            self.checked = config_fingerprint(config)
            self.checked_until = time.monotonic() + TTL_SECONDS
            return result
        if action in ('publish', 'rollback'):
            if self.checked != config_fingerprint(config) or time.monotonic() > self.checked_until:
                raise UserError('Перед публикацией или откатом необходимо повторно проверить сервер.')
            self.checked = None
            if action == 'publish':
                return deploy.publish(config, lambda _: None)
            return deploy.rollback(config, lambda _: None)
        raise UserError('Неизвестная операция.')


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, host_port, secret: str):
        super().__init__(host_port, Handler)
        self.secret = secret
        self.engine = Engine()
        self.last_request = time.monotonic()


class Handler(BaseHTTPRequestHandler):
    server: LocalServer

    def log_message(self, *_args: Any) -> None:
        # Never emit URL, authorization headers or request content.
        pass

    def _answer(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if len(body) > MAX_RESPONSE_BYTES:
            status = 500
            body = b'{"ok":false,"error":"ResponseTooLarge"}'
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self) -> bool:
        # Do not allow CORS, or any service bound to a public interface.
        host = self.headers.get('Host', '')
        if host not in (f'127.0.0.1:{self.server.server_port}',):
            self._answer(403, {'ok': False, 'error': 'ForbiddenHost'})
            return False
        if self.headers.get('Origin') is not None:
            self._answer(403, {'ok': False, 'error': 'ForbiddenOrigin'})
            return False
        given = self.headers.get('Authorization', '')
        if not hmac.compare_digest(given, 'Bearer ' + self.server.secret):
            self._answer(401, {'ok': False, 'error': 'Unauthorized'})
            return False
        self.server.last_request = time.monotonic()
        return True

    def do_GET(self):
        if not self._auth():
            return
        if self.path == '/health':
            self._answer(200, {'ok': True, 'version': 1})
        else:
            self._answer(404, {'ok': False, 'error': 'NotFound'})

    def do_POST(self):
        if not self._auth():
            return
        if self.path not in ('/command', '/shutdown'):
            self._answer(404, {'ok': False, 'error': 'NotFound'})
            return
        if self.path == '/shutdown':
            self._answer(200, {'ok': True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        try:
            if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                raise UserError('Ожидается JSON-запрос.')
            size = int(self.headers.get('Content-Length', '0'))
            if size < 1 or size > MAX_REQUEST_BYTES:
                raise UserError('Некорректный размер запроса.')
            obj = json.loads(self.rfile.read(size))
            if not isinstance(obj, dict):
                raise UserError('Некорректный JSON-запрос.')
            if obj.get('action') not in ('probe', 'pin', 'inspect', 'publish', 'rollback'):
                raise UserError('Неизвестная операция.')
            if not self.server.engine.lock.acquire(blocking=False):
                raise UserError('Другая операция уже выполняется.')
            try:
                result = self.server.engine.execute(
                    obj['action'], obj.get('config'), obj.get('fingerprint'))
            finally:
                self.server.engine.lock.release()
            self._answer(200, {'ok': True, 'data': result})
        except UserError as exc:
            self._answer(400, {'ok': False, 'error': str(exc)[:500]})
        except (ValueError, TypeError, json.JSONDecodeError):
            self._answer(400, {'ok': False, 'error': 'Некорректный запрос.'})
        except Exception as exc:
            # No exception details: may include tokens, URLs or authentication info.
            self._answer(500, {'ok': False, 'error': type(exc).__name__})


def main():
    parser = argparse.ArgumentParser(description='Serviter localhost bridge')
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--secret', required=True)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or len(args.secret) < 48:
        parser.error('Invalid local bridge parameters')
    server = LocalServer(('127.0.0.1', args.port), args.secret)

    def stop_idle():
        while True:
            time.sleep(30)
            if time.monotonic() - server.last_request > 1200:
                server.shutdown()
                break
    threading.Thread(target=stop_idle, daemon=True).start()
    try:
        server.serve_forever(poll_interval=.2)
    finally:
        server.server_close()


if __name__ == '__main__':
    main()