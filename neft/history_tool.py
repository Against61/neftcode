"""Optional MCP history tool; archive paths belong to server configuration."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid
from .agent_tool import encoded, strict_loads, POLICY as TOOL_POLICY
from .history_adapter import POLICY, bounds, sha

ROOT = Path(__file__).resolve().parents[1]
HISTORY = 'get_refinery_history'


def description():
    return {'name': HISTORY,
            'description': 'Read the configured local historical archive at as_of. Returns eligible laboratory observations, archive diagnostics, missing model inputs and the read-only Python-cycle refusal. Do not turn diagnostic telemetry into online features, change scope to synthetic_model, invent missing inputs or issue plant commands. Source paths and SHA are configured by the server. LIMS sample+4h is an opt-in conservative bound, never observed publication.',
            'inputSchema': {'type': 'object', 'properties': {
                'as_of': {'type': 'string', 'description': 'ISO source-local time in allowed 2023–2024 history'},
                'use_lims_upper_bound': {'type': 'boolean', 'default': False},
                'scenario_parameters': {'type': 'object', 'additionalProperties': False,
                                        'properties': {k: {'type': 'number'} for k in POLICY['scenario_fields']}}},
                'required': ['as_of'], 'additionalProperties': False}}


class HistoryToolService:
    def __init__(self, audit_root, data_root, sources):
        self.audit_root = Path(audit_root).resolve()
        self.audit_root.mkdir(parents=True, exist_ok=True)
        self.data_root = Path(data_root).resolve()
        self.sources = Path(sources).resolve()

    def call(self, arguments):
        call_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:12]
        folder = self.audit_root / call_id
        folder.mkdir()
        start = time.monotonic()
        def save(name, obj): (folder / name).write_bytes(encoded(obj) + b'\n')
        result = {'tool': HISTORY, 'call_id': call_id, 'scope': 'historical_replay',
                  'industrial_command': False, 'recommendation': None,
                  'audit_manifest': str(folder / 'manifest.json')}
        error = None
        command = None
        try:
            raw = encoded(arguments)
            if len(raw) > TOOL_POLICY['max_request_bytes']: raise ValueError('REQUEST_TOO_LARGE')
            save('arguments.json', arguments)
            if not isinstance(arguments, dict) or set(arguments) - {'as_of', 'use_lims_upper_bound', 'scenario_parameters'}:
                raise ValueError('HISTORY_ARGUMENTS_SCHEMA')
            bounds(arguments.get('as_of'))
            if type(arguments.get('use_lims_upper_bound', False)) is not bool:
                raise ValueError('UPPER_BOUND_FLAG_MUST_BE_BOOLEAN')
            params = arguments.get('scenario_parameters', {})
            if not isinstance(params, dict) or set(params) - set(POLICY['scenario_fields']):
                raise ValueError('UNSUPPORTED_SCENARIO_FIELDS')
            save('scenario.json', params)
            # Freeze the operator-owned source configuration for this call.
            source_config = strict_loads(self.sources.read_text())
            save('sources.json', source_config)
            command = [sys.executable, '-I', '-B', str(ROOT / 'scripts/run_history_adapter.py'),
                       '--data-root', str(self.data_root), '--sources', str(folder / 'sources.json'),
                       '--as-of', arguments['as_of'], '--scenario', str(folder / 'scenario.json'),
                       '--output', str(folder / 'result')]
            if arguments.get('use_lims_upper_bound'): command.append('--use-lims-upper-bound')
            with (folder / 'worker.stdout.txt').open('w') as stdout, (folder / 'worker.stderr.txt').open('w') as stderr:
                worker = subprocess.run(command, cwd=ROOT, stdout=stdout, stderr=stderr, timeout=TOOL_POLICY['timeout_seconds'])
            if worker.returncode:
                path = folder / 'result/error.json'
                detail = strict_loads(path.read_text())['reasons'] if path.is_file() else ['WORKER_FAILED']
                raise ValueError('; '.join(detail))
            snapshot = strict_loads((folder / 'result/history.json').read_text())
            decision = snapshot['decision']
            if decision['recommendation'] is not None or snapshot['request']['scope'] != 'historical_replay':
                raise ValueError('HISTORY_OUTPUT_SCOPE_VIOLATION')
            result.update(status=decision['status'], reasons=decision['reasons'], as_of=snapshot['as_of'],
                          request=snapshot['request'], selected_lims=snapshot['selected_lims'],
                          lims_series_status=snapshot['lims_series_status'],
                          missing_model_fields=snapshot['missing_model_fields'], mapping_limits=snapshot['mapping_limits'],
                          pac=snapshot['pac'], telemetry_context={
                              'status': 'ARCHIVE_DIAGNOSTIC_ONLY_NOT_ONLINE_FEATURES',
                              'cutoff': snapshot['telemetry']['cutoff'],
                              'online_eligible': False,
                              'channel_count': len(snapshot['telemetry']['channels']),
                              'flagged_channels': {r['tag']: r['flags'] for r in snapshot['telemetry']['channels'] if r['flags']}},
                          artifacts={k: str(folder / 'result' / v) for k, v in
                                     [('history_json', 'history.json'), ('request_json', 'request.json'),
                                      ('decision_json', 'decision.json'), ('report_html', 'report.html')]})
        except subprocess.TimeoutExpired:
            error = 'WORKER_TIMEOUT'
        except (ValueError, TypeError, OSError, KeyError, RecursionError) as exc:
            error = str(exc)
        if error:
            result.update(status='TOOL_ERROR', reasons=[error], recommendation=None)
        save('response.json', result)
        save('manifest.json', {'call_id': call_id, 'tool': HISTORY, 'status': 'tool_error' if error else 'completed',
                               'seconds': time.monotonic() - start, 'command': command,
                               'artifacts_sha256': {str(p.relative_to(folder)): sha(p) for p in sorted(folder.rglob('*')) if p.is_file()}})
        return {'is_error': bool(error), 'output': result}
