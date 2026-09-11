"""Read an explicit local archive snapshot and pass eligible data to the Python cycle."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import zipfile
from xml.etree import ElementTree as ET
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from neft.agent_tool import strict_loads
from neft.history_adapter import HistoryArchive, sha
from neft.history_report import render_history


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data-root', type=Path, required=True)
    ap.add_argument('--sources', type=Path, required=True)
    ap.add_argument('--as-of', required=True)
    ap.add_argument('--use-lims-upper-bound', action='store_true')
    ap.add_argument('--scenario', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    out = args.output.resolve()
    if out.exists(): ap.error('Output already exists; choose a new directory')
    out.mkdir(parents=True)
    start = time.monotonic()
    manifest = {'schema': 'history-adapter-run-v1', 'started_at': datetime.now(timezone.utc).isoformat(),
                'command': [sys.executable, *sys.argv], 'python': sys.version,
                'model_fits': 0, 'model_inference_calls': 0, 'industrial_command': False}
    files = [ROOT / 'neft' / f for f in ('history_adapter.py', 'history_report.py', 'decision_cycle.py',
             'cycle_state.py', 'expert_contracts.py', 'cycle_gate.py', 'dynamic_planner.py', 'scenario_sensitivity.py', 'cycle_report.py', 'agent_tool.py')]
    files += [ROOT / 'configs' / (n + '_v1.json')
             for n in ('history_adapter', 'process_scenario', 'scenario_sensitivity', 'dynamic_blending', 'python_cycle', 'agent_tool')]
    files.append(Path(__file__).resolve())
    manifest['code_sha256'] = {str(p.relative_to(ROOT)): sha(p) for p in files}
    try:
        sources = strict_loads(args.sources.read_text())
        params = strict_loads(args.scenario.read_text()) if args.scenario else None
        archive = HistoryArchive(args.data_root, sources, [args.as_of])
        snapshot = archive.snapshot(args.as_of, use_lims_upper_bound=args.use_lims_upper_bound, scenario_parameters=params)
        archive.verify()
        if manifest['code_sha256'] != {str(p.relative_to(ROOT)): sha(p) for p in files}:
            raise ValueError('CODE_CHANGED_DURING_READ')
        save(out / 'history.json', snapshot); save(out / 'request.json', snapshot['request'])
        save(out / 'decision.json', snapshot['decision'])
        (out / 'report.html').write_text(render_history(snapshot))
        manifest.update(status='completed', sources=sources, read_audit=snapshot['read_audit'], returncode=0)
    except (ValueError, OSError, KeyError, EOFError, TypeError, StopIteration, zipfile.BadZipFile, ET.ParseError) as exc:
        manifest.update(status='failed', error=str(exc), returncode=1)
        save(out / 'error.json', {'status': 'ADAPTER_ERROR', 'reasons': [str(exc)], 'recommendation': None})
    manifest['seconds'] = time.monotonic() - start
    manifest['artifacts_sha256'] = {p.name: sha(p) for p in sorted(out.iterdir()) if p.is_file()}
    save(out / 'manifest.json', manifest)
    print(json.dumps({'status': manifest['status'], 'output': str(out)}))
    return manifest['returncode']


if __name__ == '__main__': raise SystemExit(main())
