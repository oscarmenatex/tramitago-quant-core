import json
from pathlib import Path
import tempfile
import unittest

import pipeline as p


class M11T6PaperCycleResultTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.decision = self.root / "decision-output"
        self.risk = self.root / "risk-output"
        self.state = self.root / "state.json"
        self.fixture = self.root / "operational-input.json"
        self.acceptance = self.root / "operational-acceptance.json"
        self.indicator = self.root / "indicator.json"
        p.initialize_paper_session(
            self.state, self.root / "init", "PAPER_SESSION|t6-cycle-001", "PAPER",
            "2024-01-05T00:00:00Z")
        observations = self.observations()
        p.prepare_paper_session_fixture(
            self.state, self.fixture, self.root / "fixture-output", observations,
            "2024-01-05T00:05:00Z")
        p.validate_paper_session_operational_observation(
            self.state, self.fixture, self.acceptance, self.root / "acceptance-output")
        p.compose_paper_session_sma3(
            self.state, self.fixture, self.acceptance, self.indicator,
            self.root / "indicator-output")
        p.persist_paper_session_sma3_decision(
            self.state, self.fixture, self.acceptance, self.indicator, self.decision)
        decision = p.load_paper_session(self.state)["decisions"][0]
        p.apply_paper_risk_to_session(
            self.state, self.risk, decision["identity"], "2024-01-05T00:06:00Z")

    @staticmethod
    def observations():
        values = (
            ("2024-01-01T00:00:00Z", 42000.0, 43000.0, 41000.0, 42500.0, 100.0),
            ("2024-01-02T00:00:00Z", 42500.0, 43500.0, 41500.0, 43000.0, 110.0),
            ("2024-01-03T00:00:00Z", 43000.0, 44000.0, 42000.0, 43500.0, 120.0),
            ("2024-01-04T00:00:00Z", 43500.0, 44500.0, 42500.0, 44000.0, 130.0),
        )
        return [{"identity": f"BTC-USD|86400|{stamp}", "instrument": "BTC-USD",
                 "timestamp": stamp, "open": opening, "high": high, "low": low,
                 "close": close, "volume": volume,
                 **({"accepted_at_utc": "2024-01-05T00:01:00Z"}
                    if stamp == "2024-01-04T00:00:00Z" else {})}
                for stamp, opening, high, low, close, volume in values]

    def run_cycle(self, cycle_id, cycle_path=None, output=None):
        return p.run_paper_cycle(
            self.state, self.fixture, self.acceptance, self.indicator,
            cycle_path or self.root / "cycle.json", output or self.root / "cycle-output",
            cycle_id, "2024-01-05T00:05:00Z")

    @staticmethod
    def cycle_id():
        return ("PAPER_CYCLE|PAPER_SESSION|t6-cycle-001|"
                "BTC-USD|86400|2024-01-04T00:00:00Z|2024-01-05T00:05:00Z")

    def test_valid_cycle_completes_with_reconstructible_evidence(self):
        result = self.run_cycle(self.cycle_id())
        self.assertEqual(result["terminal_result"], "COMPLETED")
        evidence = json.loads((self.root / "cycle.json").read_bytes())
        self.assertEqual(evidence["decision"]["decision"], "ENTER")
        self.assertEqual(evidence["risk"]["risk_result"], "ALLOWED")
        self.assertEqual(evidence["position_before"], "FLAT")
        self.assertEqual(evidence["position_after"], "LONG")
        self.assertEqual(evidence["broker_position_observed"], "UNKNOWN")
        self.assertEqual(evidence["indicator"]["sma_close_3"], 43500.0)
        self.assertEqual(evidence["validations"]["lookahead"], "NOT_USED")

    def test_replay_is_idempotent_and_no_new_observation_is_nothing_due(self):
        first = self.run_cycle(self.cycle_id())
        before = (self.root / "cycle.json").read_bytes()
        replay = self.run_cycle(self.cycle_id(), self.root / "cycle-replay.json", self.root / "replay-output")
        self.assertFalse(replay["created"] is False and replay["terminal_result"] != "COMPLETED")
        self.assertEqual((self.root / "cycle-replay.json").read_bytes(), before)
        due = self.run_cycle("PAPER_CYCLE|other-cycle", self.root / "nothing.json", self.root / "nothing-output")
        self.assertEqual(due["terminal_result"], "NOTHING_DUE")

    def test_missing_or_inconsistent_inputs_are_blocked(self):
        fixture = json.loads(self.fixture.read_bytes())
        fixture["processing_instant_utc"] = "2024-01-06T00:05:00Z"
        bad_fixture = self.root / "bad-fixture.json"
        bad_fixture.write_bytes(p.encoded(fixture))
        result = p.run_paper_cycle(
            self.state, bad_fixture, self.acceptance, self.indicator,
            self.root / "blocked.json", self.root / "blocked-output",
            self.cycle_id(), "2024-01-05T00:05:00Z")
        self.assertEqual(result["terminal_result"], "BLOCKED")

    def test_local_persistence_failure_is_recoverable(self):
        decision = p.load_paper_session(self.state)["decisions"][0]
        self.root.joinpath("cycle-directory").mkdir()
        result = p.run_paper_cycle(
            self.state, self.fixture, self.acceptance, self.indicator,
            self.root / "cycle-directory", self.root / "recoverable-output",
            self.cycle_id(), "2024-01-05T00:05:00Z")
        self.assertEqual(result["terminal_result"], "RECOVERABLE_ERROR")
        self.assertEqual(p.load_paper_session(self.state)["decisions"][0]["identity"], decision["identity"])


if __name__ == "__main__":
    unittest.main()
