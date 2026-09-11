import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET

from neft.action_data_intake import discover_command_sources
from neft.action_pipeline import final_result, select_command_source


ROOT = Path(__file__).resolve().parent
INTAKE = json.loads((ROOT / "configs/action_data_intake_v1.json").read_text())
PIPELINE = json.loads((ROOT / "configs/action_pipeline_v1.json").read_text())
COLLECTION = json.loads((ROOT / "configs/action_collection_v1.json").read_text())
REQUIRED = INTAKE["required_command_columns"]


def write_csv(path, headers=REQUIRED):
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow(headers)


def empty_quality(path):
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow(COLLECTION["quality_schema"])


def xlsx_header(path):
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    sheet = ET.Element("{" + ns + "}worksheet")
    data = ET.SubElement(sheet, "{" + ns + "}sheetData")
    row = ET.SubElement(data, "{" + ns + "}row", r="1")
    for index, value in enumerate(REQUIRED):
        cell = ET.SubElement(row, "{" + ns + "}c", r=chr(65 + index) + "1", t="inlineStr")
        inline = ET.SubElement(cell, "{" + ns + "}is")
        ET.SubElement(inline, "{" + ns + "}t").text = value
    workbook = (f'<workbook xmlns="{ns}" xmlns:r="{rel}"><sheets>'
                '<sheet name="Commands" sheetId="1" r:id="rId1"/></sheets></workbook>')
    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
            '</Relationships>')
    with zipfile.ZipFile(path, "w") as book:
        book.writestr("xl/workbook.xml", workbook)
        book.writestr("xl/_rels/workbook.xml.rels", rels)
        book.writestr("xl/worksheets/sheet1.xml", ET.tostring(sheet))


class ActionPipelineTests(unittest.TestCase):
    def test_no_source_blocks_without_calibration(self):
        inventory = {"exact_command_sources": [], "files": []}
        gate = select_command_source(inventory, PIPELINE)
        result = final_result(gate, {"status": "MISSING_COMMAND_SOURCE"})
        self.assertEqual(result["stage"], "BLOCKED_NO_COMMAND_SOURCE")
        self.assertFalse(result["calibration_started"])
        self.assertFalse(result["holdout_numeric_read"])

    def test_two_exact_sources_are_ambiguous(self):
        inventory = {"exact_command_sources": ["a.csv", "b.csv"], "files": []}
        gate = select_command_source(inventory, PIPELINE)
        self.assertEqual(gate["stage"], "BLOCKED_AMBIGUOUS_COMMAND_SOURCES")

    def test_exact_xlsx_is_discovered_but_not_executed(self):
        with tempfile.TemporaryDirectory() as folder:
            xlsx_header(Path(folder) / "commands.xlsx")
            inventory = discover_command_sources(folder, INTAKE)
            gate = select_command_source(inventory, PIPELINE)
        self.assertEqual(gate["stage"], "COMMAND_FORMAT_NOT_EXECUTABLE")
        self.assertFalse(gate["ready"])

    def test_exact_csv_is_selected_by_absolute_path(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "commands.csv"
            write_csv(path)
            inventory = discover_command_sources(folder, INTAKE)
            gate = select_command_source(inventory, PIPELINE)
        self.assertTrue(gate["ready"])
        self.assertEqual(gate["command_source"], str(path.resolve()))

    def test_one_command_pipeline_delegates_and_keeps_holdout_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            data = folder / "data"; data.mkdir()
            write_csv(data / "commands.csv")
            quality = folder / "quality.csv"; empty_quality(quality)
            output = folder / "output"
            process = subprocess.run([
                sys.executable, str(ROOT / "scripts/run_action_pipeline.py"),
                "--data-root", str(data), "--quality-source", str(quality),
                "--output", str(output)], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads((output / "pipeline.json").read_text())
        self.assertEqual(result["stage"], "CALIBRATION_COMPLETE")
        self.assertEqual(result["readiness"], "INSUFFICIENT_TRAIN_SUPPORT")
        self.assertEqual(result["model_fits"], 0)
        self.assertFalse(result["holdout_numeric_read"])


if __name__ == "__main__":
    unittest.main()
