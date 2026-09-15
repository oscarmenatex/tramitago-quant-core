import copy
import json
from pathlib import Path
import tempfile
import unittest

import pipeline as p


class M11T5PaperRiskStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / "state.json"
        p.initialize_paper_session(
            self.state, self.root / "init", "PAPER_SESSION|t5-risk-001", "PAPER",
            "2024-01-05T00:00:00Z")

    def add_decision(self, action, position="FLAT", **fields):
        state = p.load_paper_session(self.state)
        state["internal_position_state"] = position
        decision = {
            "identity": f"SMA3_DECISION|t5|{action}|{position}",
            "session_id": state["session_id"],
            "observation_identity": "BTC-USD|86400|2024-01-04T00:00:00Z",
            "instrument": "BTC-USD", "frequency_seconds": 86400,
            "strategy": "SMA3_LONG_ONLY", "configuration_identity": "SMA3_CONFIG|test",
            "processing_instant_utc": "2024-01-05T00:05:00Z", "decision": action,
            "previous_position": 0 if position == "FLAT" else 1,
            "target_position": {"ENTER": 1, "EXIT": 0}.get(action),
        }
        decision.update(fields)
        state["decisions"] = [decision]
        self.state.write_bytes(p.encoded(state))
        return decision["identity"]

    def apply(self, decision_id, profile=None, output="risk"):
        return p.apply_paper_risk_to_session(
            self.state, self.root / output, decision_id,
            "2024-01-05T00:06:00Z", profile)

    def test_enter_from_flat_is_allowed_and_updates_only_internal_position(self):
        decision_id = self.add_decision("ENTER")
        before = p.load_paper_session(self.state)
        result = self.apply(decision_id)
        state = p.load_paper_session(self.state)
        self.assertEqual(result["risk_result"], "ALLOWED")
        self.assertEqual(result["paper_effect"], "UPDATED")
        self.assertEqual((result["position_before"], result["position_after"]), ("FLAT", "LONG"))
        self.assertEqual(state["internal_position_state"], "LONG")
        self.assertEqual(state["broker_position_observed"], before["broker_position_observed"])
        self.assertEqual(state["executions"], [])
        self.assertEqual(state["pending_actions"], [])
        self.assertEqual(result["evaluation"]["dollar_limit_validation"], "DEFERRED_NO_ORDER_INTENT")

    def test_exit_from_long_is_allowed_and_hold_no_effect(self):
        exit_id = self.add_decision("EXIT", position="LONG")
        result = self.apply(exit_id, output="exit-risk")
        self.assertEqual((result["risk_result"], result["position_after"]), ("ALLOWED", "FLAT"))
        hold_id = self.add_decision("HOLD", position="LONG")
        hold = self.apply(hold_id, output="hold-risk")
        self.assertEqual((hold["risk_result"], hold["paper_effect"]), ("NO_EFFECT", "NO_EFFECT"))
        self.assertEqual(hold["position_after"], "LONG")

    def test_no_decision_and_incompatible_transition_are_fail_closed(self):
        no_id = self.add_decision("NO_DECISION")
        no_effect = self.apply(no_id, output="no-decision-risk")
        self.assertEqual(no_effect["risk_result"], "NO_EFFECT")
        enter_id = self.add_decision("ENTER", position="LONG")
        blocked = self.apply(enter_id, output="blocked-risk")
        self.assertEqual((blocked["risk_result"], blocked["paper_effect"]), ("BLOCKED", "BLOCKED"))
        self.assertEqual(blocked["position_after"], "LONG")

    def test_invalid_profile_live_mode_and_excess_exposure_are_blocked(self):
        decision_id = self.add_decision("ENTER")
        live = copy.deepcopy(p.PAPER_RISK_PROFILE)
        live["mode"] = "LIVE"
        blocked = self.apply(decision_id, live, "live-risk")
        self.assertEqual(blocked["risk_result"], "BLOCKED")
        self.assertEqual(p.load_paper_session(self.state)["internal_position_state"], "FLAT")
        exposed_id = self.add_decision("ENTER")
        exposed = self.add_decision("ENTER", notional_usd="20000.01")
        limited = self.apply(exposed, output="exposure-risk")
        self.assertEqual(limited["risk_result"], "BLOCKED")
        self.assertEqual(p.load_paper_session(self.state)["internal_position_state"], "FLAT")

    def test_persistence_reload_idempotence_and_determinism(self):
        decision_id = self.add_decision("ENTER")
        first = self.apply(decision_id)
        state_bytes = self.state.read_bytes()
        replay = self.apply(decision_id, output="replay-risk")
        self.assertFalse(replay["created"])
        self.assertEqual(replay["evaluation"], first["evaluation"])
        self.assertEqual(self.state.read_bytes(), state_bytes)
        recovered = p.load_paper_session(self.state)
        self.assertEqual(len(recovered["paper_risk_evaluations"]), 1)
        self.assertEqual(recovered["paper_risk_evaluations"][0]["decision_identity"], decision_id)
        self.assertEqual(recovered["broker_position_observed"], "UNKNOWN")

    def test_invalid_state_mode_and_instrument_fail_closed(self):
        decision_id = self.add_decision("ENTER")
        state = p.load_paper_session(self.state)
        state["mode"] = "LIVE"
        self.state.write_bytes(p.encoded(state))
        with self.assertRaises(ValueError):
            self.apply(decision_id, output="live-state")
        state["mode"] = "PAPER"
        state["instrument"] = "ETH-USD"
        self.state.write_bytes(p.encoded(state))
        with self.assertRaises(ValueError):
            self.apply(decision_id, output="eth-state")


if __name__ == "__main__":
    unittest.main()
