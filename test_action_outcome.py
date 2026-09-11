import csv
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree as ET
import zipfile

from neft.action_outcome import (
    calibrate,
    evaluate_holdout,
    link_outcomes,
    load_config,
    read_commands,
    read_quality_xlsx,
    run_analysis,
    validate_pac_metadata,
)


ROOT = Path(__file__).resolve().parent
CONFIG = load_config(ROOT / "configs/action_outcome_v1.json")


def event(control, quarter, delta, values):
    return {
        "command_id": f"{control}-{quarter}-{delta}-{values[0]}",
        "control": control,
        "issued_time": "2024-01-01T00:00:00",
        "executed_time": "2024-01-01T00:01:00",
        "value_before": 0.0,
        "value_after": delta,
        "delta_command": float(delta),
        "unit": CONFIG["controls"][control]["unit"],
        "status": "executed",
        "source_system": "synthetic-test",
        "quarter": quarter,
        "isolated": True,
        "overlapping_commands": [],
        "baseline": {"event_time": "2023-12-31T23:00:00", "value_numeric": 10.0},
        "after": [
            {"bin_hours": hours, "sample": {}, "delta_quality": float(value), "reason": None}
            for hours, value in zip(CONFIG["response_bins_hours"], values)
        ],
        "exclusion_reasons": [],
    }


def synthetic_train():
    rows = []
    quarters = ["2023Q1", "2023Q2", "2023Q3", "2023Q4"]
    for control_index, control in enumerate(CONFIG["controls"]):
        for index in range(24):
            delta = (-1 if index % 2 else 1) * (1 + index % 5)
            noise = ((index * 7 + control_index) % 5 - 2) * 0.04
            correct = 0.3 + (1.5 + 0.2 * control_index) * delta + noise
            wrong_early = ((index * 11 + control_index) % 9 - 4) * 2.0
            wrong_late = ((index * 13 + 2 * control_index) % 11 - 5) * 1.7
            rows.append(event(control, quarters[index % 4], delta,
                              [wrong_early, correct, wrong_late]))
    return rows


class ActionOutcomeTests(unittest.TestCase):
    def test_raw_lims_reader_matches_registered_pair_and_seals_future_value(self):
        sheet_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "lims.xlsx"
            sheet = ET.Element("{" + sheet_ns + "}worksheet")
            data = ET.SubElement(sheet, "{" + sheet_ns + "}sheetData")

            def cell(row, column, value, numeric=False):
                item = ET.SubElement(row, "{" + sheet_ns + "}c",
                                     r=column + row.get("r"),
                                     t="n" if numeric else "inlineStr")
                if numeric:
                    ET.SubElement(item, "{" + sheet_ns + "}v").text = str(value)
                else:
                    inline = ET.SubElement(item, "{" + sheet_ns + "}is")
                    ET.SubElement(inline, "{" + sheet_ns + "}t").text = str(value)

            row = ET.SubElement(data, "{" + sheet_ns + "}row", r="1")
            cell(row, "CE", "Установка 'Гидроочистка'. Точка отбора '2'")
            row = ET.SubElement(data, "{" + sheet_ns + "}row", r="2")
            cell(row, "CQ", "Mg.Sulfur")
            row = ET.SubElement(data, "{" + sheet_ns + "}row", r="3")
            cell(row, "CQ", "мг/кг")
            for row_id, at, value, numeric in [
                    (5, datetime(2024, 6, 1, 10), 7.5, True),
                    (6, datetime(2025, 1, 1, 10), "SECRET", False)]:
                row = ET.SubElement(data, "{" + sheet_ns + "}row", r=str(row_id))
                serial = (at - datetime(1899, 12, 30)).total_seconds() / 86400
                cell(row, "CQ", serial, True)
                cell(row, "CR", value, numeric)
            workbook = (f'<workbook xmlns="{sheet_ns}" xmlns:r="{rel_ns}">'
                        '<sheets><sheet name="Лист1" sheetId="1" r:id="rId1"/>'
                        '</sheets></workbook>')
            relationships = ('<Relationships xmlns="http://schemas.openxmlformats.org/'
                             'package/2006/relationships"><Relationship Id="rId1" '
                             'Target="worksheets/sheet1.xml"/></Relationships>')
            with zipfile.ZipFile(path, "w") as book:
                book.writestr("xl/workbook.xml", workbook)
                book.writestr("xl/_rels/workbook.xml.rels", relationships)
                book.writestr("xl/worksheets/sheet1.xml", ET.tostring(sheet))
            rows, audit = read_quality_xlsx(path, CONFIG, "2023-01-01", "2025-01-01")
            self.assertEqual([(row["event_time"], row["value_numeric"]) for row in rows],
                             [("2024-06-01T10:00:00", 7.5)])
            self.assertEqual(audit["stopping_timestamp_only"], "2025-01-01T10:00:00")
            self.assertFalse(audit["later_numeric_values_parsed"])

    def test_command_reader_stops_before_holdout_numeric_values(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "commands.csv"
            columns = [
                "command_id", "control", "issued_time", "executed_time",
                "value_before", "value_after", "unit", "status", "source_system",
            ]
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns)
                writer.writeheader()
                writer.writerow({
                    "command_id": "train-1", "control": "P8",
                    "issued_time": "2024-06-01 10:00", "executed_time": "2024-06-01 10:05",
                    "value_before": "330", "value_after": "335", "unit": "degC",
                    "status": "executed", "source_system": "DCS",
                })
                writer.writerow({
                    "command_id": "holdout-secret", "control": "P8",
                    "issued_time": "2025-01-01 00:00", "executed_time": "SECRET",
                    "value_before": "SECRET", "value_after": "SECRET", "unit": "SECRET",
                    "status": "SECRET", "source_system": "SECRET",
                })
            rows, audit = read_commands(
                path, CONFIG, CONFIG["train"]["start"], CONFIG["train"]["end_exclusive"])
            self.assertEqual([row["command_id"] for row in rows], ["train-1"])
            self.assertEqual(audit["stopping_timestamp_only"], "2025-01-01T00:00:00")
            self.assertFalse(audit["later_numeric_values_parsed"])

    def test_telemetry_is_never_substituted_for_command_log(self):
        result = run_analysis(CONFIG, command_log=None, quality_db=None)
        self.assertEqual(result["readiness"]["status"], "MISSING_COMMAND_LOG")
        self.assertEqual(result["model_fits"], 0)
        self.assertFalse(result["holdout_numeric_read"])
        self.assertFalse(result["sealed_release_numeric_read"])

    def test_pac_gate_requires_time_availability_and_health(self):
        self.assertFalse(validate_pac_metadata(None)["eligible"])
        valid = {
            "schema": "pac-availability-v1",
            "timestamp_basis": "measurement_time",
            "availability": {"mode": "confirmed_max_delay", "minutes": 15,
                             "evidence": "signed interface specification"},
            "health": {"source": "analyzer-status", "status_field": "health",
                       "accepted_values": ["ok"]},
            "calibration": {"source": "calibration-log", "status_field": "state",
                            "accepted_values": ["valid"]},
        }
        gate = validate_pac_metadata(valid)
        self.assertTrue(gate["eligible"])
        self.assertFalse(gate["pac_used"])

    def test_linker_uses_execution_time_and_unique_independent_samples(self):
        commands = [
            {"command_id": "c1", "control": "P8", "issued_time": "2024-01-01T09:55:00",
             "executed_time": "2024-01-01T10:00:00", "value_before": 330.0,
             "value_after": 335.0, "delta_command": 5.0, "unit": "degC",
             "status": "executed", "source_system": "DCS"},
            {"command_id": "c2", "control": "P8", "issued_time": "2024-01-01T19:55:00",
             "executed_time": "2024-01-01T20:00:00", "value_before": 335.0,
             "value_after": 330.0, "delta_command": -5.0, "unit": "degC",
             "status": "executed", "source_system": "DCS"},
        ]
        quality = []
        for hour, value in [(9, 10), (10.5, 11), (11.5, 12), (12.5, 13),
                            (19, 20), (20.5, 19), (21.5, 18), (22.5, 17)]:
            whole = int(hour)
            minute = 30 if hour % 1 else 0
            quality.append({"event_time": f"2024-01-01T{whole:02d}:{minute:02d}:00",
                            "value_numeric": float(value)})
        linked, audit = link_outcomes(commands, quality, CONFIG)
        self.assertEqual([row["baseline"]["value_numeric"] for row in linked], [10.0, 20.0])
        self.assertEqual([[item["delta_quality"] for item in row["after"]]
                          for row in linked], [[1.0, 2.0, 3.0], [-1.0, -2.0, -3.0]])
        self.assertEqual(audit["unique_quality_samples_used"], 8)

    def test_train_selects_true_lag_and_holdout_uses_frozen_models(self):
        config = json.loads(json.dumps(CONFIG))
        config["selection"]["bootstrap_repetitions"] = 80
        models, audit = calibrate(synthetic_train(), config)
        self.assertLessEqual(audit["model_fits"], config["budget"]["model_fits_max"])
        for control_index, control in enumerate(config["controls"]):
            self.assertEqual(models[control]["selected_bin_hours"], [1, 2])
            self.assertAlmostEqual(models[control]["slope"], 1.5 + 0.2 * control_index,
                                   delta=0.03)
            self.assertEqual(models[control]["train_pairs"], 24)
        holdout = []
        for control_index, control in enumerate(config["controls"]):
            for index in range(12):
                delta = (-1 if index % 2 else 1) * (1 + index % 4)
                predicted = 0.3 + (1.5 + 0.2 * control_index) * delta
                holdout.append(event(control, "2025Q1", delta,
                                     [20 - index, predicted, index - 20]))
        assessment = evaluate_holdout(models, holdout, config)
        self.assertTrue(assessment["accepted"])
        self.assertTrue(all(row["events"] == 12 and row["passed"]
                            for row in assessment["controls"].values()))

    def test_cv_variance_failure_rejects_without_opening_holdout(self):
        rows = []
        for control in CONFIG["controls"]:
            for index in range(24):
                quarter = ["2023Q1", "2023Q2", "2023Q3"][index % 3]
                delta = float(-(1 + index % 4) if quarter == "2023Q1" else 1)
                rows.append(event(control, quarter, delta,
                                  [delta, 2 * delta, 3 * delta]))
        models, audit = calibrate(rows, CONFIG)
        self.assertIsNone(models)
        self.assertTrue(audit["reason"].startswith("NO_IDENTIFIABLE_LAG_BIN"))
        self.assertGreater(audit["model_fits"], 0)


if __name__ == "__main__":
    unittest.main()
