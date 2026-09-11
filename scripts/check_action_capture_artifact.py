#!/usr/bin/env python3
"""Independent semantic and hash checks for an action-capture protocol run."""
import argparse
import csv
import hashlib
import json
from pathlib import Path


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def json_digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def journal_valid(path):
    previous = None
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        claimed = record.pop("record_sha256", None)
        if record.get("previous_sha256") != previous or json_digest(record) != claimed:
            return False, count
        previous = claimed
        count += 1
    return True, count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run = args.run.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((run / "manifest.json").read_text())
    protocol = json.loads((run / "protocol.json").read_text())
    status = json.loads((run / "store/status.json").read_text())
    chain, event_count = journal_valid(run / "store/events.jsonl")
    with (run / "store/commands.csv").open(newline="", encoding="utf-8") as handle:
        command_rows = list(csv.DictReader(handle))
    with (run / "store/quality_samples.csv").open(newline="", encoding="utf-8") as handle:
        quality_rows = list(csv.DictReader(handle))
    report = (run / "report.html").read_text(encoding="utf-8")
    hashes = manifest.get("artifacts_sha256", {})
    checks = {
        "manifest_completed": manifest.get("status") == "completed",
        "all_artifact_hashes_match": bool(hashes) and all(
            (run / name).is_file() and digest(run / name) == value
            for name, value in hashes.items()),
        "synthetic_fixture_is_explicit": protocol.get("fixture") ==
                                        "SYNTHETIC_PROTOCOL_TEST_NOT_PRODUCTION_DATA",
        "protocol_checks_pass": protocol.get("passed") is True and
                                all(protocol.get("checks", {}).values()),
        "journal_hash_chain_valid": chain and event_count == 3,
        "canonical_rows_match": len(command_rows) == 1 and len(quality_rows) == 1,
        "status_hashes_match": status.get("sha256", {}).get("journal") ==
                               digest(run / "store/events.jsonl") and
                               status.get("sha256", {}).get("commands") ==
                               digest(run / "store/commands.csv") and
                               status.get("sha256", {}).get("quality_samples") ==
                               digest(run / "store/quality_samples.csv"),
        "coverage_is_zero_fit_and_future_closed":
            status["coverage"]["model_fits"] == 0 and
            not status["coverage"]["holdout_numeric_read"] and
            not status["coverage"]["sealed_2026_numeric_read"],
        "no_industrial_command": status.get("industrial_command") is False and
                                 status.get("system_initiates_commands") is False,
        "unknown_availability_preserved": quality_rows[0]["available_time"] == "" and
                                          status["coverage"]["audit"]["quality"]
                                          ["unknown_available_time"] == 1,
        "report_labels_scope": "SYNTHETIC_PROTOCOL_TEST_NOT_PRODUCTION_DATA" in report and
                               "COLLECTION_INCOMPLETE" in report,
    }
    result = {"schema": "action-capture-artifact-qa-v1",
              "passed": all(checks.values()), "checks": checks,
              "run": str(run)}
    (output / "artifact_qa.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
