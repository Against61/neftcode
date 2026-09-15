import json
from pathlib import Path
import tempfile
import unittest

from neft.decision_cycle import run_cycle
from neft.operator_console import OperatorConsoleService, contract, demo_state, evaluate_snapshot
from neft.operator_tool import HistoryScenarioToolService
from neft.operator_ui import render_console, render_result
from scripts.configure_local_data import build_config
from scripts.agent_connection import configuration, resolve_resources
from test_history_adapter import fixture, ORIGIN


class OperatorConsoleTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='neft-console-test-')
        self.addCleanup(tmp.cleanup); self.root = Path(tmp.name)
        self.sources = fixture(self.root)
        self.sources_path = self.root / 'sources.json'
        self.sources_path.write_text(json.dumps(self.sources))
        self.service = OperatorConsoleService(self.root / 'calls', self.root, self.sources_path)

    def test_contract_marks_example_and_claims(self):
        c = contract(); state = demo_state()
        self.assertEqual(set(state), set(c['scenario_schema']['required']))
        self.assertIn('ILLUSTRATIVE', c['demo_state_label'])
        self.assertFalse(c['claims']['historical_recommendation'])
        self.assertFalse(c['claims']['telemetry_used_as_online_feature'])

    def test_agent_connection_enables_both_history_tools(self):
        c = configuration(self.root / 'audit', self.root, self.sources_path)
        self.assertEqual(c['enabled_tools'], ['get_refinery_contract', 'recommend_refinery_plan',
                                             'get_refinery_history',
                                             'evaluate_refinery_scenario_with_history'])

    def test_agent_connection_autoconfigures_archive_and_bundled_model(self):
        (self.root/'242000_tags.csv').write_bytes((self.root/'ht.csv').read_bytes())
        (self.root/'ЛИМС fixture.xlsx').write_bytes((self.root/'lims.xlsx').read_bytes())
        generated=self.root/'generated-sources.json'
        data_root,sources,bundle,digest=resolve_resources(
            self.root,generated_sources=generated,use_bundled_intelligence=True)
        self.assertEqual(data_root,self.root)
        self.assertEqual(sources,generated.resolve())
        self.assertEqual(json.loads(generated.read_text()),build_config(self.root))
        self.assertTrue(bundle.is_file());self.assertEqual(len(digest),64)

    def test_manual_scenario_matches_direct_cycle(self):
        snapshot = self.service.history(ORIGIN)
        package = evaluate_snapshot(snapshot, demo_state())
        self.assertEqual(package['decision'], run_cycle(package['request']))
        self.assertIn(package['status'], {'MODEL_PLAN', 'NO_CHANGE', 'ABSTAIN_NO_FULL_PLAN'})
        self.assertFalse(package['historical_recommendation'])
        self.assertFalse(package['industrial_command'])
        self.assertEqual(package['historical_context_used_for_model'], [])
        self.assertTrue(all(v['basis'] == 'explicit_model_assumption'
                            for v in package['input_provenance'].values()))

    def test_only_eligible_feed_t95_can_bind(self):
        snapshot = self.service.history(ORIGIN, use_lims_upper_bound=True)
        state = demo_state(); state.pop('feed_t95')
        package = evaluate_snapshot(snapshot, state, use_historical_feed_t95=True)
        self.assertEqual(package['decision'], run_cycle(package['request']))
        self.assertEqual(package['decision']['model_input']['feed_t95'], 345)
        self.assertEqual(package['historical_context_used_for_model'], ['feed_t95'])
        self.assertEqual(package['input_provenance']['feed_t95']['basis'],
                         'eligible_lims_sample_plus_4h_upper_bound')
        self.assertIsNone(package['input_provenance']['feed_t95']['availability']['actual_publication'])
        self.assertEqual(package['decision']['model_input']['crude_sulfur'], .4)

    def test_history_binding_requires_upper_bound_and_available_sample(self):
        state = demo_state(); state.pop('feed_t95')
        with self.assertRaisesRegex(ValueError, 'UPPER_BOUND_MUST_BE_ENABLED'):
            evaluate_snapshot(self.service.history(ORIGIN), state, use_historical_feed_t95=True)
        with self.assertRaisesRegex(ValueError, 'UNAVAILABLE_OR_AMBIGUOUS'):
            evaluate_snapshot(self.service.history('2024-03-24T12:00:00', use_lims_upper_bound=True),
                              state, use_historical_feed_t95=True)

    def test_complete_explicit_shape_and_numbers_required(self):
        snapshot = self.service.history(ORIGIN)
        bad = demo_state(); bad.pop('feed_cn')
        with self.assertRaisesRegex(ValueError, 'SCENARIO_FIELDS'):
            evaluate_snapshot(snapshot, bad)
        bad = demo_state(); bad['feed_cn'] = float('nan')
        with self.assertRaisesRegex(ValueError, 'NONFINITE'):
            evaluate_snapshot(snapshot, bad)
        bad = demo_state(); bad['extra'] = 1
        with self.assertRaisesRegex(ValueError, 'SCENARIO_FIELDS'):
            evaluate_snapshot(snapshot, bad)

    def test_hard_sulfur_cap_remains_in_python_gate(self):
        state = demo_state(); state['sulfur_limit'] = 30
        package = evaluate_snapshot(self.service.history(ORIGIN), state)
        self.assertEqual(package['status'], 'INVALID_INPUT')
        self.assertIn('SULFUR_HARD_LIMIT_10', package['decision']['reasons'])
        self.assertIsNone(package['recommendation'])

    def test_audited_calls_keep_snapshot_and_scenario_separate(self):
        history = self.service.audited_call('history', {'as_of': ORIGIN})
        self.assertFalse(history['is_error']); self.assertEqual(history['output']['scope'], 'historical_replay')
        scenario = self.service.audited_call('evaluate', {'as_of': ORIGIN, 'scenario': demo_state()})
        self.assertFalse(scenario['is_error'])
        folder = Path(scenario['output']['audit_manifest']).parent
        self.assertTrue((folder / 'history.json').is_file())
        self.assertTrue((folder / 'scenario.json').is_file())
        self.assertNotEqual(json.loads((folder / 'history.json').read_text())['scope'],
                            json.loads((folder / 'scenario.json').read_text())['scope'])

    def test_mcp_service_matches_direct_package(self):
        tool = HistoryScenarioToolService(self.root / 'tool-calls', self.root, self.sources_path)
        args = {'as_of': ORIGIN, 'scenario': demo_state()}
        result = tool.call(args)
        self.assertFalse(result['is_error'])
        saved = json.loads(Path(result['output']['artifacts']['scenario_json']).read_text())
        snapshot, direct = self.service.evaluate(ORIGIN, demo_state())
        direct = json.loads(json.dumps(direct))
        self.assertEqual(saved['request'], direct['request'])
        self.assertEqual(saved['decision'], direct['decision'])
        self.assertFalse(result['output']['historical_recommendation'])

    def test_config_builder_requires_bounded_explicit_sources(self):
        config = build_config(self.root, 'ht.csv', 'lims.xlsx')
        self.assertEqual(config, self.sources)
        outside = self.root.parent / 'outside-neft.csv'
        outside.write_text('x')
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        with self.assertRaisesRegex(ValueError, 'OUTSIDE'):
            build_config(self.root, '../outside-neft.csv', 'lims.xlsx')

    def test_ui_is_self_contained_and_static_result_is_labeled(self):
        html = render_console()
        self.assertIn('/api/history', html); self.assertIn('/api/evaluate', html)
        self.assertIn('historical_recommendation=false', html)
        self.assertNotIn('https://', html)
        package = evaluate_snapshot(self.service.history(ORIGIN), demo_state())
        card = render_result(package)
        self.assertIn('Не историческая рекомендация', card)
        self.assertIn('industrial_command=false', card)


if __name__ == '__main__':
    unittest.main()
