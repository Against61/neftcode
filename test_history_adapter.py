import copy
import csv
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess
from xml.etree import ElementTree as ET
import zipfile

from neft.history_adapter import HistoryArchive, POLICY, bounds, sha
from neft.history_tool import HistoryToolService

ORIGIN = '2024-01-02T12:00:00'
S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def fixture(root, *, missing=None, last_bad=None, duplicate_csv=False, bad_unit=False,
            duplicate_lab=False, bad_lab=False, future_value='future-must-not-be-read'):
    """Minimal independent XLSX + CSV with known arithmetic, no plant records."""
    tags = [t for t in POLICY['telemetry_channels'] if t != missing]
    rows = []
    for i in range(145):
        at = datetime(2024, 1, 1, 11, 50) + timedelta(minutes=10 * i)
        rows.append([at.isoformat()] + [('bad' if i == 144 and tag == last_bad else
                                        12 if tag == 'T12' else j + i / 10) for j, tag in enumerate(tags)])
    if duplicate_csv: rows.insert(1, rows[0])
    rows.append(['2025-01-01T00:00:00'] + [future_value] * len(tags))
    with (root / 'ht.csv').open('w', newline='') as f:
        w = csv.writer(f); w.writerow(['date'] + tags); w.writerows(rows)
    sheet = ET.Element('{' + S + '}worksheet')
    data = ET.SubElement(sheet, '{' + S + '}sheetData')
    def cell(row, col, value, numeric=False):
        c = ET.SubElement(row, '{' + S + '}c', r=col + row.get('r'), t='n' if numeric else 'inlineStr')
        if numeric: ET.SubElement(c, '{' + S + '}v').text = str(value)
        else: ET.SubElement(ET.SubElement(c, '{' + S + '}is'), '{' + S + '}t').text = str(value)
    group = ET.SubElement(data, '{' + S + '}row', r='1')
    cell(group, 'BO', "Гидроочистка. Точка отбора '1'")
    cell(group, 'CE', "Гидроочистка. Точка отбора '2'")
    for n in (2, 3):
        row = ET.SubElement(data, '{' + S + '}row', r=str(n))
        for spec in POLICY['lims_series']:
            cell(row, spec['date_column'], spec['indicator'] if n == 2 else
                 ('wrong-unit' if bad_unit else spec['header_unit']))
    for row_id, at in [(5, datetime(2024, 1, 2, 8)), (6, datetime(2024, 1, 2, 11)), (7, datetime(2025, 1, 1))]:
        row = ET.SubElement(data, '{' + S + '}row', r=str(row_id))
        for spec in POLICY['lims_series']:
            time = datetime(2024, 1, 2, 8) if duplicate_lab and row_id == 6 else at
            # Different per-pair dates: product cetane is from an hour earlier.
            if spec['field'] == 'ht_product_cn': time -= timedelta(hours=1)
            cell(row, spec['date_column'], (time - datetime(1899, 12, 30)).total_seconds() / 86400, True)
            value = {'ht_feed_sulfur': .5, 'ht_feed_t95': 345, 'ht_product_sulfur': 7,
                     'ht_product_t95': 350, 'ht_product_cn': 52}[spec['field']]
            cell(row, spec['value_column'], future_value if row_id == 7 else
                 'bad' if bad_lab and row_id == 5 else value, numeric=row_id != 7 and not (bad_lab and row_id == 5))
    wb = f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets><sheet name="Лист1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    rel = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>'
    with zipfile.ZipFile(root / 'lims.xlsx', 'w') as z:
        z.writestr('xl/workbook.xml', wb); z.writestr('xl/_rels/workbook.xml.rels', rel)
        z.writestr('xl/worksheets/sheet1.xml', ET.tostring(sheet))
    return {'schema': 'history-sources-v1', 'telemetry': {'path': 'ht.csv', 'sha256': sha(root / 'ht.csv')},
            'lims': {'path': 'lims.xlsx', 'sha256': sha(root / 'lims.xlsx')}}


class HistoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='neft-history-test-')
        self.addCleanup(tmp.cleanup); self.root = Path(tmp.name)
        self.sources = fixture(self.root)

    def archive(self, **kwargs):
        if kwargs: self.sources = fixture(self.root, **kwargs)
        return HistoryArchive(self.root, self.sources, [ORIGIN])

    def test_default_does_not_publish_lims_or_telemetry(self):
        s = self.archive().snapshot(ORIGIN)
        self.assertFalse(s['selected_lims']); self.assertEqual(s['request']['observations'], [])
        self.assertTrue(all(r['value'] is None for r in s['lims_selection_log']))
        self.assertTrue(all(not r['online_eligible'] and r['available_time'] is None for r in s['telemetry']['channels']))
        self.assertEqual(s['decision']['status'], 'ABSTAIN_DATA')
        self.assertEqual(len(s['lims_series_status']), 5)
        self.assertTrue(all(r['status'] == 'UNAVAILABLE' and r['reasons'] for r in s['lims_series_status']))

    def test_explicit_four_hour_boundary_and_no_fake_publication(self):
        a = HistoryArchive(self.root, self.sources, ['2024-01-02T11:59:59', ORIGIN])
        before = a.snapshot('2024-01-02T11:59:59', use_lims_upper_bound=True)
        after = a.snapshot(ORIGIN, use_lims_upper_bound=True)
        self.assertNotIn('ht_product_sulfur', before['selected_lims'])
        lab = after['selected_lims']['ht_product_sulfur']
        self.assertEqual(lab['event_time'], '2024-01-02T08:00:00')
        self.assertIsNone(lab['availability']['actual_publication'])
        self.assertEqual(lab['availability']['eligibility_time'], ORIGIN)
        self.assertEqual(lab['value_cell'], 'CR5')
        self.assertNotIn('available_time', after['request']['observations'][0])

    def test_independent_pair_dates_and_units(self):
        s = self.archive().snapshot(ORIGIN, use_lims_upper_bound=True)
        self.assertEqual(s['selected_lims']['ht_product_cn']['event_time'], '2024-01-02T07:00:00')
        self.assertEqual(s['selected_lims']['ht_feed_sulfur']['value'], 5000)
        self.assertEqual(s['selected_lims']['ht_feed_sulfur']['unit'], 'mg/kg')

    def test_no_feed_product_crude_substitution_or_default_preset(self):
        s = self.archive().snapshot(ORIGIN, use_lims_upper_bound=True)
        self.assertEqual(s['request']['state'], {})
        self.assertEqual({r['field'] for r in s['request']['observations']}, {'feed_t95', 'product_sulfur'})
        self.assertEqual(s['decision']['model_input'], {'feed_t95': 345})
        self.assertEqual(s['decision']['status'], 'ABSTAIN_HISTORICAL_SCOPE')
        self.assertIsNone(s['decision']['recommendation'])
        self.assertIn('crude_sulfur', s['missing_model_fields'])

    def test_future_values_have_no_effect_on_decision(self):
        a = self.archive().snapshot(ORIGIN, use_lims_upper_bound=True)
        b = self.archive(future_value='another-invalid-future-value').snapshot(ORIGIN, use_lims_upper_bound=True)
        # Source hashes/provenance must change, but selected numbers and results may not.
        self.assertEqual(a['decision']['status'], b['decision']['status'])
        self.assertEqual(a['decision']['model_input'], b['decision']['model_input'])
        self.assertEqual([r['value'] for r in a['request']['observations']], [r['value'] for r in b['request']['observations']])
        self.assertEqual(a['read_audit']['lims']['pairs_numeric_parsed'], 10)
        self.assertFalse(a['read_audit']['lims']['future_values_parsed'])

    def test_bad_latest_telemetry_not_carried_forward(self):
        s = self.archive(last_bad='P8').snapshot(ORIGIN)
        p8 = next(r for r in s['telemetry']['channels'] if r['tag'] == 'ht:P8')
        self.assertIsNone(p8['archive_value']); self.assertIsNotNone(p8['latest_finite_value'])
        self.assertIn('LATEST_NONFINITE', p8['flags'])

    def test_missing_channel_visible(self):
        s = self.archive(missing='T11').snapshot(ORIGIN)
        r = next(r for r in s['telemetry']['channels'] if r['tag'] == 'ht:T11')
        self.assertIn('MISSING_CHANNEL', r['flags']); self.assertIsNone(r['archive_value'])

    def test_duplicates_rejected(self):
        with self.assertRaisesRegex(ValueError, 'DUPLICATE_OR_UNORDERED'): self.archive(duplicate_csv=True)
        s = self.archive(duplicate_lab=True).snapshot(ORIGIN, use_lims_upper_bound=True)
        self.assertFalse(s['selected_lims'])
        self.assertTrue(all('DUPLICATE_SAMPLE_TIME' in r['reasons'] for r in s['lims_selection_log']))

    def test_invalid_latest_available_lab_is_not_selected(self):
        s = self.archive(bad_lab=True).snapshot(ORIGIN, use_lims_upper_bound=True)
        self.assertFalse(s['selected_lims'])

    def test_wrong_header_unit_is_not_silently_converted(self):
        with self.assertRaisesRegex(ValueError, 'UNIT_MISMATCH'): self.archive(bad_unit=True)

    def test_constant_history_and_grid(self):
        s = self.archive().snapshot(ORIGIN)
        r = next(r for r in s['telemetry']['channels'] if r['tag'] == 'ht:T12')
        self.assertEqual(r['finite_grid_points'], 145)
        self.assertEqual(len(r['constant_runs']), 1)
        self.assertIn('CONSTANT_HISTORY_REVIEW', r['flags'])

    def test_no_pac_values_or_eligibility(self):
        s = self.archive().snapshot(ORIGIN, use_lims_upper_bound=True)
        self.assertEqual(s['pac']['values_read'], 0)
        self.assertTrue(all(r['source'] == 'lims' for r in s['request']['observations']))

    def test_source_hash_checked(self):
        self.sources['telemetry']['sha256'] = 'a' * 64
        with self.assertRaisesRegex(ValueError, 'HASH_MISMATCH'): self.archive()

    def test_path_cannot_escape_data_root(self):
        self.sources['telemetry']['path'] = '../outside.csv'
        with self.assertRaisesRegex(ValueError, 'OUTSIDE_DATA_ROOT'): self.archive()

    def test_protected_time_and_timezone(self):
        for origin in ('2025-01-01', '2026-01-01', '2024-01-02T12:00:00Z', '2023-01-01'):
            with self.assertRaises(ValueError): bounds(origin)

    def test_scenario_parameters_explicit_and_bounded(self):
        a = self.archive()
        for p in ({'crude_sulfur': .5}, {'sulfur_limit': 30}, {'demand': float('nan')}, {'step': True}, {'step': 20}, {'horizon': 0}, {'horizon': .6, 'step': 30}):
            with self.assertRaises(ValueError): a.snapshot(ORIGIN, scenario_parameters=p)
        s = a.snapshot(ORIGIN, scenario_parameters={'demand': 90, 'horizon': 3, 'step': 30})
        self.assertEqual(s['request']['state'], s['scenario_parameters'])
        self.assertEqual(s['request']['scope'], 'historical_replay')

    def test_upper_flag_and_loaded_origin(self):
        a = self.archive()
        with self.assertRaises(ValueError): a.snapshot(ORIGIN, use_lims_upper_bound='true')
        with self.assertRaises(ValueError): a.snapshot('2024-02-01')

    def service(self):
        path = self.root / 'sources.json'
        path.write_text(json.dumps(self.sources))
        return HistoryToolService(self.root / 'calls', self.root, path)

    def test_tool_and_direct_api_match(self):
        r = self.service().call({'as_of': ORIGIN, 'use_lims_upper_bound': True})
        self.assertFalse(r['is_error'])
        actual = json.loads(Path(r['output']['artifacts']['history_json']).read_text())
        direct = json.loads(json.dumps(self.archive().snapshot(ORIGIN, use_lims_upper_bound=True)))
        self.assertEqual(actual, direct)
        self.assertIsNone(r['output']['recommendation'])

    def test_tool_cannot_override_paths_or_scope(self):
        service = self.service()
        for args in ({'as_of': ORIGIN, 'data_root': '/'}, {'as_of': ORIGIN, 'scope': 'synthetic_model'},
                     {'as_of': ORIGIN, 'sources': 'other.json'}, {'as_of': '2026-01-01'}):
            r = service.call(args)
            self.assertTrue(r['is_error']); self.assertIsNone(r['output']['recommendation'])

    def test_tool_timeout_and_bad_hash_leave_audit_without_recommendation(self):
        service = self.service()
        with patch('neft.history_tool.subprocess.run', side_effect=subprocess.TimeoutExpired('worker', 20)):
            r = service.call({'as_of': ORIGIN})
        self.assertEqual(r['output']['reasons'], ['WORKER_TIMEOUT'])
        self.assertTrue(Path(r['output']['audit_manifest']).exists())
        self.sources['lims']['sha256'] = 'f' * 64
        r = self.service().call({'as_of': ORIGIN})
        self.assertTrue(r['is_error']); self.assertIn('HASH_MISMATCH', r['output']['reasons'][0])

    def test_stale_telemetry_and_gap_break_constant_run(self):
        a = self.archive()
        a.rows = [r for r in a.rows if r['at'] <= datetime(2024, 1, 2, 9, 50)]
        s = a.snapshot(ORIGIN)
        r = s['telemetry']['channels'][0]
        self.assertEqual(r['age_at_cutoff_minutes'], 120)
        self.assertIn('STALE_LAST_FINITE', r['flags'])
        a = self.archive()
        a.rows = [r for r in a.rows if r['at'] != datetime(2024, 1, 1, 23, 50)]
        t12 = next(r for r in a.snapshot(ORIGIN)['telemetry']['channels'] if r['tag'] == 'ht:T12')
        self.assertEqual(len(t12['constant_runs']), 2)

    def test_html_contains_only_eligible_lab_values(self):
        from neft.history_report import render_history
        s = self.archive().snapshot(ORIGIN)
        html = render_history(s)
        self.assertIn('выключено', html); self.assertIn('ABSTAIN_DATA', html)
        self.assertNotIn('5000', html)

    def test_partial_grid_origin_matches_expected_cadence(self):
        a = HistoryArchive(self.root, self.sources, ['2024-01-02T11:55:00'])
        s = a.snapshot('2024-01-02T11:55:00')
        self.assertEqual(s['telemetry']['channels'][0]['expected_grid_points'], 144)


if __name__ == '__main__': unittest.main()
