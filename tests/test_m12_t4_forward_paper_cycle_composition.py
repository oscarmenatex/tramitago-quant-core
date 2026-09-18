"""Focused proof for composing an accepted T3 observation through M1.1."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pipeline as p


class M12T4ForwardPaperCycleCompositionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.session_path = self.root / "session" / "state.json"
        self.configuration_path = self.root / "configuration" / "forward-paper.json"
        self.invocation_path = self.root / "invocations" / "one.json"
        self.dataset_path = self.root / "datasets" / "t2.json"
        self.selection_path = self.root / "selections" / "one.json"
        self.fixture_path = self.root / "m11" / "fixture.json"
        self.acceptance_path = self.root / "m11" / "acceptance.json"
        self.indicator_path = self.root / "m11" / "indicator.json"
        self.cycle_path = self.root / "m11" / "cycle.json"
        self.output = self.root / "outputs"
        self.session_id = "PAPER_SESSION|m12-t4-composition-001"
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
            "instrument": "BTC-USD", "granularity_seconds": 86400,
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

    def compose(self):
        return p.compose_forward_paper_cycle(
            self.session_path, self.configuration_path, self.invocation_path,
            self.dataset_path, self.selection_path, self.fixture_path,
            self.acceptance_path, self.indicator_path, self.cycle_path,
            self.output, "2026-01-05T12:01:00Z")

    def test_accepted_observation_uses_all_canonical_m11_contracts(self):
        self.select()
        with patch.object(p, "prepare_forward_paper_session_fixture",
                  wraps=p.prepare_forward_paper_session_fixture) as fixture, \
                patch.object(p, "compose_paper_session_sma3",
                             wraps=p.compose_paper_session_sma3) as sma3, \
                patch.object(p, "persist_paper_session_sma3_decision",
                             wraps=p.persist_paper_session_sma3_decision) as decision, \
                patch.object(p, "apply_paper_risk_to_session",
                             wraps=p.apply_paper_risk_to_session) as risk, \
                patch.object(p, "run_paper_cycle",
                             wraps=p.run_paper_cycle) as cycle:
            result = self.compose()
        self.assertEqual(result["composition_result"], "COMPLETED")
        self.assertEqual(result["sma3"]["sma_close_3"], 13.0)
        self.assertEqual(result["decision"]["decision"]["decision"], "ENTER")
        self.assertEqual(result["risk"]["risk_result"], "ALLOWED")
        self.assertEqual(result["position_before"], "FLAT")
        self.assertEqual(result["position_after"], "LONG")
        self.assertEqual(result["broker_position_observed"], "UNKNOWN")
        self.assertEqual(result["processed_observations"], 1)
        self.assertEqual(result["decisions"], 1)
        self.assertEqual(result["risk_evaluations"], 1)
        self.assertEqual(result["executions"], 0)
        self.assertEqual(result["pending_actions"], 0)
        self.assertEqual(result["proposals"], 0)
        for mock in (fixture, sma3, decision, risk, cycle):
            self.assertEqual(mock.call_count, 1)

    def test_replay_is_idempotent_across_m11_effects(self):
        self.select()
        first = self.compose()
        state_before = self.session_path.read_bytes()
        cycle_before = self.cycle_path.read_bytes()
        second = self.compose()
        state = p.load_paper_session(self.session_path)
        self.assertEqual(first["composition_result"], "COMPLETED")
        self.assertEqual(second["composition_result"], "COMPLETED")
        self.assertEqual(self.session_path.read_bytes(), state_before)
        self.assertEqual(self.cycle_path.read_bytes(), cycle_before)
        self.assertEqual(len(state["processed_observations"]), 1)
        self.assertEqual(len(state["decisions"]), 1)
        self.assertEqual(len(state["paper_risk_evaluations"]), 1)
        self.assertEqual(state["internal_position_state"], "LONG")

    def test_nothing_due_stops_before_m11(self):
        state = json.loads(self.session_path.read_bytes())
        state["processed_observations"] = [
            {
                "identity": item["identity"], "instrument": item["instrument"],
                "timestamp": item["interval_start_utc"], "open": item["open"],
                "high": item["high"], "low": item["low"],
                "close": item["close"], "volume": item["volume"],
            }
            for item in self.observations
        ]
        self.session_path.write_bytes(p.encoded(state))
        selected = self.select()
        self.assertEqual(selected["selection_result"], "NOTHING_DUE")
        with patch.object(p, "compose_paper_session_sma3",
                          side_effect=AssertionError("M1.1 SMA3 must not run")):
            result = self.compose()
        self.assertEqual(result["composition_result"], "NOTHING_DUE")
        self.assertEqual(self.session_path.read_bytes(), p.encoded(state))
        self.assertFalse(self.fixture_path.exists())

    def test_blocked_dataset_stops_before_m11(self):
        self.select()
        state_before = self.session_path.read_bytes()
        receipt_before = self.selection_path.read_bytes()
        invalid = json.loads(self.dataset_path.read_bytes())
        invalid["observations"][3]["accepted_at_utc"] = self.started_at
        self.dataset_path.write_bytes(p.encoded(invalid))
        with patch.object(p, "compose_paper_session_sma3",
                          side_effect=AssertionError("M1.1 SMA3 must not run")):
            result = self.compose()
        self.assertEqual(result["composition_result"], "BLOCKED")
        self.assertEqual(self.session_path.read_bytes(), state_before)
        self.assertEqual(self.selection_path.read_bytes(), receipt_before)
        self.assertFalse(self.fixture_path.exists())

    def test_incompatible_receipt_fails_closed_without_replacing_state(self):
        self.select()
        state_before = self.session_path.read_bytes()
        receipt = json.loads(self.selection_path.read_bytes())
        receipt["configuration_id"] = "OTHER_CONFIGURATION"
        self.selection_path.write_bytes(p.encoded(receipt))
        receipt_before = self.selection_path.read_bytes()
        result = self.compose()
        self.assertEqual(result["composition_result"], "BLOCKED")
        self.assertEqual(self.session_path.read_bytes(), state_before)
        self.assertEqual(self.selection_path.read_bytes(), receipt_before)
        self.assertFalse(self.fixture_path.exists())


if __name__ == "__main__":
    unittest.main()
