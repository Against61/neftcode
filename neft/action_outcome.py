"""Strict command-to-quality linking, lag selection and gated calibration."""
import csv
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import zipfile
from xml.etree import ElementTree as ET

import numpy as np
import pandas as pd

COMMAND_COLUMNS = {
    'command_id', 'control', 'issued_time', 'executed_time',
    'value_before', 'value_after', 'unit', 'status', 'source_system',
}
SHEET_NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
OFFICE_REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def local_time(value, field):
    try:
        result = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('INVALID_' + field.upper()) from exc
    if pd.isna(result) or result.tzinfo is not None:
        raise ValueError(field.upper() + '_MUST_BE_NAIVE_SOURCE_LOCAL')
    return result


def validate_pac_metadata(metadata):
    reasons = []
    if not isinstance(metadata, dict):
        return {'status': 'EXCLUDED', 'eligible': False,
                'reasons': ['PAC_METADATA_MISSING'], 'pac_used': False}
    if metadata.get('schema') != 'pac-availability-v1':
        reasons.append('PAC_METADATA_SCHEMA')
    if metadata.get('timestamp_basis') != 'measurement_time':
        reasons.append('PAC_TIMESTAMP_BASIS_UNCONFIRMED')
    availability = metadata.get('availability')
    if not isinstance(availability, dict) or availability.get('mode') not in {
            'per_record_available_time', 'confirmed_max_delay'}:
        reasons.append('PAC_AVAILABILITY_UNCONFIRMED')
    elif availability['mode'] == 'per_record_available_time':
        if not availability.get('field') or not availability.get('evidence'):
            reasons.append('PAC_AVAILABLE_TIME_SOURCE_MISSING')
    else:
        delay = availability.get('minutes')
        if (isinstance(delay, bool) or not isinstance(delay, (int, float)) or
                not math.isfinite(delay) or delay < 0 or not availability.get('evidence')):
            reasons.append('PAC_DELAY_EVIDENCE_MISSING')
    for name in ('health', 'calibration'):
        item = metadata.get(name)
        if (not isinstance(item, dict) or not item.get('source') or
                not item.get('status_field') or not item.get('accepted_values')):
            reasons.append('PAC_' + name.upper() + '_SOURCE_MISSING')
    return {'status': 'ELIGIBLE_FOR_SEPARATE_VALIDATION' if not reasons else 'EXCLUDED',
            'eligible': not reasons, 'reasons': reasons, 'pac_used': False}


def read_commands(path, config, start, end_exclusive):
    """Read command rows for one split without parsing later numeric values."""
    source = Path(path).resolve()
    start = local_time(start, 'split_start')
    end = local_time(end_exclusive, 'split_end')
    controls = config['controls']
    rows = []
    seen = set()
    previous = None
    stop = None
    status_counts = {}
    with source.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if set(reader.fieldnames or []) != COMMAND_COLUMNS:
            raise ValueError('COMMAND_COLUMNS_SCHEMA')
        for source_row, raw in enumerate(reader, 2):
            issued = local_time(raw['issued_time'], 'issued_time')
            if previous is not None and issued < previous:
                raise ValueError('COMMANDS_UNORDERED')
            previous = issued
            if issued >= end:
                stop = issued.isoformat()
                break
            if issued < start:
                continue
            command_id = raw['command_id'].strip()
            if not command_id or command_id in seen:
                raise ValueError('COMMAND_ID_MISSING_OR_DUPLICATE')
            seen.add(command_id)
            control = raw['control'].strip()
            if control not in controls:
                raise ValueError('UNKNOWN_CONTROL: ' + control)
            status = raw['status'].strip().lower()
            if status not in {'executed', 'cancelled', 'failed'}:
                raise ValueError('COMMAND_STATUS')
            status_counts[status] = status_counts.get(status, 0) + 1
            item = {'command_id': command_id, 'control': control,
                    'issued_time': issued.isoformat(), 'status': status,
                    'source_system': raw['source_system'].strip(),
                    'source_file': str(source), 'source_row': source_row}
            if not item['source_system']:
                raise ValueError('COMMAND_SOURCE_SYSTEM_MISSING')
            if status == 'executed':
                executed = local_time(raw['executed_time'], 'executed_time')
                if executed < issued or executed >= end:
                    raise ValueError('EXECUTED_TIME_OUTSIDE_SPLIT_OR_BEFORE_ISSUE')
                if raw['unit'].strip() != controls[control]['unit']:
                    raise ValueError('COMMAND_UNIT_MISMATCH: ' + control)
                try:
                    before, after = float(raw['value_before']), float(raw['value_after'])
                except ValueError as exc:
                    raise ValueError('COMMAND_VALUE_NONNUMERIC') from exc
                if not np.isfinite([before, after]).all():
                    raise ValueError('COMMAND_VALUE_NONFINITE')
                item.update(executed_time=executed.isoformat(), value_before=before,
                            value_after=after, delta_command=after - before,
                            unit=raw['unit'].strip())
            else:
                if any(raw[key].strip() for key in
                       ('executed_time', 'value_before', 'value_after', 'unit')):
                    raise ValueError('NONEXECUTED_COMMAND_HAS_EXECUTION_FIELDS')
                item.update(executed_time=None, value_before=None, value_after=None,
                            delta_command=None, unit=None)
            rows.append(item)
    return rows, {'source': str(source), 'sha256': sha(source),
                  'rows_in_split': len(rows), 'status_counts': status_counts,
                  'stopping_timestamp_only': stop,
                  'later_numeric_values_parsed': False}


def read_quality_sqlite(path, series_id, start, end_exclusive):
    query = '''SELECT event_time,value_numeric,quality_status,unit_canonical,
source_file,sheet,row,value_column FROM quality_observation
WHERE series_id=? AND event_time>=? AND event_time<? ORDER BY event_time,row'''
    source = Path(path).resolve()
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as database:
        frame = pd.read_sql_query(query, database,
                                  params=(series_id, start, end_exclusive))
    if frame.empty:
        return [], {'source': str(source), 'sha256': sha(source), 'rows_read': 0,
                    'valid_unique': 0, 'query': query}
    frame['event_time'] = pd.to_datetime(frame.event_time)
    if frame.event_time.dt.tz is not None:
        raise ValueError('QUALITY_TIME_MUST_BE_NAIVE_SOURCE_LOCAL')
    duplicate = frame.event_time.duplicated(keep=False)
    valid = ((frame.quality_status == 'valid') &
             np.isfinite(frame.value_numeric) & ~duplicate)
    kept = frame.loc[valid].copy()
    if set(kept.unit_canonical) - {'mg/kg'}:
        raise ValueError('QUALITY_UNIT_MISMATCH')
    records = [{**row.to_dict(), 'event_time': row.event_time.isoformat(),
                'value_numeric': float(row.value_numeric), 'row': int(row['row'])}
               for _, row in kept.iterrows()]
    return records, {'source': str(source), 'sha256': sha(source),
                     'rows_read': len(frame), 'valid_unique': len(records),
                     'duplicates_excluded': int(duplicate.sum()), 'query': query}


def _cell_text(cell, strings):
    if cell is None:
        return None
    if cell.find('s:f', SHEET_NS) is not None:
        raise ValueError('FORMULA_CELL_NOT_SUPPORTED')
    kind = cell.get('t')
    if kind == 'inlineStr':
        return ''.join(cell.itertext())
    value = cell.findtext('s:v', namespaces=SHEET_NS)
    if kind == 's':
        return strings[int(value)] if value is not None else None
    if kind in {'b', 'e'}:
        return None
    return value


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).strip().replace(',', '.'))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _excel_time(cell, strings, epoch1904):
    raw = _cell_text(cell, strings)
    if raw in (None, ''):
        return None
    if cell.get('t') in (None, 'n'):
        serial = _number(raw)
        if serial is None or not 0 <= serial < 1000000:
            raise ValueError('INVALID_EXCEL_DATE')
        base = datetime(1904, 1, 1) if epoch1904 else datetime(1899, 12, 30)
        day, fraction = divmod(serial, 1)
        if not epoch1904 and 0 < serial < 60:
            day += 1
        return pd.Timestamp(base + timedelta(days=day,
                                              milliseconds=round(fraction * 86400000)))
    return local_time(raw, 'quality_time')


def read_quality_xlsx(path, config, start, end_exclusive):
    """Read the registered LIMS CQ:CR pair and stop before later numeric values."""
    source = Path(path).resolve()
    spec = config['quality_excel']
    start = local_time(start, 'split_start')
    end = local_time(end_exclusive, 'split_end')
    records = []
    previous = None
    stop = None
    headers = set()
    examined = 0
    with zipfile.ZipFile(source) as book:
        workbook = ET.fromstring(book.read('xl/workbook.xml'))
        properties = workbook.find('s:workbookPr', SHEET_NS)
        epoch1904 = properties is not None and properties.get('date1904') in {'1', 'true'}
        sheets = [sheet for sheet in workbook.findall('s:sheets/s:sheet', SHEET_NS)
                  if sheet.get('name') == spec['sheet']]
        if len(sheets) != 1:
            raise ValueError('QUALITY_SHEET_MISMATCH')
        relations = ET.fromstring(book.read('xl/_rels/workbook.xml.rels'))
        relation_id = sheets[0].get('{' + OFFICE_REL + '}id')
        relation = next((item for item in relations.iter()
                         if item.get('Id') == relation_id), None)
        if relation is None or relation.get('TargetMode') == 'External':
            raise ValueError('QUALITY_WORKBOOK_RELATIONSHIP')
        target = relation.get('Target')
        target = target.lstrip('/') if target.startswith('/') else 'xl/' + target
        strings = []
        if 'xl/sharedStrings.xml' in book.namelist():
            shared = ET.fromstring(book.read('xl/sharedStrings.xml'))
            strings = [''.join(item.itertext()) for item in shared]
        with book.open(target) as stream:
            for _, row in ET.iterparse(stream, events=('end',)):
                if row.tag != '{' + SHEET_NS['s'] + '}row':
                    continue
                row_id = int(row.get('r'))
                cells = {re.sub(r'\d+$', '', cell.get('r')): cell
                         for cell in row.findall('s:c', SHEET_NS)}
                if row_id == 1:
                    group_header = (_cell_text(
                        cells.get(spec['group_header_column']), strings) or '')
                    if not all(fragment in group_header
                               for fragment in spec['group_header_contains']):
                        raise ValueError('QUALITY_GROUP_HEADER_MISMATCH')
                    headers.add(1)
                elif row_id == 2:
                    if _cell_text(cells.get(spec['date_column']), strings) != spec['indicator']:
                        raise ValueError('QUALITY_INDICATOR_MISMATCH')
                    headers.add(2)
                elif row_id == 3:
                    if _cell_text(cells.get(spec['date_column']), strings) != spec['header_unit']:
                        raise ValueError('QUALITY_UNIT_MISMATCH')
                    headers.add(3)
                elif row_id >= 5:
                    if headers != {1, 2, 3}:
                        raise ValueError('QUALITY_HEADERS_MISSING')
                    examined += 1
                    date_cell = cells.get(spec['date_column'])
                    at = _excel_time(date_cell, strings, epoch1904)
                    if at is None:
                        if _cell_text(date_cell, strings) not in (None, '') or _cell_text(
                                cells.get(spec['value_column']), strings) not in (None, ''):
                            raise ValueError('INVALID_QUALITY_DATE: ' + str(row_id))
                        row.clear()
                        continue
                    if previous is not None and at < previous:
                        raise ValueError('UNORDERED_QUALITY_PAIR')
                    previous = at
                    if at >= end:
                        stop = at.isoformat()
                        row.clear()
                        break
                    if at >= start:
                        value = _number(_cell_text(cells.get(spec['value_column']), strings))
                        records.append({
                            'event_time': at.isoformat(), 'value_numeric': value,
                            'quality_status': 'valid' if value is not None else 'invalid',
                            'unit_canonical': config['target_unit'], 'source_file': str(source),
                            'sheet': spec['sheet'], 'row': row_id,
                            'value_column': spec['value_column'],
                        })
                row.clear()
    if headers != {1, 2, 3}:
        raise ValueError('QUALITY_HEADERS_MISSING')
    counts = {}
    for record in records:
        counts[record['event_time']] = counts.get(record['event_time'], 0) + 1
    kept = [record for record in records if record['quality_status'] == 'valid' and
            counts[record['event_time']] == 1]
    return kept, {
        'source': str(source), 'sha256': sha(source), 'rows_examined': examined,
        'rows_in_split': len(records), 'valid_unique': len(kept),
        'duplicates_excluded': sum(count > 1 for count in counts.values()),
        'stopping_timestamp_only': stop, 'later_numeric_values_parsed': False,
        'sheet': spec['sheet'], 'date_column': spec['date_column'],
        'value_column': spec['value_column'],
    }


def read_quality_source(path, config, start, end_exclusive):
    suffix = Path(path).suffix.lower()
    if suffix in {'.xlsx', '.xlsm'}:
        return read_quality_xlsx(path, config, start, end_exclusive)
    if suffix in {'.sqlite', '.sqlite3', '.db'}:
        return read_quality_sqlite(path, config['target_series'], start, end_exclusive)
    raise ValueError('QUALITY_SOURCE_MUST_BE_XLSX_OR_SQLITE')


def link_outcomes(commands, quality, config):
    executed = [row for row in commands if row['status'] == 'executed' and
                row['delta_command'] != 0]
    times = [local_time(row['executed_time'], 'executed_time') for row in executed]
    isolation = pd.Timedelta(hours=config['isolation_hours'])
    quality = sorted(quality, key=lambda row: local_time(row['event_time'], 'quality_time'))
    used_samples = set()
    events = []
    for index, command in enumerate(executed):
        origin = times[index]
        overlapping = [executed[j]['command_id'] for j in range(len(executed))
                       if j != index and abs(times[j] - origin) <= isolation]
        event = {**command, 'quarter': str(origin.to_period('Q')),
                 'isolated': not overlapping, 'overlapping_commands': overlapping,
                 'baseline': None, 'after': [], 'exclusion_reasons': []}
        if overlapping:
            event['exclusion_reasons'].append('OVERLAPPING_EXECUTED_COMMAND')
            events.append(event)
            continue
        before_candidates = [row for row in quality
                             if origin - pd.Timedelta(hours=config['baseline_before_hours']) <=
                             local_time(row['event_time'], 'quality_time') < origin]
        before = before_candidates[-1] if before_candidates else None
        if before is None:
            event['exclusion_reasons'].append('BASELINE_SAMPLE_MISSING')
        elif before['event_time'] in used_samples:
            event['exclusion_reasons'].append('BASELINE_SAMPLE_REUSED')
            before = None
        else:
            used_samples.add(before['event_time'])
            event['baseline'] = before
        for lo, hi in config['response_bins_hours']:
            candidates = [row for row in quality
                          if origin + pd.Timedelta(hours=lo) <
                          local_time(row['event_time'], 'quality_time') <=
                          origin + pd.Timedelta(hours=hi)]
            chosen = candidates[0] if candidates else None
            reason = None
            if chosen is None:
                reason = 'POST_SAMPLE_MISSING'
            elif chosen['event_time'] in used_samples:
                reason = 'POST_SAMPLE_REUSED'; chosen = None
            if chosen is not None:
                used_samples.add(chosen['event_time'])
            delta = (chosen['value_numeric'] - before['value_numeric']
                     if chosen is not None and before is not None else None)
            event['after'].append({'bin_hours': [lo, hi], 'sample': chosen,
                                   'delta_quality': delta, 'reason': reason})
        events.append(event)
    return events, {'commands_total': len(commands), 'executed_nonzero': len(executed),
                    'isolated': sum(row['isolated'] for row in events),
                    'unique_quality_samples_used': len(used_samples)}


def readiness(events, config):
    minimum = config['minimum']
    controls = {}
    for control in config['controls']:
        rows = [row for row in events if row['control'] == control and row['isolated']]
        directions = {'up': sum(row['delta_command'] > 0 for row in rows),
                      'down': sum(row['delta_command'] < 0 for row in rows)}
        bins = []
        for bin_index, hours in enumerate(config['response_bins_hours']):
            paired = [row for row in rows if row['baseline'] is not None and
                      row['after'][bin_index]['delta_quality'] is not None]
            quarters = sorted({row['quarter'] for row in paired})
            eligible = (len(paired) >= minimum['paired_events_per_control_bin'] and
                        len(quarters) >= minimum['train_quarters_per_control_bin'])
            bins.append({'bin_hours': hours, 'pairs': len(paired),
                         'quarters': quarters, 'eligible': eligible})
        control_ready = (len(rows) >= minimum['executed_commands_per_control'] and
                         min(directions.values()) >= minimum['each_direction_per_control'] and
                         any(row['eligible'] for row in bins))
        controls[control] = {'isolated_executed': len(rows), 'directions': directions,
                             'bins': bins, 'ready': control_ready}
    return {'status': 'READY' if all(row['ready'] for row in controls.values())
            else 'INSUFFICIENT_TRAIN_SUPPORT', 'ready': all(
                row['ready'] for row in controls.values()), 'controls': controls}


def _fit_linear(x, y):
    x = np.asarray(x, float); y = np.asarray(y, float)
    if len(x) < 2 or np.ptp(x) <= 0:
        raise ValueError('COMMAND_DELTA_VARIANCE_REQUIRED')
    design = np.column_stack([np.ones(len(x)), x])
    intercept, slope = np.linalg.lstsq(design, y, rcond=None)[0]
    return float(intercept), float(slope)


def calibrate(events, config):
    gate = readiness(events, config)
    if not gate['ready']:
        return None, {'model_fits': 0, 'reason': gate['status']}
    rng = np.random.default_rng(config['selection']['seed'])
    models = {}; fits = 0
    for control in config['controls']:
        candidates = []
        for bin_index, hours in enumerate(config['response_bins_hours']):
            rows = [row for row in events if row['control'] == control and row['isolated'] and
                    row['baseline'] is not None and
                    row['after'][bin_index]['delta_quality'] is not None]
            if not next(item for item in gate['controls'][control]['bins']
                        if item['bin_hours'] == hours)['eligible']:
                continue
            predictions = []; actual = []; identifiable = True
            for quarter in sorted({row['quarter'] for row in rows}):
                train = [row for row in rows if row['quarter'] != quarter]
                test = [row for row in rows if row['quarter'] == quarter]
                try:
                    intercept, slope = _fit_linear(
                        [row['delta_command'] for row in train],
                        [row['after'][bin_index]['delta_quality'] for row in train])
                except ValueError:
                    identifiable = False
                    break
                finally:
                    fits += 1
                predictions.extend(intercept + slope * row['delta_command'] for row in test)
                actual.extend(row['after'][bin_index]['delta_quality'] for row in test)
            if not identifiable:
                continue
            mae = float(np.mean(np.abs(np.asarray(actual) - np.asarray(predictions))))
            candidates.append((mae, -bin_index, bin_index, hours, rows))
        if not candidates:
            return None, {'model_fits': fits,
                          'reason': 'NO_IDENTIFIABLE_LAG_BIN: ' + control}
        cv_mae, _, bin_index, hours, rows = min(candidates)
        x = np.asarray([row['delta_command'] for row in rows])
        y = np.asarray([row['after'][bin_index]['delta_quality'] for row in rows])
        intercept, slope = _fit_linear(x, y); fits += 1
        residual = y - (intercept + slope * x)
        radius = float(np.quantile(np.abs(residual),
                                   config['selection']['prediction_interval_coverage']))
        quarter_indexes = {
            quarter: np.asarray([index for index, row in enumerate(rows)
                                 if row['quarter'] == quarter], dtype=int)
            for quarter in sorted({row['quarter'] for row in rows})
        }
        quarters = list(quarter_indexes)
        slopes = []
        for _ in range(config['selection']['bootstrap_repetitions']):
            sampled_quarters = rng.choice(quarters, size=len(quarters), replace=True)
            chosen = np.concatenate([quarter_indexes[quarter]
                                     for quarter in sampled_quarters])
            try:
                slopes.append(_fit_linear(x[chosen], y[chosen])[1])
            except ValueError:
                continue
            finally:
                fits += 1
        if not slopes:
            return None, {'model_fits': fits,
                          'reason': 'BOOTSTRAP_SLOPE_UNAVAILABLE: ' + control}
        models[control] = {'selected_bin_hours': hours, 'bin_index': bin_index,
                           'intercept': intercept, 'slope': slope,
                           'slope_interval_90': [float(np.quantile(slopes, .05)),
                                                 float(np.quantile(slopes, .95))],
                           'residual_radius_90': radius, 'cv_mae': cv_mae,
                           'train_pairs': len(rows),
                           'train_target_median': float(np.median(y))}
    if fits > config['budget']['model_fits_max']:
        return None, {'model_fits': fits, 'reason': 'MODEL_FIT_BUDGET_EXCEEDED'}
    return models, {'model_fits': fits, 'reason': None}


def evaluate_holdout(models, events, config):
    result = {}; accepted = True
    for control, model in models.items():
        index = model['bin_index']
        rows = [row for row in events if row['control'] == control and row['isolated'] and
                row['baseline'] is not None and row['after'][index]['delta_quality'] is not None]
        actual = np.asarray([row['after'][index]['delta_quality'] for row in rows])
        prediction = np.asarray([model['intercept'] + model['slope'] * row['delta_command']
                                 for row in rows])
        enough = len(rows) >= config['minimum']['holdout_events_per_control']
        if len(rows):
            mae = float(np.mean(np.abs(actual - prediction)))
            baseline_mae = float(np.mean(np.abs(actual - model['train_target_median'])))
            improvement = ((baseline_mae - mae) / baseline_mae
                           if baseline_mae > 0 else None)
            coverage = float(np.mean(np.abs(actual - prediction) <=
                                     model['residual_radius_90']))
        else:
            mae = baseline_mae = improvement = coverage = None
        passed = (enough and improvement is not None and
                  improvement >= config['holdout_acceptance']['mae_improvement_fraction_vs_train_median'] and
                  config['holdout_acceptance']['minimum_interval_coverage'] <= coverage <=
                  config['holdout_acceptance']['maximum_interval_coverage'])
        result[control] = {'events': len(rows), 'mae': mae,
                           'baseline_mae': baseline_mae,
                           'mae_improvement_fraction': improvement,
                           'interval_coverage': coverage, 'passed': passed}
        accepted &= passed
    return {'status': 'ACCEPT' if accepted else 'REJECT',
            'accepted': accepted, 'controls': result}


def run_analysis(config, *, command_log=None, quality_db=None, pac_metadata=None):
    pac_gate = validate_pac_metadata(pac_metadata)
    base = {'schema': config['id'], 'experiment_id': config['experiment_id'],
            'pac_gate': pac_gate, 'pac_used': False,
            'holdout_numeric_read': False, 'sealed_release_numeric_read': False,
            'model_fits': 0, 'models': None, 'holdout': None}
    if command_log is None or not Path(command_log).is_file():
        return {**base, 'status': 'completed', 'decision': 'reject',
                'readiness': {'status': 'MISSING_COMMAND_LOG', 'ready': False},
                'reasons': ['MISSING_COMMAND_LOG_WITH_ISSUED_AND_EXECUTED_TIMES']}
    if quality_db is None or not Path(quality_db).is_file():
        return {**base, 'status': 'completed', 'decision': 'reject',
                'readiness': {'status': 'MISSING_QUALITY_SOURCE', 'ready': False},
                'reasons': ['MISSING_INDEPENDENT_LIMS_SOURCE']}
    train = config['train']
    commands, command_audit = read_commands(command_log, config, **train)
    quality, quality_audit = read_quality_source(quality_db, config, **train)
    events, link_audit = link_outcomes(commands, quality, config)
    gate = readiness(events, config)
    result = {**base, 'status': 'completed', 'readiness': gate,
              'train_audit': {'commands': command_audit, 'quality': quality_audit,
                              'link': link_audit}, 'train_events': events}
    if not gate['ready']:
        return {**result, 'decision': 'reject',
                'reasons': ['INSUFFICIENT_TRAIN_ACTION_OUTCOME_SUPPORT']}
    models, fit_audit = calibrate(events, config)
    result.update(models=models, model_fits=fit_audit['model_fits'])
    if models is None:
        return {**result, 'decision': 'reject',
                'reasons': [fit_audit['reason']]}
    holdout = config['holdout']
    holdout_commands, holdout_command_audit = read_commands(
        command_log, config, **holdout)
    if {row['command_id'] for row in commands} & {
            row['command_id'] for row in holdout_commands}:
        raise ValueError('COMMAND_ID_DUPLICATE_ACROSS_SPLITS')
    holdout_quality, holdout_quality_audit = read_quality_source(
        quality_db, config, **holdout)
    holdout_events, holdout_link_audit = link_outcomes(
        holdout_commands, holdout_quality, config)
    assessment = evaluate_holdout(models, holdout_events, config)
    result.update(holdout_numeric_read=True, holdout=assessment,
                  holdout_audit={'commands': holdout_command_audit,
                                 'quality': holdout_quality_audit,
                                 'link': holdout_link_audit},
                  decision='accept' if assessment['accepted'] else 'reject',
                  reasons=[] if assessment['accepted'] else ['HOLDOUT_ACCEPTANCE_FAILED'])
    return result


def load_config(path):
    return json.loads(Path(path).read_text())
