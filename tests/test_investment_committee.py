"""Proof for CAP-010 Investment Committee (Etapa 4.5, M4.5-T3, 2026-09-29):
the five independent evaluations of DOC-008 §4, replacing the single
collapsed AUTHORIZED/REJECTED.

The properties that matter: each of IC-1..IC-4 is required and stays
separately traceable; IC-5 (Fiscal) is optional and never blocks by its
absence; the aggregate is risk-first per MR-008-001/003 (a veto is
absolute, an inconclusive core evaluation defers rather than approves);
and the sealed record re-derives its decision, failing closed on tampering.
"""

import tempfile
import unittest
from pathlib import Path

import pipeline as p


def _core(v1="APPROVE", v2="APPROVE", v3="APPROVE", v4="APPROVE"):
    verdicts = {"IC-1": v1, "IC-2": v2, "IC-3": v3, "IC-4": v4}
    return [p.committee_evaluation(
        member=m, verdict=verdicts[m], evidence_reference=f"EVIDENCE|{m}",
        rationale=f"{m} rationale") for m in ("IC-1", "IC-2", "IC-3", "IC-4")]


class CommitteeDecisionRuleTests(unittest.TestCase):
    def test_all_approve_is_approved(self):
        self.assertEqual(p.committee_decision(_core())["code"], "D-008-001")

    def test_any_reject_vetoes_absolutely(self):
        # Even with three approvals, one risk veto rejects (capital preservation first)
        d = p.committee_decision(_core(v2="REJECT"))
        self.assertEqual(d["outcome"], "REJECTED")
        self.assertEqual(d["code"], "D-008-002")

    def test_reject_beats_reduce_and_inconclusive(self):
        d = p.committee_decision(_core(v1="REDUCE", v2="REJECT", v3="INCONCLUSIVE"))
        self.assertEqual(d["outcome"], "REJECTED")

    def test_inconclusive_defers_never_approves(self):
        # A missing/uncertain verdict must never be read as approval
        d = p.committee_decision(_core(v3="INCONCLUSIVE"))
        self.assertEqual(d["outcome"], "DEFERRED")
        self.assertEqual(d["code"], "D-008-004")

    def test_reduce_without_veto_reduces(self):
        self.assertEqual(p.committee_decision(_core(v4="REDUCE"))["code"], "D-008-003")

    def test_optional_fiscal_can_veto_but_absence_never_blocks(self):
        approved = p.committee_decision(_core())
        self.assertEqual(approved["outcome"], "APPROVED")  # IC-5 absent, still decides
        with_fiscal_reject = _core() + [p.committee_evaluation(
            member="IC-5", verdict="REJECT", evidence_reference="TAX|x", rationale="bad after tax")]
        self.assertEqual(p.committee_decision(with_fiscal_reject)["outcome"], "REJECTED")


class CommitteeRecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = Path(self.tmp.name) / "committees.json"

    def _constitute(self, evaluations, purpose=None, evidence_resolver=None):
        return p.constitute_investment_committee(
            self.registry, recommendation_reference="RECOMMENDATION|r1",
            evaluations=evaluations, decided_at="2026-09-29T15:00:00Z",
            committee_code_revision="0" * 40,
            purpose=purpose or p.COMMITTEE_PURPOSE_MACHINERY,
            evidence_resolver=evidence_resolver)

    def test_constituted_record_keeps_all_evaluations_separately(self):
        record = self._constitute(_core(v2="REDUCE"))
        self.assertEqual(record["decision"]["code"], "D-008-003")
        members = [e["member"] for e in record["evaluations"]]
        self.assertEqual(members, ["IC-1", "IC-2", "IC-3", "IC-4"])  # traceable, not collapsed
        self.assertEqual(record["evaluations"][1]["verdict"], "REDUCE")

    def test_the_four_core_evaluations_are_required(self):
        with self.assertRaises(ValueError):
            self._constitute(_core()[:3])  # missing IC-4

    def test_a_member_evaluates_at_most_once(self):
        dup = _core() + [p.committee_evaluation(
            member="IC-1", verdict="REJECT", evidence_reference="E|x", rationale="second")]
        with self.assertRaises(ValueError):
            self._constitute(dup)

    def test_verified_reproduces_and_detects_a_flipped_verdict(self):
        record = self._constitute(_core())
        self.assertEqual(
            p.verified_investment_committee(self.registry, record["committee_id"]), record)

        tampered = p._load_committee_registry(self.registry)
        tampered["committees"][0]["evaluations"][1]["verdict"] = "REJECT"
        p._atomic_write(self.registry, p.encoded(tampered))
        with self.assertRaises(ValueError):
            p.verified_investment_committee(self.registry, record["committee_id"])

    def test_constitution_is_idempotent(self):
        first = self._constitute(_core())
        second = self._constitute(_core())
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
