"""Join a read-only archive snapshot with an explicit synthetic model scenario.

The join is intentionally narrow: historical HT telemetry remains diagnostic,
PAC remains excluded, and only an eligible LIMS feed T95 may bind model state.
"""
from collections import OrderedDict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import threading
import time
import uuid

from .agent_tool import encoded, strict_loads
from .cycle_state import digest
from .decision_cycle import contracts, run_cycle
from .history_adapter import HistoryArchive, bounds, sha

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / 'configs/operator_console_v1.json').read_text())
MODEL, _, DYNAMIC, CYCLE_POLICY = contracts()
MODEL_FIELDS = tuple(POLICY['model_fields'])


def demo_state():
    """An explicit illustrative starting point; never described as archive data."""
    bridge = next(p for p in DYNAMIC['presets'] if p['id'] == 'bridge')
    return {**MODEL['defaults'], **DYNAMIC['defaults'], **bridge['overrides']}


def contract():
    field_schema = {}
    for name in MODEL_FIELDS:
        if name == 'data_ok':
            field_schema[name] = {'type': 'boolean', 'const': True}
        elif name in MODEL['bounds']:
            lo, hi = MODEL['bounds'][name]
            field_schema[name] = {'type': 'number', 'minimum': lo,
                                  'maximum': min(hi, 10) if name == 'sulfur_limit' else hi}
        else:
            field_schema[name] = {'type': 'number', 'minimum': 0, 'maximum': 200}
    field_schema['step']['enum'] = [15, 30, 60]
    field_schema['horizon']['exclusiveMinimum'] = 0
    return {
        'schema': POLICY['id'],
        'scenario_schema': {'type': 'object', 'properties': field_schema,
                            'required': list(MODEL_FIELDS), 'additionalProperties': False},
        'history_binding': POLICY['history_binding'],
        'demo_state_label': 'EXPLICIT_ILLUSTRATIVE_MODEL_ASSUMPTIONS_NOT_ARCHIVE_DATA',
        'demo_state': demo_state(),
        'demo_origins': POLICY['demo_origins'],
        'field_groups': POLICY['field_groups'], 'labels': POLICY['labels'],
        'claims': POLICY['claims'],
        'limits': [
            'Historical HT telemetry is diagnostic and never enters model state.',
            'PAC is excluded because timestamp availability and health are unknown.',
            'Only feed_t95 may come from eligible LIMS under an explicitly enabled sample+4h upper bound.',
            'Every other input is an explicit model assumption; the result is not historical or industrial advice.'
        ]}


def _validate_shape(scenario, use_historical_feed_t95):
    if not isinstance(scenario, dict):
        raise ValueError('SCENARIO_MUST_BE_OBJECT')
    expected = set(MODEL_FIELDS)
    if use_historical_feed_t95:
        expected.remove('feed_t95')
    actual = set(scenario)
    if actual != expected:
        missing = ','.join(sorted(expected - actual)) or '-'
        extra = ','.join(sorted(actual - expected)) or '-'
        raise ValueError('SCENARIO_FIELDS missing=' + missing + ' extra=' + extra)
    if scenario.get('data_ok') is not True:
        raise ValueError('DATA_OK_MUST_BE_EXPLICIT_TRUE')
    for key, value in scenario.items():
        if key == 'data_ok':
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('NONFINITE_SCENARIO_FIELD: ' + key)


def evaluate_snapshot(snapshot, scenario, *, use_historical_feed_t95=False):
    """Evaluate one complete model scenario while retaining source separation."""
    if type(use_historical_feed_t95) is not bool:
        raise ValueError('HISTORY_BINDING_FLAG_MUST_BE_BOOLEAN')
    _validate_shape(scenario, use_historical_feed_t95)
    observations = list(snapshot['request']['observations'])
    bindings = {}
    state = dict(scenario)
    if use_historical_feed_t95:
        if not snapshot['use_lims_upper_bound']:
            raise ValueError('LIMS_UPPER_BOUND_MUST_BE_ENABLED_FOR_HISTORY_BINDING')
        eligible = [row for row in observations if row.get('field') == 'feed_t95']
        if len(eligible) != 1:
            raise ValueError('HISTORICAL_FEED_T95_UNAVAILABLE_OR_AMBIGUOUS')
        bindings['feed_t95'] = 'feed_t95'
    request = {'schema': CYCLE_POLICY['id'], 'scope': 'synthetic_model',
               'origin': snapshot['as_of'], 'state': state,
               'observations': observations, 'bindings': bindings,
               'use_lims_upper_bound': snapshot['use_lims_upper_bound']}
    decision = run_cycle(request)
    provenance = {name: {'basis': 'explicit_model_assumption', 'origin': snapshot['as_of']}
                  for name in state}
    if use_historical_feed_t95:
        chosen = next(row for row in observations if row['field'] == 'feed_t95')
        selected = decision['trace'][0]['output']['provenance'].get('feed_t95') if decision.get('trace') else None
        provenance['feed_t95'] = {
            'basis': 'eligible_lims_sample_plus_4h_upper_bound',
            'event_time': chosen['event_time'], 'provenance': chosen['provenance'],
            'availability': selected.get('availability') if selected else None}
    package = {
        'schema': POLICY['id'], 'scope': 'synthetic_model_with_historical_context',
        'as_of': snapshot['as_of'],
        'history_snapshot_id': digest({'as_of': snapshot['as_of'],
                                       'source_config_sha256': snapshot['source_config_sha256'],
                                       'policy_sha256': snapshot['policy_sha256'],
                                       'use_lims_upper_bound': snapshot['use_lims_upper_bound']}),
        'request': request, 'decision': decision, 'input_provenance': provenance,
        'historical_context_used_for_model': ['feed_t95'] if use_historical_feed_t95 else [],
        'historical_context_diagnostic_only': {
            'telemetry_cutoff': snapshot['telemetry']['cutoff'],
            'telemetry_channels': len(snapshot['telemetry']['channels']),
            'selected_lims_fields': sorted(snapshot['selected_lims']),
            'pac': snapshot['pac']},
        'status': decision['status'], 'recommendation': decision['recommendation'],
        'historical_recommendation': False, 'industrial_command': False,
        'model_effect_validated_on_history': False, 'model_fits': 0,
        'model_inference_calls': 0,
        'limitations': list(snapshot['mapping_limits']) + [
            'MODEL_SCENARIO_CONDITIONED_ON_EXPLICIT_INPUTS',
            'HISTORICAL_CONTEXT_IS_NOT_ACTION_OUTCOME_EVIDENCE']}
    package['scenario_id'] = digest({'request': request,
                                     'history_snapshot_id': package['history_snapshot_id']})
    return package


class OperatorConsoleService:
    """In-process loopback service with bounded archive cache and durable audit."""
    def __init__(self, audit_root, data_root, sources, cache_size=4):
        self.audit_root = Path(audit_root).resolve()
        self.audit_root.mkdir(parents=True, exist_ok=True)
        self.data_root = Path(data_root).resolve()
        self.sources_path = Path(sources).resolve()
        self.source_config = strict_loads(self.sources_path.read_text())
        self.cache_size = cache_size
        self._cache = OrderedDict()
        self._lock = threading.RLock()

    def _archive(self, as_of):
        bounds(as_of)
        with self._lock:
            if as_of in self._cache:
                archive = self._cache.pop(as_of)
                self._cache[as_of] = archive
                archive.verify()
                return archive
            archive = HistoryArchive(self.data_root, self.source_config, [as_of])
            self._cache[as_of] = archive
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)
            return archive

    def history(self, as_of, *, use_lims_upper_bound=False):
        if type(use_lims_upper_bound) is not bool:
            raise ValueError('UPPER_BOUND_FLAG_MUST_BE_BOOLEAN')
        return self._archive(as_of).snapshot(as_of, use_lims_upper_bound=use_lims_upper_bound)

    def evaluate(self, as_of, scenario, *, use_lims_upper_bound=False,
                 use_historical_feed_t95=False):
        snapshot = self.history(as_of, use_lims_upper_bound=use_lims_upper_bound)
        return snapshot, evaluate_snapshot(snapshot, scenario,
                                           use_historical_feed_t95=use_historical_feed_t95)

    def audited_call(self, operation, arguments):
        call_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:10]
        folder = self.audit_root / call_id
        folder.mkdir()
        started = time.monotonic()
        def save(name, value):
            (folder / name).write_bytes(encoded(value) + b'\n')
        response, error = None, None
        try:
            if len(encoded(arguments)) > POLICY['max_request_bytes']:
                raise ValueError('REQUEST_TOO_LARGE')
            save('arguments.json', arguments)
            if not isinstance(arguments, dict):
                raise ValueError('ARGUMENTS_MUST_BE_OBJECT')
            if operation == 'history':
                if set(arguments) - {'as_of', 'use_lims_upper_bound'}:
                    raise ValueError('HISTORY_ARGUMENTS_SCHEMA')
                snapshot = self.history(arguments.get('as_of'),
                                        use_lims_upper_bound=arguments.get('use_lims_upper_bound', False))
                save('history.json', snapshot)
                response = snapshot
            elif operation == 'evaluate':
                allowed = {'as_of', 'scenario', 'use_lims_upper_bound', 'use_historical_feed_t95'}
                if set(arguments) - allowed or 'scenario' not in arguments:
                    raise ValueError('EVALUATE_ARGUMENTS_SCHEMA')
                snapshot, package = self.evaluate(
                    arguments.get('as_of'), arguments['scenario'],
                    use_lims_upper_bound=arguments.get('use_lims_upper_bound', False),
                    use_historical_feed_t95=arguments.get('use_historical_feed_t95', False))
                save('history.json', snapshot); save('request.json', package['request'])
                save('decision.json', package['decision']); save('scenario.json', package)
                response = package
            else:
                raise ValueError('UNKNOWN_OPERATION')
        except (ValueError, TypeError, KeyError, OSError, RecursionError) as exc:
            error = str(exc)
            response = {'schema': POLICY['id'], 'status': 'TOOL_ERROR',
                        'reasons': [error], 'recommendation': None,
                        'historical_recommendation': False, 'industrial_command': False}
        response = {**response, 'call_id': call_id,
                    'audit_manifest': str(folder / 'manifest.json')}
        save('response.json', response)
        manifest = {'schema': POLICY['id'], 'call_id': call_id, 'operation': operation,
                    'status': 'tool_error' if error else 'completed',
                    'seconds': time.monotonic() - started,
                    'source_config_sha256': digest(self.source_config),
                    'model_fits': 0, 'model_inference_calls': 0,
                    'artifacts_sha256': {str(p.relative_to(folder)): sha(p)
                                         for p in sorted(folder.iterdir()) if p.is_file()}}
        save('manifest.json', manifest)
        return {'is_error': bool(error), 'output': response}
