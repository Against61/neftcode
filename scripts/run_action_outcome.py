"""Portable CLI for the P8/T11/F19 command-to-LIMS calibration adapter."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from neft.action_outcome import load_config, run_analysis
from neft.action_outcome_report import render
COMMAND_COLUMNS = [
    "command_id", "control", "issued_time", "executed_time", "value_before",
    "value_after", "unit", "status", "source_system",
]


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                               allow_nan=False) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--command-log", type=Path)
    parser.add_argument("--quality-source", type=Path)
    parser.add_argument("--pac-metadata", type=Path)
    parser.add_argument("--config", type=Path,
                        default=ROOT / "configs/action_outcome_v1.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    config = load_config(args.config)
    output = (args.output or ROOT / "output/action-outcome" /
              datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")).resolve()
    if output.exists():
        raise SystemExit("output already exists: " + str(output))
    for name, path in (("command log", args.command_log),
                       ("quality source", args.quality_source),
                       ("PAC metadata", args.pac_metadata)):
        if path is not None and not path.is_file():
            raise SystemExit(name + " does not exist: " + str(path))
    output.mkdir(parents=True)
    command_log = args.command_log.resolve() if args.command_log else None
    quality_source = args.quality_source.resolve() if args.quality_source else None
    pac_metadata = (json.loads(args.pac_metadata.read_text(encoding="utf-8"))
                    if args.pac_metadata else None)
    result = run_analysis(config, command_log=command_log,
                          quality_db=quality_source, pac_metadata=pac_metadata)
    sources = {}
    if command_log:
        sources["command_log"] = {
            "path": str(command_log), "role": "source_recorded_commands",
            "sha256": digest(command_log), "sha256_verified": True,
            "registered_as_command_log": True,
        }
    if quality_source:
        sources["quality"] = {
            "path": str(quality_source), "role": "independent_lims_quality",
            "sha256": digest(quality_source), "sha256_verified": True,
            "registered_as_command_log": False,
        }
    inventory = {
        "scope": "explicit CLI sources",
        "registered_sources": sources,
        "command_log_registered": command_log is not None,
        "command_log": sources.get("command_log"),
        "required_command_columns": COMMAND_COLUMNS,
        "telemetry_transition_substitution_allowed": False,
    }
    save(output / "config.json", config)
    save(output / "results.json", result)
    save(output / "decision.json", {
        "decision": result["decision"], "reasons": result["reasons"],
        "readiness": result["readiness"]["status"],
        "model_fits": result["model_fits"],
        "holdout_numeric_read": result["holdout_numeric_read"],
        "sealed_release_numeric_read": result["sealed_release_numeric_read"],
        "pac_used": result["pac_used"],
    })
    save(output / "source_inventory.json", inventory)
    save(output / "pac_gate.json", result["pac_gate"])
    (output / "report.html").write_text(render(result, config, inventory), encoding="utf-8")
    artifacts = ["config.json", "results.json", "decision.json",
                 "source_inventory.json", "pac_gate.json", "report.html"]
    save(output / "manifest.json", {
        "schema": "action-outcome-portable-run-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "completed", "decision": result["decision"],
        "config_sha256": digest(args.config),
        "sources_sha256": {name: item["sha256"] for name, item in sources.items()},
        "artifacts_sha256": {name: digest(output / name) for name in artifacts},
    })
    print(json.dumps({"status": "completed", "decision": result["decision"],
                      "readiness": result["readiness"]["status"],
                      "output": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
