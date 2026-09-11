"""Versioned input selection for a synthetic decision cycle or read-only replay.

No file imports, inferred publication times or cross-property substitutions.
Unavailable observations never enter the model state or role messages as values.
"""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from .expert_contracts import publication_gate, SOURCE_SHA

UNITS = {'crude_sulfur': 'mass_percent', 'crude_flow': 't/h', 'feed_t95': 'degC',
         'feed_cn': 'cetane_number', 'clean_s': 'mg/kg', 'clean_t95': 'degC',
         'clean_cn': 'cetane_number', 'heavy_s': 'mg/kg', 'heavy_t95': 'degC',
         'heavy_cn': 'cetane_number', 'product_sulfur': 'mg/kg'}
BINDABLE = set(UNITS) - {'product_sulfur'}


def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def digest(x):
    return hashlib.sha256(json.dumps(x, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def local_time(x):
    if not isinstance(x, str):
        raise ValueError('Timestamp must be a string')
    dt = datetime.fromisoformat(x)
    if dt.tzinfo is not None:
        raise ValueError('Common source-local clock required; UTC offset unknown')
    return dt


@dataclass(frozen=True)
class StateAssessment:
    state: dict
    selected: dict
    provenance: dict
    excluded: list
    conflicts: list
    issues: list

    def summary(self):
        return {'selected': self.selected, 'provenance': self.provenance,
                'excluded': self.excluded, 'conflicts': self.conflicts,
                'issues': self.issues}


def select_state(request, policy):
    issues, excluded, eligible, provenance = [], [], {}, {}
    state = dict(request.get('state', {})) if isinstance(request.get('state'), dict) else {}
    origin = local_time(request.get('origin'))
    observations = request.get('observations', [])
    bindings = request.get('bindings', {})
    if not isinstance(observations, list) or len(observations) > 1000:
        raise ValueError('Observations must be a bounded list')
    if not isinstance(bindings, dict):
        raise ValueError('Bindings must be a mapping')
    ids = set()
    for index, obs in enumerate(observations):
        oid = obs.get('id') if isinstance(obs, dict) else None
        record = {'id': oid if isinstance(oid, str) else f'row:{index}'}
        try:
            if not isinstance(obs, dict) or not isinstance(oid, str) or not oid or oid in ids:
                raise ValueError('INVALID_OR_DUPLICATE_ID')
            ids.add(oid)
            kind, field = obs.get('source'), obs.get('field')
            if kind not in ('lims', 'pac', 'virtual') or field not in UNITS:
                raise ValueError('UNKNOWN_SOURCE_OR_FIELD')
            record.update(source=kind, field=field)
            if obs.get('unit') != UNITS[field]:
                raise ValueError('UNIT_MISMATCH')
            if not isinstance(obs.get('provenance'), str) or not obs['provenance']:
                raise ValueError('PROVENANCE_REQUIRED')
            event = local_time(obs.get('event_time'))
            age = (origin-event).total_seconds()/3600
            if age < 0:
                raise ValueError('FUTURE_EVENT')
            if age > policy['freshness_hours'][kind]:
                raise ValueError('STALE')
            if kind == 'pac' and (obs.get('event_time_basis') != 'confirmed_measurement'
                                  or obs.get('health') != 'healthy'):
                raise ValueError('PAC_TIME_OR_HEALTH_UNKNOWN_OR_BAD')
            if kind in ('lims', 'pac'):
                gate = publication_gate(kind, obs['event_time'], request['origin'],
                    published=obs.get('available_time'), received=obs.get('received_time'),
                    use_reported_upper_bound=request.get('use_lims_upper_bound') is True,
                    source_sha=policy['lims_upper_bound_source_sha256'])
            else:
                # Virtual records must attest dependency availability; no inference from event time.
                if obs.get('dependencies_available') is not True:
                    raise ValueError('VIRTUAL_DEPENDENCIES_UNAVAILABLE')
                available = local_time(obs.get('available_time'))
                receipt = local_time(obs['received_time']) if obs.get('received_time') else available
                if min(available, receipt) < event:
                    raise ValueError('AVAILABILITY_BEFORE_EVENT')
                eligible_time = max(available, receipt)
                gate = {'allowed': eligible_time <= origin,
                        'reason': 'ELIGIBLE' if eligible_time <= origin else 'NOT_YET_AVAILABLE',
                        'actual_publication': available.isoformat(),
                        'eligibility_time': eligible_time.isoformat(), 'basis': 'explicit_virtual_availability'}
            if not gate['allowed']:
                raise ValueError(gate['reason'])
            if not finite(obs.get('value')):
                raise ValueError('NONFINITE_VALUE')
            record.update(value=obs['value'], unit=obs['unit'], event_time=obs['event_time'],
                          age_hours=age, provenance=obs['provenance'], availability=gate)
            eligible.setdefault(field, []).append(record)
        except (ValueError, TypeError, KeyError) as exc:
            # Exclude the value itself from the feature/role path.
            excluded.append({**record, 'reason': str(exc)})
            if str(exc) == 'INVALID_OR_DUPLICATE_ID':
                issues.append('AMBIGUOUS_OBSERVATION_ID')
    selected, conflicts = {}, []
    for field, records in eligible.items():
        records.sort(key=lambda r: (('lims','pac','virtual').index(r['source']),
                                    r['age_hours'], r['id']))
        winner = records[0]
        selected[field] = winner
        others = [r['id'] for r in records[1:] if r['source'] != winner['source']
                  and abs(r['value']-winner['value']) > 1e-9]
        if others:
            conflicts.append({'field': field, 'selected_id': winner['id'], 'other_ids': others,
                              'reason': 'SOURCE_DISAGREEMENT_NOT_AUTOMATIC_SENSOR_FAILURE'})
    for field in state:
        provenance[field] = {'basis': 'manual_synthetic_input' if request['scope']=='synthetic_model'
                             else 'unmapped_replay_input', 'origin': request['origin']}
    for field, selected_field in bindings.items():
        if field not in BINDABLE or selected_field != field:
            issues.append('INVALID_BINDING_'+str(field))
        elif field not in selected:
            state.pop(field, None)
            provenance.pop(field, None)
            issues.append('REQUIRED_SOURCE_UNAVAILABLE_'+field)
        else:
            state[field] = selected[field]['value']
            provenance[field] = selected[field]
    return StateAssessment(state, selected, provenance, excluded, conflicts, issues)
