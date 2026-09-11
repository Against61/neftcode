#!/usr/bin/env python3
"""Verify a completed action-pipeline package without rerunning calibration."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


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
    parser.add_argument("--expected-stage")
    args = parser.parse_args()
    run = args.run.resolve()
    manifest = json.loads((run / "manifest.json").read_text())
    result = json.loads((run / "pipeline.json").read_text())
    hashes = {name: (run / name).is_file() and digest(run / name) == expected
              for name, expected in manifest["artifacts_sha256"].items()}
    parser_links = Links()
    parser_links.feed((run / "report.html").read_text(encoding="utf-8"))
    local_links = [href.split("#", 1)[0] for href in parser_links.hrefs
                   if "://" not in href and not href.startswith("#")]
    missing = [href for href in local_links if not (run / href).is_file()]
    blocked = not result["calibration_started"]
    checks = {
        "manifest_completed": manifest["status"] == "completed",
        "expected_stage": (args.expected_stage is None or
                           result["stage"] == args.expected_stage),
        "artifact_hashes_match": all(hashes.values()),
        "intake_manifest_matches": digest(run / "intake/manifest.json") ==
                                  manifest["intake_manifest_sha256"],
        "report_links_resolve": not missing,
        "blocked_did_not_create_calibration": (not blocked or
                                                not (run / "calibration").exists()),
        "blocked_zero_fit": not blocked or result["model_fits"] == 0,
        "blocked_holdout_closed": not blocked or not result["holdout_numeric_read"],
        "sealed_2026_closed": not result["sealed_2026_numeric_read"],
    }
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    qa = {"schema": "action-pipeline-qa-v1", "source_run": str(run),
          "passed": all(checks.values()), "checks": checks,
          "artifact_hash_checks": hashes, "missing_links": missing}
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
