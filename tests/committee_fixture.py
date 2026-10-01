"""A sealed committee for tests that exercise the order path.

The proposal boundary now requires a verified committee (audit "Vinculo
Evidencia-Operacion", 2026-09-30), so every test that prepares an order needs
one. These declare MACHINERY_VERIFICATION, which is the honest description of
what they are: proofs that the order path behaves, not capital decisions. That
purpose needs no evidence resolver, and `execute_alpaca_live_order` refuses it,
so no fixture here can be mistaken for an authorization to use real capital.
"""

import pipeline as p

APPROVALS = [
    {"member": "IC-1", "verdict": "APPROVE",
     "evidence_reference": "TEST|order-path-exercise",
     "rationale": "Machinery verification: the order path is under test, not a strategy."},
    {"member": "IC-2", "verdict": "APPROVE",
     "evidence_reference": "TEST|risk-bounded",
     "rationale": "Bounded offline fixture, no broker transport."},
    {"member": "IC-3", "verdict": "APPROVE",
     "evidence_reference": "TEST|offline",
     "rationale": "No market interaction in this test."},
    {"member": "IC-4", "verdict": "APPROVE",
     "evidence_reference": "TEST|no-capital",
     "rationale": "No capital is allocated by a machinery verification."},
]


def _supportive_resolver(reference):
    """Stands in for the sealed registries where a test needs a CAPITAL purpose.

    Only for tests whose subject is the LIVE path itself, which refuses a
    machinery purpose by design. Tests of the binding use the real resolver.
    """
    return {"resolved": True, "supports_capital": True, "reference": reference,
            "detail": "test double"}


def sealed_machinery_committee(root, *, verdicts=None, purpose=None):
    """Seal one committee under `root` and return (registry_path, committee_id)."""
    registry = root / "investment-committees.json"
    declared = verdicts or APPROVALS
    purpose = purpose or p.COMMITTEE_PURPOSE_MACHINERY
    evaluations = [p.committee_evaluation(
        member=item["member"], verdict=item["verdict"],
        evidence_reference=item["evidence_reference"], rationale=item["rationale"])
        for item in declared]
    record = p.constitute_investment_committee(
        registry,
        recommendation_reference="RECOMMENDATION|test-order-path",
        evaluations=evaluations,
        decided_at="2026-09-30T00:00:00Z",
        committee_code_revision="0" * 40,
        purpose=purpose,
        evidence_resolver=_supportive_resolver)
    return registry, record["committee_id"]
