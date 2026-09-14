"""Offline proof for the Phase 4 manual approval gate."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pipeline as p


class ManualApprovalGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def risk(self):
        return {
            "broker": "Alpaca", "account_target": "real/live",
            "instrument": "BTC-USD", "max_capital_usd": "200",
            "max_exposure_usd": "50", "risk_budget_usd": "5",
            "proposed_notional_usd": "50", "operational_risk_usd": "5",
            "order_type": "limit", "limit_price": "10",
            "manual_approval_required": True,
            "risk_contract_version": p.PHASE4_RISK_CONTRACT_VERSION,
            "risk_contract_identity": None,
        }

    def pending(self, name):
        root = self.root / name
        root.mkdir()
        decision = {
            "identity": "SMA3|BTC-USD|86400|2024-01-03T00:00:00Z",
            "observation_identity": "BTC-USD|86400|2024-01-03T00:00:00Z",
            "instrument": "BTC-USD", "frequency_seconds": 86400,
            "timestamp": "2024-01-03T00:00:00Z", "strategy": "SMA3_LONG_ONLY",
            "decision": "ENTER", "target_position": 1,
        }
        state = {
            "instrument": "BTC-USD", "frequency_seconds": 86400,
            "observations": [{"identity": decision["observation_identity"]}],
            "decisions": [decision], "executions": [], "pending_actions": [],
            "virtual_position": 0, "realized_results": [{"identity": "realized"}],
            "unrealized_marks": [{"identity": "mark"}],
        }
        path = root / "state.json"
        path.write_bytes(p.encoded(state))
        risk = self.risk()
        risk["risk_contract_identity"] = p.phase4_risk_contract_identity(risk)
        result = p.prepare_real_order_proposal(
            path, root / "proposal", decision["identity"], risk)
        return root, result["proposal"]

    def review(self, root, proposal, decision="APPROVED", output="approval",
               actor="operator-01", timestamp="2024-01-03T12:00:00Z",
               identity=None):
        return p.record_manual_approval(
            root / "state.json", root / output, proposal["identity"],
            identity or proposal["proposal_identity"], actor, timestamp, decision)

    def test_pending_is_blocked_and_valid_approval_is_persisted(self):
        root, proposal = self.pending("approved")
        preserved = json.loads((root / "state.json").read_bytes())
        self.assertFalse(p.proposal_is_manually_approved(proposal))
        with patch("pipeline.urlopen", side_effect=AssertionError("network forbidden")):
            result = self.review(root, proposal)
        self.assertTrue(result["authorized"])
        self.assertFalse(result["broker_request_sent"])
        self.assertEqual(result["real_market_effect"], "NONE")
        record = result["approval_record"]
        self.assertEqual(record["actor"], "operator-01")
        self.assertEqual(record["approved_at"], "2024-01-03T12:00:00Z")
        self.assertEqual(record["proposal_identity"], proposal["proposal_identity"])
        recovered = json.loads((root / "state.json").read_bytes())
        self.assertTrue(p.proposal_is_manually_approved(recovered["real_order_proposals"][0]))
        for key in preserved:
            if key != "real_order_proposals":
                self.assertEqual(recovered[key], preserved[key])

    def test_rejection_is_persisted_blocked_and_irreversible(self):
        root, proposal = self.pending("rejected")
        first = self.review(root, proposal, decision="REJECTED", output="first")
        self.assertFalse(first["authorized"])
        self.assertEqual(first["proposal"]["status"], "REJECTED")
        replay = self.review(root, proposal, decision="REJECTED", output="replay")
        self.assertEqual(first, replay)
        with self.assertRaisesRegex(ValueError, "cannot be changed"):
            self.review(root, proposal, decision="APPROVED", output="changed")

    def test_incomplete_invalid_and_unbound_approvals_fail_closed(self):
        cases = [
            ({"actor": ""}, "actor"),
            ({"timestamp": ""}, "timestamp"),
            ({"timestamp": "2024-01-03T12:00:00+01:00"}, "timestamp"),
            ({"identity": "wrong-version"}, "exact persisted proposal"),
        ]
        for index, (arguments, message) in enumerate(cases):
            with self.subTest(arguments=arguments):
                root, proposal = self.pending(str(index))
                before = (root / "state.json").read_bytes()
                with self.assertRaisesRegex(ValueError, message):
                    self.review(root, proposal, **arguments)
                self.assertEqual((root / "state.json").read_bytes(), before)
                self.assertFalse(p.proposal_is_manually_approved(proposal))
        self.assertFalse(p.proposal_is_manually_approved({"status": "UNKNOWN"}))
        self.assertFalse(p.proposal_is_manually_approved({"status": "INVALID"}))

    def test_tampering_invalidates_binding(self):
        root, proposal = self.pending("tampered")
        approved = self.review(root, proposal)["proposal"]
        altered = copy.deepcopy(approved)
        altered["exposure_usd"] = "49"
        self.assertFalse(p.proposal_is_manually_approved(altered))

        other, pending = self.pending("tampered-before")
        state = json.loads((other / "state.json").read_bytes())
        state["real_order_proposals"][0]["limit_price"] = "11"
        (other / "state.json").write_bytes(p.encoded(state))
        with self.assertRaisesRegex(ValueError, "exact persisted proposal"):
            self.review(other, pending)

    def test_replay_recovery_and_independent_states_are_deterministic(self):
        first, first_proposal = self.pending("first")
        second, second_proposal = self.pending("second")
        first_result = self.review(first, first_proposal, output="first-review")
        first_bytes = (first / "state.json").read_bytes()
        replay = self.review(first, first_proposal, output="replay")
        second_result = self.review(second, second_proposal, output="first-review")
        self.assertEqual(first_result, replay)
        self.assertEqual(first_result, second_result)
        self.assertEqual(first_bytes, (first / "state.json").read_bytes())
        self.assertEqual(first_bytes, (second / "state.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
