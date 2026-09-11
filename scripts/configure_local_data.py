#!/usr/bin/env python3
"""Create a local, hash-pinned history source config without copying data."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from neft.history_adapter import sha


def inside(root, path):
    resolved = path.resolve()
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise ValueError('SOURCE_OUTSIDE_DATA_ROOT_OR_MISSING: ' + str(path))
    return resolved


def choose(root, explicit, candidates, label):
    if explicit:
        return inside(root, root / explicit)
    found = [inside(root, path) for path in candidates if path.is_file() and not path.name.startswith('~$')]
    unique = sorted(set(found))
    if len(unique) != 1:
        raise ValueError(label + '_SOURCE_NOT_UNIQUE: ' + ', '.join(str(p.relative_to(root)) for p in unique))
    return unique[0]


def build_config(data_root, telemetry=None, lims=None):
    root = Path(data_root).resolve()
    if not root.is_dir():
        raise ValueError('DATA_ROOT_NOT_DIRECTORY')
    telemetry_path = choose(root, telemetry,
                            [root / 'data/242000_tags.csv', root / '242000_tags.csv'], 'TELEMETRY')
    lims_path = choose(root, lims, list(root.glob('ЛИМС*.xlsx')) + list((root / 'data').glob('ЛИМС*.xlsx')), 'LIMS')
    return {'schema': 'history-sources-v1',
            'telemetry': {'path': str(telemetry_path.relative_to(root)), 'sha256': sha(telemetry_path)},
            'lims': {'path': str(lims_path.relative_to(root)), 'sha256': sha(lims_path)}}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data-root', type=Path, required=True)
    ap.add_argument('--telemetry', help='Relative path inside data root; autodetected when omitted')
    ap.add_argument('--lims', help='Relative path inside data root; autodetected when omitted')
    ap.add_argument('--output', type=Path, default=ROOT / 'history_sources.local.json')
    args = ap.parse_args()
    out = args.output.resolve()
    if out.exists():
        ap.error('Output already exists; remove it explicitly or choose another path')
    try:
        config = build_config(args.data_root, args.telemetry, args.lims)
    except ValueError as exc:
        ap.error(str(exc))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': 'configured', 'output': str(out),
                      'data_root': str(args.data_root.resolve()), 'sources': config}, ensure_ascii=False))


if __name__ == '__main__':
    main()
