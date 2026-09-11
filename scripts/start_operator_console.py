#!/usr/bin/env python3
"""Configure, diagnose and start the local console with one command."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from neft.doctor import run_doctor
from scripts.configure_local_data import build_config


def prepare(data_root, sources_path, telemetry=None, lims=None, *, as_of=None):
    sources_path = Path(sources_path).resolve()
    created = False
    if not sources_path.exists():
        config = build_config(data_root, telemetry, lims)
        sources_path.parent.mkdir(parents=True, exist_ok=True)
        sources_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
        created = True
    report = run_doctor(data_root, sources_path, as_of=as_of)
    return sources_path, created, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--history-sources', type=Path,
                        default=ROOT / 'history_sources.local.json')
    parser.add_argument('--telemetry', help='Relative telemetry path for first-time configuration')
    parser.add_argument('--lims', help='Relative LIMS path for first-time configuration')
    parser.add_argument('--as-of', help='Doctor probe time')
    parser.add_argument('--audit-root', type=Path, default=ROOT / 'operator-calls')
    parser.add_argument('--host', choices=['127.0.0.1', 'localhost', '::1'], default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    try:
        sources, created, report = prepare(
            args.data_root, args.history_sources, args.telemetry, args.lims,
            as_of=args.as_of)
    except (ValueError, TypeError, OSError) as exc:
        parser.error(str(exc))
    report['source_config_created'] = created
    report['source_config'] = str(sources)
    print(json.dumps(report, ensure_ascii=False, allow_nan=False), flush=True)
    if report['status'] != 'passed':
        return 1
    if args.check_only:
        return 0
    command = [
        sys.executable, str(ROOT / 'scripts/serve_operator_console.py'),
        '--data-root', str(args.data_root.resolve()),
        '--history-sources', str(sources),
        '--audit-root', str(args.audit_root.resolve()),
        '--host', args.host, '--port', str(args.port),
    ]
    os.execv(sys.executable, command)


if __name__ == '__main__':
    raise SystemExit(main())
