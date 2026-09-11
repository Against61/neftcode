"""Real stdio exchange on synthetic archive fixtures; no LLM or plant data."""
import argparse
import asyncio
from datetime import timedelta
import json
from pathlib import Path
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from neft.history_adapter import HistoryArchive, sha
from neft.history_tool import HISTORY
from neft.operator_console import demo_state, evaluate_snapshot
from neft.operator_tool import SCENARIO
from test_history_adapter import fixture, ORIGIN


def save(path, value): path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


async def check(out):
    out.mkdir(parents=True, exist_ok=False)
    data = out / 'fixtures'; data.mkdir()
    sources = fixture(data)
    save(out / 'sources.json', sources)
    archive = HistoryArchive(data, sources, [ORIGIN])
    params = StdioServerParameters(command=sys.executable, args=['-B', str(ROOT / 'scripts/serve_agent_tool.py'),
        '--audit-root', str(out / 'calls'), '--data-root', str(data), '--history-sources', str(out / 'sources.json')])
    cases = []
    with (out / 'server.stderr.txt').open('w') as stderr:
        async with stdio_client(params, errlog=stderr) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as client:
                await client.initialize()
                listing = await client.list_tools()
                assert {t.name for t in listing.tools} == {'get_refinery_contract', 'recommend_refinery_plan', HISTORY, SCENARIO}
                save(out / 'discovery.json', listing.model_dump(mode='json'))
                for upper in (False, True):
                    arguments = {'as_of': ORIGIN, 'use_lims_upper_bound': upper}
                    result = await client.call_tool(HISTORY, arguments)
                    assert not result.isError
                    output = result.structuredContent
                    actual = json.loads(Path(output['artifacts']['history_json']).read_text())
                    expected = json.loads(json.dumps(archive.snapshot(ORIGIN, use_lims_upper_bound=upper)))
                    assert actual == expected
                    assert output['request'] == expected['request'] and output['recommendation'] is None
                    cases.append({'upper_bound': upper, 'status': output['status'], 'exact_direct_match': True})
                    save(out / ('upper.json' if upper else 'strict.json'), result.model_dump(mode='json'))
                for name, args in [('path-override', {'as_of': ORIGIN, 'data_root': '/'}),
                                   ('protected-period', {'as_of': '2026-01-01'}),
                                   ('cap-override', {'as_of': ORIGIN, 'scenario_parameters': {'sulfur_limit': 30}}),
                                   ('invalid-boolean', {'as_of': ORIGIN, 'use_lims_upper_bound': 'true'})]:
                    result = await client.call_tool(HISTORY, args)
                    assert result.isError and result.structuredContent['recommendation'] is None
                    cases.append({'case': name, 'status': result.structuredContent['status']})
                    save(out / (name + '.json'), result.model_dump(mode='json'))
                state = demo_state()
                for name, arguments in [
                    ('scenario-manual', {'as_of': ORIGIN, 'scenario': state}),
                    ('scenario-lims-t95', {'as_of': ORIGIN, 'use_lims_upper_bound': True,
                                            'use_historical_feed_t95': True,
                                            'scenario': {k: v for k, v in state.items() if k != 'feed_t95'}})]:
                    result = await client.call_tool(SCENARIO, arguments)
                    assert not result.isError
                    output = result.structuredContent
                    expected_snapshot = archive.snapshot(ORIGIN, use_lims_upper_bound=arguments.get('use_lims_upper_bound', False))
                    expected = evaluate_snapshot(expected_snapshot, arguments['scenario'],
                                                 use_historical_feed_t95=arguments.get('use_historical_feed_t95', False))
                    expected = json.loads(json.dumps(expected))
                    saved = json.loads(Path(output['artifacts']['scenario_json']).read_text())
                    assert saved == expected and output['recommendation'] == expected['recommendation']
                    assert output['historical_recommendation'] is False
                    cases.append({'case': name, 'status': output['status'], 'exact_direct_match': True})
                    save(out / (name + '.json'), result.model_dump(mode='json'))
                for name, arguments in [
                    ('scenario-missing', {'as_of': ORIGIN, 'scenario': {}}),
                    ('scenario-binding-without-upper', {'as_of': ORIGIN, 'use_historical_feed_t95': True,
                                                        'scenario': {k: v for k, v in state.items() if k != 'feed_t95'}})]:
                    result = await client.call_tool(SCENARIO, arguments)
                    assert result.isError and result.structuredContent['recommendation'] is None
                    cases.append({'case': name, 'status': result.structuredContent['status']})
                    save(out / (name + '.json'), result.model_dump(mode='json'))
    verified = 0
    for path in (out / 'calls').glob('*/manifest.json'):
        for filename, expected in json.loads(path.read_text())['artifacts_sha256'].items():
            assert sha(path.parent / filename) == expected
            verified += 1
    report = {'status': 'passed', 'cases': cases, 'audit_hashes_verified': verified,
              'real_stdio_client': True, 'language_model_used': False, 'production_rows_read': 0}
    save(out / 'protocol_qa.json', report)
    print(json.dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, required=True)
    asyncio.run(check(parser.parse_args().output.resolve()))
