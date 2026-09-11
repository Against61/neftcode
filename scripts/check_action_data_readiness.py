#!/usr/bin/env python3
"""Scan a data drop and write a reproducible action-data readiness package."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from neft.action_data_intake import (build_readiness, discover_command_sources,
                                     load_json, profile_lims, sha256)
from neft.action_data_intake_report import render


def request_text(readiness, lims_profile):
    minimum = readiness["minimum_request"]
    cadence = "не проверена"
    if lims_profile:
        median = lims_profile["median_interval_hours"]
        median_text = f'{median:.2f} ч' if median is not None else 'не вычисляется'
        fraction = lims_profile["short_interval_counts"]["3"]["fraction"]
        fraction_text = f'{100 * fraction:.1f}%' if fraction is not None else '—'
        cadence = (f'{lims_profile["samples"]} проб за 2023–2024; медианный интервал '
                   f'{median_text}; интервалов ≤3 ч — '
                   f'{lims_profile["short_interval_counts"]["3"]["count"]} '
                   f'({fraction_text}).')
    columns = ", ".join(readiness["required_command_columns"])
    bins = ", ".join(f"{left}–{right} ч" for left, right in minimum["post_bins_hours"])
    return f"""# Запрос данных для проверки эффекта команд P8/T11/F19

Просим read-only выгрузку за 2023–2025 годы с одной строкой на команду и точными полями:

`{columns}`

`issued_time` и `executed_time` должны быть записаны системой-источником. Переходы
телеметрии P8/T11/F19 не заменяют эти события. Нужны выполненные, отменённые и
ошибочные команды, единицы и идентификатор source system.

Для train 2023–2024 просим по каждому из P8/T11/F19 не менее
{minimum['isolated_executed_per_control_train']} изолированных исполненных команд,
не менее {minimum['each_direction_per_control_train']} команд каждого направления,
покрытие минимум {minimum['train_quarters_per_control']} кварталов и не менее
{minimum['paired_samples_per_control_per_lag_bin_train']} независимых пар качества
в каждом окне {bins}. Для каждой команды нужна независимая проба не более чем за
{minimum['baseline_window_hours']} ч до исполнения и пробы после воздействия.
Одна проба не должна одновременно подтверждать несколько команд.

Для временного holdout 2025 просим минимум
{minimum['holdout_events_per_control']} событий на каждый control. Его числовые
значения будут открыты только после прохождения train-gate.

Текущий профиль LIMS: {cadence}
Если штатная LIMS остаётся редкой, нужна отдельная более частая независимая выборка
качества с тем же местом отбора и подтверждённым временем пробы.

Для будущего подключения ПАК отдельно нужны: смысл timestamp, recorded available_time
или доказанный предел задержки, статус исправности и журнал калибровки. До этого ПАК
не используется как онлайн-признак.
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--lims", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path,
                        default=ROOT / "configs/action_data_intake_v1.json")
    parser.add_argument("--action-config", type=Path,
                        default=ROOT / "configs/action_outcome_v1.json")
    args = parser.parse_args()
    config = load_json(args.config)
    action_config = load_json(args.action_config)
    inventory = discover_command_sources(args.data_root, config)
    lims_profile = profile_lims(args.lims, action_config, config) if args.lims else None
    readiness = build_readiness(inventory, lims_profile, config)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    artifacts = {
        "inventory.json": json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
        "readiness.json": json.dumps(readiness, ensure_ascii=False, indent=2) + "\n",
        "lims_profile.json": json.dumps(lims_profile, ensure_ascii=False, indent=2) + "\n",
        "data_request.md": request_text(readiness, lims_profile),
        "report.html": render(inventory, readiness, lims_profile),
    }
    for name, body in artifacts.items():
        (output / name).write_text(body, encoding="utf-8")
    manifest = {
        "schema": "action-data-intake-manifest-v1", "status": "completed",
        "created_at": datetime.now(timezone.utc).isoformat(), "config": str(args.config.resolve()),
        "config_sha256": sha256(args.config), "action_config_sha256": sha256(args.action_config),
        "data_root": str(args.data_root.resolve()),
        "lims_source": str(args.lims.resolve()) if args.lims else None,
        "decision": readiness["status"], "model_fits": 0,
        "artifacts_sha256": {name: sha256(output / name) for name in artifacts},
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": readiness["status"],
                      "ready_for_fit": readiness["ready_for_action_outcome_fit"],
                      "model_fits": 0, "output": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
