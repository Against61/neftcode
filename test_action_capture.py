import csv
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from neft.action_capture import (ActionCaptureStore, COMMAND_COLUMNS,
                                 QUALITY_COLUMNS)
from neft.action_capture_lims import quality_events
from scripts.serve_action_capture import make_server


ROOT = Path(__file__).resolve().parent
CAPTURE = json.loads((ROOT / "configs/action_capture_v1.json").read_text())
ACTION = json.loads((ROOT / "configs/action_outcome_v1.json").read_text())
COLLECTION = json.loads((ROOT / "configs/action_collection_v1.json").read_text())
SEED = json.loads((ROOT / "configs/action_capture_lims_seed_v1.json").read_text())


def issue(event_id="e-issue", command_id="cmd-1", at="2024-01-15T10:00:00"):
    return {"event_id": event_id, "command_id": command_id, "control": "P8",
            "issued_time": at, "value_before": 300.0, "unit": "degC",
            "source_system": "DCS"}


def terminal(event_id="e-terminal", command_id="cmd-1",
             at="2024-01-15T10:05:00"):
    return {"event_id": event_id, "command_id": command_id, "status": "executed",
            "executed_time": at, "value_after": 302.0, "source_system": "DCS"}


def sample(event_id="e-sample", sample_id="sample-1",
           at="2024-01-15T10:35:00", value=8.0):
    return {"event_id": event_id, "sample_id": sample_id, "sample_time": at,
            "value_numeric": value, "unit": "mg/kg", "quality_status": "valid",
            "source_system": "LIMS", "source_record_id": "record-" + sample_id}


class ActionCaptureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="neft-action-capture-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = ActionCaptureStore(self.root, CAPTURE, ACTION, COLLECTION)

    def rows(self, name):
        with (self.root / name).open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def test_empty_store_has_exact_csv_and_zero_fit_coverage(self):
        with (self.root / "commands.csv").open(newline="") as handle:
            self.assertEqual(next(csv.reader(handle)), COMMAND_COLUMNS)
        with (self.root / "quality_samples.csv").open(newline="") as handle:
            self.assertEqual(next(csv.reader(handle)), QUALITY_COLUMNS)
        status = self.store.status()
        self.assertEqual(status["events"], 0)
        self.assertEqual(status["coverage"]["status"], "COLLECTION_INCOMPLETE")
        self.assertEqual(status["coverage"]["model_fits"], 0)
        self.assertFalse(status["industrial_command"])

    def test_two_phase_command_materializes_and_replay_is_idempotent(self):
        first = self.store.record("command_issued", issue())
        self.assertEqual(first["capture"]["pending_command_ids"], ["cmd-1"])
        self.assertEqual(self.rows("commands.csv"), [])
        complete = self.store.record("command_terminal", terminal())
        self.assertEqual(complete["capture"]["complete_commands"], 1)
        row = self.rows("commands.csv")[0]
        self.assertEqual(row["issued_time"], "2024-01-15T10:00:00")
        self.assertEqual(row["executed_time"], "2024-01-15T10:05:00")
        self.assertEqual(row["value_after"], "302.0")
        replay = self.store.record("command_terminal", terminal())
        self.assertTrue(replay["idempotent"])
        self.assertEqual(len((self.root / "events.jsonl").read_text().splitlines()), 2)

    def test_terminal_may_arrive_first_but_pair_is_checked(self):
        pending = self.store.record("command_terminal", terminal())
        self.assertEqual(pending["capture"]["orphan_terminal_ids"], ["cmd-1"])
        self.assertEqual(self.rows("commands.csv"), [])
        done = self.store.record("command_issued", issue())
        self.assertEqual(done["capture"]["orphan_terminal_ids"], [])
        self.assertEqual(len(self.rows("commands.csv")), 1)
        self.store.record("command_terminal", terminal(
            event_id="other-terminal", command_id="other"))
        with self.assertRaisesRegex(ValueError, "EXECUTED_TIME_BEFORE_ISSUE"):
            self.store.record("command_issued", issue(
                event_id="late-issue", command_id="other", at="2024-01-15T11:00:00"))

    def test_event_id_conflict_cannot_change_artifacts(self):
        self.store.record("command_issued", issue())
        before = self.store.status()["sha256"]
        changed = issue()
        changed["value_before"] = 999.0
        with self.assertRaisesRegex(ValueError, "EVENT_ID_CONFLICT"):
            self.store.record("command_issued", changed)
        self.assertEqual(self.store.status()["sha256"], before)

    def test_execution_cannot_cross_registered_split(self):
        self.store.record("command_issued", issue(at="2024-12-31T23:55:00"))
        before = self.store.status()["sha256"]
        with self.assertRaisesRegex(ValueError, "EXECUTION_CROSSES_REGISTERED_SPLIT"):
            self.store.record("command_terminal", terminal(at="2025-01-01T00:05:00"))
        self.assertEqual(self.store.status()["sha256"], before)

    def test_quality_keeps_unknown_availability_and_rejects_duplicate_time(self):
        result = self.store.record("quality_sample", sample())
        self.assertEqual(result["capture"]["quality_samples"], 1)
        row = self.rows("quality_samples.csv")[0]
        self.assertEqual(row["available_time"], "")
        self.assertEqual(result["capture"]["coverage"]["audit"]["quality"]
                         ["unknown_available_time"], 1)
        with self.assertRaisesRegex(ValueError, "SAMPLE_TIME_ALREADY_RECORDED"):
            self.store.record("quality_sample", sample(
                event_id="e-sample-2", sample_id="sample-2"))

    def test_current_events_are_captured_but_sealed_from_coverage(self):
        result = self.store.record("quality_sample", sample(
            at="2026-09-11T12:00:00", value=123.456))
        status = result["capture"]
        self.assertEqual(status["sealed_2026_plus_events"], 1)
        audit = status["coverage"]["audit"]["quality"]
        self.assertEqual(audit["rows_in_train"], 0)
        self.assertEqual(audit["stopping_timestamp_only"], "2026-09-11T12:00:00")
        self.assertFalse(status["coverage"]["sealed_2026_numeric_read"])

    def test_failed_command_has_no_execution_values_in_canonical_csv(self):
        self.store.record("command_issued", issue())
        self.store.record("command_terminal", {
            "event_id": "failed", "command_id": "cmd-1", "status": "failed",
            "source_system": "DCS"})
        row = self.rows("commands.csv")[0]
        self.assertEqual(row["status"], "failed")
        for field in ("executed_time", "value_before", "value_after", "unit"):
            self.assertEqual(row[field], "")

    def test_hash_chain_tampering_is_detected(self):
        self.store.record("quality_sample", sample())
        text = (self.root / "events.jsonl").read_text()
        (self.root / "events.jsonl").write_text(text.replace("8.0", "9.0"))
        with self.assertRaisesRegex(ValueError, "CORRUPT_JOURNAL_HASH_CHAIN"):
            self.store.status()

    def test_batch_conflict_is_atomic_and_replay_is_idempotent(self):
        first = sample(event_id="batch-1", sample_id="batch-sample-1",
                       at="2024-02-01T10:00:00")
        conflict = sample(event_id="batch-2", sample_id="batch-sample-2",
                          at="2024-02-01T10:00:00")
        with self.assertRaisesRegex(ValueError, "SAMPLE_TIME_ALREADY_RECORDED"):
            self.store.record_many([("quality_sample", first),
                                    ("quality_sample", conflict)])
        self.assertEqual(self.store.status()["events"], 0)
        second = sample(event_id="batch-2", sample_id="batch-sample-2",
                        at="2024-02-01T11:00:00")
        added = self.store.record_many([("quality_sample", first),
                                        ("quality_sample", second)])
        replay = self.store.record_many([("quality_sample", first),
                                         ("quality_sample", second)])
        self.assertEqual((added["new_events"], replay["new_events"]), (2, 0))
        self.assertEqual(replay["idempotent_events"], 2)

    def test_lims_mapping_has_deterministic_source_ids_and_unknown_availability(self):
        rows = [{"event_time": "2024-01-01T10:00:00", "value_numeric": 7.5,
                 "unit_canonical": "mg/kg", "sheet": "Лист1", "row": 55,
                 "value_column": "CR"}]
        events = quality_events(rows, {"sha256": "a" * 64}, SEED)
        event_type, value = events[0]
        self.assertEqual(event_type, "quality_sample")
        self.assertIn("aaaaaaaaaaaaaaaa:Лист1:55:CR", value["event_id"])
        self.assertEqual(value["source_record_id"], "a" * 64 + ":Лист1:55:CR")
        self.assertNotIn("available_time", value)

    def test_loopback_http_accepts_json_and_exposes_status(self):
        server = make_server(("127.0.0.1", 0), self.store, CAPTURE)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("POST", "/api/v1/commands/issued",
                           body=json.dumps(issue()),
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertTrue(json.loads(response.read())["accepted"])
        connection.request("GET", "/api/v1/status")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read())["pending_command_ids"], ["cmd-1"])
        connection.close()


if __name__ == "__main__":
    unittest.main()
