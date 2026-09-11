import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from neft.doctor import run_doctor
from neft.operator_console import OperatorConsoleService, demo_state
from neft.operator_runtime import IsolatedOperatorConsoleRuntime
from scripts.start_operator_console import prepare
from test_history_adapter import fixture, ORIGIN


class RuntimeV5Tests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='neft-runtime-v5-test-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.sources = fixture(self.root)
        self.sources_path = self.root / 'sources.json'
        self.sources_path.write_text(json.dumps(self.sources))
        self.audit = self.root / 'audit'
        self.runtime = IsolatedOperatorConsoleRuntime(
            self.audit, self.root, self.sources_path)

    def test_isolated_history_and_scenario_match_direct_core(self):
        history = self.runtime.audited_call('history', {'as_of': ORIGIN})
        scenario = self.runtime.audited_call(
            'evaluate', {'as_of': ORIGIN, 'scenario': demo_state()})
        self.assertFalse(history['is_error'])
        self.assertFalse(scenario['is_error'])
        direct = OperatorConsoleService(
            self.root / 'direct-audit', self.root, self.sources_path)
        expected_history = direct.history(ORIGIN)
        _, expected_scenario = direct.evaluate(ORIGIN, demo_state())
        expected_history = json.loads(json.dumps(expected_history))
        expected_scenario = json.loads(json.dumps(expected_scenario))
        for actual, expected in ((history['output'], expected_history),
                                 (scenario['output'], expected_scenario)):
            comparable = {key: value for key, value in actual.items()
                          if key not in {'call_id', 'audit_manifest', 'runtime'}}
            self.assertEqual(comparable, expected)
            self.assertEqual(actual['runtime']['mode'], 'isolated_subprocess')
            folder = Path(actual['audit_manifest']).parent
            self.assertTrue((folder / 'worker.stdout.txt').is_file())
            self.assertTrue((folder / 'worker-result/manifest.json').is_file())
        self.assertFalse(scenario['output']['historical_recommendation'])
        self.assertFalse(scenario['output']['industrial_command'])

    def test_timeout_and_capacity_refuse_without_recommendation(self):
        with patch('neft.operator_runtime.subprocess.run',
                   side_effect=subprocess.TimeoutExpired('worker', 20)):
            timed = self.runtime.audited_call('history', {'as_of': ORIGIN})
        self.assertTrue(timed['is_error'])
        self.assertEqual(timed['output']['reasons'], ['WORKER_TIMEOUT'])
        self.assertIsNone(timed['output']['recommendation'])
        for _ in range(self.runtime.max_concurrent_requests):
            self.assertTrue(self.runtime._slots.acquire(blocking=False))
        try:
            busy = self.runtime.audited_call('history', {'as_of': ORIGIN})
        finally:
            for _ in range(self.runtime.max_concurrent_requests):
                self.runtime._slots.release()
        self.assertTrue(busy['is_error'])
        self.assertEqual(busy['output']['reasons'], ['RUNTIME_BUSY'])
        self.assertIsNone(busy['output']['recommendation'])

    def test_doctor_checks_hashes_strict_replay_and_worker(self):
        report = run_doctor(self.root, self.sources_path, as_of=ORIGIN)
        self.assertEqual(report['status'], 'passed')
        self.assertTrue(report['probe']['worker_isolated'])
        self.assertEqual(report['probe']['selected_lims_count'], 0)
        self.assertIn('STRICT_LIMS_NONDISCLOSURE', report['checks'])
        bad = json.loads(self.sources_path.read_text())
        bad['telemetry']['sha256'] = '0' * 64
        self.sources_path.write_text(json.dumps(bad))
        failed = run_doctor(self.root, self.sources_path, as_of=ORIGIN)
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['errors'], ['TELEMETRY_HASH_MISMATCH'])

    def test_one_command_prepare_creates_pinned_config(self):
        config = self.root / 'generated.local.json'
        sources, created, report = prepare(
            self.root, config, 'ht.csv', 'lims.xlsx', as_of=ORIGIN)
        self.assertTrue(created)
        self.assertEqual(sources, config.resolve())
        self.assertEqual(report['status'], 'passed')
        _, created_again, second = prepare(
            self.root, config, 'ignored.csv', 'ignored.xlsx', as_of=ORIGIN)
        self.assertFalse(created_again)
        self.assertEqual(second['status'], 'passed')


if __name__ == '__main__':
    unittest.main()
