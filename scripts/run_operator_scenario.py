#!/usr/bin/env python3
"""Run one audited model scenario against one configured historical slice."""
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
from neft.operator_ui import render_result


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data-root', type=Path, required=True)
    ap.add_argument('--sources', type=Path, required=True)
    ap.add_argument('--input', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args(); out = args.output.resolve()
    if out.exists(): ap.error('Output already exists')
    out.mkdir(parents=True); started = time.monotonic()
    manifest = {'schema': 'operator-scenario-run-v1',
                'started_at': datetime.now(timezone.utc).isoformat(),
                'command': [sys.executable, *sys.argv], 'python': sys.version,
                'model_fits': 0, 'model_inference_calls': 0, 'industrial_command': False}
    try:
        sources = strict_loads(args.sources.read_text()); body = strict_loads(args.input.read_text())
        allowed = {'as_of', 'scenario', 'use_lims_upper_bound', 'use_historical_feed_t95'}
        if not isinstance(body, dict) or set(body) - allowed or 'scenario' not in body:
            raise ValueError('EVALUATE_ARGUMENTS_SCHEMA')
        archive = HistoryArchive(args.data_root, sources, [body.get('as_of')])
        snapshot = archive.snapshot(body['as_of'], use_lims_upper_bound=body.get('use_lims_upper_bound', False))
        package = evaluate_snapshot(snapshot, body['scenario'],
                                    use_historical_feed_t95=body.get('use_historical_feed_t95', False))
        archive.verify()
        save(out / 'history.json', snapshot); save(out / 'request.json', package['request'])
        save(out / 'decision.json', package['decision']); save(out / 'scenario.json', package)
        (out / 'report.html').write_text(render_result(package))
        manifest.update(status='completed', returncode=0, source_config_sha256=package['history_snapshot_id'])
    except (ValueError, TypeError, KeyError, OSError, RecursionError) as exc:
        manifest.update(status='failed', returncode=1, error=str(exc))
        save(out / 'error.json', {'status': 'TOOL_ERROR', 'reasons': [str(exc)],
                                  'recommendation': None, 'industrial_command': False})
    manifest['seconds'] = time.monotonic() - started
    manifest['artifacts_sha256'] = {p.name: sha(p) for p in sorted(out.iterdir()) if p.is_file()}
    save(out / 'manifest.json', manifest)
    print(json.dumps({'status': manifest['status'], 'output': str(out)}))
    return manifest['returncode']


if __name__ == '__main__':
    raise SystemExit(main())
