import copy
import json
from pathlib import Path
import tempfile
import unittest

import pipeline as p


class M11T2TemporalEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / "state.json"
        self.fixture = self.root / "operational-input.json"
        self.acceptance = self.root / "acceptance.json"
        self.output = self.root / "output"
        self.session_id = "PAPER_SESSION|m11-t1-historical-001"
        self.started_at = "2024-01-05T00:00:00Z"
        p.initialize_paper_session(
            self.state, self.root / "init", self.session_id, "PAPER", self.started_at)
        p.prepare_paper_session_fixture(
            self.state, self.fixture, self.root / "fixture-output",
            self.observations(), "2024-01-05T00:05:00Z")

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

    def validate(self, output=None):
        return p.validate_paper_session_operational_observation(
            self.state, self.fixture, self.acceptance, output or self.output)

    def read_fixture(self):
        return json.loads(self.fixture.read_bytes())

    def write_fixture(self, fixture):
        self.fixture.write_bytes(p.encoded(fixture))

    def test_accepts_closed_operational_observation_without_lookahead(self):
        state_before = self.state.read_bytes()
        result = self.validate()
        self.assertTrue(result["observation_accepted"])
        self.assertEqual(result["lookahead"], "NOT_USED")
        self.assertEqual(result["processing_instant_utc"], "2024-01-05T00:05:00Z")
        self.assertEqual(result["interval_start_utc"], "2024-01-04T00:00:00Z")
        self.assertEqual(result["interval_end_utc"], "2024-01-05T00:00:00Z")
        self.assertEqual(result["accepted_at_utc"], "2024-01-05T00:01:00Z")
        self.assertEqual(self.state.read_bytes(), state_before)
        self.assertEqual(result["decisions"], 0)
        self.assertEqual(result["executions"], 0)
        self.assertEqual(result["pending_actions"], 0)
        self.assertEqual(result["proposals"], 0)

    def test_persists_and_reloads_acceptance_idempotently(self):
        first = self.validate()
        acceptance_before = self.acceptance.read_bytes()
        state_before = self.state.read_bytes()
        replay = self.validate(output=self.root / "replay-output")
        self.assertFalse(replay["created"])
        self.assertEqual(self.acceptance.read_bytes(), acceptance_before)
        self.assertEqual(self.state.read_bytes(), state_before)
        self.assertEqual(replay["acceptance_sha256"], first["acceptance_sha256"])
        persisted = json.loads(self.acceptance.read_bytes())
        self.assertEqual(persisted["processing_instant_utc"], "2024-01-05T00:05:00Z")
        self.assertEqual(persisted["lookahead"], "NOT_USED")

    def test_processing_instant_is_required_and_must_be_utc(self):
        for processing in (None, "", "2024-01-05T00:05:00+01:00", "2024-01-05T00:05:00"):
            with self.subTest(processing=processing):
                fixture = self.read_fixture()
                if processing is None:
                    del fixture["processing_instant_utc"]
                else:
                    fixture["processing_instant_utc"] = processing
                self.write_fixture(fixture)
                with self.assertRaises(ValueError):
                    self.validate(output=self.root / f"bad-{processing or 'missing'}")
                self.write_fixture(self._valid_fixture())

    def test_open_operational_candle_is_rejected(self):
        fixture = self.read_fixture()
        fixture["processing_instant_utc"] = "2024-01-04T23:59:59Z"
        self.write_fixture(fixture)
        with self.assertRaises(ValueError):
            self.validate()
        self.assertFalse(self.acceptance.exists())

    def test_acceptance_timestamp_is_required_and_after_session_start(self):
        for accepted_at in (None, "2024-01-05T00:00:00Z", "2024-01-04T23:59:59Z"):
            with self.subTest(accepted_at=accepted_at):
                fixture = self._valid_fixture()
                operational = fixture["operational_observations"][0]
                if accepted_at is None:
                    del operational["accepted_at_utc"]
                else:
                    operational["accepted_at_utc"] = accepted_at
                self.write_fixture(fixture)
                with self.assertRaises(ValueError):
                    self.validate(output=self.root / f"bad-acceptance-{accepted_at}")

    def test_warmup_does_not_require_acceptance_timestamp(self):
        fixture = self.read_fixture()
        self.assertTrue(all("accepted_at_utc" not in item
                            for item in fixture["warmup_observations"]))
        result = self.validate()
        self.assertTrue(result["observation_accepted"])

    def _valid_fixture(self):
        fixture = self.read_fixture()
        fixture["processing_instant_utc"] = "2024-01-05T00:05:00Z"
        fixture["operational_observations"][0]["accepted_at_utc"] = "2024-01-05T00:01:00Z"
        return fixture


if __name__ == "__main__":
    unittest.main()
