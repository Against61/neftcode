"""Bounded subprocess runtime for the loopback operator console.

The HTTP server never executes archive parsing or planning in its request
threads.  Each admitted call gets a fresh worker, a hard timeout and a durable
audit directory.  Source paths and policy remain process-owned.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from .agent_tool import encoded, output_admissible, strict_loads
from .cycle_state import digest
from .history_adapter import sha
from .operator_console import POLICY

ROOT = Path(__file__).resolve().parents[1]


class IsolatedOperatorConsoleRuntime:
    """Run HTTP operations in bounded child processes with per-call audits."""

    def __init__(self, audit_root, data_root, sources):
        self.audit_root = Path(audit_root).resolve()
        self.audit_root.mkdir(parents=True, exist_ok=True)
        self.data_root = Path(data_root).resolve()
        self.sources = Path(sources).resolve()
        self.source_config = strict_loads(self.sources.read_text())
        self.source_config_sha256 = digest(self.source_config)
        self.max_concurrent_requests = int(POLICY['max_concurrent_requests'])
        self.timeout_seconds = float(POLICY['timeout_seconds'])
        self.http_read_timeout_seconds = float(POLICY['http_read_timeout_seconds'])
        if self.max_concurrent_requests < 1 or self.timeout_seconds <= 0:
            raise ValueError('INVALID_RUNTIME_POLICY')
        self._slots = threading.BoundedSemaphore(self.max_concurrent_requests)

    def health(self):
        return {
            'status': 'ok',
            'schema': POLICY['id'],
            'runtime': 'isolated_subprocess',
            'timeout_seconds': self.timeout_seconds,
            'http_read_timeout_seconds': self.http_read_timeout_seconds,
            'max_concurrent_requests': self.max_concurrent_requests,
            'source_config_sha256': self.source_config_sha256,
        }

    @staticmethod
    def _validate_output(operation, output):
        if not isinstance(output, dict):
            raise ValueError('WORKER_OUTPUT_MUST_BE_OBJECT')
        if operation == 'history':
            decision = output.get('decision') or {}
            if (output.get('scope') != 'historical_replay' or
                    (output.get('request') or {}).get('scope') != 'historical_replay' or
                    decision.get('recommendation') is not None or
                    decision.get('industrial_command') is not False):
                raise ValueError('HISTORY_OUTPUT_SCOPE_VIOLATION')
        elif operation == 'evaluate':
            decision = output.get('decision') or {}
            if (output.get('scope') != 'synthetic_model_with_historical_context' or
                    output.get('historical_recommendation') is not False or
                    output.get('industrial_command') is not False or
                    (output.get('request') or {}).get('scope') != 'synthetic_model' or
                    not output_admissible(decision)):
                raise ValueError('SCENARIO_OUTPUT_CONTRACT_VIOLATION')
        else:
            raise ValueError('UNKNOWN_OPERATION')

    def audited_call(self, operation, arguments):
        call_id = (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') +
                   '-' + uuid.uuid4().hex[:10])
        folder = self.audit_root / call_id
        folder.mkdir()
        started = time.monotonic()

        def save(name, value):
            (folder / name).write_bytes(encoded(value) + b'\n')

        error = None
        command = None
        acquired = False
        output = None
        try:
            raw = encoded(arguments)
            if len(raw) > POLICY['max_request_bytes']:
                raise ValueError('REQUEST_TOO_LARGE')
            if not isinstance(arguments, dict):
                raise ValueError('ARGUMENTS_MUST_BE_OBJECT')
            if operation not in {'history', 'evaluate'}:
                raise ValueError('UNKNOWN_OPERATION')
            save('arguments.json', arguments)
            # Freeze the operator-owned mapping for this call.  The worker never
            # accepts a source path from the HTTP request.
            save('sources.json', self.source_config)
            acquired = self._slots.acquire(blocking=False)
            if not acquired:
                raise ValueError('RUNTIME_BUSY')
            command = [
                sys.executable, '-I', '-B', str(ROOT / 'scripts/run_operator_call.py'),
                '--operation', operation,
                '--data-root', str(self.data_root),
                '--sources', str(folder / 'sources.json'),
                '--input', str(folder / 'arguments.json'),
                '--output', str(folder / 'worker-result'),
            ]
            with (folder / 'worker.stdout.txt').open('w') as stdout, \
                    (folder / 'worker.stderr.txt').open('w') as stderr:
                worker = subprocess.run(
                    command, cwd=ROOT, stdout=stdout, stderr=stderr,
                    timeout=self.timeout_seconds, check=False)
            if worker.returncode:
                path = folder / 'worker-result/error.json'
                reasons = strict_loads(path.read_text()).get('reasons', []) if path.is_file() else []
                raise ValueError('; '.join(reasons) if reasons else 'WORKER_FAILED')
            output = strict_loads((folder / 'worker-result/output.json').read_text())
            self._validate_output(operation, output)
        except subprocess.TimeoutExpired:
            error = 'WORKER_TIMEOUT'
        except (ValueError, TypeError, OSError, KeyError, RecursionError, json.JSONDecodeError) as exc:
            error = str(exc)
        finally:
            if acquired:
                self._slots.release()

        if error:
            output = {
                'schema': POLICY['id'], 'status': 'TOOL_ERROR',
                'reasons': [error], 'recommendation': None,
                'historical_recommendation': False, 'industrial_command': False,
            }
        output = {
            **output,
            'call_id': call_id,
            'audit_manifest': str(folder / 'manifest.json'),
            'runtime': {
                'mode': 'isolated_subprocess',
                'timeout_seconds': self.timeout_seconds,
                'max_concurrent_requests': self.max_concurrent_requests,
            },
        }
        save('response.json', output)
        manifest = {
            'schema': POLICY['id'], 'call_id': call_id, 'operation': operation,
            'status': 'tool_error' if error else 'completed',
            'error': error,
            'started_at': datetime.now(timezone.utc).isoformat(),
            'seconds': time.monotonic() - started,
            'command': command,
            'runtime': output['runtime'],
            'source_config_sha256': self.source_config_sha256,
            'model_fits': 0, 'model_inference_calls': 0,
            'artifacts_sha256': {
                str(path.relative_to(folder)): sha(path)
                for path in sorted(folder.rglob('*'))
                if path.is_file() and path.name != 'manifest.json'
            },
        }
        save('manifest.json', manifest)
        return {'is_error': bool(error), 'output': output}
