"""Proof that the Investment Committee is wired into the operational chain as
a CLI gate (Etapa 4.5, M4.5-T3 wired, 2026-09-29), sitting after the risk gate
and before order preparation per DOC-009 §5. Only a full APPROVED lets the
sequence proceed; every other decision halts it."""

import tempfile
import unittest
from pathlib import Path

import pipeline as p


def _payload(v2="APPROVE", purpose="MACHINERY_VERIFICATION"):
    return {
        "recommendation_reference": "RECOMMENDATION|r1",
        "purpose": purpose,
        "decided_at": "2026-09-29T15:00:00Z",
        "committee_code_revision": "0" * 40,
        "evaluations": [
            {"member": "IC-1", "verdict": "APPROVE", "evidence_reference": "KNOWLEDGE|k",
             "rationale": "validated"},
            {"member": "IC-2", "verdict": v2, "evidence_reference": "RISKGATE|g",
             "rationale": "risk read"},
            {"member": "IC-3", "verdict": "APPROVE", "evidence_reference": "MARKET|m",
             "rationale": "liquid"},
            {"member": "IC-4", "verdict": "APPROVE", "evidence_reference": "CAPITAL|c",
             "rationale": "within exposure"},
        ],
    }


class InvestmentCommitteeGateCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _run(self, payload):
        input_path = self.root / "in.json"
        input_path.write_bytes(p.encoded(payload))
        return p.run_investment_committee_gate(
            input_path, self.root / "committees.json", self.root / "out")

    def test_full_approval_passes(self):
        result = self._run(_payload("APPROVE"))
        self.assertEqual(result["decision"]["outcome"], "APPROVED")
        self.assertEqual(result["status"], "PASS")

    def test_any_veto_fails(self):
        result = self._run(_payload("REJECT"))
        self.assertEqual(result["decision"]["outcome"], "REJECTED")
        self.assertEqual(result["status"], "FAIL")

    def test_reduced_halts_rather_than_silently_passing(self):
        result = self._run(_payload("REDUCE"))
        self.assertEqual(result["decision"]["code"], "D-008-003")
        self.assertEqual(result["status"], "FAIL")

    def test_the_decision_is_sealed_to_the_registry(self):
        result = self._run(_payload("APPROVE"))
        record = p.verified_investment_committee(
            self.root / "committees.json", result["committee_id"])
        self.assertEqual(record["decision"]["outcome"], "APPROVED")


if __name__ == "__main__":
    unittest.main()
