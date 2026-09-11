#!/usr/bin/env python3
"""Check local data and the isolated console runtime before startup."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from neft.doctor import run_doctor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--history-sources', type=Path, required=True)
    parser.add_argument('--as-of', help='Strict historical probe time; defaults to a registered demo slice')
    parser.add_argument('--output', type=Path, help='Optional JSON report path')
    args = parser.parse_args()
    report = run_doctor(
        args.data_root, args.history_sources, as_of=args.as_of)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if args.output:
        target = args.output.resolve()
        if target.exists():
            parser.error('Output already exists')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered)
    print(rendered, end='')
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
