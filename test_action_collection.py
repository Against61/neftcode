import csv
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from neft.action_collection import (collection_coverage, read_quality_collection,
                                    validate_collection)
from scripts.build_action_collection_kit import source_inventory


ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "configs/action_collection_v1.json").read_text())
ACTION = json.loads((ROOT / "configs/action_outcome_v1.json").read_text())


def write_quality(path, rows):
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CONFIG["quality_schema"])
        writer.writeheader(); writer.writerows(rows)


def quality_row(sample_id, at, value, available=""):
    return {"sample_id": sample_id, "sample_time": at,
            "available_time": available, "value_numeric": value, "unit": "mg/kg",
            "quality_status": "valid", "source_system": "LIMS",
            "source_record_id": "record-" + sample_id}


def synthetic_complete():
    commands = []; quality = []; sample_index = 0
    quarters = [datetime(2023, 1, 15), datetime(2023, 4, 15), datetime(2023, 7, 15)]
    for control_index, control in enumerate(CONFIG["controls"]):
        for index in range(20):
            origin = quarters[index % 3] + timedelta(days=index * 4 + control_index,
                                                      hours=12)
            delta = 1.0 if index % 2 == 0 else -1.0
            commands.append({"command_id": f"{control}-{index}", "control": control,
                             "issued_time": (origin - timedelta(minutes=5)).isoformat(),
                             "executed_time": origin.isoformat(), "value_before": 10.0,
                             "value_after": 10.0 + delta, "delta_command": delta,
                             "unit": ACTION["controls"][control]["unit"],
                             "status": "executed", "source_system": "DCS"})
            if index < 10:
                for minutes, value in [(-30, 10), (30, 11), (90, 12), (150, 13)]:
                    sample_index += 1
                    quality.append({"sample_id": f"s{sample_index}",
                                    "event_time": (origin + timedelta(minutes=minutes)).isoformat(),
                                    "value_numeric": float(value), "available_time": None,
                                    "online_feature_allowed": False})
    commands.sort(key=lambda row: row["executed_time"])
    quality.sort(key=lambda row: row["event_time"])
    return commands, quality


class ActionCollectionTests(unittest.TestCase):
    def test_source_audit_counts_exact_collection_csvs(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            with (folder / "commands.csv").open("w", newline="") as handle:
                csv.writer(handle).writerow(CONFIG["command_schema"])
            write_quality(folder / "quality.csv", [])
            inventory = source_inventory(folder, CONFIG)
        self.assertEqual(inventory["command_sources"], 1)
        self.assertEqual(inventory["denser_independent_quality_sources"], 1)

    def test_quality_reader_stops_before_holdout_numeric_value(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "quality.csv"
            write_quality(path, [quality_row("a", "2024-01-01 10:00", "7.5"),
                                 quality_row("b", "2025-01-01 10:00", "SECRET")])
            rows, audit = read_quality_collection(path, CONFIG, "2023-01-01", "2025-01-01")
        self.assertEqual(rows[0]["value_numeric"], 7.5)
        self.assertEqual(audit["stopping_timestamp_only"], "2025-01-01T10:00:00")
        self.assertFalse(audit["later_numeric_values_parsed"])

    def test_unknown_availability_never_allows_online_feature(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "quality.csv"
            write_quality(path, [quality_row("a", "2024-01-01 10:00", "7.5")])
            rows, audit = read_quality_collection(path, CONFIG, "2023-01-01", "2025-01-01")
        self.assertFalse(rows[0]["online_feature_allowed"])
        self.assertEqual(audit["unknown_available_time"], 1)
        self.assertEqual(audit["online_feature_rows"], 0)

    def test_available_time_before_sample_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "quality.csv"
            write_quality(path, [quality_row("a", "2024-01-01 10:00", "7.5",
                                             "2024-01-01 09:59")])
            with self.assertRaisesRegex(ValueError, "AVAILABLE_TIME_BEFORE_SAMPLE"):
                read_quality_collection(path, CONFIG, "2023-01-01", "2025-01-01")

    def test_complete_minimum_passes_all_three_lag_bins(self):
        commands, quality = synthetic_complete()
        result = collection_coverage(commands, quality, ACTION, CONFIG)
        self.assertEqual(result["status"], "READY_FOR_EXP_0019")
        for item in result["controls"].values():
            self.assertEqual(item["isolated_executed"], 20)
            self.assertEqual(item["complete_four_sample_sets"], 10)
            self.assertTrue(all(row["pairs"] == 10 and not row["pair_shortfall"]
                                and not row["quarter_shortfall"] for row in item["bins"]))

    def test_empty_templates_report_exact_shortfalls(self):
        with tempfile.TemporaryDirectory() as folder:
            commands = Path(folder) / "commands.csv"
            quality = Path(folder) / "quality.csv"
            with commands.open("w", newline="") as handle:
                csv.writer(handle).writerow(CONFIG["command_schema"])
            write_quality(quality, [])
            result = validate_collection(commands, quality, ACTION, CONFIG)
        self.assertEqual(result["status"], "COLLECTION_INCOMPLETE")
        for item in result["controls"].values():
            self.assertEqual(item["command_shortfall"], 20)
            self.assertEqual(item["direction_shortfall"], {"up": 5, "down": 5})
            self.assertTrue(all(row["pair_shortfall"] == 10 and
                                row["quarter_shortfall"] == 3 for row in item["bins"]))
        self.assertFalse(result["holdout_numeric_read"])
        self.assertEqual(result["model_fits"], 0)


if __name__ == "__main__":
    unittest.main()
