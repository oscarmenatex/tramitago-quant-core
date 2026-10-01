"""The binding DOC-008 §10 required and the chain never had.

Audit "Vinculo Evidencia-Operacion" (2026-09-30) found that `evidence_reference`
was validated only as "a non-empty string", that the committee gate wrote a file
no later step read, and that the one run reaching the broker approved IC-1 with
an identifier resolving to nothing. These are the proofs that each of those
cannot recur, plus the proof that closing them did NOT cost the ability to
exercise the execution path while no Hypothesis passes DOC-011 §2.1.
"""

import json
from pathlib import Path
import tempfile
import unittest

import pipeline as p
from committee_fixture import sealed_machinery_committee


def _evaluations(ic1_reference, ic1_verdict="APPROVE"):
    declared = [
        ("IC-1", ic1_verdict, ic1_reference, "strategy read"),
        ("IC-2", "APPROVE", "RISKGATE|g", "risk read"),
        ("IC-3", "APPROVE", "MARKET|m", "liquid"),
        ("IC-4", "APPROVE", "CAPITAL|c", "sized"),
    ]
    return [p.committee_evaluation(member=m, verdict=v, evidence_reference=r, rationale=t)
            for m, v, r, t in declared]


class EvidenceResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dispositions = self.root / "dispositions.json"
        self.dispositions.write_bytes(p.encoded({"schema_version": "1", "dispositions": [
            {"disposition_id": "DISPOSITION|" + "a" * 64, "outcome": "ACCEPTED"},
            {"disposition_id": "DISPOSITION|" + "b" * 64, "outcome": "REJECTED"},
        ]}))
        self.knowledge = self.root / "knowledge.json"
        self.knowledge.write_bytes(p.encoded({"schema_version": "1", "records": [
            {"knowledge_id": "KNOWLEDGE|" + "c" * 64,
             "references": {"disposition": {"disposition_id": "DISPOSITION|" + "b" * 64}}},
        ]}))
        self.resolver = p.sealed_evidence_resolver(
            disposition_paths=[self.dispositions], knowledge_paths=[self.knowledge])

    def test_a_readable_label_that_looks_like_an_identifier_does_not_resolve(self):
        # The exact shape used on 2026-09-30: plausible, sealed-looking, absent.
        resolution = self.resolver("KNOWLEDGE|sma3-reversal-hypothesis-2026-09-29")
        self.assertFalse(resolution["resolved"])
        self.assertFalse(resolution["supports_capital"])

    def test_an_accepted_disposition_supports_capital_and_a_rejected_one_does_not(self):
        self.assertTrue(self.resolver("DISPOSITION|" + "a" * 64)["supports_capital"])
        self.assertFalse(self.resolver("DISPOSITION|" + "b" * 64)["supports_capital"])

    def test_a_knowledge_record_is_followed_to_its_disposition_not_trusted_itself(self):
        # Knowledge preserves negative results too, so existing proves nothing.
        resolution = self.resolver("KNOWLEDGE|" + "c" * 64)
        self.assertTrue(resolution["resolved"])
        self.assertFalse(resolution["supports_capital"])
        self.assertIn("REJECTED", resolution["detail"])


class CommitteeBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.registry = self.root / "committees.json"

    def _seal(self, evaluations, purpose, resolver=None):
        return p.constitute_investment_committee(
            self.registry, recommendation_reference="RECOMMENDATION|r",
            evaluations=evaluations, decided_at="2026-09-30T00:00:00Z",
            committee_code_revision="0" * 40, purpose=purpose, evidence_resolver=resolver)

    @staticmethod
    def _resolver(supports, resolved=True):
        return lambda reference: {"resolved": resolved, "supports_capital": supports,
                                  "reference": reference, "detail": "double"}

    def test_capital_approval_on_unresolvable_evidence_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            self._seal(_evaluations("KNOWLEDGE|not-a-real-id"), p.COMMITTEE_PURPOSE_CAPITAL,
                       self._resolver(supports=False, resolved=False))
        self.assertIn("does not resolve", str(caught.exception))

    def test_capital_approval_on_unsupportive_evidence_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            self._seal(_evaluations("DISPOSITION|x"), p.COMMITTEE_PURPOSE_CAPITAL,
                       self._resolver(supports=False))
        self.assertIn("does not support it", str(caught.exception))

    def test_capital_requires_a_resolver_at_all(self):
        with self.assertRaises(ValueError):
            self._seal(_evaluations("DISPOSITION|x"), p.COMMITTEE_PURPOSE_CAPITAL)

    def test_capital_approval_on_supportive_evidence_is_sealed(self):
        record = self._seal(_evaluations("DISPOSITION|ok"), p.COMMITTEE_PURPOSE_CAPITAL,
                            self._resolver(supports=True))
        self.assertEqual(record["decision"]["outcome"], "APPROVED")
        self.assertEqual(record["purpose"], p.COMMITTEE_PURPOSE_CAPITAL)

    def test_an_ic1_veto_needs_no_supporting_evidence_to_stand(self):
        record = self._seal(_evaluations("DISPOSITION|x", ic1_verdict="REJECT"),
                            p.COMMITTEE_PURPOSE_CAPITAL)
        self.assertEqual(record["decision"]["outcome"], "REJECTED")

    def test_machinery_verification_may_ride_a_rejected_signal(self):
        # The whole point of the purpose split: closing the gap must not forbid
        # exercising the execution path while zero Hypotheses pass DOC-011 2.1.
        record = self._seal(_evaluations("KNOWLEDGE|whatever"),
                            p.COMMITTEE_PURPOSE_MACHINERY)
        self.assertEqual(record["decision"]["outcome"], "APPROVED")
        self.assertEqual(record["purpose"], p.COMMITTEE_PURPOSE_MACHINERY)

    def test_a_committee_must_declare_a_purpose(self):
        with self.assertRaises(ValueError):
            self._seal(_evaluations("DISPOSITION|x"), "WHATEVER")

    def test_the_purpose_is_sealed_into_the_identity(self):
        machinery = self._seal(_evaluations("E|x"), p.COMMITTEE_PURPOSE_MACHINERY)
        capital = self._seal(_evaluations("E|x"), p.COMMITTEE_PURPOSE_CAPITAL,
                             self._resolver(supports=True))
        self.assertNotEqual(machinery["committee_id"], capital["committee_id"])

    def test_a_legacy_schema_one_record_still_verifies(self):
        # The one pre-existing sealed record must stay readable as evidence; the
        # audit trail is the reason this gap is known at all.
        record = self._seal(_evaluations("E|x"), p.COMMITTEE_PURPOSE_MACHINERY)
        legacy = json.loads(self.registry.read_bytes())
        content = {"schema_version": "1",
                   "recommendation_reference": record["recommendation_reference"],
                   "evaluations": record["evaluations"], "decision": record["decision"]}
        committee_id = "INVESTMENT_COMMITTEE|" + p.digest(p.encoded(content))
        rebuilt = {"committee_id": committee_id, **content,
                   "materialization": record["materialization"], "status": record["status"]}
        rebuilt["record_id"] = ("INVESTMENT_COMMITTEE_RECORD|" + committee_id + "|"
                                + p.digest(p.encoded({k: rebuilt[k] for k in rebuilt})))
        legacy["committees"] = [rebuilt]
        p._atomic_write(self.registry, p.encoded(legacy))
        self.assertEqual(
            p.verified_investment_committee(self.registry, committee_id)["schema_version"], "1")


class OrderPathRequiresTheCommitteeTests(unittest.TestCase):
    """The gate stops being an adjacent CLI call and becomes a data dependency."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _state(self, name):
        root = self.root / name
        root.mkdir()
        start = p.epoch(p.CONFIG["start"])
        raw = [[start + i * 86400, c - 1, c + 1, c - .5, c, 1]
               for i, c in enumerate([10, 11, 12])]
        from datetime import datetime, timezone
        p.observe(root / "input", root / "state.json", root / "observation",
                  raw=json.dumps(raw).encode(), now=datetime(2024, 1, 10, tzinfo=timezone.utc))
        decisions = p.decide(root / "state.json", root / "decision")["new_decisions"]
        return root, decisions[-1]["identity"]

    @staticmethod
    def _config():
        config = {
            "broker": "Alpaca", "account_target": "real/live", "instrument": "BTC-USD",
            "max_capital_usd": "200", "max_exposure_usd": "50", "risk_budget_usd": "5",
            "proposed_notional_usd": "50", "operational_risk_usd": "5",
            "order_type": "limit", "limit_price": "10", "manual_approval_required": True,
            "risk_contract_version": p.PHASE4_RISK_CONTRACT_VERSION,
        }
        config["risk_contract_identity"] = p.phase4_risk_contract_identity(config)
        return config

    def test_a_proposal_cannot_be_built_without_a_registered_committee(self):
        root, identity = self._state("absent")
        with self.assertRaises(ValueError):
            p.prepare_real_order_proposal(
                root / "state.json", root / "out", identity, self._config(),
                committee_registry_path=root / "missing.json",
                committee_id="INVESTMENT_COMMITTEE|" + "f" * 64)

    def test_a_proposal_carries_the_committee_and_its_purpose(self):
        root, identity = self._state("linked")
        registry, committee_id = sealed_machinery_committee(root)
        result = p.prepare_real_order_proposal(
            root / "state.json", root / "out", identity, self._config(),
            committee_registry_path=registry, committee_id=committee_id)
        self.assertEqual(result["proposal"]["committee_id"], committee_id)
        self.assertEqual(result["proposal"]["purpose"], p.COMMITTEE_PURPOSE_MACHINERY)

    def test_a_vetoed_committee_blocks_the_proposal(self):
        root, identity = self._state("vetoed")
        vetoed = [{"member": "IC-1", "verdict": "REJECT",
                   "evidence_reference": "E|x", "rationale": "no"},
                  {"member": "IC-2", "verdict": "APPROVE",
                   "evidence_reference": "E|x", "rationale": "ok"},
                  {"member": "IC-3", "verdict": "APPROVE",
                   "evidence_reference": "E|x", "rationale": "ok"},
                  {"member": "IC-4", "verdict": "APPROVE",
                   "evidence_reference": "E|x", "rationale": "ok"}]
        registry, committee_id = sealed_machinery_committee(root, verdicts=vetoed)
        result = p.prepare_real_order_proposal(
            root / "state.json", root / "out", identity, self._config(),
            committee_registry_path=registry, committee_id=committee_id)
        # The rejection is itself persisted evidence, so a record exists -- what
        # must not exist is a proposal anything downstream can act on.
        proposal = result["proposal"]
        self.assertEqual(proposal["status"], "REJECTED")
        self.assertTrue(any("committee did not approve" in r
                            for r in proposal["rejection_reasons"]))
        self.assertFalse(p.proposal_is_manually_approved(proposal))


class MachineryVerificationNeverReachesRealCapitalTests(unittest.TestCase):
    """The line that makes the purpose exemption safe.

    A machinery-verification run is deliberately allowed to ride a rejected
    signal -- that is the only way to exercise the execution path while no
    Hypothesis passes DOC-011 §2.1. That licence is defensible ONLY because such
    a run can never become a capital decision by pointing at LIVE.
    """

    def setUp(self):
        import tests.test_alpaca_request as request_tests
        self.builder = request_tests.AlpacaRequestPreparationTests()
        self.builder.setUp()
        self.addCleanup(self.builder.temporary.cleanup)

    def _live_request(self, purpose):
        self.builder.committee_purpose = purpose
        root, proposal, revalidation, config = self.builder.state("live")
        request = self.builder.prepare(
            root, proposal, revalidation, config, environment="LIVE")["prepared_request"]
        return root, request

    @staticmethod
    def _forbidden(*args, **kwargs):
        raise AssertionError("a machinery-verification run must never reach the broker")

    def test_a_machinery_purpose_is_blocked_before_any_transport(self):
        root, request = self._live_request(p.COMMITTEE_PURPOSE_MACHINERY)
        result = p.execute_alpaca_live_order(
            root / "state.json", root / "submit", request["identity"],
            "2024-01-03T14:05:00Z", 5.0, self._forbidden, self._forbidden)
        self.assertEqual(result["status"], "BLOCKED_REQUEST")
        self.assertFalse(result["live_request_sent"])
        self.assertFalse(result["real_capital_used_during_implementation"])

    def test_the_same_request_under_a_capital_purpose_is_not_blocked_for_that_reason(self):
        # Proves the block above is the PURPOSE, not some unrelated invalidity.
        root, request = self._live_request(p.COMMITTEE_PURPOSE_CAPITAL)
        state = json.loads((root / "state.json").read_bytes())
        self.assertEqual(p._request_committee_purpose(state, request),
                         p.COMMITTEE_PURPOSE_CAPITAL)
        self.assertTrue(p.prepared_alpaca_request_is_valid(state, request))


if __name__ == "__main__":
    unittest.main()
