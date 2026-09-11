#!/usr/bin/env python3
"""Generate blank templates and a minimum observation plan."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def source_inventory(root, config):
    root = Path(root).resolve()
    suffixes = {".csv", ".xlsx", ".xlsm", ".xls", ".db", ".sqlite", ".sqlite3"}
    excluded = {".git", "runs", "reports", "neftcode-public"}
    files = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if (not path.is_file() or len(relative.parts) > 3 or
                any(part in excluded for part in relative.parts) or
                path.suffix.lower() not in suffixes):
            continue
        command_source = False
        dense_quality_source = False
        headers = []
        if path.suffix.lower() == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                first_line = handle.readline()
            try:
                delimiter = csv.Sniffer().sniff(first_line, delimiters=",;\t|").delimiter
            except csv.Error:
                delimiter = ","
            headers = [value.strip() for value in next(csv.reader([first_line],
                                                                  delimiter=delimiter), [])]
            command_source = (set(headers) == set(config["command_schema"]) and
                              len(headers) == len(set(headers)))
            dense_quality_source = (set(headers) == set(config["quality_schema"]) and
                                    len(headers) == len(set(headers)))
        name = path.name.lower()
        if command_source: role = "exact_command_collection_csv"
        elif dense_quality_source: role = "exact_quality_collection_csv"
        elif name == "242000_tags.csv": role = "ht_telemetry_not_commands"
        elif name == "avt_tags.csv": role = "avt_telemetry_not_commands"
        elif "лимс" in name: role = "existing_sparse_lims_quality"
        elif "пак" in name: role = "pac_excluded_pending_metadata"
        elif "тег" in name: role = "tag_dictionary"
        else: role = "unclassified"
        files.append({"path": str(path), "relative_path": str(relative),
                      "size_bytes": path.stat().st_size, "sha256": digest(path),
                      "role": role, "headers": headers,
                      "command_source": command_source,
                      "dense_quality_source": dense_quality_source})
    return {"schema": "action-source-audit-v1", "root": str(root),
            "files": files, "file_count": len(files),
            "command_sources": sum(item["command_source"] for item in files),
            "denser_independent_quality_sources": sum(
                item["dense_quality_source"] for item in files)}


def write_plan(path, config):
    fields = ["slot_id", "split", "control", "direction", "quarter_requirement",
              "sampling_mode", "baseline_offset_min", "post_0_1h_offset_min",
              "post_1_2h_offset_min", "post_2_3h_offset_min"]
    offsets = config["suggested_observation_offsets_minutes"]
    rows = []
    for control in config["controls"]:
        for index in range(config["minimum"]["isolated_executed_per_control_train"]):
            sampled = index < config["minimum"]["paired_samples_per_control_per_lag_bin_train"]
            rows.append({
                "slot_id": f"train-{control}-{index + 1:02d}", "split": "train",
                "control": control, "direction": "up" if index % 2 == 0 else "down",
                "quarter_requirement": (f"distinct_quarter_slot_{index % 3 + 1}"
                                        if sampled else "any_train_quarter"),
                "sampling_mode": "baseline_plus_all_three_bins" if sampled else "command_only",
                "baseline_offset_min": offsets["baseline"] if sampled else "",
                "post_0_1h_offset_min": offsets["post_0_1h"] if sampled else "",
                "post_1_2h_offset_min": offsets["post_1_2h"] if sampled else "",
                "post_2_3h_offset_min": offsets["post_2_3h"] if sampled else "",
            })
        for index in range(config["minimum"]["holdout_events_per_control"]):
            rows.append({
                "slot_id": f"holdout-{control}-{index + 1:02d}", "split": "holdout",
                "control": control, "direction": "up" if index % 2 == 0 else "down",
                "quarter_requirement": "historical_2025_after_train_gate",
                "sampling_mode": "baseline_plus_frozen_selected_bin",
                "baseline_offset_min": offsets["baseline"],
                "post_0_1h_offset_min": "after_train_selects_lag",
                "post_1_2h_offset_min": "after_train_selects_lag",
                "post_2_3h_offset_min": "after_train_selects_lag",
            })
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path,
                        default=ROOT / "configs/action_collection_v1.json")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    for name, fields in (("commands_template.csv", config["command_schema"]),
                         ("quality_samples_template.csv", config["quality_schema"])):
        with (output / name).open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(fields)
    rows = write_plan(output / "collection_plan.csv", config)
    inventory = source_inventory(args.source_root, config)
    (output / "source_inventory.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n")
    validator_output = output / "empty_coverage"
    command = [sys.executable, str(ROOT / "scripts/check_action_collection.py"),
               "--commands", str(output / "commands_template.csv"),
               "--quality-samples", str(output / "quality_samples_template.csv"),
               "--output", str(validator_output)]
    process = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                             timeout=180, check=False)
    (output / "validator.stdout.txt").write_text(process.stdout)
    (output / "validator.stderr.txt").write_text(process.stderr)
    if process.returncode:
        raise RuntimeError("empty template validator failed")
    readme = f"""# Пакет сбора action data

- Найдено исходных производственных таблиц: {inventory['file_count']}.
- Найдено журналов команд: {inventory['command_sources']}.
- План: {len(rows)} слотов — 60 train-команд и 30 holdout-команд.
- Минимум train: 30 полностью наблюдаемых действий и 120 независимых проб
  (baseline + три post-bin); остальные 30 train-слотов фиксируют команды.
- Holdout: 30 действий и минимум 60 проб после заморозки выбранного лага.

Holdout-строки относятся к историческому 2025 году и не читаются до train-gate.
Новые строки 2026+ требуют отдельного заранее зарегистрированного протокола.

Система не инициирует команды. План применяется к штатным разрешённым действиям.
`sample_time` — время отбора; пустой `available_time` не разрешает онлайн-признак.

Проверка заполненных файлов:

```bash
python scripts/check_action_collection.py --commands commands.csv \\
  --quality-samples quality_samples.csv --output output/coverage
```
"""
    (output / "README.md").write_text(readme)
    artifacts = ["commands_template.csv", "quality_samples_template.csv",
                 "collection_plan.csv", "source_inventory.json", "README.md",
                 "validator.stdout.txt", "validator.stderr.txt"]
    manifest = {"schema": "action-collection-kit-manifest-v1", "status": "completed",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "config_sha256": digest(args.config), "model_fits": 0,
                "holdout_numeric_read": False, "sealed_2026_numeric_read": False,
                "plan_slots": len(rows),
                "empty_coverage_manifest_sha256": digest(validator_output / "manifest.json"),
                "artifacts_sha256": {name: digest(output / name) for name in artifacts}}
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": "completed", "plan_slots": len(rows),
                      "sources": inventory["file_count"],
                      "command_sources": inventory["command_sources"],
                      "output": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
