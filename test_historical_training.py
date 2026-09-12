import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from neft.historical_training import portable_config, prepare_telemetry


class HistoricalTrainingTest(unittest.TestCase):
    def test_telemetry_stops_before_parsing_future_numeric_values(self):
        tags = ["F1", "P8", "T11", "F19"]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / "telemetry.csv"; output = root / "prepared.npz"
            source.write_text(
                ",date," + ",".join(tags) + "\n" +
                "0,2023-01-01 00:00:00,1,2,3,4\n" +
                "1,2024-12-31 23:50:00,5,6,7,8\n" +
                "2,2025-01-01 00:00:00,SECRET,SECRET,SECRET,SECRET\n",
                encoding="utf-8")
            audit = prepare_telemetry(source, output, "2023-01-01T00:00:00",
                                      "2025-01-01T00:00:00", set(tags))
            data = np.load(output, allow_pickle=False)
            self.assertEqual(data["values"].shape, (2, 4))
            self.assertEqual(audit["stopping_timestamp_only"], "2025-01-01T00:00:00")
            self.assertFalse(audit["later_numeric_values_parsed"])

    def test_portable_config_hash_pins_only_prepared_inputs(self):
        template = json.loads((Path(__file__).parent / "configs/historical_intelligence_v1.json").read_text())
        config = portable_config(template, "prepared/t.npz", "a" * 64,
                                 "prepared/q.jsonl", "b" * 64)
        self.assertEqual(set(config["source_sha256"]), {"prepared/t.npz", "prepared/q.jsonl"})
        self.assertFalse(config["agent_contract"]["pac_included"])


if __name__ == "__main__":
    unittest.main()
