#!/usr/bin/env python3
"""Serve the observe-only action-capture API on loopback."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from neft.action_capture import ActionCaptureStore, capture_contract
from neft.agent_tool import strict_loads


class Handler(BaseHTTPRequestHandler):
    server_version = "NeftActionCapture/1"

    def _send(self, status, value):
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self):
        raw = self.headers.get("Host", "").lower()
        if raw.startswith("["):
            host = raw[1:].split("]", 1)[0]
        else:
            host = raw.rsplit(":", 1)[0] if raw.count(":") == 1 else raw
        return host in {"127.0.0.1", "localhost", "::1"}

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"status": "rejected", "reason": "LOOPBACK_HOST_REQUIRED"})
        if self.path == "/healthz":
            return self._send(200, {"status": "ok", "observe_only": True,
                                    "industrial_command": False})
        if self.path == "/api/v1/contract":
            return self._send(200, capture_contract(self.server.capture_config))
        if self.path == "/api/v1/status":
            return self._send(200, self.server.store.status())
        return self._send(404, {"status": "rejected", "reason": "NOT_FOUND"})

    def do_POST(self):
        if not self._host_ok():
            return self._send(403, {"status": "rejected", "reason": "LOOPBACK_HOST_REQUIRED"})
        if self.headers.get_content_type() != "application/json":
            return self._send(415, {"status": "rejected", "reason": "JSON_REQUIRED"})
        try:
            self.connection.settimeout(self.server.http_read_timeout_seconds)
            length = int(self.headers.get("Content-Length", "-1"))
            if length < 0 or length > self.server.max_request_bytes:
                raise ValueError("REQUEST_SIZE")
            value = strict_loads(self.rfile.read(length).decode("utf-8"))
            event_type = {
                "/api/v1/commands/issued": "command_issued",
                "/api/v1/commands/terminal": "command_terminal",
                "/api/v1/quality-samples": "quality_sample",
            }.get(self.path)
            if event_type is None:
                return self._send(404, {"status": "rejected", "reason": "NOT_FOUND"})
            result = self.server.store.record(event_type, value)
            return self._send(200, result)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError, OSError) as exc:
            reason = str(exc)
            status = 409 if "CONFLICT" in reason or "ALREADY_RECORDED" in reason else 400
            return self._send(status, {"status": "rejected", "reason": reason})

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def make_server(address, store, capture_config):
    server = ThreadingHTTPServer(address, Handler)
    server.daemon_threads = True
    server.store = store
    server.capture_config = capture_config
    server.max_request_bytes = capture_config["max_request_bytes"]
    server.http_read_timeout_seconds = capture_config["http_read_timeout_seconds"]
    return server


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--capture-config", type=Path,
                        default=ROOT / "configs/action_capture_v1.json")
    parser.add_argument("--action-config", type=Path,
                        default=ROOT / "configs/action_outcome_v1.json")
    parser.add_argument("--collection-config", type=Path,
                        default=ROOT / "configs/action_collection_v1.json")
    parser.add_argument("--host", choices=["127.0.0.1", "localhost", "::1"],
                        default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be 0..65535")
    capture_config = load(args.capture_config)
    store = ActionCaptureStore(args.store, capture_config, load(args.action_config),
                               load(args.collection_config))
    server = make_server((args.host, args.port), store, capture_config)
    print("http://%s:%d" % (args.host, server.server_port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
