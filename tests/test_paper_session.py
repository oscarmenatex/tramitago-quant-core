"""Local proof for clean, recoverable PAPER session initialization."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pipeline as p


class PaperSessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.started_at = "2026-09-14T00:00:00Z"
        self.session_id = "PAPER_SESSION|operator-cycle-001"

    def initialize(self, name="state.json", session_id=None, mode="PAPER", output="init"):
        return p.initialize_paper_session(
            self.root / name, self.root / output, session_id or self.session_id,
            mode, self.started_at)

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

    def test_clean_session_is_new_paper_flat_and_broker_position_is_separate(self):
        with patch("pipeline.urlopen", side_effect=AssertionError("network forbidden")):
            result = self.initialize()
        state = result["session"]
        self.assertTrue(result["created"])
        self.assertEqual(state["session_id"], self.session_id)
        self.assertEqual(state["mode"], "PAPER")
        self.assertEqual(state["internal_position_state"], "FLAT")
        self.assertEqual(state["broker_position_observed"], "UNKNOWN")
        self.assertEqual(state["reconciliation_status"], "PENDING_BROKER_OBSERVATION")
        self.assertEqual(state["processed_observations"], [])
        self.assertEqual(state["decisions"], [])
        self.assertEqual(state["executions"], [])
        self.assertEqual(state["pending_actions"], [])
        self.assertEqual(state["broker_submissions"], [])
        self.assertFalse(result["credentials_used"])
        self.assertEqual(result["network_calls"], 0)

    def test_each_explicit_identity_is_distinct(self):
        first = self.initialize("first.json", "PAPER_SESSION|first", output="first")
        second = self.initialize("second.json", "PAPER_SESSION|second", output="second")
        self.assertNotEqual(first["session"]["session_id"], second["session"]["session_id"])
        self.assertNotEqual(first["session"]["session_identity"],
                            second["session"]["session_identity"])

    def test_persistence_reload_and_reinitialization_are_idempotent(self):
        first = self.initialize()
        before = (self.root / "state.json").read_bytes()
        recovered = p.load_paper_session(self.root / "state.json")
        replay = self.initialize(output="replay")
        self.assertEqual(recovered, first["session"])
        self.assertFalse(replay["created"])
        self.assertEqual((self.root / "state.json").read_bytes(), before)
        self.assertEqual(replay["session"], recovered)
        self.assertEqual(recovered["decisions"], [])
        self.assertEqual(recovered["executions"], [])

    def test_warmup_loads_without_operational_processing_or_decisions(self):
        self.initialize()
        warmup = self.warmup()
        result = p.load_paper_session_warmup(
            self.root / "state.json", self.root / "warmup", warmup)
        self.assertTrue(result["loaded"])
        self.assertEqual(result["warmup_observations"], 3)
        self.assertEqual(result["processed_operational_observations"], 0)
        self.assertEqual(result["retroactive_decisions"], 0)
        self.assertEqual(result["executions"], 0)
        self.assertEqual(result["pending_actions"], 0)
        self.assertEqual(result["broker_submissions"], 0)
        state = p.load_paper_session(self.root / "state.json")
        self.assertEqual(state["warmup_observations"], warmup)
        self.assertEqual(state["processed_observations"], [])
        before = (self.root / "state.json").read_bytes()
        replay = p.load_paper_session_warmup(
            self.root / "state.json", self.root / "warmup-replay", warmup)
        self.assertFalse(replay["loaded"])
        self.assertEqual((self.root / "state.json").read_bytes(), before)

    def test_missing_ambiguous_and_live_modes_fail_before_state_creation(self):
        for index, mode in enumerate((None, "", "UNKNOWN", "LIVE", "paper")):
            with self.subTest(mode=mode):
                path = self.root / f"mode-{index}.json"
                with self.assertRaisesRegex(ValueError, "explicit PAPER"):
                    p.initialize_paper_session(
                        path, self.root / f"mode-output-{index}", self.session_id,
                        mode, self.started_at)
                self.assertFalse(path.exists())

    def test_phase3_state_and_another_session_cannot_be_reused(self):
        phase3 = self.root / "phase3.json"
        phase3.write_bytes(p.encoded({
            "instrument": "BTC-USD", "frequency_seconds": 86400,
            "observations": [], "decisions": [], "virtual_position": 0}))
        original = phase3.read_bytes()
        with self.assertRaisesRegex(ValueError, "not an operational PAPER session"):
            p.initialize_paper_session(
                phase3, self.root / "phase3-output", self.session_id,
                "PAPER", self.started_at)
        self.assertEqual(phase3.read_bytes(), original)

        self.initialize()
        before = (self.root / "state.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "another identity"):
            self.initialize(session_id="PAPER_SESSION|different", output="different")
        self.assertEqual((self.root / "state.json").read_bytes(), before)

    def test_warmup_cannot_inject_decisions_or_post_start_observations(self):
        self.initialize()
        candidates = []
        with_decision = copy.deepcopy(self.warmup())
        with_decision[-1]["decision"] = "ENTER"
        candidates.append(with_decision)
        post_start = copy.deepcopy(self.warmup())
        post_start[-1].update({
            "identity": "BTC-USD|86400|2026-09-14T00:00:00Z",
            "timestamp": "2026-09-14T00:00:00Z"})
        candidates.append(post_start)
        duplicated = self.warmup()
        duplicated[-1] = copy.deepcopy(duplicated[-2])
        candidates.append(duplicated)
        for index, observations in enumerate(candidates):
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    p.load_paper_session_warmup(
                        self.root / "state.json", self.root / f"bad-{index}", observations)
        state = p.load_paper_session(self.root / "state.json")
        self.assertEqual(state["warmup_observations"], [])
        self.assertEqual(state["decisions"], [])
        self.assertEqual(state["executions"], [])
        self.assertEqual(state["pending_actions"], [])


if __name__ == "__main__":
    unittest.main()
