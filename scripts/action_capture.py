#!/usr/bin/env python3
"""Initialize, write to, or inspect an action-capture store."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from neft.action_capture import ActionCaptureStore
from neft.agent_tool import strict_loads


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--capture-config", type=Path,
                        default=ROOT / "configs/action_capture_v1.json")
    parser.add_argument("--action-config", type=Path,
                        default=ROOT / "configs/action_outcome_v1.json")
    parser.add_argument("--collection-config", type=Path,
                        default=ROOT / "configs/action_collection_v1.json")
    sub = parser.add_subparsers(dest="operation", required=True)
    sub.add_parser("init")
    sub.add_parser("status")
    record = sub.add_parser("record")
    record.add_argument("--type", choices=["command_issued", "command_terminal",
                                           "quality_sample"], required=True)
    record.add_argument("--input", type=Path,
                        help="JSON file; omit to read one JSON object from stdin")
    args = parser.parse_args()
    store = ActionCaptureStore(args.store, load(args.capture_config),
                               load(args.action_config), load(args.collection_config))
    if args.operation == "record":
        raw = (args.input.read_text(encoding="utf-8") if args.input
               else sys.stdin.read())
        result = store.record(args.type, strict_loads(raw))
    else:
        result = store.status()
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
