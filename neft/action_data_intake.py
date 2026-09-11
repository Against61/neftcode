"""Read-only discovery of command logs and train-only LIMS cadence profiling."""
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import zipfile
from xml.etree import ElementTree as ET

SHEET_NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def sha256(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _normalise_headers(values):
    return [str(value).strip().lstrip("\ufeff") for value in values
            if value is not None and str(value).strip()]


def _classification(headers, required):
    actual = set(headers)
    expected = set(required)
    if actual == expected and len(headers) == len(actual):
        return "exact_command_schema"
    if expected.issubset(actual):
        return "command_schema_with_extras"
    return "not_command_schema"


def _csv_headers(path):
    """Decode one CSV record only; no data row is parsed."""
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        first_line = handle.readline()
    if not first_line:
        return [], None
    try:
        dialect = csv.Sniffer().sniff(first_line, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    headers = next(csv.reader([first_line], delimiter=delimiter), [])
    return _normalise_headers(headers), delimiter


def _cell_value(cell, strings):
    if cell.get("t") == "inlineStr":
        inline = cell.find("s:is", SHEET_NS)
        return "" if inline is None else "".join(inline.itertext())
    value = cell.findtext("s:v", namespaces=SHEET_NS)
    if cell.get("t") == "s" and value is not None:
        return strings[int(value)]
    return value


def _xlsx_headers(path, required, maximum_rows):
    """Inspect at most maximum_rows rows per sheet and retain the best header row."""
    candidates = []
    with zipfile.ZipFile(path) as book:
        strings = []
        if "xl/sharedStrings.xml" in book.namelist():
            shared = ET.fromstring(book.read("xl/sharedStrings.xml"))
            strings = ["".join(item.itertext()) for item in shared]
        workbook = ET.fromstring(book.read("xl/workbook.xml"))
        relationships = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
        targets = {item.get("Id"): item.get("Target") for item in relationships
                   if item.get("TargetMode") != "External"}
        for sheet in workbook.findall("s:sheets/s:sheet", SHEET_NS):
            relation_id = sheet.get("{" + OFFICE_REL + "}id")
            target = targets.get(relation_id)
            if not target:
                continue
            target = target.lstrip("/") if target.startswith("/") else "xl/" + target
            target = str(Path(target))
            if target not in book.namelist():
                continue
            root = ET.fromstring(book.read(target))
            for row in root.findall("s:sheetData/s:row", SHEET_NS):
                row_number = int(row.get("r", "0"))
                if row_number > maximum_rows:
                    break
                values = [_cell_value(cell, strings) for cell in row.findall("s:c", SHEET_NS)]
                headers = _normalise_headers(values)
                if headers:
                    overlap = len(set(headers) & set(required))
                    candidates.append({"sheet": sheet.get("name"), "row": row_number,
                                       "headers": headers, "overlap": overlap})
    if not candidates:
        return [], None, None
    best = max(candidates, key=lambda item: (item["overlap"], len(item["headers"])))
    return best["headers"], best["sheet"], best["row"]


def inspect_file(path, config, root):
    path = Path(path).resolve()
    suffix = path.suffix.lower()
    result = {
        "path": str(path), "relative_path": str(path.relative_to(Path(root).resolve())),
        "extension": suffix, "size_bytes": path.stat().st_size,
        "sha256": sha256(path), "content_decoded": "headers_only",
    }
    try:
        if suffix == ".csv":
            headers, delimiter = _csv_headers(path)
            result.update(headers=headers, delimiter=delimiter, sheet=None, header_row=1)
        else:
            headers, sheet, row = _xlsx_headers(
                path, config["required_command_columns"], config["xlsx_header_rows"])
            result.update(headers=headers, delimiter=None, sheet=sheet, header_row=row)
        result["classification"] = _classification(
            result["headers"], config["required_command_columns"])
        result["missing_columns"] = sorted(
            set(config["required_command_columns"]) - set(result["headers"]))
        result["extra_columns"] = sorted(
            set(result["headers"]) - set(config["required_command_columns"]))
    except (OSError, ValueError, KeyError, IndexError, ET.ParseError, zipfile.BadZipFile) as exc:
        result.update(headers=[], classification="unreadable_header", error=type(exc).__name__)
        result["missing_columns"] = list(config["required_command_columns"])
        result["extra_columns"] = []
    return result


def discover_command_sources(data_root, config):
    root = Path(data_root).resolve()
    if not root.is_dir():
        raise ValueError("DATA_ROOT_MUST_BE_DIRECTORY")
    extensions = set(config["scan_extensions"])
    paths = sorted(path for path in root.rglob("*")
                   if path.is_file() and path.suffix.lower() in extensions)
    truncated = len(paths) > config["maximum_files"]
    paths = paths[:config["maximum_files"]]
    files = [inspect_file(path, config, root) for path in paths]
    exact = [item["relative_path"] for item in files
             if item["classification"] == "exact_command_schema"]
    compatible = [item["relative_path"] for item in files
                  if item["classification"] == "command_schema_with_extras"]
    if len(exact) == 1:
        status = "READY_COMMAND_SOURCE"
    elif len(exact) > 1:
        status = "AMBIGUOUS_COMMAND_SOURCES"
    else:
        status = "MISSING_COMMAND_SOURCE"
    return {
        "schema": "action-data-inventory-v1", "data_root": str(root),
        "scan_policy": "headers_only; file hashes read as bytes; data rows not decoded",
        "scanned_files": len(files), "truncated": truncated, "files": files,
        "exact_command_sources": exact, "compatible_sources_requiring_column_removal": compatible,
        "status": status,
    }


def profile_lims(path, action_config, intake_config):
    """Read only the registered train window; do not open holdout numeric values."""
    from neft.action_outcome import read_quality_source

    train = intake_config["train"]
    rows, audit = read_quality_source(
        path, action_config, train["start"], train["end_exclusive"])
    audit = dict(audit)
    audit.setdefault("later_numeric_values_parsed", False)
    timestamps = sorted(datetime.fromisoformat(row["event_time"]) for row in rows)
    intervals = [(right - left).total_seconds() / 3600
                 for left, right in zip(timestamps, timestamps[1:])]
    thresholds = [1, 2, 3]
    short = {str(hours): {
        "count": sum(gap <= hours for gap in intervals),
        "fraction": (sum(gap <= hours for gap in intervals) / len(intervals)
                     if intervals else None),
    } for hours in thresholds}
    median = None
    if intervals:
        ordered = sorted(intervals)
        middle = len(ordered) // 2
        median = (ordered[middle] if len(ordered) % 2 else
                  (ordered[middle - 1] + ordered[middle]) / 2)
    return {
        "schema": "lims-cadence-profile-v1", "source": str(Path(path).resolve()),
        "source_sha256": sha256(path), "window": train, "samples": len(rows),
        "first_event_time": timestamps[0].isoformat() if timestamps else None,
        "last_event_time": timestamps[-1].isoformat() if timestamps else None,
        "intervals": len(intervals), "median_interval_hours": median,
        "short_interval_counts": short,
        "intervals_over_3h": sum(gap > 3 for gap in intervals),
        "lag_pair_coverage_status": "UNKNOWN_UNTIL_COMMAND_ALIGNMENT",
        "adapter_audit": audit, "holdout_numeric_read": False,
        "sealed_2026_numeric_read": False,
    }


def build_readiness(inventory, lims_profile, config):
    status = inventory["status"]
    missing = []
    if status == "MISSING_COMMAND_SOURCE":
        missing.append("exact command log with source-recorded issue/execution timestamps")
    elif status == "AMBIGUOUS_COMMAND_SOURCES":
        missing.append("one explicitly selected exact command source")
    if lims_profile is None:
        missing.append("registered independent LIMS quality source")
    pair_coverage_known = False
    if lims_profile is not None:
        missing.append("command-aligned proof of minimum LIMS pairs in every 0–3 hour bin")
    return {
        "schema": "action-data-readiness-v1", "status": status,
        "ready_for_action_outcome_fit": status == "READY_COMMAND_SOURCE" and pair_coverage_known,
        "quality_pair_coverage": "UNKNOWN_UNTIL_COMMAND_ALIGNMENT",
        "model_fits": 0, "holdout_numeric_read": False,
        "sealed_2026_numeric_read": False, "missing": missing,
        "required_command_columns": config["required_command_columns"],
        "controls": config["controls"], "minimum_request": config["minimum_request"],
    }


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
