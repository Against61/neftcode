#!/usr/bin/env python3
"""Verify hashes, links and fail-closed properties of an intake package."""
import argparse
from html.parser import HTMLParser
import json
from pathlib import Path

from check_action_data_readiness import sha256


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-status")
    args = parser.parse_args()
    run = args.run.resolve()
    manifest = json.loads((run / "manifest.json").read_text())
    inventory = json.loads((run / "inventory.json").read_text())
    readiness = json.loads((run / "readiness.json").read_text())
    lims = json.loads((run / "lims_profile.json").read_text())
    hash_checks = {name: (run / name).is_file() and sha256(run / name) == digest
                   for name, digest in manifest["artifacts_sha256"].items()}
    links = Links()
    links.feed((run / "report.html").read_text(encoding="utf-8"))
    local_links = [href.split("#", 1)[0] for href in links.hrefs
                   if "://" not in href and not href.startswith("#")]
    missing_links = [href for href in local_links if not (run / href).is_file()]
    checks = {
        "manifest_completed": manifest["status"] == "completed",
        "all_artifact_hashes_match": all(hash_checks.values()),
        "expected_status": (args.expected_status is None or
                            readiness["status"] == args.expected_status),
        "inventory_status_matches": readiness["status"] == inventory["status"],
        "headers_only_discovery": all(item["content_decoded"] == "headers_only"
                                      for item in inventory["files"]),
        "telemetry_not_promoted": all(
            item["classification"] != "exact_command_schema"
            for item in inventory["files"]
            if item["relative_path"] in {"242000_tags.csv", "avt_tags.csv"}),
        "zero_model_fits": readiness["model_fits"] == 0,
        "holdout_not_read": not readiness["holdout_numeric_read"],
        "sealed_not_read": not readiness["sealed_2026_numeric_read"],
        "lims_stopped_before_holdout_numbers": (
            lims is None or (not lims["holdout_numeric_read"] and
                             not lims["sealed_2026_numeric_read"] and
                             not lims["adapter_audit"]["later_numeric_values_parsed"])),
        "all_report_links_resolve": not missing_links,
    }
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    qa = {"schema": "action-data-intake-qa-v1", "source_run": str(run),
          "checks": checks, "passed": all(checks.values()),
          "artifact_hash_checks": hash_checks, "missing_links": missing_links}
    (output / "artifact_qa.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=2) + "\n")
    (output / "manifest.json").write_text(json.dumps({
        "source_run": str(run), "source_manifest_sha256": sha256(run / "manifest.json"),
        "artifact_qa_sha256": sha256(output / "artifact_qa.json"),
        "passed": qa["passed"],
    }, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": qa["passed"], "checks": checks,
                      "output": str(output)}, ensure_ascii=False))
    return 0 if qa["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
