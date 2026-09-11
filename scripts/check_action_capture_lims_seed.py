#!/usr/bin/env python3
"""Independent QA for a train-only LIMS seed artifact and its store snapshot."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sqlite3


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def journal_valid(path):
    previous = None
    count = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            claimed = record.pop("record_sha256", None)
            raw = json.dumps(record, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
            if (record.get("previous_sha256") != previous or
                    hashlib.sha256(raw).hexdigest() != claimed):
                return False, count
            previous = claimed
            count += 1
    return True, count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference-sqlite", type=Path)
    args = parser.parse_args()
    run = args.run.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((run / "manifest.json").read_text())
    result = json.loads((run / "import.json").read_text())
    snapshot = json.loads((run / "status_snapshot.json").read_text())
    source = Path(manifest["source"])
    store = run / "store_snapshot"
    chain, journal_rows = journal_valid(store / "events.jsonl")
    with (store / "commands.csv").open(newline="", encoding="utf-8") as handle:
        command_rows = list(csv.DictReader(handle))
    with (store / "quality_samples.csv").open(newline="", encoding="utf-8") as handle:
        quality_rows = list(csv.DictReader(handle))
    artifact_hashes = manifest["artifacts_sha256"]
    store_hashes = manifest["store_sha256"]
    report = (run / "report.html").read_text(encoding="utf-8")
    checks = {
        "manifest_completed": manifest["status"] == "completed",
        "artifact_hashes_match": all(digest(run / name) == value
                                      for name, value in artifact_hashes.items()),
        "source_hash_still_matches": source.is_file() and
                                     digest(source) == manifest["source_sha256"],
        "registered_reader_passed": result["status"] == "passed" and
                                    all(result["checks"].values()),
        "exact_792_train_samples": result["source_audit"]["valid_unique"] == 792 and
                                   len(quality_rows) == 792,
        "holdout_stopped_before_numeric":
            result["source_audit"]["stopping_timestamp_only"] is not None and
            result["source_audit"]["later_numeric_values_parsed"] is False and
            all(row["sample_time"] < "2025-01-01" for row in quality_rows),
        "availability_unknown": all(row["available_time"] == "" for row in quality_rows) and
                                snapshot["coverage"]["audit"]["quality"]
                                ["unknown_available_time"] == 792,
        "no_online_features": snapshot["coverage"]["audit"]["quality"]
                              ["online_feature_rows"] == 0,
        "no_commands_or_fit": not command_rows and snapshot["complete_commands"] == 0 and
                              snapshot["coverage"]["model_fits"] == 0,
        "future_periods_closed": not snapshot["coverage"]["holdout_numeric_read"] and
                                 not snapshot["coverage"]["sealed_2026_numeric_read"],
        "batch_replay_idempotent": result["batch"]["new_events"] +
                                   result["batch"]["idempotent_events"] == 792 and
                                   result["replay"]["new_events"] == 0 and
                                   result["replay"]["idempotent_events"] == 792,
        "journal_chain_and_store_hashes": chain and journal_rows == 792 and
            digest(store / "events.jsonl") == store_hashes["journal"] and
            digest(store / "commands.csv") == store_hashes["commands"] and
            digest(store / "quality_samples.csv") == store_hashes["quality_samples"],
        "report_labels_limit": "observations без command log" in report and
                               "available_time` неизвестен" in report,
    }
    reference = None
    if args.reference_sqlite is not None:
        reference_path = args.reference_sqlite.resolve()
        query = """SELECT event_time,value_numeric,row FROM quality_observation
WHERE series_id='lims:ht:2:Mg.Sulfur:CQ'
AND event_time>='2023-01-01' AND event_time<'2025-01-01'
AND quality_status='valid' AND event_time IN (
  SELECT event_time FROM quality_observation
  WHERE series_id='lims:ht:2:Mg.Sulfur:CQ'
  AND event_time>='2023-01-01' AND event_time<'2025-01-01'
  GROUP BY event_time HAVING count(*)=1)
ORDER BY event_time,row"""
        with sqlite3.connect(reference_path.as_uri() + "?mode=ro", uri=True) as database:
            old = [(str(at).replace(" ", "T"), float(value), int(row))
                   for at, value, row in database.execute(query)]
        new = [(row["sample_time"], float(row["value_numeric"]),
                int(row["source_record_id"].split(":")[-2])) for row in quality_rows]
        reference = {"path": str(reference_path), "sha256": digest(reference_path),
                     "rows": len(old), "capture_rows": len(new),
                     "exact_timestamp_value_row_match": old == new}
        checks["reference_sqlite_exact_match"] = old == new and len(old) == 792
    qa = {"schema": "action-capture-lims-seed-qa-v1",
          "passed": all(checks.values()), "checks": checks,
          "run": str(run), "source_sha256": manifest["source_sha256"],
          "reference": reference}
    (output / "artifact_qa.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    print(json.dumps(qa, ensure_ascii=False))
    return 0 if qa["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
