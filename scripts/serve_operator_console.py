#!/usr/bin/env python3
"""Serve the local operator console on loopback only."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from neft.operator_console import POLICY, contract
from neft.operator_runtime import IsolatedOperatorConsoleRuntime
from neft.operator_ui import render_console
from neft.agent_tool import strict_loads


class Handler(BaseHTTPRequestHandler):
    server_version = 'NeftOperatorConsole/1'

    def _headers(self, status, content_type, length):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(length))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.end_headers()

    def _send(self, status, value, content_type='application/json; charset=utf-8'):
        body = (value if isinstance(value, bytes) else
                json.dumps(value, ensure_ascii=False, allow_nan=False).encode())
        self._headers(status, content_type, len(body)); self.wfile.write(body)

    def _host_ok(self):
        raw = self.headers.get('Host', '').lower()
        if raw.startswith('['):
            host = raw[1:].split(']', 1)[0]
        else:
            host = raw.rsplit(':', 1)[0] if raw.count(':') == 1 else raw
        return host in {'127.0.0.1', 'localhost', '::1'}

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {'status': 'TOOL_ERROR', 'reasons': ['LOOPBACK_HOST_REQUIRED']})
        if self.path == '/':
            return self._send(200, render_console().encode(), 'text/html; charset=utf-8')
        if self.path == '/api/contract':
            return self._send(200, contract())
        if self.path == '/healthz':
            return self._send(200, self.server.service.health())
        self._send(404, {'status': 'TOOL_ERROR', 'reasons': ['NOT_FOUND']})

    def do_POST(self):
        if not self._host_ok():
            return self._send(403, {'status': 'TOOL_ERROR', 'reasons': ['LOOPBACK_HOST_REQUIRED']})
        if self.headers.get_content_type() != 'application/json':
            return self._send(415, {'status': 'TOOL_ERROR', 'reasons': ['JSON_REQUIRED']})
        try:
            self.connection.settimeout(POLICY['http_read_timeout_seconds'])
            length = int(self.headers.get('Content-Length', '-1'))
            if length < 0 or length > POLICY['max_request_bytes']:
                raise ValueError('REQUEST_SIZE')
            raw = self.rfile.read(length)
            body = strict_loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError, TimeoutError, OSError):
            return self._send(400, {'status': 'TOOL_ERROR', 'reasons': ['INVALID_JSON_OR_SIZE']})
        operation = {'/api/history': 'history', '/api/evaluate': 'evaluate'}.get(self.path)
        if operation is None:
            return self._send(404, {'status': 'TOOL_ERROR', 'reasons': ['NOT_FOUND']})
        result = self.server.service.audited_call(operation, body)
        self._send(400 if result['is_error'] else 200, result['output'])

    def log_message(self, fmt, *args):
        sys.stderr.write('%s - %s\n' % (self.address_string(), fmt % args))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data-root', type=Path, required=True)
    ap.add_argument('--history-sources', type=Path, required=True)
    ap.add_argument('--audit-root', type=Path, default=ROOT / 'operator-calls')
    ap.add_argument('--host', choices=['127.0.0.1', 'localhost', '::1'], default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8765)
    args = ap.parse_args()
    if not 0 <= args.port <= 65535:
        ap.error('port must be 0..65535')
    service = IsolatedOperatorConsoleRuntime(
        args.audit_root, args.data_root, args.history_sources)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.service = service
    print('http://%s:%d' % (args.host, server.server_port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
