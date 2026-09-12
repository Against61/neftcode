#!/usr/bin/env python3
"""Build a hash-pinned historical intelligence bundle from local CSV/XLSX."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from neft.historical_intelligence import run_experiment, save_json, sha256, validate_config
from neft.historical_training import prepare_training_package


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--sources", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--config", type=Path, default=ROOT / "configs/historical_intelligence_v1.json")
    args = ap.parse_args()
    output = args.output.resolve()
    if output.exists():
        ap.error("Output already exists; choose a new directory")
    output.mkdir(parents=True)
    started = time.monotonic()
    sources = json.loads(args.sources.read_text())
    if set(sources) != {"schema", "telemetry", "lims"} or sources["schema"] != "history-sources-v1":
        raise ValueError("INVALID_SOURCE_CONFIG")
    template = json.loads(args.config.read_text())
    history_policy = json.loads((ROOT / "configs/history_adapter_v1.json").read_text())
    action_config = json.loads((ROOT / "configs/action_outcome_v1.json").read_text())
    config, input_audit = prepare_training_package(
        args.data_root, sources, template, history_policy, action_config, output)
    validate_config(config)
    model_output = output / "model"; model_output.mkdir()
    save_json(model_output / "config.json", config)
    metrics = run_experiment(config, output, model_output)
    manifest = {"schema": "historical-training-package-v1", "status": "completed",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "seconds": time.monotonic() - started, "fit_count": 20,
                "bundle": str(model_output / "bundle.joblib"),
                "bundle_sha256": sha256(model_output / "bundle.joblib"),
                "quality_component": metrics["decision"]["quality_component"],
                "control_component": metrics["decision"]["control_component"],
                "raw_source_sha256": input_audit["raw_source_sha256"],
                "artifacts_sha256": {str(path.relative_to(output)): sha256(path)
                                     for path in sorted(output.rglob("*"))
                                     if path.is_file() and path.name != "manifest.json"},
                "boundaries": ["offline research only", "controls are historical telemetry, not commands",
                               "PAC excluded", "no industrial promotion"]}
    save_json(output / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
