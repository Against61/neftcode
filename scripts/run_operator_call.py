#!/usr/bin/env python3
"""Execute one operator-console operation inside an isolated worker."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from neft.agent_tool import strict_loads
from neft.history_adapter import HistoryArchive, sha
from neft.operator_console import evaluate_snapshot


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def execute(operation, data_root, sources, arguments):
    if operation == 'history':
        if not isinstance(arguments, dict) or set(arguments) - {'as_of', 'use_lims_upper_bound'}:
            raise ValueError('HISTORY_ARGUMENTS_SCHEMA')
        if type(arguments.get('use_lims_upper_bound', False)) is not bool:
            raise ValueError('UPPER_BOUND_FLAG_MUST_BE_BOOLEAN')
        archive = HistoryArchive(data_root, sources, [arguments.get('as_of')])
        snapshot = archive.snapshot(
            arguments.get('as_of'),
            use_lims_upper_bound=arguments.get('use_lims_upper_bound', False))
        archive.verify()
        return snapshot, {'history.json': snapshot,
                          'request.json': snapshot['request'],
                          'decision.json': snapshot['decision']}
    if operation == 'evaluate':
        allowed = {'as_of', 'scenario', 'use_lims_upper_bound', 'use_historical_feed_t95'}
        if (not isinstance(arguments, dict) or set(arguments) - allowed or
                'scenario' not in arguments):
            raise ValueError('EVALUATE_ARGUMENTS_SCHEMA')
        for flag in ('use_lims_upper_bound', 'use_historical_feed_t95'):
            if flag in arguments and type(arguments[flag]) is not bool:
                raise ValueError(flag.upper() + '_MUST_BE_BOOLEAN')
        archive = HistoryArchive(data_root, sources, [arguments.get('as_of')])
        snapshot = archive.snapshot(
            arguments.get('as_of'),
            use_lims_upper_bound=arguments.get('use_lims_upper_bound', False))
        package = evaluate_snapshot(
            snapshot, arguments['scenario'],
            use_historical_feed_t95=arguments.get('use_historical_feed_t95', False))
        archive.verify()
        return package, {
            'history.json': snapshot,
            'request.json': package['request'],
            'decision.json': package['decision'],
            'scenario.json': package,
        }
    raise ValueError('UNKNOWN_OPERATION')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operation', choices=['history', 'evaluate'], required=True)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output_dir = args.output.resolve()
    if output_dir.exists():
        parser.error('Output already exists')
    output_dir.mkdir(parents=True)
    started = time.monotonic()
    manifest = {
        'schema': 'operator-call-worker-v1',
        'started_at': datetime.now(timezone.utc).isoformat(),
        'operation': args.operation,
        'python': sys.version,
        'model_fits': 0,
        'model_inference_calls': 0,
    }
    try:
        sources = strict_loads(args.sources.read_text())
        arguments = strict_loads(args.input.read_text())
        output, artifacts = execute(args.operation, args.data_root, sources, arguments)
        for name, value in artifacts.items():
            save(output_dir / name, value)
        save(output_dir / 'output.json', output)
        manifest.update(status='completed', returncode=0)
    except (ValueError, TypeError, KeyError, OSError, RecursionError, json.JSONDecodeError) as exc:
        save(output_dir / 'error.json', {
            'status': 'TOOL_ERROR', 'reasons': [str(exc)],
            'recommendation': None, 'industrial_command': False,
        })
        manifest.update(status='failed', returncode=1, error=str(exc))
    manifest['seconds'] = time.monotonic() - started
    manifest['artifacts_sha256'] = {
        path.name: sha(path) for path in sorted(output_dir.iterdir()) if path.is_file()
    }
    save(output_dir / 'manifest.json', manifest)
    print(json.dumps({'status': manifest['status'], 'output': str(output_dir)}))
    return manifest['returncode']


if __name__ == '__main__':
    raise SystemExit(main())
