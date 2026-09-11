#!/usr/bin/env python3
"""Create a synthetic, auditable smoke artifact for action capture."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from neft.action_capture import ActionCaptureStore
from neft.action_capture_report import render


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                     allow_nan=False) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture-config", type=Path,
                        default=ROOT / "configs/action_capture_v1.json")
    parser.add_argument("--action-config", type=Path,
                        default=ROOT / "configs/action_outcome_v1.json")
    parser.add_argument("--collection-config", type=Path,
                        default=ROOT / "configs/action_collection_v1.json")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    capture = load(args.capture_config)
    action = load(args.action_config)
    collection = load(args.collection_config)
    store = ActionCaptureStore(output / "store", capture, action, collection)
    issued = {"event_id": "synthetic-issue", "command_id": "synthetic-command",
              "control": "P8", "issued_time": "2024-03-15T10:00:00",
              "value_before": 300.0, "unit": "degC",
              "source_system": "SYNTHETIC_PROTOCOL_FIXTURE"}
    terminal = {"event_id": "synthetic-terminal",
                "command_id": "synthetic-command", "status": "executed",
                "executed_time": "2024-03-15T10:05:00", "value_after": 302.0,
                "source_system": "SYNTHETIC_PROTOCOL_FIXTURE"}
    quality = {"event_id": "synthetic-quality", "sample_id": "synthetic-sample",
               "sample_time": "2024-03-15T10:35:00", "value_numeric": 8.4,
               "unit": "mg/kg", "quality_status": "valid",
               "source_system": "SYNTHETIC_PROTOCOL_FIXTURE",
               "source_record_id": "synthetic-record"}
    store.record("command_issued", issued)
    store.record("command_terminal", terminal)
    store.record("quality_sample", quality)
    replay = store.record("quality_sample", quality)
    before_conflict = store.status()["sha256"]
    conflict = dict(quality, value_numeric=9.9)
    conflict_reason = None
    try:
        store.record("quality_sample", conflict)
    except ValueError as exc:
        conflict_reason = str(exc)
    restarted = ActionCaptureStore(output / "store", capture, action, collection).status()
    checks = {
        "three_events": restarted["events"] == 3,
        "one_complete_command": restarted["complete_commands"] == 1,
        "one_quality_sample": restarted["quality_samples"] == 1,
        "exact_replay_idempotent": replay["idempotent"] is True,
        "conflict_rejected": conflict_reason == "EVENT_ID_CONFLICT",
        "conflict_changed_nothing": restarted["sha256"] == before_conflict,
        "restart_verified_hash_chain": restarted["status"] == "operational",
        "coverage_still_incomplete": restarted["coverage"]["status"] ==
                                     "COLLECTION_INCOMPLETE",
        "zero_fit": restarted["coverage"]["model_fits"] == 0,
        "future_closed": not restarted["coverage"]["holdout_numeric_read"] and
                         not restarted["coverage"]["sealed_2026_numeric_read"],
        "unknown_availability_preserved": restarted["coverage"]["audit"]["quality"]
                                          ["unknown_available_time"] == 1,
        "no_industrial_command": restarted["industrial_command"] is False,
    }
    result = {"schema": "action-capture-protocol-check-v1",
              "fixture": "SYNTHETIC_PROTOCOL_TEST_NOT_PRODUCTION_DATA",
              "passed": all(checks.values()), "checks": checks,
              "conflict_reason": conflict_reason}
    save(output / "protocol.json", result)
    (output / "report.html").write_text(render(result, restarted), encoding="utf-8")
    artifacts = ["protocol.json", "store/events.jsonl", "store/commands.csv",
                 "store/quality_samples.csv", "store/status.json", "report.html"]
    manifest = {"schema": "action-capture-protocol-manifest-v1",
                "status": "completed" if result["passed"] else "failed",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "fixture": result["fixture"], "model_fits": 0,
                "artifacts_sha256": {name: digest(output / name) for name in artifacts}}
    save(output / "manifest.json", manifest)
    print(json.dumps({"passed": result["passed"], "checks": checks,
                      "output": str(output)}, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
