"""Offline proof for Phase 4 post-approval risk revalidation."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pipeline as p


class PostApprovalRiskRevalidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def risk(self, **changes):
        config = {
            "broker": "Alpaca", "account_target": "real/live",
            "instrument": "BTC-USD", "max_capital_usd": "200",
            "max_exposure_usd": "50", "risk_budget_usd": "5",
            "proposed_notional_usd": "50", "operational_risk_usd": "5",
            "order_type": "limit", "limit_price": "10",
            "manual_approval_required": True,
            "risk_contract_version": p.PHASE4_RISK_CONTRACT_VERSION,
        }
        config.update(changes)
        config["risk_contract_identity"] = p.phase4_risk_contract_identity(config)
        return config

    def proposal(self, name, review="APPROVED"):
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
        config = self.risk()
        proposal = p.prepare_real_order_proposal(
            path, root / "proposal", decision["identity"], config)["proposal"]
        if review is not None:
            p.record_manual_approval(
                path, root / "approval", proposal["identity"],
                proposal["proposal_identity"], "operator-01",
                "2024-01-03T12:00:00Z", review)
            proposal = json.loads(path.read_bytes())["real_order_proposals"][0]
        return root, proposal, config

    def revalidate(self, root, proposal, config=None, output="revalidation",
                   proposal_id=None, identity=None, timestamp="2024-01-03T13:00:00Z"):
        return p.revalidate_approved_proposal(
            root / "state.json", root / output,
            proposal_id or proposal["identity"],
            identity or proposal["proposal_identity"],
            self.risk() if config is None else config, timestamp)

    def test_valid_approved_proposal_is_ready_and_persisted_without_network(self):
        root, proposal, config = self.proposal("ready")
        before = json.loads((root / "state.json").read_bytes())
        with patch("pipeline.urlopen", side_effect=AssertionError("network forbidden")):
            result = self.revalidate(root, proposal, config)
        self.assertEqual(result["status"], "READY_FOR_SUBMISSION")
        self.assertFalse(result["broker_request_sent"])
        self.assertEqual(result["real_market_effect"], "NONE")
        record = result["revalidation"]
        self.assertTrue(all(record["checks"].values()))
        self.assertEqual(record["approval_identity"],
                         proposal["approval_record"]["identity"])
        self.assertEqual(record["risk_budget_semantics"],
                         "OPERATIONAL_THRESHOLD_NOT_GUARANTEED_MAXIMUM_LOSS")
        recovered = json.loads((root / "state.json").read_bytes())
        self.assertEqual(recovered.pop("risk_revalidations"), [record])
        self.assertEqual(recovered, before)

    def test_pending_rejected_missing_and_unknown_approval_are_blocked(self):
        for name, review in (("pending", None), ("rejected", "REJECTED")):
            with self.subTest(review=review):
                root, proposal, config = self.proposal(name, review)
                self.assertEqual(self.revalidate(root, proposal, config)["status"],
                                 "BLOCKED_RISK_REVALIDATION")
        root, proposal, config = self.proposal("unknown")
        state = json.loads((root / "state.json").read_bytes())
        state["real_order_proposals"][0]["status"] = "UNKNOWN"
        (root / "state.json").write_bytes(p.encoded(state))
        self.assertEqual(self.revalidate(root, proposal, config)["status"],
                         "BLOCKED_RISK_REVALIDATION")
        missing = self.revalidate(root, proposal, config, output="missing",
                                  proposal_id="missing-proposal")
        self.assertEqual(missing["status"], "BLOCKED_RISK_REVALIDATION")

    def test_integrity_instrument_action_quantity_and_exposure_fail_closed(self):
        cases = (
            ("instrument", "ETH-USD"),
            ("action", "HOLD"),
            ("quantity", "0"),
            ("limit_price", "invalid"),
            ("exposure_usd", "50.01"),
        )
        for field, value in cases:
            with self.subTest(field=field):
                root, proposal, config = self.proposal(field)
                state = json.loads((root / "state.json").read_bytes())
                state["real_order_proposals"][0][field] = value
                (root / "state.json").write_bytes(p.encoded(state))
                result = self.revalidate(root, proposal, config)
                self.assertEqual(result["status"], "BLOCKED_RISK_REVALIDATION")
        root, proposal, config = self.proposal("wrong-hash")
        self.assertEqual(self.revalidate(root, proposal, config, identity="wrong")["status"],
                         "BLOCKED_RISK_REVALIDATION")

    def test_current_contract_capital_version_and_absence_are_blocked(self):
        cases = (
            self.risk(max_capital_usd="201"),
            self.risk(risk_contract_version="2"),
            self.risk(risk_budget_usd="6"),
            self.risk(max_exposure_usd="51"),
        )
        for index, config in enumerate(cases):
            with self.subTest(config=config):
                root, proposal, _ = self.proposal(f"contract-{index}")
                result = self.revalidate(root, proposal, config)
                self.assertEqual(result["status"], "BLOCKED_RISK_REVALIDATION")
        root, proposal, _ = self.proposal("contract-absent")
        absent = p.revalidate_approved_proposal(
            root / "state.json", root / "revalidation", proposal["identity"],
            proposal["proposal_identity"], None, "2024-01-03T13:00:00Z")
        self.assertEqual(absent["status"], "BLOCKED_RISK_REVALIDATION")

    def test_idempotence_recovery_and_independent_determinism(self):
        first, first_proposal, first_config = self.proposal("first")
        second, second_proposal, second_config = self.proposal("second")
        result = self.revalidate(first, first_proposal, first_config, output="one")
        first_bytes = (first / "state.json").read_bytes()
        replay = self.revalidate(first, first_proposal, first_config, output="two")
        other = self.revalidate(second, second_proposal, second_config, output="one")
        self.assertEqual(result, replay)
        self.assertEqual(result, other)
        self.assertEqual(first_bytes, (first / "state.json").read_bytes())
        self.assertEqual(first_bytes, (second / "state.json").read_bytes())
        self.assertEqual(len(json.loads(first_bytes)["risk_revalidations"]), 1)


if __name__ == "__main__":
    unittest.main()
