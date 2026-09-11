"""Append-only intake for observed command and independent quality events."""
import csv
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path

from neft.action_collection import validate_collection


COMMAND_COLUMNS = [
    "command_id", "control", "issued_time", "executed_time",
    "value_before", "value_after", "unit", "status", "source_system",
]
QUALITY_COLUMNS = [
    "sample_id", "sample_time", "available_time", "value_numeric", "unit",
    "quality_status", "source_system", "source_record_id",
]
EVENT_TYPES = {"command_issued", "command_terminal", "quality_sample"}


def capture_contract(config):
    return {
        "schema": "action-capture-contract-v1", "version": config["id"],
        "observe_only": True, "industrial_command": False,
        "endpoints": {
            "/api/v1/commands/issued": {
                "required": ["event_id", "command_id", "control", "issued_time",
                             "value_before", "unit", "source_system"]},
            "/api/v1/commands/terminal": {
                "required": ["event_id", "command_id", "status", "source_system"],
                "executed_requires": ["executed_time", "value_after"]},
            "/api/v1/quality-samples": {
                "required": ["event_id", "sample_id", "sample_time",
                             "value_numeric", "unit", "quality_status",
                             "source_system", "source_record_id"],
                "optional": ["available_time"]},
        },
        "controls": config["controls"], "quality_unit": config["quality_unit"],
        "time_contract": config["time_contract"], "policy": config["policy"],
    }


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _digest_file(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _text(value, name, maximum):
    if not isinstance(value, str):
        raise ValueError(name.upper() + "_MUST_BE_STRING")
    result = value.strip()
    if (not result or len(result) > maximum or
            any(ord(char) < 32 for char in result)):
        raise ValueError(name.upper() + "_INVALID")
    return result


def _time(value, name):
    value = _text(value, name, 64)
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("INVALID_" + name.upper()) from exc
    if result.tzinfo is not None:
        raise ValueError(name.upper() + "_MUST_BE_NAIVE_SOURCE_LOCAL")
    return result.isoformat()


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(name.upper() + "_MUST_BE_NUMBER")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(name.upper() + "_MUST_BE_FINITE")
    return result


def _keys(value, required, optional=()):
    if not isinstance(value, dict):
        raise ValueError("EVENT_MUST_BE_OBJECT")
    actual = set(value)
    required = set(required)
    allowed = required | set(optional)
    if not required <= actual or not actual <= allowed:
        raise ValueError("EVENT_FIELDS_SCHEMA")


class ActionCaptureStore:
    """Journal is authoritative; canonical CSV and status are projections."""

    def __init__(self, root, capture_config, action_config, collection_config):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.capture_config = capture_config
        self.action_config = action_config
        self.collection_config = collection_config
        self.journal = self.root / "events.jsonl"
        self.commands = self.root / "commands.csv"
        self.quality = self.root / "quality_samples.csv"
        self.status_path = self.root / "status.json"
        self.lock_path = self.root / ".capture.lock"
        self.journal.touch(exist_ok=True)
        self.lock_path.touch(exist_ok=True)
        with self._locked():
            events = self._read_events()
            self._materialize(events)

    def _locked(self):
        class Lock:
            def __init__(inner, path):
                inner.handle = path.open("a+")

            def __enter__(inner):
                fcntl.flock(inner.handle.fileno(), fcntl.LOCK_EX)
                return inner

            def __exit__(inner, *_):
                fcntl.flock(inner.handle.fileno(), fcntl.LOCK_UN)
                inner.handle.close()
        return Lock(self.lock_path)

    def _canonicalize(self, event_type, value):
        if event_type not in EVENT_TYPES:
            raise ValueError("UNKNOWN_EVENT_TYPE")
        maximum = self.capture_config["max_text_length"]
        event_id = _text(value.get("event_id") if isinstance(value, dict) else None,
                         "event_id", maximum)
        if event_type == "command_issued":
            _keys(value, {"event_id", "command_id", "control", "issued_time",
                          "value_before", "unit", "source_system"})
            control = _text(value["control"], "control", maximum)
            if control not in self.capture_config["controls"]:
                raise ValueError("UNKNOWN_CONTROL")
            unit = _text(value["unit"], "unit", maximum)
            if unit != self.capture_config["controls"][control]["unit"]:
                raise ValueError("COMMAND_UNIT_MISMATCH")
            payload = {
                "command_id": _text(value["command_id"], "command_id", maximum),
                "control": control,
                "issued_time": _time(value["issued_time"], "issued_time"),
                "value_before": _number(value["value_before"], "value_before"),
                "unit": unit,
                "source_system": _text(value["source_system"], "source_system", maximum),
            }
        elif event_type == "command_terminal":
            _keys(value, {"event_id", "command_id", "status", "source_system"},
                  {"executed_time", "value_after"})
            status = _text(value["status"], "status", maximum).lower()
            if status not in self.capture_config["terminal_statuses"]:
                raise ValueError("COMMAND_STATUS")
            payload = {
                "command_id": _text(value["command_id"], "command_id", maximum),
                "status": status,
                "source_system": _text(value["source_system"], "source_system", maximum),
            }
            if status == "executed":
                if "executed_time" not in value or "value_after" not in value:
                    raise ValueError("EXECUTED_FIELDS_REQUIRED")
                payload.update(
                    executed_time=_time(value["executed_time"], "executed_time"),
                    value_after=_number(value["value_after"], "value_after"),
                )
            elif "executed_time" in value or "value_after" in value:
                raise ValueError("NONEXECUTED_HAS_EXECUTION_FIELDS")
        else:
            _keys(value, {"event_id", "sample_id", "sample_time", "value_numeric",
                          "unit", "quality_status", "source_system",
                          "source_record_id"}, {"available_time"})
            status = _text(value["quality_status"], "quality_status", maximum).lower()
            if status not in {"valid", "invalid"}:
                raise ValueError("QUALITY_STATUS")
            unit = _text(value["unit"], "unit", maximum)
            if unit != self.capture_config["quality_unit"]:
                raise ValueError("QUALITY_UNIT_MISMATCH")
            sample_time = _time(value["sample_time"], "sample_time")
            available = value.get("available_time")
            if available in (None, ""):
                available = None
            else:
                available = _time(available, "available_time")
                if datetime.fromisoformat(available) < datetime.fromisoformat(sample_time):
                    raise ValueError("AVAILABLE_TIME_BEFORE_SAMPLE")
            numeric = value["value_numeric"]
            if status == "valid":
                numeric = _number(numeric, "value_numeric")
            elif numeric not in (None, ""):
                raise ValueError("INVALID_QUALITY_HAS_NUMERIC_VALUE")
            else:
                numeric = None
            payload = {
                "sample_id": _text(value["sample_id"], "sample_id", maximum),
                "sample_time": sample_time, "available_time": available,
                "value_numeric": numeric, "unit": unit, "quality_status": status,
                "source_system": _text(value["source_system"], "source_system", maximum),
                "source_record_id": _text(value["source_record_id"],
                                          "source_record_id", maximum),
            }
        return event_id, payload

    def _read_events(self):
        events = []
        previous = None
        with self.journal.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError("CORRUPT_JOURNAL_JSON_LINE_" + str(line_number)) from exc
                if record.get("schema") != "action-capture-event-v1":
                    raise ValueError("CORRUPT_JOURNAL_SCHEMA")
                claimed = record.get("record_sha256")
                core = {key: value for key, value in record.items()
                        if key != "record_sha256"}
                if core.get("previous_sha256") != previous or _digest_bytes(
                        _json_bytes(core)) != claimed:
                    raise ValueError("CORRUPT_JOURNAL_HASH_CHAIN")
                previous = claimed
                events.append(record)
        return events

    @staticmethod
    def _state(events):
        issued = {}
        terminal = {}
        quality = {}
        event_ids = {}
        for record in events:
            event_ids[record["event_id"]] = record
            payload = record["payload"]
            if record["type"] == "command_issued":
                issued[payload["command_id"]] = payload
            elif record["type"] == "command_terminal":
                terminal[payload["command_id"]] = payload
            else:
                quality[payload["sample_id"]] = payload
        return event_ids, issued, terminal, quality

    def _cross_validate(self, event_type, payload, issued, terminal, quality):
        if event_type == "quality_sample":
            if payload["sample_id"] in quality:
                raise ValueError("SAMPLE_ID_ALREADY_RECORDED")
            if any(row["source_record_id"] == payload["source_record_id"]
                   for row in quality.values()):
                raise ValueError("SOURCE_RECORD_ID_ALREADY_RECORDED")
            if any(row["sample_time"] == payload["sample_time"]
                   for row in quality.values()):
                raise ValueError("SAMPLE_TIME_ALREADY_RECORDED")
            return
        command_id = payload["command_id"]
        current = issued if event_type == "command_issued" else terminal
        if command_id in current:
            raise ValueError(event_type.upper() + "_ALREADY_RECORDED")
        other = terminal.get(command_id) if event_type == "command_issued" else issued.get(command_id)
        if other is None:
            return
        issue = payload if event_type == "command_issued" else other
        end = other if event_type == "command_issued" else payload
        if issue["source_system"] != end["source_system"]:
            raise ValueError("COMMAND_SOURCE_SYSTEM_CONFLICT")
        if (end["status"] == "executed" and
                datetime.fromisoformat(end["executed_time"]) <
                datetime.fromisoformat(issue["issued_time"])):
            raise ValueError("EXECUTED_TIME_BEFORE_ISSUE")
        if end["status"] == "executed":
            issued_at = datetime.fromisoformat(issue["issued_time"])
            executed_at = datetime.fromisoformat(end["executed_time"])
            train_start = datetime.fromisoformat(self.collection_config["train"]["start"])
            train_end = datetime.fromisoformat(
                self.collection_config["train"]["end_exclusive"])
            holdout_end = datetime.fromisoformat(
                self.collection_config["holdout"]["end_exclusive"])
            if ((train_start <= issued_at < train_end and executed_at >= train_end) or
                    (train_end <= issued_at < holdout_end and executed_at >= holdout_end)):
                raise ValueError("EXECUTION_CROSSES_REGISTERED_SPLIT")

    def _write_csv(self, path, fields, rows):
        temporary = path.with_name("." + path.name + ".tmp")
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def _materialize(self, events):
        _, issued, terminal, quality = self._state(events)
        commands = []
        for command_id, issue in issued.items():
            end = terminal.get(command_id)
            if end is None:
                continue
            row = {"command_id": command_id, "control": issue["control"],
                   "issued_time": issue["issued_time"], "source_system": issue["source_system"],
                   "status": end["status"]}
            if end["status"] == "executed":
                row.update(executed_time=end["executed_time"],
                           value_before=issue["value_before"], value_after=end["value_after"],
                           unit=issue["unit"])
            else:
                row.update(executed_time="", value_before="", value_after="", unit="")
            commands.append(row)
        commands.sort(key=lambda row: (row["issued_time"], row["command_id"]))
        samples = [dict(row) for row in quality.values()]
        samples.sort(key=lambda row: (row["sample_time"], row["sample_id"]))
        for row in samples:
            row["available_time"] = row["available_time"] or ""
            row["value_numeric"] = ("" if row["value_numeric"] is None
                                    else row["value_numeric"])
        self._write_csv(self.commands, COMMAND_COLUMNS, commands)
        self._write_csv(self.quality, QUALITY_COLUMNS, samples)
        coverage = validate_collection(self.commands, self.quality,
                                       self.action_config, self.collection_config)
        pending = sorted(set(issued) - set(terminal))
        orphan = sorted(set(terminal) - set(issued))
        complete = sorted(set(issued) & set(terminal))
        sealed = 0
        for record in events:
            payload = record["payload"]
            at = payload.get("sample_time") or payload.get("issued_time") or payload.get("executed_time")
            if at and datetime.fromisoformat(at) >= datetime(2026, 1, 1):
                sealed += 1
        status = {
            "schema": "action-capture-status-v1", "status": "operational",
            "capture_config_id": self.capture_config["id"],
            "config_sha256": {
                "capture": _digest_bytes(_json_bytes(self.capture_config)),
                "action": _digest_bytes(_json_bytes(self.action_config)),
                "collection": _digest_bytes(_json_bytes(self.collection_config)),
            },
            "events": len(events), "issued": len(issued), "terminal": len(terminal),
            "quality_samples": len(quality), "complete_commands": len(complete),
            "pending_command_ids": pending, "orphan_terminal_ids": orphan,
            "sealed_2026_plus_events": sealed, "model_fits": 0,
            "industrial_command": False, "system_initiates_commands": False,
            "commands_csv": str(self.commands), "quality_samples_csv": str(self.quality),
            "coverage": coverage,
            "sha256": {"journal": _digest_file(self.journal),
                       "commands": _digest_file(self.commands),
                       "quality_samples": _digest_file(self.quality)},
        }
        temporary = self.status_path.with_name("." + self.status_path.name + ".tmp")
        temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2,
                                        allow_nan=False) + "\n", encoding="utf-8")
        os.replace(temporary, self.status_path)
        return status

    def record(self, event_type, value):
        event_id, payload = self._canonicalize(event_type, value)
        with self._locked():
            events = self._read_events()
            event_ids, issued, terminal, quality = self._state(events)
            if event_id in event_ids:
                prior = event_ids[event_id]
                if prior["type"] == event_type and prior["payload"] == payload:
                    status = self._materialize(events)
                    return {"accepted": True, "idempotent": True,
                            "event_id": event_id, "capture": status}
                raise ValueError("EVENT_ID_CONFLICT")
            self._cross_validate(event_type, payload, issued, terminal, quality)
            previous = events[-1]["record_sha256"] if events else None
            core = {"schema": "action-capture-event-v1", "event_id": event_id,
                    "type": event_type,
                    "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                    "previous_sha256": previous, "payload": payload}
            record = {**core, "record_sha256": _digest_bytes(_json_bytes(core))}
            with self.journal.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True,
                                        allow_nan=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            events.append(record)
            status = self._materialize(events)
            return {"accepted": True, "idempotent": False,
                    "event_id": event_id, "capture": status}

    def status(self):
        with self._locked():
            return self._materialize(self._read_events())
