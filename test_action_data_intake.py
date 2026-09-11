import csv
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET

from neft.action_data_intake import (build_readiness, discover_command_sources,
                                     profile_lims)


ROOT = Path(__file__).resolve().parent
INTAKE = json.loads((ROOT / "configs/action_data_intake_v1.json").read_text())
ACTION = json.loads((ROOT / "configs/action_outcome_v1.json").read_text())
REQUIRED = INTAKE["required_command_columns"]


def write_csv(path, headers):
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(headers)


def write_xlsx(path, rows):
    sheet_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    sheet = ET.Element("{" + sheet_ns + "}worksheet")
    data = ET.SubElement(sheet, "{" + sheet_ns + "}sheetData")
    for row_number, values in enumerate(rows, 1):
        row = ET.SubElement(data, "{" + sheet_ns + "}row", r=str(row_number))
        for index, value in enumerate(values):
            column = chr(ord("A") + index)
            cell = ET.SubElement(row, "{" + sheet_ns + "}c",
                                 r=f"{column}{row_number}", t="inlineStr")
            inline = ET.SubElement(cell, "{" + sheet_ns + "}is")
            ET.SubElement(inline, "{" + sheet_ns + "}t").text = str(value)
    workbook = (f'<workbook xmlns="{sheet_ns}" xmlns:r="{rel_ns}"><sheets>'
                '<sheet name="Commands" sheetId="1" r:id="rId1"/>'
                '</sheets></workbook>')
    relationships = ('<Relationships xmlns="http://schemas.openxmlformats.org/'
                     'package/2006/relationships"><Relationship Id="rId1" '
                     'Target="worksheets/sheet1.xml"/></Relationships>')
    with zipfile.ZipFile(path, "w") as book:
        book.writestr("xl/workbook.xml", workbook)
        book.writestr("xl/_rels/workbook.xml.rels", relationships)
        book.writestr("xl/worksheets/sheet1.xml", ET.tostring(sheet))


class ActionDataIntakeTests(unittest.TestCase):
    def test_exact_csv_is_discovered(self):
        with tempfile.TemporaryDirectory() as folder:
            write_csv(Path(folder) / "commands.csv", REQUIRED)
            result = discover_command_sources(folder, INTAKE)
        self.assertEqual(result["status"], "READY_COMMAND_SOURCE")
        self.assertEqual(result["exact_command_sources"], ["commands.csv"])

    def test_telemetry_tags_are_not_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            write_csv(Path(folder) / "242000_tags.csv", ["date", "P8", "T11", "F19"])
            result = discover_command_sources(folder, INTAKE)
        self.assertEqual(result["status"], "MISSING_COMMAND_SOURCE")
        self.assertEqual(result["files"][0]["classification"], "not_command_schema")

    def test_extra_columns_require_explicit_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            write_csv(Path(folder) / "export.csv", REQUIRED + ["comment"])
            result = discover_command_sources(folder, INTAKE)
        self.assertEqual(result["status"], "MISSING_COMMAND_SOURCE")
        self.assertEqual(result["compatible_sources_requiring_column_removal"], ["export.csv"])

    def test_xlsx_header_may_be_inside_first_twenty_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            write_xlsx(Path(folder) / "commands.xlsx", [["Export"], REQUIRED])
            result = discover_command_sources(folder, INTAKE)
        self.assertEqual(result["status"], "READY_COMMAND_SOURCE")
        self.assertEqual(result["files"][0]["header_row"], 2)

    def test_profile_is_train_only_and_computes_cadence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "lims.xlsx"
            action = json.loads(json.dumps(ACTION))
            action["quality_excel"]["sheet"] = "Commands"
            action["quality_excel"]["group_header_column"] = "A"
            action["quality_excel"]["date_column"] = "A"
            action["quality_excel"]["value_column"] = "B"
            action["quality_excel"]["group_header_contains"] = ["Гидроочистка", "Точка отбора '2'"]
            rows = [["Гидроочистка Точка отбора '2'"], ["Mg.Sulfur"], ["мг/кг"], [],
                    ["2024-01-01 00:00", "5"], ["2024-01-01 02:00", "6"],
                    ["2024-01-01 06:00", "7"], ["2025-01-01 00:00", "SECRET"]]
            write_xlsx(path, rows)
            profile = profile_lims(path, action, INTAKE)
        self.assertEqual(profile["samples"], 3)
        self.assertEqual(profile["median_interval_hours"], 3)
        self.assertEqual(profile["short_interval_counts"]["3"]["count"], 1)
        self.assertFalse(profile["holdout_numeric_read"])
        self.assertFalse(profile["adapter_audit"]["later_numeric_values_parsed"])

    def test_readiness_stays_fail_closed_until_command_aligned_pair_count(self):
        inventory = {"status": "MISSING_COMMAND_SOURCE"}
        profile = {"lag_pair_coverage_status": "UNKNOWN_UNTIL_COMMAND_ALIGNMENT"}
        result = build_readiness(inventory, profile, INTAKE)
        self.assertFalse(result["ready_for_action_outcome_fit"])
        self.assertEqual(result["quality_pair_coverage"],
                         "UNKNOWN_UNTIL_COMMAND_ALIGNMENT")
        self.assertEqual(result["model_fits"], 0)


if __name__ == "__main__":
    unittest.main()
