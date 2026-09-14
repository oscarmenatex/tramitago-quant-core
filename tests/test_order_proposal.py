"""Offline proof for the Phase 4 real-order proposal boundary."""

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pipeline as p


class RealOrderProposalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def state(self, name="case", closes=None):
        root = self.root / name
        root.mkdir()
        closes = closes or [10, 11, 12, 11]
        start = p.epoch(p.CONFIG["start"])
        raw = [[start + index * 86400, close - 1, close + 1, close - .5, close, 1]
               for index, close in enumerate(closes)]
        p.observe(root / "input", root / "state.json", root / "observation",
                  raw=json.dumps(raw).encode(),
                  now=datetime(2024, 1, 10, tzinfo=timezone.utc))
        decisions = p.decide(root / "state.json", root / "decision")["new_decisions"]
        return root, decisions

    def config(self, **changes):
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

    def prepare(self, root, identity, config=None, output="proposal"):
        return p.prepare_real_order_proposal(
            root / "state.json", root / output, identity, config or self.config())

    def test_valid_enter_is_bounded_pending_and_never_sent(self):
        root, decisions = self.state(closes=[10, 11, 12])
        before = json.loads((root / "state.json").read_bytes())
        with patch("pipeline.urlopen", side_effect=AssertionError("network forbidden")):
            result = self.prepare(root, decisions[-1]["identity"])
        proposal = result["proposal"]
        self.assertEqual((proposal["action"], proposal["side"]), ("ENTER", "BUY"))
        self.assertEqual(proposal["instrument"], "BTC-USD")
        self.assertEqual(proposal["exposure_usd"], "50")
        self.assertEqual(proposal["risk"]["risk_budget_usd"], "5")
        self.assertFalse(proposal["risk"]["risk_budget_is_guaranteed_maximum_loss"])
        self.assertEqual(proposal["status"], "PENDING_MANUAL_APPROVAL")
        self.assertEqual(proposal["transmission_status"], "NOT_SENT")
        self.assertFalse(result["broker_request_sent"])
        self.assertEqual(result["real_market_effect"], "NONE")
        after = json.loads((root / "state.json").read_bytes())
        self.assertEqual(after.pop("real_order_proposals"), [proposal])
        self.assertEqual(after, before)

    def test_valid_exit_requires_existing_virtual_position(self):
        root, decisions = self.state()
        state = json.loads((root / "state.json").read_bytes())
        state["virtual_position"] = 1
        (root / "state.json").write_bytes(p.encoded(state))
        result = self.prepare(root, decisions[-1]["identity"])
        self.assertEqual((result["proposal"]["action"], result["proposal"]["side"]),
                         ("EXIT", "SELL"))
        self.assertEqual(result["proposal"]["status"], "PENDING_MANUAL_APPROVAL")

    def test_exposure_instrument_and_risk_budget_are_enforced(self):
        cases = [
            ({"max_exposure_usd": "51"}, "50 USD ceiling"),
            ({"proposed_notional_usd": "50.01"}, "configured limit"),
            ({"instrument": "ETH-USD"}, "BTC-USD"),
            ({"risk_budget_usd": "6"}, "exactly 5 USD"),
            ({"operational_risk_usd": "5.01"}, "5 USD threshold"),
            ({"manual_approval_required": False}, "manual approval"),
        ]
        for index, (change, reason) in enumerate(cases):
            with self.subTest(change=change):
                root, decisions = self.state(str(index), closes=[10, 11, 12])
                proposal = self.prepare(root, decisions[-1]["identity"],
                                        self.config(**change))["proposal"]
                self.assertEqual(proposal["status"], "REJECTED")
                self.assertEqual(proposal["transmission_status"], "NOT_SENT")
                self.assertTrue(any(reason in item for item in proposal["rejection_reasons"]))

    def test_idempotence_persistence_recovery_and_determinism(self):
        first, first_decisions = self.state("first", closes=[10, 11, 12])
        second, second_decisions = self.state("second", closes=[10, 11, 12])
        first_result = self.prepare(first, first_decisions[-1]["identity"], output="one")
        state_after_first = (first / "state.json").read_bytes()
        repeated = self.prepare(first, first_decisions[-1]["identity"], output="two")
        second_result = self.prepare(second, second_decisions[-1]["identity"], output="one")
        self.assertFalse(repeated["proposal_created"])
        self.assertEqual((first / "state.json").read_bytes(), state_after_first)
        self.assertEqual(first_result, second_result)
        self.assertEqual(state_after_first, (second / "state.json").read_bytes())
        self.assertEqual(len(json.loads(state_after_first)["real_order_proposals"]), 1)

    def test_invalid_decisions_are_rejected_and_secrets_are_not_persisted(self):
        for index in (0, 1):
            root, decisions = self.state(str(index), closes=[10, 11, 12])
            result = self.prepare(root, decisions[index]["identity"])
            self.assertEqual(result["proposal"]["status"], "REJECTED")
            self.assertEqual(result["proposal"]["transmission_status"], "NOT_SENT")
        root, decisions = self.state("secret", closes=[10, 11, 12])
        with self.assertRaisesRegex(ValueError, "unsupported"):
            self.prepare(root, decisions[-1]["identity"],
                         self.config(api_secret="must-not-persist"))
        self.assertNotIn(b"must-not-persist", (root / "state.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
