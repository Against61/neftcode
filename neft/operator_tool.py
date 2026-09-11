"""Audited MCP tool for an explicit model scenario on configured history."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from .agent_tool import encoded, output_admissible, strict_loads, POLICY as TOOL_POLICY
from .history_adapter import bounds, sha
from .operator_console import MODEL_FIELDS, POLICY, contract

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = 'evaluate_refinery_scenario_with_history'


def description():
    schema = contract()['scenario_schema']
    manual_required = list(MODEL_FIELDS)
    bound_required = [name for name in MODEL_FIELDS if name != 'feed_t95']
    return {
        'name': SCENARIO,
        'description': ('Evaluate an explicit synthetic refinery model scenario while attaching a configured read-only historical snapshot. '
                        'This is never a historical recommendation or plant command. HT telemetry stays diagnostic; PAC stays excluded. '
                        'All scenario fields are explicit model assumptions except feed_t95, which may be omitted only when '
                        'use_historical_feed_t95=true and an eligible LIMS sample exists under the explicitly enabled sample+4h upper bound.'),
        'inputSchema': {'type': 'object', 'properties': {
            'as_of': {'type': 'string', 'description': 'ISO source-local time in allowed 2023–2024 history'},
            'use_lims_upper_bound': {'type': 'boolean', 'default': False},
            'use_historical_feed_t95': {'type': 'boolean', 'default': False},
            'scenario': {'type': 'object', 'properties': schema['properties'],
                         'additionalProperties': False}},
            'required': ['as_of', 'scenario'], 'additionalProperties': False,
            'anyOf': [
                {'required': ['use_historical_feed_t95', 'use_lims_upper_bound'],
                 'properties': {'use_historical_feed_t95': {'const': True},
                                'use_lims_upper_bound': {'const': True},
                                'scenario': {'required': bound_required}}},
                {'properties': {'use_historical_feed_t95': {'enum': [False]},
                                'scenario': {'required': manual_required}}}
            ]}}


class HistoryScenarioToolService:
    def __init__(self, audit_root, data_root, sources):
        self.audit_root = Path(audit_root).resolve(); self.audit_root.mkdir(parents=True, exist_ok=True)
        self.data_root = Path(data_root).resolve(); self.sources = Path(sources).resolve()

    def call(self, arguments):
        call_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:12]
        folder = self.audit_root / call_id; folder.mkdir(); started = time.monotonic()
        def save(name, value): (folder / name).write_bytes(encoded(value) + b'\n')
        result = {'tool': SCENARIO, 'call_id': call_id,
                  'scope': 'synthetic_model_with_historical_context',
                  'historical_recommendation': False, 'industrial_command': False,
                  'recommendation': None, 'audit_manifest': str(folder / 'manifest.json')}
        error = None; command = None
        try:
            raw = encoded(arguments)
            if len(raw) > TOOL_POLICY['max_request_bytes']: raise ValueError('REQUEST_TOO_LARGE')
            save('arguments.json', arguments)
            allowed = {'as_of', 'scenario', 'use_lims_upper_bound', 'use_historical_feed_t95'}
            if not isinstance(arguments, dict) or set(arguments) - allowed or 'scenario' not in arguments:
                raise ValueError('EVALUATE_ARGUMENTS_SCHEMA')
            bounds(arguments.get('as_of'))
            for flag in ('use_lims_upper_bound', 'use_historical_feed_t95'):
                if flag in arguments and type(arguments[flag]) is not bool:
                    raise ValueError(flag.upper() + '_MUST_BE_BOOLEAN')
            source_config = strict_loads(self.sources.read_text())
            save('sources.json', source_config)
            command = [sys.executable, '-I', '-B', str(ROOT / 'scripts/run_operator_scenario.py'),
                       '--data-root', str(self.data_root), '--sources', str(folder / 'sources.json'),
                       '--input', str(folder / 'arguments.json'), '--output', str(folder / 'result')]
            with (folder / 'worker.stdout.txt').open('w') as stdout, (folder / 'worker.stderr.txt').open('w') as stderr:
                worker = subprocess.run(command, cwd=ROOT, stdout=stdout, stderr=stderr,
                                        timeout=POLICY['timeout_seconds'])
            if worker.returncode:
                path = folder / 'result/error.json'
                reasons = strict_loads(path.read_text())['reasons'] if path.is_file() else ['WORKER_FAILED']
                raise ValueError('; '.join(reasons))
            package = strict_loads((folder / 'result/scenario.json').read_text())
            decision = package['decision']
            if (package.get('historical_recommendation') is not False or
                    package.get('industrial_command') is not False or
                    package.get('scope') != 'synthetic_model_with_historical_context' or
                    package['request'].get('scope') != 'synthetic_model' or
                    not output_admissible(decision)):
                raise ValueError('SCENARIO_OUTPUT_CONTRACT_VIOLATION')
            result.update(status=decision['status'], reasons=decision.get('reasons', []),
                          explanation=decision.get('explanation'), recommendation=decision['recommendation'],
                          scenario_id=package['scenario_id'], history_snapshot_id=package['history_snapshot_id'],
                          input_provenance=package['input_provenance'],
                          historical_context_used_for_model=package['historical_context_used_for_model'],
                          historical_context_diagnostic_only=package['historical_context_diagnostic_only'],
                          limitations=package['limitations'], request=package['request'],
                          artifacts={name: str(folder / 'result' / filename) for name, filename in {
                              'history_json': 'history.json', 'request_json': 'request.json',
                              'decision_json': 'decision.json', 'scenario_json': 'scenario.json',
                              'report_html': 'report.html'}.items()})
        except subprocess.TimeoutExpired:
            error = 'WORKER_TIMEOUT'
        except (ValueError, TypeError, OSError, KeyError, RecursionError) as exc:
            error = str(exc)
        if error:
            result.update(status='TOOL_ERROR', reasons=[error], recommendation=None)
        save('response.json', result)
        save('manifest.json', {'schema': POLICY['id'], 'call_id': call_id, 'tool': SCENARIO,
                               'status': 'tool_error' if error else 'completed',
                               'seconds': time.monotonic() - started, 'command': command,
                               'model_fits': 0, 'model_inference_calls': 0,
                               'artifacts_sha256': {str(p.relative_to(folder)): sha(p)
                                                    for p in sorted(folder.rglob('*')) if p.is_file()}})
        return {'is_error': bool(error), 'output': result}
