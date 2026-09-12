"""Prepare bounded raw sources for the historical-intelligence training run."""
from __future__ import annotations

import csv
from datetime import datetime
import json
from pathlib import Path

import numpy as np

from .action_outcome import read_quality_xlsx
from .historical_intelligence import save_json, sha256
from .history_adapter import checked_source, number


def expected_telemetry_tags(history_policy: dict, model_config: dict) -> list[str]:
    included = list(history_policy["telemetry_channels"])
    excluded = [name.removeprefix("ht:") for name in model_config["feature_spec"]["excluded_channels"]]
    expected = set(included) | set(excluded)
    if len(expected) != len(included) + len(excluded):
        raise ValueError("TELEMETRY_CHANNEL_OVERLAP")
    return list(expected)


def prepare_telemetry(path: Path, output: Path, start, end_exclusive, expected_tags: set[str]) -> dict:
    """Read only timestamp metadata after the registered training boundary."""
    start = datetime.fromisoformat(str(start))
    end = datetime.fromisoformat(str(end_exclusive))
    timestamps, values = [], []
    previous = None
    stopping_timestamp = None
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        header = [value.strip() for value in next(reader)]
        if len(header) != len(set(header)) or "date" not in header:
            raise ValueError("INVALID_TELEMETRY_HEADER")
        date_column = header.index("date")
        tags = [value for value in header if value and value != "date" and not value.lower().startswith("unnamed:")]
        if set(tags) != set(expected_tags):
            raise ValueError("TELEMETRY_SCHEMA_MISMATCH")
        columns = [header.index(tag) for tag in tags]
        for row in reader:
            if len(row) != len(header):
                raise ValueError("MALFORMED_TELEMETRY_ROW")
            try:
                at = datetime.fromisoformat(row[date_column].strip())
            except (TypeError, ValueError):
                raise ValueError("INVALID_TELEMETRY_TIME") from None
            if at.tzinfo is not None:
                raise ValueError("TIMEZONE_AWARE_TELEMETRY_NOT_REGISTERED")
            if previous is not None and at <= previous:
                raise ValueError("DUPLICATE_OR_UNORDERED_TELEMETRY_TIME")
            previous = at
            if at >= end:
                stopping_timestamp = at.isoformat()
                break
            if at < start:
                continue
            timestamps.append(np.datetime64(at, "ns"))
            values.append([np.nan if (value := number(row[column])) is None else value for column in columns])
    if not timestamps:
        raise ValueError("NO_TELEMETRY_IN_TRAINING_PERIOD")
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output, timestamps=np.asarray(timestamps),
                        columns=np.asarray(["ht:" + tag for tag in tags]),
                        values=np.asarray(values, dtype=float))
    return {"rows": len(timestamps), "columns": len(tags),
            "start": str(timestamps[0]), "end": str(timestamps[-1]),
            "stopping_timestamp_only": stopping_timestamp,
            "later_numeric_values_parsed": False, "sha256": sha256(output)}


def prepare_quality(path: Path, output: Path, action_config: dict, start, end_exclusive) -> dict:
    rows, audit = read_quality_xlsx(path, action_config, start, end_exclusive)
    if not rows:
        raise ValueError("NO_QUALITY_IN_TRAINING_PERIOD")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as stream:
        for row in rows:
            normalized = dict(row)
            # Match the registered target-audit JSON precision used by EXP-0008.
            normalized["value_numeric"] = round(float(normalized["value_numeric"]), 10)
            stream.write(json.dumps(normalized, ensure_ascii=False, allow_nan=False) + "\n")
    return {**audit, "prepared_sha256": sha256(output)}


def portable_config(template: dict, telemetry_relative: str, telemetry_sha: str,
                    quality_relative: str, quality_sha: str) -> dict:
    config = json.loads(json.dumps(template))
    config["source_runs"] = {"telemetry": telemetry_relative, "quality": quality_relative}
    config["source_sha256"] = {telemetry_relative: telemetry_sha, quality_relative: quality_sha}
    config["mode"] = "offline_research_only"
    return config


def prepare_training_package(data_root: Path, sources: dict, template: dict,
                             history_policy: dict, action_config: dict, output: Path) -> tuple[dict, dict]:
    data_root = Path(data_root).resolve(); output = Path(output).resolve()
    telemetry_source = checked_source(data_root, sources["telemetry"])
    quality_source = checked_source(data_root, sources["lims"])
    prepared = output / "prepared"
    telemetry_relative = "prepared/telemetry_ht.npz"
    quality_relative = "prepared/quality_train.jsonl"
    splits = template["splits"]
    telemetry_audit = prepare_telemetry(
        telemetry_source, output / telemetry_relative, splits["fit_start"],
        splits["holdout_end_exclusive"], set(expected_telemetry_tags(history_policy, template)))
    quality_audit = prepare_quality(
        quality_source, output / quality_relative, action_config, splits["fit_start"],
        splits["holdout_end_exclusive"])
    config = portable_config(template, telemetry_relative, telemetry_audit["sha256"],
                             quality_relative, quality_audit["prepared_sha256"])
    audit = {"schema": "historical-training-input-v1", "data_root": str(data_root),
             "raw_source_sha256": {"telemetry": sources["telemetry"]["sha256"],
                                    "lims": sources["lims"]["sha256"]},
             "telemetry": telemetry_audit, "quality": quality_audit,
             "pac": {"included": False, "values_read": 0},
             "prepared_directory": str(prepared)}
    save_json(output / "input_audit.json", audit)
    return config, audit
