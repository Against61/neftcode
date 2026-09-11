"""Bounded read-only archive adapter. No guessed online telemetry or model inputs."""
from collections import Counter
import csv
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree as ET

from .cycle_state import digest, local_time
from .decision_cycle import contracts, run_cycle
from .expert_contracts import publication_gate

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / 'configs/history_adapter_v1.json').read_text())
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def number(raw):
    if raw is None or isinstance(raw, bool):
        return None
    try:
        result = float(str(raw).strip().replace(',', '.'))
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def bounds(as_of):
    at = local_time(as_of)
    cutoff = at - timedelta(minutes=POLICY['telemetry_inspection_offset_minutes'])
    left = cutoff - timedelta(hours=POLICY['lookback_hours'])
    if not (local_time(POLICY['allowed_start']) <= left < at < local_time(POLICY['allowed_end_exclusive'])):
        raise ValueError('OUTSIDE_ALLOWED_HISTORY')
    return at, left, cutoff


def checked_source(root, spec):
    if not isinstance(spec, dict) or set(spec) != {'path', 'sha256'}:
        raise ValueError('SOURCE_PATH_AND_SHA_REQUIRED')
    if not re.fullmatch(r'[a-f0-9]{64}', str(spec['sha256'])):
        raise ValueError('SOURCE_SHA_REQUIRED')
    relative = Path(spec['path'])
    path = (root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root):
        raise ValueError('SOURCE_OUTSIDE_DATA_ROOT')
    if not path.is_file() or sha(path) != spec['sha256']:
        raise ValueError('SOURCE_MISSING_OR_HASH_MISMATCH: ' + relative.name)
    return path


def read_telemetry(path, origins):
    windows = [bounds(t)[1:] for t in origins]
    maximum = max(right for _, right in windows)
    result, previous, scanned, stop = [], None, 0, None
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.reader(stream)
        header = [s.strip() for s in next(reader)]
        if len(header) != len(set(header)) or 'date' not in header:
            raise ValueError('INVALID_CSV_HEADER')
        columns = {tag: header.index(tag) for tag in POLICY['telemetry_channels'] if tag in header}
        datecol = header.index('date')
        for row in reader:
            scanned += 1
            if len(row) != len(header):
                raise ValueError('MALFORMED_CSV_ROW')
            at = local_time(row[datecol])
            if previous is not None and at <= previous:
                raise ValueError('DUPLICATE_OR_UNORDERED_CSV_TIME')
            if at > maximum:
                stop = at.isoformat()
                break
            previous = at
            if any(left <= at <= right for left, right in windows):
                result.append({'at': at, 'row': reader.line_num,
                               'values': {tag: number(row[col]) for tag, col in columns.items()}})
    return result, {'columns_1_based': {k: v + 1 for k, v in columns.items()},
                    'timestamp_rows_scanned': scanned, 'numeric_rows_selected': len(result),
                    'numeric_values_selected': len(result) * len(columns),
                    'maximum_cutoff': maximum.isoformat(), 'stopping_timestamp_only': stop,
                    'later_numeric_values_parsed': False}


def cell_text(cell, strings):
    if cell is None:
        return None
    if cell.find('s:f', NS) is not None:
        raise ValueError('FORMULA_CELL_NOT_SUPPORTED')
    kind = cell.get('t')
    if kind == 'inlineStr':
        return ''.join(cell.itertext())
    value = cell.findtext('s:v', namespaces=NS)
    if kind == 's':
        return strings[int(value)] if value is not None else None
    if kind in ('b', 'e'):
        return None
    return value


def excel_time(cell, strings, epoch1904):
    raw = cell_text(cell, strings)
    if raw is None or raw == '':
        return None
    if cell.get('t') in (None, 'n'):
        serial = number(raw)
        if serial is None or not 0 <= serial < 1000000:
            raise ValueError('INVALID_EXCEL_DATE')
        # Excel serial dates are typed date-column cells, not guessed numeric epochs.
        base = datetime(1904, 1, 1) if epoch1904 else datetime(1899, 12, 30)
        day, fraction = divmod(serial, 1)
        if not epoch1904 and 0 < serial < 60: day += 1
        return base + timedelta(days=day, milliseconds=round(fraction * 86400000))
    return local_time(raw)


def read_lims(path, origins):
    """Read registered column pairs without decoding out-of-window numeric values.

    XML is tokenized as text. Each pair's date is checked before its value is
    converted; pairs are independent. Requires ascending dates within each pair.
    """
    windows = [(bounds(t)[0] - timedelta(hours=POLICY['lims_max_sample_age_hours']), bounds(t)[0])
               for t in origins]
    maximum = max(r for _, r in windows)
    series = POLICY['lims_series']
    records = {s['id']: [] for s in series}
    previous, done, seen_headers = {}, set(), set()
    audit = {'pairs_numeric_parsed': 0, 'date_cells_examined': 0, 'headers_verified': False,
             'future_values_parsed': False, 'pair_boundaries': {}, 'invalid_dates': []}
    with zipfile.ZipFile(path) as book:
        workbook = ET.fromstring(book.read('xl/workbook.xml'))
        props = workbook.find('s:workbookPr', NS)
        epoch1904 = props is not None and props.get('date1904') in ('1', 'true')
        sheets = [s for s in workbook.findall('s:sheets/s:sheet', NS) if s.get('name') == POLICY['lims_sheet']]
        if len(sheets) != 1:
            raise ValueError('LIMS_SHEET_MISMATCH')
        relationships = ET.fromstring(book.read('xl/_rels/workbook.xml.rels'))
        rels = {r.get('Id'): r for r in relationships}
        relation = rels[sheets[0].get('{' + REL + '}id')]
        if relation.get('TargetMode') == 'External':
            raise ValueError('EXTERNAL_WORKBOOK_RELATIONSHIP')
        target = relation.get('Target')
        target = target.lstrip('/') if target.startswith('/') else 'xl/' + target
        strings = []
        if 'xl/sharedStrings.xml' in book.namelist():
            shared = ET.fromstring(book.read('xl/sharedStrings.xml'))
            strings = [''.join(t.itertext()) for t in shared]
        with book.open(target) as stream:
            for _, row in ET.iterparse(stream, events=('end',)):
                if row.tag != '{' + NS['s'] + '}row':
                    continue
                row_id = int(row.get('r'))
                cells = {re.sub(r'\d+$', '', c.get('r')): c for c in row.findall('s:c', NS)}
                if row_id in (1, 2, 3):
                    for s in series:
                        if row_id == 1:
                            col = 'BO' if s['point'] == '1' else 'CE'
                            text = cell_text(cells.get(col), strings) or ''
                            if 'Гидроочистка' not in text or "Точка отбора '" + s['point'] + "'" not in text:
                                raise ValueError('LIMS_GROUP_HEADER_MISMATCH')
                        else:
                            expected = s['indicator'] if row_id == 2 else s['header_unit']
                            if cell_text(cells.get(s['date_column']), strings) != expected:
                                raise ValueError('LIMS_INDICATOR_OR_UNIT_MISMATCH: ' + s['id'])
                    seen_headers.add(row_id)
                elif row_id >= 5:
                    if seen_headers != {1, 2, 3}:
                        raise ValueError('LIMS_HEADERS_MISSING')
                    for s in series:
                        sid = s['id']
                        if sid in done:
                            continue
                        audit['date_cells_examined'] += 1
                        cell = cells.get(s['date_column'])
                        try:
                            at = excel_time(cell, strings, epoch1904)
                        except (ValueError, OverflowError, TypeError):
                            at = None
                        if at is None:
                            if cell_text(cell, strings) not in (None, '') or cell_text(cells.get(s['value_column']), strings) not in (None, ''):
                                raise ValueError('INVALID_LIMS_DATE: ' + sid + ':' + str(row_id))
                            continue
                        if sid in previous and at < previous[sid]:
                            raise ValueError('UNORDERED_LIMS_PAIR: ' + sid)
                        previous[sid] = at
                        if at > maximum:
                            done.add(sid)
                            audit['pair_boundaries'][sid] = at.isoformat()
                            continue
                        if not any(left <= at <= right for left, right in windows):
                            continue
                        try:
                            value = number(cell_text(cells.get(s['value_column']), strings))
                        except ValueError:
                            value = None
                        audit['pairs_numeric_parsed'] += 1
                        records[sid].append({'at': at, 'raw_numeric': value, 'row': row_id})
                row.clear()
                if len(done) == len(series):
                    break
    if seen_headers != {1, 2, 3}:
        raise ValueError('LIMS_HEADERS_MISSING')
    audit['headers_verified'] = True
    return records, audit


def telemetry_snapshot(rows, as_of, audit, source):
    at, left, cutoff = bounds(as_of)
    period = timedelta(minutes=POLICY['telemetry_cadence_minutes'])
    start = left.replace(second=0, microsecond=0)
    if start < left:
        start += timedelta(minutes=1)
    if start.minute % 10:
        start += timedelta(minutes=10 - start.minute % 10)
    grid = []
    while start <= cutoff:
        grid.append(start)
        start += period
    past = [r for r in rows if left <= r['at'] <= cutoff]
    indexed = {r['at']: r for r in past}
    observed = [r for r in past if r['at'] in set(grid)]
    result = []
    for tag in POLICY['telemetry_channels']:
        missing = tag not in audit['columns_1_based']
        latest = observed[-1] if observed and not missing else None
        finite = [indexed[t] for t in grid if t in indexed and indexed[t]['values'].get(tag) is not None]
        last = finite[-1] if finite else None
        age = (cutoff - last['at']).total_seconds() / 60 if last else None
        flags = []
        if missing: flags.append('MISSING_CHANNEL')
        if latest and latest['values'].get(tag) is None: flags.append('LATEST_NONFINITE')
        if last is None: flags.append('NO_FINITE_OBSERVATION')
        elif age > POLICY['telemetry_freshness_minutes_at_cutoff']: flags.append('STALE_LAST_FINITE')
        if len(finite) / len(grid) < POLICY['telemetry_min_finite_fraction']: flags.append('LOW_FINITE_COVERAGE')
        run_start, old_value, previous, long_runs = None, None, None, []
        def close_run():
            if run_start is not None and (previous - run_start).total_seconds() >= POLICY['constant_review_hours'] * 3600:
                long_runs.append({'start': run_start.isoformat(), 'end': previous.isoformat()})
        for t in grid:
            value = indexed[t]['values'].get(tag) if t in indexed else None
            if value is None or value != old_value:
                close_run()
                run_start = t if value is not None else None
            old_value, previous = value, t
        close_run()
        if long_runs: flags.append('CONSTANT_HISTORY_REVIEW')
        result.append({'tag': 'ht:' + tag, 'context': POLICY['controls_context'].get(tag),
                       'archive_value': latest['values'].get(tag) if latest else None,
                       'event_time': latest['at'].isoformat() if latest else None,
                       'source_row': latest['row'] if latest else None,
                       'source_column_1_based': audit['columns_1_based'].get(tag),
                       'latest_finite_value': last['values'][tag] if last else None,
                       'latest_finite_time': last['at'].isoformat() if last else None,
                       'age_at_cutoff_minutes': age, 'finite_grid_points': len(finite),
                       'expected_grid_points': len(grid), 'constant_runs': long_runs, 'flags': flags,
                       'unit': None, 'unit_status': 'UNCONFIRMED_NO_CONVERSION',
                       'available_time': None, 'online_eligible': False,
                       'availability_reason': 'TELEMETRY_AVAILABILITY_UNKNOWN',
                       'source': source})
    return {'cutoff': cutoff.isoformat(), 'lookback_start': left.isoformat(),
            'offset_basis': POLICY['telemetry_offset_basis'],
            'off_grid_rows': len(past) - len(observed), 'channels': result}


def select_lims(records, as_of, use_upper, source):
    at = bounds(as_of)[0]
    selected, audits = {}, []
    for spec in POLICY['lims_series']:
        past = [r for r in records[spec['id']] if at - timedelta(hours=24) <= r['at'] <= at]
        counts = Counter(r['at'] for r in past)
        eligible = []
        for row in past:
            gate = publication_gate('lims', row['at'].isoformat(), at.isoformat(),
                                    use_reported_upper_bound=use_upper, source_sha=POLICY['expert_source_sha256'])
            item = {'series_id': spec['id'], 'field': spec['field'], 'event_time': row['at'].isoformat(),
                    'source': source, 'sheet': POLICY['lims_sheet'],
                    'date_cell': spec['date_column'] + str(row['row']),
                    'value_cell': spec['value_column'] + str(row['row']),
                    'row': row['row'], 'availability': gate, 'unit': spec['unit'],
                    'conversion': {'source_unit': spec['source_unit'], 'factor': spec['factor'],
                                   'basis': 'registered_indicator_and_QA1_units'},
                    'value': None, 'selected': False, 'reasons': []}
            if not gate['allowed']: item['reasons'].append(gate['reason'])
            if counts[row['at']] > 1: item['reasons'].append('DUPLICATE_SAMPLE_TIME')
            if row['raw_numeric'] is None: item['reasons'].append('NONFINITE_OR_MISSING_VALUE')
            # Only available, unambiguous numbers may leave the selection layer.
            if not item['reasons']:
                value = row['raw_numeric'] * spec['factor']
                if math.isfinite(value): item['value'] = value
                else: item['reasons'].append('NONFINITE_CONVERSION')
            audits.append(item)
            if gate['allowed']: eligible.append(item)
        if eligible:
            newest = max(i['event_time'] for i in eligible)
            latest = [i for i in eligible if i['event_time'] == newest]
            # A bad/duplicate latest available sample must not silently carry an older value forward.
            if len(latest) == 1 and not latest[0]['reasons']:
                latest[0]['selected'] = True
                selected[spec['field']] = latest[0]
    return selected, audits


class HistoryArchive:
    def __init__(self, data_root, source_config, origins):
        self.root = Path(data_root).resolve()
        self.origins = tuple(origins)
        if not self.origins or len(self.origins) > 20:
            raise ValueError('ONE_TO_TWENTY_ORIGINS_REQUIRED')
        for origin in self.origins: bounds(origin)
        if not isinstance(source_config, dict) or set(source_config) != {'schema', 'telemetry', 'lims'} or source_config['schema'] != 'history-sources-v1':
            raise ValueError('INVALID_SOURCE_CONFIG')
        self.sources = json.loads(json.dumps(source_config))
        self.paths = {key: checked_source(self.root, source_config[key]) for key in ('telemetry', 'lims')}
        self.rows, self.telemetry_audit = read_telemetry(self.paths['telemetry'], self.origins)
        self.labs, self.lims_audit = read_lims(self.paths['lims'], self.origins)
        self.verify()

    def verify(self):
        for key, path in self.paths.items():
            if sha(path) != self.sources[key]['sha256']:
                raise ValueError('SOURCE_CHANGED_DURING_READ')

    def snapshot(self, as_of, *, use_lims_upper_bound=False, scenario_parameters=None):
        if as_of not in self.origins: raise ValueError('ORIGIN_NOT_LOADED')
        if type(use_lims_upper_bound) is not bool: raise ValueError('UPPER_BOUND_FLAG_MUST_BE_BOOLEAN')
        params = {} if scenario_parameters is None else scenario_parameters
        if not isinstance(params, dict) or set(params) - set(POLICY['scenario_fields']):
            raise ValueError('UNSUPPORTED_SCENARIO_FIELDS')
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in params.values()):
            raise ValueError('NONFINITE_SCENARIO_PARAMETER')
        model = contracts()[0]
        for key, value in params.items():
            lo, hi = model['bounds'].get(key, (0, 200))
            if not lo <= value <= hi or (key == 'sulfur_limit' and value > 10):
                raise ValueError('INVALID_SCENARIO_PARAMETER: ' + key)
        if 'step' in params and params['step'] not in (15, 30, 60):
            raise ValueError('INVALID_SCENARIO_TIME_GRID')
        if 'horizon' in params and params['horizon'] <= 0:
            raise ValueError('INVALID_SCENARIO_TIME_GRID')
        if 'step' in params and 'horizon' in params:
            periods = params['horizon'] * 60 / params['step']
            if abs(periods - round(periods)) > 1e-9:
                raise ValueError('INVALID_SCENARIO_TIME_GRID')
        selected, log = select_lims(self.labs, as_of, use_lims_upper_bound, self.sources['lims'])
        series_status = []
        for spec in POLICY['lims_series']:
            item = selected.get(spec['field'])
            related = [r for r in log if r['series_id'] == spec['id']]
            series_status.append({'field': spec['field'], 'series_id': spec['id'], 'unit': spec['unit'],
                                  'status': 'AVAILABLE' if item else 'UNAVAILABLE',
                                  'reasons': [] if item else sorted({reason for r in related for reason in r['reasons']}) or ['NO_SAMPLE_IN_FRESHNESS_WINDOW'],
                                  'value': item['value'] if item else None,
                                  'event_time': item['event_time'] if item else None})
        observations = []
        for spec in POLICY['lims_series']:
            item = selected.get(spec['field'])
            if item and spec['cycle_field']:
                observations.append({'id': spec['id'] + ':' + item['date_cell'], 'source': 'lims',
                                     'field': spec['cycle_field'], 'value': item['value'], 'unit': spec['unit'],
                                     'event_time': item['event_time'],
                                     'provenance': 'sha256:' + self.sources['lims']['sha256'] + ':' + item['sheet'] + '!' + item['value_cell']})
        request = {'schema': 'python-cycle-v1', 'scope': 'historical_replay', 'origin': as_of,
                   'state': dict(params), 'observations': observations,
                   'bindings': {'feed_t95': 'feed_t95'}, 'use_lims_upper_bound': use_lims_upper_bound}
        decision = run_cycle(request)
        supplied = set(params) | ({'feed_t95'} if 'ht_feed_t95' in selected else set())
        expected = set(model['defaults']) | {'clean_stock', 'heavy_stock'}
        return {'schema': POLICY['id'], 'scope': 'historical_replay', 'as_of': as_of,
                'source_config_sha256': digest(self.sources), 'policy_sha256': digest(POLICY),
                'telemetry': telemetry_snapshot(self.rows, as_of, self.telemetry_audit, self.sources['telemetry']),
                'selected_lims': selected, 'lims_series_status': series_status, 'lims_selection_log': log,
                'use_lims_upper_bound': use_lims_upper_bound,
                'pac': {'status': 'EXCLUDED', 'values_read': 0, 'reason': 'PAC_TIME_AVAILABILITY_AND_HEALTH_UNKNOWN'},
                'scenario_parameters': dict(params), 'scenario_parameter_basis': 'explicit_user_scenario_not_archive',
                'missing_model_fields': sorted(expected - supplied),
                'mapping_limits': POLICY['mapping_limits'], 'request': request, 'decision': decision,
                'read_audit': {'telemetry': self.telemetry_audit, 'lims': self.lims_audit},
                'industrial_command': False, 'model_fits': 0, 'model_inference_calls': 0}
