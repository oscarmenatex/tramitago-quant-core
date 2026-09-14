"""Offline proof for Alpaca request preparation without transport."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pipeline as p


class AlpacaRequestPreparationTests(unittest.TestCase):
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

    def state(self, name, approval="APPROVED", revalidate=True):
        root = self.root / name
        root.mkdir()
        decision = {
            "identity": "SMA3|BTC-USD|86400|2024-01-03T00:00:00Z",
            "observation_identity": "BTC-USD|86400|2024-01-03T00:00:00Z",
            "instrument": "BTC-USD", "frequency_seconds": 86400,
            "timestamp": "2024-01-03T00:00:00Z", "strategy": "SMA3_LONG_ONLY",
            "decision": "ENTER", "target_position": 1,
        }
        original = {
            "instrument": "BTC-USD", "frequency_seconds": 86400,
            "observations": [{"identity": decision["observation_identity"]}],
            "decisions": [decision], "executions": [], "pending_actions": [],
            "virtual_position": 0, "realized_results": [{"identity": "realized"}],
            "unrealized_marks": [{"identity": "mark"}],
        }
        path = root / "state.json"
        path.write_bytes(p.encoded(original))
        config = self.risk()
        proposal = p.prepare_real_order_proposal(
            path, root / "proposal", decision["identity"], config)["proposal"]
        if approval is not None:
            p.record_manual_approval(
                path, root / "approval", proposal["identity"],
                proposal["proposal_identity"], "operator-01",
                "2024-01-03T12:00:00Z", approval)
            proposal = json.loads(path.read_bytes())["real_order_proposals"][0]
        revalidation = None
        if revalidate:
            revalidation = p.revalidate_approved_proposal(
                path, root / "revalidation", proposal["identity"],
                proposal["proposal_identity"], config,
                "2024-01-03T13:00:00Z")["revalidation"]
        return root, proposal, revalidation, config

    def prepare(self, root, proposal, revalidation, config, environment="LIVE",
                output="request", prepared_at="2024-01-03T14:00:00Z"):
        return p.prepare_alpaca_request(
            root / "state.json", root / output, proposal["identity"],
            proposal["proposal_identity"], revalidation["identity"], config,
            environment, prepared_at)

    def test_ready_live_request_is_persisted_not_sent_without_transport(self):
        root, proposal, revalidation, config = self.state("live")
        before = json.loads((root / "state.json").read_bytes())
        with patch("pipeline.urlopen", side_effect=AssertionError("network forbidden")):
            result = self.prepare(root, proposal, revalidation, config)
        self.assertEqual(result["status"], "NOT_SENT")
        self.assertTrue(result["request_prepared"])
        self.assertFalse(result["broker_request_sent"])
        self.assertEqual(result["real_market_effect"], "NONE")
        request = result["prepared_request"]
        self.assertEqual(request["target_environment"], "LIVE")
        self.assertEqual((request["instrument"], request["side"]), ("BTC-USD", "buy"))
        self.assertEqual((request["order_type"], request["limit_price"]), ("LIMIT", "10"))
        self.assertEqual(request["state"], "NOT_SENT")
        self.assertFalse(request["credentials_present"])
        self.assertEqual(request["payload_sha256"], p.digest(p.encoded(request["payload"])))
        recovered = json.loads((root / "state.json").read_bytes())
        self.assertTrue(p.prepared_alpaca_request_is_valid(recovered, request))
        recovered.pop("alpaca_prepared_requests")
        recovered.pop("alpaca_request_preparations")
        self.assertEqual(recovered, before)

    def test_pending_rejected_and_blocked_revalidation_create_no_request(self):
        cases = (("pending", None), ("rejected", "REJECTED"))
        for name, approval in cases:
            with self.subTest(approval=approval):
                root, proposal, revalidation, config = self.state(name, approval)
                result = self.prepare(root, proposal, revalidation, config)
                self.assertEqual(result["status"], "BLOCKED_RISK_REVALIDATION")
                self.assertFalse(result["request_prepared"])
                self.assertIsNone(result["prepared_request"])
        root, proposal, revalidation, config = self.state("blocked")
        state = json.loads((root / "state.json").read_bytes())
        state["risk_revalidations"][0]["result"] = "BLOCKED_RISK_REVALIDATION"
        (root / "state.json").write_bytes(p.encoded(state))
        result = self.prepare(root, proposal, revalidation, config)
        self.assertEqual(result["status"], "BLOCKED_RISK_REVALIDATION")

    def test_invalid_environment_is_blocked_and_paper_live_are_distinct(self):
        root, proposal, revalidation, config = self.state("ambiguous")
        for index, environment in enumerate((None, "", "live", "AUTO")):
            with self.subTest(environment=environment):
                result = self.prepare(root, proposal, revalidation, config,
                                      environment=environment, output=f"bad-{index}")
                self.assertEqual(result["status"], "BLOCKED_ENVIRONMENT")
                self.assertFalse(result["request_prepared"])
        paper, paper_proposal, paper_revalidation, paper_config = self.state("paper")
        live, live_proposal, live_revalidation, live_config = self.state("other-live")
        paper_request = self.prepare(
            paper, paper_proposal, paper_revalidation, paper_config,
            environment="PAPER")["prepared_request"]
        live_request = self.prepare(
            live, live_proposal, live_revalidation, live_config,
            environment="LIVE")["prepared_request"]
        self.assertNotEqual(paper_request["identity"], live_request["identity"])
        self.assertNotEqual(paper_request["target_environment"],
                            live_request["target_environment"])
        changed = self.prepare(paper, paper_proposal, paper_revalidation, paper_config,
                               environment="LIVE", output="changed")
        self.assertEqual(changed["status"], "BLOCKED_ENVIRONMENT")

    def test_tampering_bad_terms_and_contract_fail_closed(self):
        changes = (
            ("instrument", "ETH-USD"), ("action", "HOLD"),
            ("quantity", "0"), ("limit_price", "invalid"),
            ("exposure_usd", "50.01"),
        )
        for field, value in changes:
            with self.subTest(field=field):
                root, proposal, revalidation, config = self.state(field)
                state = json.loads((root / "state.json").read_bytes())
                state["real_order_proposals"][0][field] = value
                (root / "state.json").write_bytes(p.encoded(state))
                result = self.prepare(root, proposal, revalidation, config)
                self.assertEqual(result["status"], "BLOCKED_RISK_REVALIDATION")
        for index, config in enumerate((None, self.risk(max_capital_usd="201"),
                                        self.risk(risk_budget_usd="6"),
                                        self.risk(limit_price="11"),
                                        self.risk(proposed_notional_usd="49"),
                                        self.risk(operational_risk_usd="4"))):
            root, proposal, revalidation, _ = self.state(f"contract-{index}")
            result = self.prepare(root, proposal, revalidation, config)
            self.assertEqual(result["status"], "BLOCKED_RISK_REVALIDATION")

        root, proposal, revalidation, config = self.state("after-preparation")
        prepared = self.prepare(root, proposal, revalidation, config)["prepared_request"]
        state = json.loads((root / "state.json").read_bytes())
        state["real_order_proposals"][0]["quantity"] = "4"
        (root / "state.json").write_bytes(p.encoded(state))
        altered = json.loads((root / "state.json").read_bytes())
        self.assertFalse(p.prepared_alpaca_request_is_valid(altered, prepared))
        blocked = self.prepare(root, proposal, revalidation, config, output="after-change")
        self.assertEqual(blocked["status"], "BLOCKED_RISK_REVALIDATION")

    def test_idempotence_recovery_and_determinism(self):
        first, proposal_a, revalidation_a, config_a = self.state("first")
        second, proposal_b, revalidation_b, config_b = self.state("second")
        result = self.prepare(first, proposal_a, revalidation_a, config_a, output="one")
        first_bytes = (first / "state.json").read_bytes()
        replay = self.prepare(first, proposal_a, revalidation_a, config_a, output="two")
        other = self.prepare(second, proposal_b, revalidation_b, config_b, output="one")
        self.assertEqual(result, replay)
        self.assertEqual(result, other)
        self.assertEqual(first_bytes, (first / "state.json").read_bytes())
        self.assertEqual(first_bytes, (second / "state.json").read_bytes())
        self.assertEqual(len(json.loads(first_bytes)["alpaca_prepared_requests"]), 1)


if __name__ == "__main__":
    unittest.main()
