import copy
import json
from pathlib import Path
import tempfile
import unittest

import pipeline as p


class M11T3Sma3IndicatorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / "state.json"
        self.fixture = self.root / "operational-input.json"
        self.acceptance = self.root / "operational-acceptance.json"
        self.indicator = self.root / "sma3-indicator.json"
        self.output = self.root / "output"
        self.session_id = "PAPER_SESSION|m11-t1-historical-001"
        p.initialize_paper_session(
            self.state, self.root / "init", self.session_id, "PAPER",
            "2024-01-05T00:00:00Z")
        p.prepare_paper_session_fixture(
            self.state, self.fixture, self.root / "fixture-output",
            self.observations(), "2024-01-05T00:05:00Z")
        p.validate_paper_session_operational_observation(
            self.state, self.fixture, self.acceptance, self.root / "acceptance-output")

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

    def compose(self, output=None):
        return p.compose_paper_session_sma3(
            self.state, self.fixture, self.acceptance, self.indicator,
            output or self.output)

    def read_fixture(self):
        return json.loads(self.fixture.read_bytes())

    def write_fixture(self, fixture):
        self.fixture.write_bytes(p.encoded(fixture))

    def test_calculates_causal_sma3_from_three_warmup_and_operational_close(self):
        state_before = self.state.read_bytes()
        result = self.compose()
        self.assertEqual(result["warmup_observations"], 3)
        self.assertEqual(result["operational_observations"], 1)
        self.assertAlmostEqual(result["sma_close_3"], (43000.0 + 43500.0 + 44000.0) / 3)
        self.assertEqual(result["input_observation_identities"], [
            "BTC-USD|86400|2024-01-02T00:00:00Z",
            "BTC-USD|86400|2024-01-03T00:00:00Z",
            "BTC-USD|86400|2024-01-04T00:00:00Z",
        ])
        self.assertEqual(result["processing_instant_utc"], "2024-01-05T00:05:00Z")
        self.assertEqual(result["lookahead"], "NOT_USED")
        self.assertEqual(self.state.read_bytes(), state_before)
        self.assertEqual(result["decisions"], 0)
        self.assertEqual(result["executions"], 0)
        self.assertEqual(result["pending_actions"], 0)
        self.assertEqual(result["proposals"], 0)

    def test_persistence_reload_idempotence_and_determinism(self):
        first = self.compose()
        indicator_before = self.indicator.read_bytes()
        state_before = self.state.read_bytes()
        replay = self.compose(output=self.root / "replay-output")
        self.assertFalse(replay["created"])
        self.assertEqual(self.indicator.read_bytes(), indicator_before)
        self.assertEqual(self.state.read_bytes(), state_before)
        self.assertEqual(replay["indicator_sha256"], first["indicator_sha256"])
        persisted = json.loads(self.indicator.read_bytes())
        self.assertEqual(persisted["processing_instant_utc"], "2024-01-05T00:05:00Z")
        self.assertEqual(persisted["lookahead"], "NOT_USED")
        self.assertEqual(persisted["input_observation_identities"],
                         first["input_observation_identities"])

    def test_rejects_unaccepted_operational_observation(self):
        acceptance = json.loads(self.acceptance.read_bytes())
        acceptance["observation_accepted"] = False
        self.acceptance.write_bytes(p.encoded(acceptance))
        with self.assertRaises(ValueError):
            self.compose()

    def test_rejects_missing_or_ambiguous_processing_instant(self):
        for processing in (None, "2024-01-05T00:05:00", "2024-01-05T00:05:00+01:00"):
            with self.subTest(processing=processing):
                fixture = self.read_fixture()
                if processing is None:
                    del fixture["processing_instant_utc"]
                else:
                    fixture["processing_instant_utc"] = processing
                self.write_fixture(fixture)
                with self.assertRaises(ValueError):
                    self.compose(output=self.root / f"bad-processing-{processing or 'missing'}")
                self.write_fixture(self._valid_fixture())

    def test_rejects_open_or_future_operational_observation(self):
        for processing in ("2024-01-04T23:59:59Z", "2024-01-03T00:00:00Z"):
            with self.subTest(processing=processing):
                fixture = self._valid_fixture()
                fixture["processing_instant_utc"] = processing
                self.write_fixture(fixture)
                with self.assertRaises(ValueError):
                    self.compose(output=self.root / f"bad-candle-{processing}")

    def test_rejects_warmup_contamination_and_phase3_state(self):
        fixture = self._valid_fixture()
        fixture["warmup_observations"] = [copy.deepcopy(fixture["operational_observations"][0])] * 3
        self.write_fixture(fixture)
        with self.assertRaises(ValueError):
            self.compose(output=self.root / "contaminated")
        phase3 = self.root / "phase3.json"
        phase3.write_bytes(p.encoded({
            "instrument": "BTC-USD", "frequency_seconds": 86400,
            "observations": [], "decisions": [], "virtual_position": 0}))
        with self.assertRaises(ValueError):
            p.compose_paper_session_sma3(
                phase3, self.fixture, self.acceptance, self.indicator,
                self.root / "phase3-output")

    def test_warmup_remains_separate_and_has_no_acceptance_requirement(self):
        fixture = self.read_fixture()
        self.assertEqual(len(fixture["warmup_observations"]), 3)
        self.assertEqual(len(fixture["operational_observations"]), 1)
        self.assertTrue(all("accepted_at_utc" not in item
                            for item in fixture["warmup_observations"]))
        result = self.compose()
        self.assertEqual(result["warmup_observations"], 3)

    def _valid_fixture(self):
        fixture = self.read_fixture()
        fixture["processing_instant_utc"] = "2024-01-05T00:05:00Z"
        fixture["warmup_observations"] = self.observations()[:3]
        fixture["operational_observations"] = self.observations()[3:]
        return fixture


if __name__ == "__main__":
    unittest.main()
