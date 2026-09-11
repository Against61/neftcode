#!/usr/bin/env python3
"""One command: discover a command source, then run the strict calibration gate."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from neft.action_pipeline import (final_result, resolve_registered_path,
                                  select_command_source)
from neft.action_pipeline_report import render


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                     allow_nan=False) + "\n", encoding="utf-8")


def run_step(command, stdout_path, stderr_path):
    with stdout_path.open("w", encoding="utf-8") as stdout, \
            stderr_path.open("w", encoding="utf-8") as stderr:
        return subprocess.run(command, cwd=ROOT, stdout=stdout, stderr=stderr,
                              timeout=180, check=False).returncode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--quality-source", type=Path, required=True)
    parser.add_argument("--pac-metadata", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--config", type=Path,
                        default=ROOT / "configs/action_pipeline_v1.json")
    args = parser.parse_args()
    for name, path, kind in (("data root", args.data_root, "directory"),
                             ("quality source", args.quality_source, "file"),
                             ("PAC metadata", args.pac_metadata, "file")):
        if path is not None and ((kind == "directory" and not path.is_dir()) or
                                 (kind == "file" and not path.is_file())):
            raise SystemExit(name + " does not exist: " + str(path))
    output = (args.output or ROOT / "output/action-pipeline" /
              datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")).resolve()
    if output.exists():
        raise SystemExit("output already exists: " + str(output))
    output.mkdir(parents=True)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    intake_config = resolve_registered_path(ROOT, config["intake_config"])
    action_config = resolve_registered_path(ROOT, config["action_config"])
    steps = []
    intake_command = [
        sys.executable, str(ROOT / "scripts/check_action_data_readiness.py"),
        "--data-root", str(args.data_root.resolve()),
        "--lims", str(args.quality_source.resolve()),
        "--output", str(output / "intake"),
        "--config", str(intake_config), "--action-config", str(action_config),
    ]
    code = run_step(intake_command, output / "intake.stdout.txt",
                    output / "intake.stderr.txt")
    steps.append({"name": "intake", "returncode": code, "command": intake_command})
    if code:
        save(output / "pipeline.json", {"schema": "action-pipeline-result-v1",
             "status": "failed", "stage": "INTAKE_INFRASTRUCTURE_FAILURE"})
        return 1
    inventory = json.loads((output / "intake/inventory.json").read_text())
    intake_readiness = json.loads((output / "intake/readiness.json").read_text())
    gate = select_command_source(inventory, config)
    action_decision = None
    if gate["ready"]:
        action_command = [
            sys.executable, str(ROOT / "scripts/run_action_outcome.py"),
            "--command-log", gate["command_source"],
            "--quality-source", str(args.quality_source.resolve()),
            "--config", str(action_config), "--output", str(output / "calibration"),
        ]
        if args.pac_metadata:
            action_command += ["--pac-metadata", str(args.pac_metadata.resolve())]
        code = run_step(action_command, output / "calibration.stdout.txt",
                        output / "calibration.stderr.txt")
        steps.append({"name": "calibration", "returncode": code,
                      "command": action_command})
        if code:
            save(output / "pipeline.json", {"schema": "action-pipeline-result-v1",
                 "status": "failed", "stage": "CALIBRATION_INFRASTRUCTURE_FAILURE"})
            return 1
        action_decision = json.loads((output / "calibration/decision.json").read_text())
    result = final_result(gate, intake_readiness, action_decision)
    save(output / "pipeline.json", result)
    (output / "report.html").write_text(render(result), encoding="utf-8")
    artifacts = ["pipeline.json", "report.html", "intake.stdout.txt",
                 "intake.stderr.txt"]
    if gate["ready"]:
        artifacts += ["calibration.stdout.txt", "calibration.stderr.txt"]
    manifest = {
        "schema": "action-pipeline-manifest-v1", "status": "completed",
        "created_at": datetime.now(timezone.utc).isoformat(), "steps": steps,
        "pipeline_config_sha256": digest(args.config),
        "intake_manifest_sha256": digest(output / "intake/manifest.json"),
        "calibration_manifest_sha256": (digest(output / "calibration/manifest.json")
                                        if gate["ready"] else None),
        "artifacts_sha256": {name: digest(output / name) for name in artifacts},
    }
    save(output / "manifest.json", manifest)
    print(json.dumps({"status": result["status"], "stage": result["stage"],
                      "decision": result["decision"], "output": str(output)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
