import copy
import json
from pathlib import Path
import tempfile
import unittest

import pipeline as p


class M11T1PaperFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state_path = self.root / "session" / "state.json"
        self.fixture_path = self.root / "session" / "historical-input.json"
        self.output_path = self.root / "evidence"
        self.session_id = "PAPER_SESSION|m11-t1-historical-001"
        self.started_at = "2024-01-05T00:00:00Z"
        p.initialize_paper_session(
            self.state_path, self.root / "init", self.session_id, "PAPER", self.started_at)

    @staticmethod
    def observations():
        values = (
            ("2024-01-01T00:00:00Z", 42000.0, 43000.0, 41000.0, 42500.0, 100.0),
            ("2024-01-02T00:00:00Z", 42500.0, 43500.0, 41500.0, 43000.0, 110.0),
            ("2024-01-03T00:00:00Z", 43000.0, 44000.0, 42000.0, 43500.0, 120.0),
            ("2024-01-04T00:00:00Z", 43500.0, 44500.0, 42500.0, 44000.0, 130.0),
        )
        return [
            {"identity": f"BTC-USD|86400|{timestamp}", "instrument": "BTC-USD",
             "timestamp": timestamp, "open": opening, "high": high, "low": low,
             "close": close, "volume": volume,
             **({"accepted_at_utc": "2024-01-05T00:01:00Z"}
                if timestamp == "2024-01-04T00:00:00Z" else {})}
            for timestamp, opening, high, low, close, volume in values
        ]

    def prepare(self, observations=None, processing="2024-01-05T00:05:00Z", output=None):
        return p.prepare_paper_session_fixture(
            self.state_path, self.fixture_path, output or self.output_path,
            observations or self.observations(), processing)

    def test_prepares_persisted_isolated_fixture_without_processing(self):
        result = self.prepare()
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["created"])
        self.assertEqual(result["warmup_observations"], 3)
        self.assertEqual(result["operational_input_observations"], 1)
        self.assertEqual(result["processed_operational_observations"], 0)
        self.assertEqual(result["decisions"], 0)
        self.assertEqual(result["executions"], 0)
        self.assertEqual(result["pending_actions"], 0)
        state = p.load_paper_session(self.state_path)
        fixture = p.load_paper_session_fixture(self.state_path, self.fixture_path)
        self.assertEqual(state["mode"], "PAPER")
        self.assertEqual(state["instrument"], "BTC-USD")
        self.assertEqual(state["started_at"], self.started_at)
        self.assertEqual(state["internal_position_state"], "FLAT")
        self.assertEqual(state["broker_position_observed"], "UNKNOWN")
        self.assertEqual(state["reconciliation_status"], "PENDING_BROKER_OBSERVATION")
        self.assertEqual(state["warmup_observations"], self.observations()[:3])
        self.assertEqual(state["processed_observations"], [])
        self.assertEqual(fixture["operational_observations"], self.observations()[3:])
        self.assertEqual(fixture["processing_instant_utc"], "2024-01-05T00:05:00Z")
        self.assertEqual(len(fixture["warmup_observations"]), 3)
        self.assertEqual(len(fixture["operational_observations"]), 1)
        self.assertEqual(fixture["operational_observations"][0]["accepted_at_utc"],
                         "2024-01-05T00:01:00Z")

    def test_acceptance_temporal_boundaries(self):
        for accepted_at, should_pass in (
                ("2024-01-05T00:01:00Z", True),
                ("2024-01-05T00:00:00Z", False),
                ("2024-01-04T23:59:59Z", False),
                (None, False)):
            with self.subTest(accepted_at=accepted_at):
                observations = self.observations()
                if accepted_at is None:
                    del observations[3]["accepted_at_utc"]
                else:
                    observations[3]["accepted_at_utc"] = accepted_at
                if should_pass:
                    self.prepare(observations)
                else:
                    with self.assertRaises(ValueError):
                        self.prepare(observations)

    def test_processing_instant_before_operational_close_is_rejected(self):
        with self.assertRaises(ValueError):
            self.prepare(processing="2024-01-04T23:59:59Z")

    def test_reload_is_idempotent_and_does_not_process_operational_input(self):
        first = self.prepare()
        state_before = self.state_path.read_bytes()
        fixture_before = self.fixture_path.read_bytes()
        replay = self.prepare(output=self.root / "evidence-replay")
        self.assertFalse(replay["created"])
        self.assertEqual(self.state_path.read_bytes(), state_before)
        self.assertEqual(self.fixture_path.read_bytes(), fixture_before)
        self.assertEqual(replay["fixture_sha256"], first["fixture_sha256"])

    def test_rejects_missing_ambiguous_or_live_mode(self):
        for mode in (None, "", "UNKNOWN", "LIVE"):
            with self.subTest(mode=mode):
                root = self.root / f"mode-{mode or 'missing'}"
                state = root / "state.json"
                with self.assertRaises(ValueError):
                    p.initialize_paper_session(
                        state, root / "output", self.session_id, mode, self.started_at)
                self.assertFalse(state.exists())

    def test_rejects_open_invalid_duplicate_and_out_of_order_input(self):
        cases = []
        open_candle = self.observations()
        open_candle[3]["timestamp"] = "2024-01-04T00:00:00Z"
        cases.append((open_candle, "2024-01-04T12:00:00Z"))
        invalid_ohlcv = self.observations()
        invalid_ohlcv[2]["low"] = 50000.0
        cases.append((invalid_ohlcv, "2024-01-05T00:05:00Z"))
        duplicate = self.observations()
        duplicate[3] = copy.deepcopy(duplicate[2])
        cases.append((duplicate, "2024-01-05T00:05:00Z"))
        unordered = self.observations()
        unordered[2], unordered[3] = unordered[3], unordered[2]
        cases.append((unordered, "2024-01-05T00:05:00Z"))
        for index, (observations, processing) in enumerate(cases):
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    self.prepare(observations, processing)
        self.assertEqual(p.load_paper_session(self.state_path)["warmup_observations"], [])
        self.assertFalse(self.fixture_path.exists())

    def test_rejects_phase3_state_and_missing_processing_instant(self):
        phase3 = self.root / "phase3.json"
        phase3.write_bytes(p.encoded({
            "instrument": "BTC-USD", "frequency_seconds": 86400,
            "observations": [], "decisions": [], "virtual_position": 0}))
        with self.assertRaises(ValueError):
            p.prepare_paper_session_fixture(
                phase3, self.fixture_path, self.output_path,
                self.observations(), "2024-01-05T00:05:00Z")
        self.assertEqual(json.loads(phase3.read_bytes())["virtual_position"], 0)
        with self.assertRaises(ValueError):
            self.prepare(processing=None)
        self.assertFalse(self.fixture_path.exists())


if __name__ == "__main__":
    unittest.main()
