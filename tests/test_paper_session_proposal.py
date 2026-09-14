"""Focal proof for the PAPER-session proposal bridge (separate storage, no
virtual_position reuse, no mutation of the session's own state.json)."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pipeline as p


WARMUP = (
    ("2026-09-10T00:00:00Z", 78283.98, 78554.18, 76440.0, 76536.55, 6390.0),
    ("2026-09-11T00:00:00Z", 76536.55, 79852.22, 76030.0, 77208.55, 7310.0),
    ("2026-09-12T00:00:00Z", 77208.55, 77495.0, 77049.56, 77262.85, 1669.0),
)


def candle(timestamp, o, h, l, c, v):
    return {"identity": f"BTC-USD|86400|{timestamp}", "instrument": "BTC-USD",
            "timestamp": timestamp, "open": o, "high": h, "low": l, "close": c, "volume": v}


class PaperSessionProposalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.started_at = "2026-09-14T00:00:00Z"

    def session(self, name, session_id, enter=True):
        root = self.root / name
        root.mkdir()
        session_path = root / "state.json"
        with patch("pipeline.urlopen", side_effect=AssertionError("network forbidden")):
            p.initialize_paper_session(session_path, root / "init", session_id, "PAPER", self.started_at)
        p.load_paper_session_warmup(
            session_path, root / "warmup",
            [candle(*values) for values in WARMUP])
        close = 90000.0 if enter else 50000.0
        operational = candle("2026-09-15T00:00:00Z", 77262.85, 91000.0, 49000.0, close, 100.0)
        decision_result = p.process_paper_session_observation(
            session_path, root / "decision", operational, "2026-09-16T00:00:00Z")
        return root, session_path, decision_result["decision"]

    @staticmethod
    def config(**changes):
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

    def prepare(self, session_path, proposals_path, decision_identity, config=None, output="proposal"):
        return p.prepare_paper_session_proposal(
            session_path, proposals_path, self.root / output,
            decision_identity, config or self.config())

    def test_enter_decision_produces_pending_proposal(self):
        root, session_path, decision = self.session("enter", "PAPER_SESSION|prop-enter")
        self.assertEqual(decision["decision"], "ENTER")
        before = session_path.read_bytes()
        result = self.prepare(session_path, root / "proposals.json", decision["identity"])
        self.assertTrue(result["proposal_created"])
        proposal = result["proposal"]
        self.assertEqual((proposal["action"], proposal["side"]), ("ENTER", "BUY"))
        self.assertEqual(proposal["instrument"], "BTC-USD")
        self.assertEqual(proposal["exposure_usd"], "50")
        self.assertEqual(proposal["status"], "PENDING_MANUAL_APPROVAL")
        self.assertEqual(proposal["transmission_status"], "NOT_SENT")
        self.assertEqual(proposal["session_id"], "PAPER_SESSION|prop-enter")
        self.assertEqual(proposal["observation_identity"], decision["observation_identity"])
        self.assertEqual(session_path.read_bytes(), before)

    def test_exit_decision_requires_existing_long_position(self):
        root, session_path, decision = self.session("exit", "PAPER_SESSION|prop-exit", enter=True)
        # Force the session into LONG so the next decision can be an EXIT-compatible check.
        state = json.loads(session_path.read_bytes())
        state["internal_position_state"] = "LONG"
        session_path.write_bytes(p.encoded(state))
        # Fabricate a persisted EXIT-shaped decision consistent with the LONG position,
        # without inventing decision content: mutate a copy of the real ENTER record's shape.
        exit_decision = dict(decision, identity="SMA3|BTC-USD|86400|2026-09-16T00:00:00Z",
                             observation_identity="BTC-USD|86400|2026-09-16T00:00:00Z",
                             decision="EXIT", target_position=0, previous_position=1)
        state["decisions"].append(exit_decision)
        session_path.write_bytes(p.encoded(state))
        result = self.prepare(session_path, root / "proposals.json", exit_decision["identity"])
        self.assertTrue(result["proposal_created"])
        self.assertEqual((result["proposal"]["action"], result["proposal"]["side"]), ("EXIT", "SELL"))
        self.assertEqual(result["proposal"]["status"], "PENDING_MANUAL_APPROVAL")

    def test_hold_and_no_decision_identities_are_rejected_not_fabricated(self):
        root, session_path, decision = self.session("hold", "PAPER_SESSION|prop-hold")
        state = json.loads(session_path.read_bytes())
        hold_decision = dict(decision, identity="SMA3|HOLD-CASE", decision="HOLD", target_position=0)
        no_decision = dict(decision, identity="SMA3|NO-DECISION-CASE", decision="NO_DECISION",
                           target_position=None, sma_close_3=None)
        state["decisions"].extend([hold_decision, no_decision])
        session_path.write_bytes(p.encoded(state))
        for index, identity in enumerate((hold_decision["identity"], no_decision["identity"])):
            with self.subTest(identity=identity):
                result = self.prepare(session_path, root / "proposals.json", identity,
                                      output=f"case-{index}")
                self.assertEqual(result["proposal"]["status"], "REJECTED")
                self.assertTrue(any("ENTER or EXIT" in reason
                                    for reason in result["proposal"]["rejection_reasons"]))

    def test_phase3_demo_schema_is_rejected(self):
        demo_path = self.root / "phase3.json"
        demo_path.write_bytes(p.encoded({
            "instrument": "BTC-USD", "frequency_seconds": 86400,
            "observations": [], "decisions": [], "virtual_position": 0}))
        with self.assertRaisesRegex(ValueError, "invalid or incompatible"):
            self.prepare(demo_path, self.root / "proposals.json", "anything")

    def test_position_incompatibility_is_rejected(self):
        root, session_path, decision = self.session("incompatible", "PAPER_SESSION|prop-incompatible")
        state = json.loads(session_path.read_bytes())
        state["internal_position_state"] = "LONG"  # already long; ENTER is incompatible
        session_path.write_bytes(p.encoded(state))
        result = self.prepare(session_path, root / "proposals.json", decision["identity"])
        self.assertEqual(result["proposal"]["status"], "REJECTED")
        self.assertTrue(any("incompatible with the current PAPER session position" in reason
                            for reason in result["proposal"]["rejection_reasons"]))

    def test_exposure_over_fifty_is_rejected(self):
        root, session_path, decision = self.session("exposure", "PAPER_SESSION|prop-exposure")
        result = self.prepare(session_path, root / "proposals.json", decision["identity"],
                              self.config(max_exposure_usd="51"))
        self.assertEqual(result["proposal"]["status"], "REJECTED")
        self.assertTrue(any("50 USD ceiling" in reason
                            for reason in result["proposal"]["rejection_reasons"]))

    def test_proposal_is_persisted_separately_from_session_state(self):
        root, session_path, decision = self.session("separate", "PAPER_SESSION|prop-separate")
        proposals_path = root / "proposals.json"
        before = session_path.read_bytes()
        self.prepare(session_path, proposals_path, decision["identity"])
        self.assertEqual(session_path.read_bytes(), before)
        session_after = json.loads(session_path.read_bytes())
        self.assertNotIn("real_order_proposals", session_after)
        self.assertTrue(p.paper_session_state_is_valid(session_after))
        registry = json.loads(proposals_path.read_bytes())
        self.assertEqual(len(registry["proposals"]), 1)

    def test_recovery_after_restart(self):
        root, session_path, decision = self.session("recover", "PAPER_SESSION|prop-recover")
        proposals_path = root / "proposals.json"
        first = self.prepare(session_path, proposals_path, decision["identity"])
        reloaded = json.loads(proposals_path.read_bytes())
        self.assertEqual(reloaded["proposals"], [first["proposal"]])

    def test_idempotence_no_duplicate_proposals(self):
        root, session_path, decision = self.session("idempotent", "PAPER_SESSION|prop-idempotent")
        proposals_path = root / "proposals.json"
        first = self.prepare(session_path, proposals_path, decision["identity"], output="one")
        before = proposals_path.read_bytes()
        replay = self.prepare(session_path, proposals_path, decision["identity"], output="two")
        self.assertFalse(replay["proposal_created"])
        self.assertEqual(replay["proposal"], first["proposal"])
        self.assertEqual(proposals_path.read_bytes(), before)
        self.assertEqual(len(json.loads(before)["proposals"]), 1)

    def test_determinism_across_independent_sessions(self):
        root_a, session_a, decision_a = self.session("det-a", "PAPER_SESSION|prop-det")
        root_b, session_b, decision_b = self.session("det-b", "PAPER_SESSION|prop-det")
        self.assertEqual(decision_a, decision_b)
        result_a = self.prepare(session_a, root_a / "proposals.json", decision_a["identity"], output="a")
        result_b = self.prepare(session_b, root_b / "proposals.json", decision_b["identity"], output="b")
        self.assertEqual(result_a["proposal"], result_b["proposal"])
        self.assertEqual((root_a / "proposals.json").read_bytes(), (root_b / "proposals.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
