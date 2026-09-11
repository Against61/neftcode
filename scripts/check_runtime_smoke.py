#!/usr/bin/env python3
"""Live generated-fixture smoke test for doctor, HTTP UI and worker runtime."""
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from neft.operator_console import demo_state
from test_history_adapter import fixture, ORIGIN


class IdCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name == 'id' and value:
                self.ids.add(value)


def request(url, path, body=None):
    data = None if body is None else json.dumps(body, allow_nan=False).encode()
    req = urllib.request.Request(
        url + path, data=data,
        headers={'Content-Type': 'application/json'} if data is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, response.read(), response.headers.get_content_type()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get_content_type()


def main():
    with tempfile.TemporaryDirectory(prefix='neft-runtime-smoke-') as name:
        root = Path(name)
        sources = root / 'history_sources.local.json'
        audit = root / 'audit'
        fixture(root)
        check = subprocess.run([
            sys.executable, str(ROOT / 'scripts/start_operator_console.py'),
            '--data-root', str(root), '--history-sources', str(sources),
            '--telemetry', 'ht.csv', '--lims', 'lims.xlsx',
            '--as-of', ORIGIN, '--check-only',
        ], cwd=ROOT, capture_output=True, text=True, timeout=30, check=False)
        if check.returncode:
            raise RuntimeError('CHECK_ONLY_FAILED: ' + check.stderr + check.stdout)
        doctor = json.loads(check.stdout)
        if doctor['status'] != 'passed' or not doctor['source_config_created']:
            raise RuntimeError('DOCTOR_CONTRACT_FAILED')

        process = subprocess.Popen([
            sys.executable, str(ROOT / 'scripts/serve_operator_console.py'),
            '--data-root', str(root), '--history-sources', str(sources),
            '--audit-root', str(audit), '--port', '0',
        ], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            url = process.stdout.readline().strip()
            if not url.startswith('http://127.0.0.1:'):
                raise RuntimeError('SERVER_START_FAILED: ' + url)
            deadline = time.monotonic() + 10
            while True:
                try:
                    status, raw, _ = request(url, '/healthz')
                    if status == 200:
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.05)
            health = json.loads(raw)
            if health['runtime'] != 'isolated_subprocess':
                raise RuntimeError('HEALTH_RUNTIME_MODE')

            status, html, content_type = request(url, '/')
            parser = IdCollector(); parser.feed(html.decode())
            required_ids = {'asof', 'load', 'history', 'groups', 'result'}
            if status != 200 or content_type != 'text/html' or not required_ids <= parser.ids:
                raise RuntimeError('UI_CONTRACT_FAILED')

            status, raw, _ = request(url, '/api/history', {'as_of': ORIGIN})
            history = json.loads(raw)
            if (status != 200 or history['decision']['status'] != 'ABSTAIN_DATA' or
                    history['selected_lims'] or history['runtime']['mode'] != 'isolated_subprocess'):
                raise RuntimeError('HISTORY_SMOKE_FAILED')

            status, raw, _ = request(url, '/api/evaluate', {
                'as_of': ORIGIN, 'scenario': demo_state(),
            })
            scenario = json.loads(raw)
            if (status != 200 or scenario['status'] != 'MODEL_PLAN' or
                    scenario['historical_recommendation'] is not False or
                    scenario['industrial_command'] is not False):
                raise RuntimeError('SCENARIO_SMOKE_FAILED')

            status, raw, _ = request(url, '/api/evaluate', {
                'as_of': ORIGIN, 'scenario': {**demo_state(), 'unexpected': 1},
            })
            rejected = json.loads(raw)
            if status != 400 or rejected['recommendation'] is not None:
                raise RuntimeError('REJECTION_SMOKE_FAILED')

            calls = sorted(path for path in audit.iterdir() if path.is_dir())
            if len(calls) != 3 or not all((path / 'manifest.json').is_file() for path in calls):
                raise RuntimeError('AUDIT_SMOKE_FAILED')
            result = {
                'status': 'passed',
                'doctor': doctor['status'],
                'source_config_created': True,
                'health_runtime': health['runtime'],
                'ui_ids_checked': sorted(required_ids),
                'history_status': history['decision']['status'],
                'scenario_status': scenario['status'],
                'rejection_http_status': status,
                'audited_calls': len(calls),
                'synthetic_fixture_only': True,
            }
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
            if process.returncode not in (0, -15):
                stderr = process.stderr.read()
                if stderr:
                    print(stderr, file=sys.stderr)


if __name__ == '__main__':
    main()
