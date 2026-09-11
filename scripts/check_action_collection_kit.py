#!/usr/bin/env python3
"""Independent integrity and semantics checks for a generated collection kit."""
import argparse
import csv
import hashlib
import json
from pathlib import Path


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--expected-source-files", type=int)
    parser.add_argument("--expected-command-sources", type=int)
    parser.add_argument("--expected-dense-quality-sources", type=int)
    args = parser.parse_args()
    run = args.run.resolve()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    manifest = json.loads((run / "manifest.json").read_text())
    inventory = json.loads((run / "source_inventory.json").read_text())
    coverage = json.loads((run / "empty_coverage/coverage.json").read_text())
    hashes = {name: (run / name).is_file() and digest(run / name) == expected
              for name, expected in manifest["artifacts_sha256"].items()}
    with (run / "commands_template.csv").open(newline="", encoding="utf-8") as handle:
        command_header = next(csv.reader(handle))
    with (run / "quality_samples_template.csv").open(newline="", encoding="utf-8") as handle:
        quality_header = next(csv.reader(handle))
    with (run / "collection_plan.csv").open(newline="", encoding="utf-8") as handle:
        plan = list(csv.DictReader(handle))
    train = [row for row in plan if row["split"] == "train"]
    holdout = [row for row in plan if row["split"] == "holdout"]
    sampled_train = [row for row in train if row["sampling_mode"] ==
                     "baseline_plus_all_three_bins"]
    exact_gaps = all(
        item["command_shortfall"] == 20 and
        item["direction_shortfall"] == {"up": 5, "down": 5} and
        all(lag["pair_shortfall"] == 10 and lag["quarter_shortfall"] == 3
            for lag in item["bins"])
        for item in coverage["controls"].values())
    checks = {
        "manifest_completed": manifest["status"] == "completed",
        "artifact_hashes_match": all(hashes.values()),
        "child_manifest_matches": digest(run / "empty_coverage/manifest.json") ==
                                  manifest["empty_coverage_manifest_sha256"],
        "command_template_exact": command_header == config["command_schema"],
        "quality_template_exact": quality_header == config["quality_schema"],
        "plan_has_60_train_slots": len(train) == 60,
        "plan_has_30_holdout_slots": len(holdout) == 30,
        "holdout_is_historical_2025": all(
            row["quarter_requirement"] == "historical_2025_after_train_gate"
            for row in holdout),
        "plan_has_30_fully_sampled_train_actions": len(sampled_train) == 30,
        "expected_source_file_count": (args.expected_source_files is None or
                                       inventory["file_count"] == args.expected_source_files),
        "expected_command_source_count": (args.expected_command_sources is None or
            inventory["command_sources"] == args.expected_command_sources),
        "expected_dense_quality_source_count": (
            args.expected_dense_quality_sources is None or
            inventory["denser_independent_quality_sources"] ==
            args.expected_dense_quality_sources),
        "empty_templates_have_exact_gaps": exact_gaps,
        "zero_fit_and_future_closed": (coverage["model_fits"] == 0 and
                                       not coverage["holdout_numeric_read"] and
                                       not coverage["sealed_2026_numeric_read"]),
    }
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    qa = {"schema": "action-collection-kit-qa-v1", "source_run": str(run),
          "passed": all(checks.values()), "checks": checks,
          "artifact_hash_checks": hashes}
    (output / "artifact_qa.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=2) + "\n")
    (output / "manifest.json").write_text(json.dumps({
        "source_manifest_sha256": digest(run / "manifest.json"),
        "artifact_qa_sha256": digest(output / "artifact_qa.json"),
        "passed": qa["passed"],
    }, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": qa["passed"], "checks": checks,
                      "output": str(output)}, ensure_ascii=False))
    return 0 if qa["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
