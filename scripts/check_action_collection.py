#!/usr/bin/env python3
"""Validate collected action data and report exact train coverage shortfalls."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from neft.action_collection import validate_collection
from neft.action_collection_report import render


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def gap_text(result):
    lines = ["# Дефициты покрытия action data", "", f"Статус: `{result['status']}`.", ""]
    for control, item in result["controls"].items():
        lines += [f"## {control}", "",
                  f"- изолированные команды: {item['isolated_executed']}, не хватает {item['command_shortfall']};",
                  f"- направления up/down: {item['directions']['up']}/{item['directions']['down']}, "
                  f"не хватает {item['direction_shortfall']['up']}/{item['direction_shortfall']['down']};"]
        for lag in item["bins"]:
            name = f"{lag['bin_hours'][0]}–{lag['bin_hours'][1]} ч"
            lines.append(f"- {name}: {lag['pairs']} пар в {len(lag['quarters'])} кварталах; "
                         f"не хватает {lag['pair_shortfall']} пар и {lag['quarter_shortfall']} кварталов.")
        lines.append("")
    lines += ["Проверка читает только train2023–2024, выполняет0fit и не открывает числа holdout2025/2026.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commands", type=Path, required=True)
    parser.add_argument("--quality-samples", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path,
                        default=ROOT / "configs/action_collection_v1.json")
    parser.add_argument("--action-config", type=Path,
                        default=ROOT / "configs/action_outcome_v1.json")
    args = parser.parse_args()
    for path in (args.commands, args.quality_samples, args.config, args.action_config):
        if not path.is_file():
            raise SystemExit("file does not exist: " + str(path))
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    action_config = json.loads(args.action_config.read_text(encoding="utf-8"))
    result = validate_collection(args.commands, args.quality_samples,
                                 action_config, config)
    (output / "coverage.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    (output / "gaps.md").write_text(gap_text(result), encoding="utf-8")
    (output / "report.html").write_text(render(result), encoding="utf-8")
    artifacts = ["coverage.json", "gaps.md", "report.html"]
    manifest = {
        "schema": "action-collection-manifest-v1", "status": "completed",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "decision": result["status"], "model_fits": 0,
        "holdout_numeric_read": False, "sealed_2026_numeric_read": False,
        "sources_sha256": {"commands": digest(args.commands),
                           "quality_samples": digest(args.quality_samples)},
        "config_sha256": digest(args.config),
        "action_config_sha256": digest(args.action_config),
        "artifacts_sha256": {name: digest(output / name) for name in artifacts},
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "model_fits": 0,
                      "output": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
