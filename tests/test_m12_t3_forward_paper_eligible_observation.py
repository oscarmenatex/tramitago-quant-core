"""Focused local proof for causal FORWARD_PAPER observation selection."""

import json
from pathlib import Path
import tempfile
import unittest

import pipeline as p


class M12T3ForwardPaperEligibleObservationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.session_path = self.root / "session" / "state.json"
        self.configuration_path = self.root / "configuration" / "forward-paper.json"
        self.invocation_path = self.root / "invocations" / "one.json"
        self.dataset_path = self.root / "datasets" / "t2.json"
        self.selection_path = self.root / "selections" / "one.json"
        self.session_id = "PAPER_SESSION|m12-t3-selection-001"
        self.started_at = "2026-01-04T12:00:00Z"
        self.processing_instant = "2026-01-05T12:00:00Z"
        preparation = p.prepare_forward_paper_invocation(
            self.session_path, self.configuration_path, self.invocation_path,
            self.session_id, self.started_at, self.processing_instant)
        self.state = preparation["session"]
        self.configuration = preparation["configuration"]
        self.invocation = preparation["invocation"]
        self.observations = [self.observation(day, day + 10) for day in range(1, 5)]
        self.dataset = p._forward_paper_dataset(
            self.state, self.configuration, self.invocation,
            p._forward_paper_query_window(self.processing_instant, 86400),
            self.observations, 0)
        self.dataset_path.parent.mkdir(parents=True)
        self.dataset_path.write_bytes(p.encoded(self.dataset))

    @staticmethod
    def observation(day, close):
        timestamp = f"2026-01-{day:02d}T00:00:00Z"
        return {
            "identity": f"BTC-USD|86400|{timestamp}",
            "instrument": "BTC-USD",
            "granularity_seconds": 86400,
            "interval_start_utc": timestamp,
            "interval_end_utc": p.iso(p.epoch(timestamp) + 86400),
            "accepted_at_utc": "2026-01-05T12:00:00Z",
            "open": float(close - 1), "high": float(close + 1),
            "low": float(close - 2), "close": float(close), "volume": 1.0,
            "source": "COINBASE_PUBLIC",
        }

    def select(self):
        return p.select_forward_paper_eligible_observation(
            self.session_path, self.configuration_path, self.invocation_path,
            self.dataset_path, self.selection_path)

    def assert_invalid_observation_is_blocked_without_effects(self, mutate):
        accepted = self.select()
        self.assertEqual(accepted["selection_result"],
                         "OPERATIONAL_OBSERVATION_ACCEPTED")
        state_before = self.session_path.read_bytes()
        receipt_before = self.selection_path.read_bytes()
        invalid_dataset = json.loads(self.dataset_path.read_bytes())
        mutate(invalid_dataset["observations"][3])
        self.dataset_path.write_bytes(p.encoded(invalid_dataset))

        result = self.select()
        self.assertEqual(result["selection_result"], "BLOCKED")
        self.assertIsNone(result.get("operational_observation"))
        self.assertEqual(self.session_path.read_bytes(), state_before)
        self.assertEqual(p.digest(self.session_path.read_bytes()), p.digest(state_before))
        state = p.load_paper_session(self.session_path)
        before_state = json.loads(state_before)
        for field in ("processed_observations", "decisions", "paper_risk_evaluations",
                      "internal_position_state", "executions", "pending_actions",
                      "proposals"):
            self.assertEqual(state.get(field), before_state.get(field))
        self.assertEqual(self.selection_path.read_bytes(), receipt_before)
        self.assertEqual(p.digest(self.selection_path.read_bytes()), p.digest(receipt_before))

    def test_selects_fourth_candle_after_three_context_observations(self):
        state_before = self.session_path.read_bytes()
        result = self.select()
        self.assertEqual(result["selection_result"], "OPERATIONAL_OBSERVATION_ACCEPTED")
        self.assertEqual(result["warmup_observations"], self.observations[:3])
        self.assertEqual(result["operational_observation"], self.observations[3])
        self.assertEqual(result["lookahead"], "NOT_USED")
        self.assertEqual(self.session_path.read_bytes(), state_before)
        self.assertEqual(result["decisions"], 0)
        self.assertEqual(result["risk_evaluations"], 0)
        self.assertEqual(result["executions"], 0)
        self.assertEqual(result["pending_actions"], 0)

    def test_warmup_never_contains_candidate_or_future_observation(self):
        result = self.select()
        warmup_ids = [item["identity"] for item in result["warmup_observations"]]
        self.assertEqual(warmup_ids, [item["identity"] for item in self.observations[:3]])
        self.assertNotIn(self.observations[3]["identity"], warmup_ids)

    def test_candle_opened_before_session_is_accepted_after_session(self):
        self.assertLess(p.epoch(self.observations[3]["interval_start_utc"]),
                        p.epoch(self.started_at))
        result = self.select()
        self.assertEqual(result["selection_result"], "OPERATIONAL_OBSERVATION_ACCEPTED")

    def test_no_new_candidate_or_insufficient_context_is_nothing_due(self):
        state = json.loads(self.session_path.read_bytes())
        state["processed_observations"] = self.observations[:4]
        self.session_path.write_bytes(p.encoded(state))
        result = self.select()
        self.assertEqual(result["selection_result"], "NOTHING_DUE")
        self.assertEqual(result["warmup_observations"], [])
        self.assertIsNone(result["operational_observation"])

        short_dataset = dict(self.dataset)
        short_dataset["observations"] = self.observations[:3]
        short_dataset["closed_observation_count"] = 3
        content = {key: short_dataset[key] for key in short_dataset if key != "dataset_id"}
        short_dataset["dataset_id"] = "FORWARD_PAPER_DATASET|" + p.digest(p.encoded(content))
        self.dataset_path.write_bytes(p.encoded(short_dataset))
        state["processed_observations"] = []
        self.session_path.write_bytes(p.encoded(state))
        result = self.select()
        self.assertEqual(result["selection_result"], "BLOCKED")

    def test_exact_replay_is_idempotent_and_conflict_is_rejected(self):
        first = self.select()
        before = self.selection_path.read_bytes()
        replay = self.select()
        self.assertFalse(replay["created"])
        self.assertEqual(replay["selection"], first["selection"])
        self.assertEqual(self.selection_path.read_bytes(), before)
        altered = json.loads(before)
        altered["result"] = "NOTHING_DUE"
        self.selection_path.write_bytes(p.encoded(altered))
        with self.assertRaisesRegex(ValueError, "cannot be silently replaced"):
            self.select()

    def test_invalid_dataset_is_blocked_without_state_or_selection_effects(self):
        state_before = self.session_path.read_bytes()
        invalid = dict(self.dataset)
        invalid["observations"] = list(self.dataset["observations"])
        invalid["observations"][3] = dict(invalid["observations"][3])
        invalid["observations"][3]["accepted_at_utc"] = self.started_at
        self.dataset_path.write_bytes(p.encoded(invalid))
        result = self.select()
        self.assertEqual(result["selection_result"], "BLOCKED")
        self.assertEqual(self.session_path.read_bytes(), state_before)
        self.assertFalse(self.selection_path.exists())

    def test_missing_accepted_at_is_blocked_without_effects_or_receipt_replacement(self):
        def remove_accepted_at(observation):
            del observation["accepted_at_utc"]

        self.assert_invalid_observation_is_blocked_without_effects(remove_accepted_at)

    def test_pre_start_accepted_at_is_blocked_without_effects_or_receipt_replacement(self):
        def set_pre_start_acceptance(observation):
            observation["accepted_at_utc"] = "2026-01-04T11:59:59Z"

        self.assert_invalid_observation_is_blocked_without_effects(set_pre_start_acceptance)

    def test_open_interval_is_blocked_without_effects_or_receipt_replacement(self):
        def open_interval(observation):
            observation["interval_end_utc"] = "2026-01-06T00:00:00Z"

        self.assert_invalid_observation_is_blocked_without_effects(open_interval)


if __name__ == "__main__":
    unittest.main()
