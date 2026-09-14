"""Focal proof for the operational observation -> causal SMA3 PAPER decision bridge."""

import json
from pathlib import Path
import tempfile
import unittest

import pipeline as p


class PaperOperationalDecisionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.started_at = "2026-09-14T00:00:00Z"
        self.session_id = "PAPER_SESSION|operator-cycle-001"

    def initialize(self, name="state.json", started_at=None, output="init"):
        path = self.root / name
        p.initialize_paper_session(
            path, self.root / output, self.session_id, "PAPER",
            started_at or self.started_at)
        return path

    @staticmethod
    def warmup():
        values = (
            ("2026-09-10T00:00:00Z", 78283.98, 78554.18, 76440.0, 76536.55, 6390.0),
            ("2026-09-11T00:00:00Z", 76536.55, 79852.22, 76030.0, 77208.55, 7310.0),
            ("2026-09-12T00:00:00Z", 77208.55, 77495.0, 77049.56, 77262.85, 1669.0),
        )
        return [{"identity": f"BTC-USD|86400|{timestamp}",
                 "instrument": "BTC-USD", "timestamp": timestamp,
                 "open": open_, "high": high, "low": low,
                 "close": close, "volume": volume}
                for timestamp, open_, high, low, close, volume in values]

    @staticmethod
    def candle(timestamp, open_, high, low, close, volume):
        return {"identity": f"BTC-USD|86400|{timestamp}", "instrument": "BTC-USD",
                "timestamp": timestamp, "open": open_, "high": high,
                "low": low, "close": close, "volume": volume}

    def process(self, path, observation, processed_at, output="decision"):
        return p.process_paper_session_observation(
            path, self.root / output, observation, processed_at)

    def test_closed_candle_after_started_at_is_accepted_and_persisted(self):
        path = self.initialize()
        p.load_paper_session_warmup(path, self.root / "warmup", self.warmup())
        candle = self.candle("2026-09-15T00:00:00Z", 77262.85, 91000.0, 77000.0, 90000.0, 100.0)
        result = self.process(path, candle, "2026-09-16T00:00:00Z")
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["new_observation"])
        self.assertTrue(result["new_decision"])
        state = p.load_paper_session(path)
        self.assertEqual(state["processed_observations"], [candle])
        self.assertEqual(len(state["decisions"]), 1)
        self.assertEqual(state["decisions"][0]["observation_identity"], candle["identity"])

    def test_open_candle_is_rejected_without_state_change(self):
        path = self.initialize()
        p.load_paper_session_warmup(path, self.root / "warmup", self.warmup())
        before = path.read_bytes()
        candle = self.candle("2026-09-15T00:00:00Z", 77262.85, 91000.0, 77000.0, 90000.0, 100.0)
        with self.assertRaises(ValueError):
            self.process(path, candle, "2026-09-15T12:00:00Z")
        self.assertEqual(path.read_bytes(), before)

    def test_observation_before_or_at_started_at_is_rejected(self):
        path = self.initialize()
        p.load_paper_session_warmup(path, self.root / "warmup", self.warmup())
        before = path.read_bytes()
        for timestamp in ("2026-09-12T00:00:00Z", "2026-09-14T00:00:00Z"):
            with self.subTest(timestamp=timestamp):
                candle = self.candle(timestamp, 77262.85, 91000.0, 77000.0, 90000.0, 100.0)
                with self.assertRaises(ValueError):
                    self.process(path, candle, "2026-09-16T00:00:00Z")
        self.assertEqual(path.read_bytes(), before)

    def test_duplicate_observation_is_idempotent_and_altered_replay_is_rejected(self):
        path = self.initialize()
        p.load_paper_session_warmup(path, self.root / "warmup", self.warmup())
        candle = self.candle("2026-09-15T00:00:00Z", 77262.85, 91000.0, 77000.0, 90000.0, 100.0)
        first = self.process(path, candle, "2026-09-16T00:00:00Z")
        before = path.read_bytes()
        replay = self.process(path, candle, "2026-09-16T00:00:00Z", output="replay")
        self.assertFalse(replay["new_observation"])
        self.assertFalse(replay["new_decision"])
        self.assertEqual(replay["decision"], first["decision"])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(replay["decision_count"], 1)

        altered = dict(candle, volume=999.0)
        with self.assertRaisesRegex(ValueError, "silently altered"):
            self.process(path, altered, "2026-09-16T00:00:00Z", output="altered")
        self.assertEqual(path.read_bytes(), before)

    def test_warmup_never_receives_a_retroactive_decision(self):
        path = self.initialize()
        p.load_paper_session_warmup(path, self.root / "warmup", self.warmup())
        candle = self.candle("2026-09-15T00:00:00Z", 77262.85, 91000.0, 77000.0, 90000.0, 100.0)
        self.process(path, candle, "2026-09-16T00:00:00Z")
        state = p.load_paper_session(path)
        warmup_ids = {item["identity"] for item in state["warmup_observations"]}
        decided_ids = {item["observation_identity"] for item in state["decisions"]}
        self.assertEqual(warmup_ids & decided_ids, set())
        self.assertEqual(len(state["decisions"]), 1)

    def test_sma3_is_causal_enter_exit_and_hold_sequence(self):
        path = self.initialize()
        p.load_paper_session_warmup(path, self.root / "warmup", self.warmup())

        enter_candle = self.candle("2026-09-15T00:00:00Z", 77262.85, 91000.0, 77000.0, 90000.0, 1.0)
        enter = self.process(path, enter_candle, "2026-09-16T00:00:00Z", output="enter")
        self.assertAlmostEqual(enter["decision"]["sma_close_3"],
                               (77208.55 + 77262.85 + 90000.0) / 3.0)
        self.assertEqual(enter["decision"]["previous_position"], 0)
        self.assertEqual(enter["decision"]["target_position"], 1)
        self.assertEqual(enter["decision"]["decision"], "ENTER")

        exit_candle = self.candle("2026-09-16T00:00:00Z", 90000.0, 90500.0, 49000.0, 50000.0, 1.0)
        exit_ = self.process(path, exit_candle, "2026-09-17T00:00:00Z", output="exit")
        self.assertAlmostEqual(exit_["decision"]["sma_close_3"],
                               (77262.85 + 90000.0 + 50000.0) / 3.0)
        self.assertEqual(exit_["decision"]["previous_position"], 1)
        self.assertEqual(exit_["decision"]["target_position"], 0)
        self.assertEqual(exit_["decision"]["decision"], "EXIT")

        hold_candle = self.candle("2026-09-17T00:00:00Z", 50000.0, 60500.0, 49500.0, 60000.0, 1.0)
        hold = self.process(path, hold_candle, "2026-09-18T00:00:00Z", output="hold")
        self.assertAlmostEqual(hold["decision"]["sma_close_3"],
                               (90000.0 + 50000.0 + 60000.0) / 3.0)
        self.assertEqual(hold["decision"]["previous_position"], 0)
        self.assertEqual(hold["decision"]["target_position"], 0)
        self.assertEqual(hold["decision"]["decision"], "HOLD")

    def test_no_decision_without_three_available_closes(self):
        path = self.initialize()
        candle = self.candle("2026-09-15T00:00:00Z", 77262.85, 91000.0, 77000.0, 90000.0, 1.0)
        result = self.process(path, candle, "2026-09-16T00:00:00Z")
        self.assertEqual(result["decision"]["decision"], "NO_DECISION")
        self.assertIsNone(result["decision"]["sma_close_3"])
        self.assertIsNone(result["decision"]["target_position"])

    def test_persistence_and_reload_recover_the_same_decision(self):
        path = self.initialize()
        p.load_paper_session_warmup(path, self.root / "warmup", self.warmup())
        candle = self.candle("2026-09-15T00:00:00Z", 77262.85, 91000.0, 77000.0, 90000.0, 1.0)
        result = self.process(path, candle, "2026-09-16T00:00:00Z")
        recovered = p.load_paper_session(path)
        self.assertEqual(recovered["decisions"], [result["decision"]])
        self.assertEqual(recovered["processed_observations"], [candle])

    def test_independent_states_are_deterministic(self):
        first = self.initialize("first.json", output="init-a")
        second = self.initialize("second.json", output="init-b")
        p.load_paper_session_warmup(first, self.root / "warmup-a", self.warmup())
        p.load_paper_session_warmup(second, self.root / "warmup-b", self.warmup())
        candle = self.candle("2026-09-15T00:00:00Z", 77262.85, 91000.0, 77000.0, 90000.0, 1.0)
        result_a = self.process(first, candle, "2026-09-16T00:00:00Z", output="decision-a")
        result_b = self.process(second, candle, "2026-09-16T00:00:00Z", output="decision-b")
        self.assertEqual(result_a["decision"], result_b["decision"])
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_live_mode_state_is_rejected(self):
        path = self.root / "live.json"
        path.write_bytes(p.encoded({
            "session_id": self.session_id,
            "session_identity": p._paper_session_identity(self.session_id, self.started_at),
            "schema_version": p.PAPER_SESSION_SCHEMA_VERSION, "mode": "LIVE",
            "instrument": "BTC-USD", "started_at": self.started_at,
            "internal_position_state": "FLAT", "broker_position_observed": "UNKNOWN",
            "reconciliation_status": "PENDING_BROKER_OBSERVATION",
            "warmup_observations": [], "processed_observations": [],
            "decisions": [], "executions": [], "pending_actions": [],
            "broker_submissions": []}))
        candle = self.candle("2026-09-15T00:00:00Z", 77262.85, 91000.0, 77000.0, 90000.0, 1.0)
        with self.assertRaisesRegex(ValueError, "invalid or incompatible"):
            self.process(path, candle, "2026-09-16T00:00:00Z")

    def test_phase3_demo_state_is_rejected(self):
        path = self.root / "phase3.json"
        path.write_bytes(p.encoded({
            "instrument": "BTC-USD", "frequency_seconds": 86400,
            "observations": [], "decisions": [], "virtual_position": 0}))
        candle = self.candle("2026-09-15T00:00:00Z", 77262.85, 91000.0, 77000.0, 90000.0, 1.0)
        with self.assertRaisesRegex(ValueError, "invalid or incompatible"):
            self.process(path, candle, "2026-09-16T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
