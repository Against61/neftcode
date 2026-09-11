#!/usr/bin/env python3
"""Import only registered 2023-2024 LIMS samples into an action-capture store."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from neft.action_capture import ActionCaptureStore
from neft.action_capture_lims import quality_events
from neft.action_capture_lims_report import render
from neft.action_outcome import read_quality_xlsx


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
    parser.add_argument("--lims", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed-config", type=Path,
                        default=ROOT / "configs/action_capture_lims_seed_v1.json")
    parser.add_argument("--capture-config", type=Path,
                        default=ROOT / "configs/action_capture_v1.json")
    parser.add_argument("--action-config", type=Path,
                        default=ROOT / "configs/action_outcome_v1.json")
    parser.add_argument("--collection-config", type=Path,
                        default=ROOT / "configs/action_collection_v1.json")
    args = parser.parse_args()
    source = args.lims.resolve()
    if not source.is_file():
        parser.error("LIMS source does not exist")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    seed = load(args.seed_config)
    capture = load(args.capture_config)
    action = load(args.action_config)
    collection = load(args.collection_config)
    source_before = {"sha256": digest(source), "size_bytes": source.stat().st_size,
                     "mtime_ns": source.stat().st_mtime_ns}
    rows, audit = read_quality_xlsx(source, action, **seed["train"])
    events = quality_events(rows, audit, seed)
    if len(events) != audit["valid_unique"]:
        raise RuntimeError("GENERATED_EVENT_COUNT_MISMATCH")
    store = ActionCaptureStore(args.store, capture, action, collection)
    before_status = store.status()
    batch = store.record_many(events)
    replay = store.record_many(events)
    status = store.status()
    source_after = {"sha256": digest(source), "size_bytes": source.stat().st_size,
                    "mtime_ns": source.stat().st_mtime_ns}
    checks = {
        "source_unchanged": source_after == source_before,
        "event_count_matches_reader": len(events) == audit["valid_unique"],
        "later_numeric_values_not_parsed": audit["later_numeric_values_parsed"] is False,
        "stopped_at_or_after_holdout_start": audit["stopping_timestamp_only"] is not None and
            audit["stopping_timestamp_only"][:10] >= seed["train"]["end_exclusive"],
        "replay_added_zero": replay["new_events"] == 0,
        "replay_all_idempotent": replay["idempotent_events"] == len(events),
        "availability_unknown": all("available_time" not in value for _, value in events),
        "online_feature_rows_zero": status["coverage"]["audit"]["quality"]
                                    ["online_feature_rows"] == 0,
        "commands_unchanged": before_status["complete_commands"] ==
                              status["complete_commands"],
        "collection_incomplete": status["coverage"]["status"] ==
                                 "COLLECTION_INCOMPLETE",
        "zero_fit_and_future_closed": status["coverage"]["model_fits"] == 0 and
            not status["coverage"]["holdout_numeric_read"] and
            not status["coverage"]["sealed_2026_numeric_read"],
    }
    summary = {
        "schema": "action-capture-lims-seed-result-v1",
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks, "source_audit": audit,
        "batch": {key: batch[key] for key in ("batch_size", "new_events",
                                               "idempotent_events")},
        "replay": {key: replay[key] for key in ("batch_size", "new_events",
                                                 "idempotent_events")},
        "capture": status,
    }
    save(output / "import.json", summary)
    shutil.copyfile(store.status_path, output / "status_snapshot.json")
    (output / "report.html").write_text(render(summary), encoding="utf-8")
    snapshot = output / "store_snapshot"
    snapshot.mkdir()
    for source_path in (store.journal, store.commands, store.quality,
                        store.status_path):
        shutil.copyfile(source_path, snapshot / source_path.name)
    artifacts = ["import.json", "status_snapshot.json", "report.html",
                 "store_snapshot/events.jsonl", "store_snapshot/commands.csv",
                 "store_snapshot/quality_samples.csv", "store_snapshot/status.json"]
    manifest = {
        "schema": "action-capture-lims-seed-manifest-v1",
        "status": "completed" if all(checks.values()) else "failed",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source), "source_sha256": source_before["sha256"],
        "model_fits": 0, "holdout_numeric_read": False,
        "sealed_2026_numeric_read": False,
        "config_sha256": {str(path.name): digest(path) for path in
                          (args.seed_config, args.capture_config, args.action_config,
                           args.collection_config)},
        "code_sha256": {
            "import_lims_to_action_capture.py": digest(Path(__file__)),
            "neft/action_capture.py": digest(ROOT / "neft/action_capture.py"),
            "neft/action_capture_lims.py": digest(ROOT / "neft/action_capture_lims.py"),
            "neft/action_capture_lims_report.py": digest(
                ROOT / "neft/action_capture_lims_report.py"),
        },
        "store_sha256": status["sha256"],
        "artifacts_sha256": {name: digest(output / name) for name in artifacts},
    }
    save(output / "manifest.json", manifest)
    print(json.dumps({"status": summary["status"], "valid_unique": len(events),
                      "new_events": batch["new_events"],
                      "replay_new_events": replay["new_events"],
                      "output": str(output)}, ensure_ascii=False))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
