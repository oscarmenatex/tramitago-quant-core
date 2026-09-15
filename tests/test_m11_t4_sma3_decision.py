import copy
import json
from pathlib import Path
import tempfile
import unittest

import pipeline as p


class M11T4Sma3DecisionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / "state.json"
        self.fixture = self.root / "operational-input.json"
        self.acceptance = self.root / "operational-acceptance.json"
        self.indicator = self.root / "indicator.json"
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
        p.compose_paper_session_sma3(
            self.state, self.fixture, self.acceptance, self.indicator,
            self.root / "indicator-output")

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

    def persist(self, output=None):
        return p.persist_paper_session_sma3_decision(
            self.state, self.fixture, self.acceptance, self.indicator,
            output or self.output)

    def read_json(self, path):
        return json.loads(Path(path).read_bytes())

    def read_indicator(self):
        return self.read_json(self.indicator)

    def write_indicator(self, indicator):
        self.indicator.write_bytes(p.encoded(indicator))

    def test_consumes_indicator_and_persists_natural_enter(self):
        result = self.persist()
        decision = result["decision"]
        self.assertEqual(decision["decision"], "ENTER")
        self.assertEqual(decision["observation_identity"],
                         "BTC-USD|86400|2024-01-04T00:00:00Z")
        self.assertEqual(decision["sma_close_3"], 43500.0)
        self.assertEqual(decision["previous_position"], 0)
        self.assertEqual(decision["target_position"], 1)
        self.assertEqual(decision["processing_instant_utc"], "2024-01-05T00:05:00Z")
        self.assertEqual(decision["lookahead"], "NOT_USED")
        self.assertTrue(decision["configuration_identity"].startswith("SMA3_CONFIG|"))
        state = p.load_paper_session(self.state)
        self.assertEqual(len(state["processed_observations"]), 1)
        self.assertEqual(len(state["decisions"]), 1)
        self.assertEqual(state["executions"], [])
        self.assertEqual(state["pending_actions"], [])
        self.assertEqual(result["proposals"], 0)

    def test_reload_and_repetition_are_idempotent_and_deterministic(self):
        first = self.persist()
        before = self.state.read_bytes()
        decision_file = self.root / "decision.json"
        decision_file.write_bytes(p.encoded(first["decision"]))
        replay = self.persist(output=self.root / "replay-output")
        self.assertFalse(replay["created"])
        self.assertEqual(replay["decision"], first["decision"])
        self.assertEqual(replay["state_sha256"], first["state_sha256"])
        self.assertEqual(self.state.read_bytes(), before)
        recovered = p.load_paper_session(self.state)
        self.assertEqual(recovered["decisions"], [first["decision"]])
        self.assertEqual(recovered["processed_observations"], self.observations()[3:])

    def test_natural_hold_and_exit_use_previous_position_without_recalculation(self):
        indicator = self.read_indicator()
        indicator["sma_close_3"] = 44000.0
        self.write_indicator(indicator)
        hold = self.persist(output=self.root / "hold-output")
        self.assertEqual(hold["decision"]["decision"], "HOLD")
        state = p.load_paper_session(self.state)
        state["decisions"][0]["identity"] = "SMA3_DECISION|prior-position"
        state["decisions"][0]["target_position"] = 1
        self.state.write_bytes(p.encoded(state))
        exit_result = self.persist(output=self.root / "exit-output")
        self.assertEqual(exit_result["decision"]["decision"], "EXIT")
        self.assertEqual(exit_result["decision"]["previous_position"], 1)

    def test_missing_or_altered_inputs_are_rejected_without_state_change(self):
        cases = []
        missing = self.root / "missing-indicator.json"
        cases.append((missing, self.acceptance, "missing"))
        altered = self.read_indicator()
        altered["parameters"] = {"window": 4, "source": "close", "formula": "mean(close[t-2:t+1])"}
        altered_path = self.root / "altered-indicator.json"
        altered_path.write_bytes(p.encoded(altered))
        cases.append((altered_path, self.acceptance, "altered"))
        unaccepted = self.read_json(self.acceptance)
        unaccepted["observation_accepted"] = False
        unaccepted_path = self.root / "unaccepted.json"
        unaccepted_path.write_bytes(p.encoded(unaccepted))
        cases.append((self.indicator, unaccepted_path, "unaccepted"))
        before = self.state.read_bytes()
        for indicator_path, acceptance_path, name in cases:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    p.persist_paper_session_sma3_decision(
                        self.state, self.fixture, acceptance_path, indicator_path,
                        self.root / f"bad-{name}")
                self.assertEqual(self.state.read_bytes(), before)

    def test_phase3_state_and_future_observation_are_rejected(self):
        phase3 = self.root / "phase3.json"
        phase3.write_bytes(p.encoded({
            "instrument": "BTC-USD", "frequency_seconds": 86400,
            "observations": [], "decisions": [], "virtual_position": 0}))
        with self.assertRaises(ValueError):
            p.persist_paper_session_sma3_decision(
                phase3, self.fixture, self.acceptance, self.indicator,
                self.root / "phase3-output")
        fixture = self.read_json(self.fixture)
        fixture["operational_observations"][0]["timestamp"] = "2024-01-05T00:00:00Z"
        fixture["operational_observations"][0]["identity"] = "BTC-USD|86400|2024-01-05T00:00:00Z"
        future = self.root / "future.json"
        future.write_bytes(p.encoded(fixture))
        with self.assertRaises(ValueError):
            p.persist_paper_session_sma3_decision(
                self.state, future, self.acceptance, self.indicator,
                self.root / "future-output")


if __name__ == "__main__":
    unittest.main()
