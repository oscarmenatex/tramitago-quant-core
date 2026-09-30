"""Proof for the Discovery / Finding phase (2026-09-29): the "Idea" box of
DOC-004 §4 that the platform skipped.

A Finding records an observation from exploration. It is deliberately
weaker than a Hypothesis, and the transition Finding -> Hypothesis is the
only place the frozen contract is imposed -- kept manual on purpose (an
automatic rule on the same data would re-create data snooping). The tests
below cover the lightweight registry, the OPEN -> PROMOTED/DISCARDED
transitions with their guards, and the full end-to-end transition where a
Finding becomes a real Hypothesis carrying `FINDING|<id>` provenance,
with a bidirectional link and zero change to the existing Hypothesis
contract.
"""

import tempfile
import unittest
from pathlib import Path

import pipeline as p


class FindingRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.registry = Path(self.temporary.name) / "findings.json"

    def _constitute(self):
        return p.constitute_finding(
            self.registry,
            observation="For BTC-USD 2025, FUNDING_RATE_SURGE gave 5/5 negative folds",
            exploration_context="swept SMA/Momentum/Volume/Funding on BTC-USD, 2025, daily",
            supporting_evidence=["walk-forward-fold-results-funding-surge.json"],
            created_by="oscar+claude-analysis", created_at="2026-09-29T12:00:00Z")

    def test_constituted_finding_is_open_and_reloadable(self):
        record = self._constitute()
        self.assertTrue(p._finding_id_is_valid(record["finding_id"]))
        self.assertEqual(record["status"], p.FINDING_STATUS_OPEN)
        self.assertEqual(p.load_finding(self.registry, record["finding_id"]), record)
        # A Finding deliberately has NO acceptance criterion, period, universe or dataset.
        self.assertNotIn("acceptance_criterion", record)
        self.assertNotIn("constraints", record)

    def test_a_finding_carries_no_frozen_contract_fields(self):
        record = self._constitute()
        self.assertEqual(set(record), {
            "finding_id", "schema_version", "observation", "exploration_context",
            "supporting_evidence", "status", "created_by", "created_at", "history"})

    def test_promote_moves_open_to_promoted_and_records_the_hypothesis(self):
        record = self._constitute()
        promoted = p.promote_finding(
            self.registry, record["finding_id"],
            hypothesis_id="HYPOTHESIS|00000000-0000-0000-0000-000000000000",
            at="2026-09-29T13:00:00Z")
        self.assertEqual(promoted["status"], p.FINDING_STATUS_PROMOTED)
        self.assertIn("HYPOTHESIS|00000000-0000-0000-0000-000000000000",
                      promoted["history"][-1]["note"])

    def test_discard_moves_open_to_discarded_with_a_reason(self):
        record = self._constitute()
        discarded = p.discard_finding(
            self.registry, record["finding_id"],
            reason="in-sample only; did not survive out-of-sample 2024",
            at="2026-09-29T13:00:00Z")
        self.assertEqual(discarded["status"], p.FINDING_STATUS_DISCARDED)

    def test_a_finding_can_only_transition_once(self):
        record = self._constitute()
        p.discard_finding(self.registry, record["finding_id"],
                          reason="not worth testing", at="2026-09-29T13:00:00Z")
        with self.assertRaises(ValueError):
            p.promote_finding(self.registry, record["finding_id"],
                              hypothesis_id="HYPOTHESIS|00000000-0000-0000-0000-000000000000",
                              at="2026-09-29T14:00:00Z")

    def test_query_filters_by_status(self):
        keep = self._constitute()
        drop = self._constitute()
        p.discard_finding(self.registry, drop["finding_id"], reason="redundant",
                          at="2026-09-29T13:00:00Z")
        open_ids = {f["finding_id"] for f in p.query_findings(self.registry, status=p.FINDING_STATUS_OPEN)}
        self.assertEqual(open_ids, {keep["finding_id"]})
        self.assertEqual(len(p.query_findings(self.registry)), 2)

    def test_rejects_a_finding_without_an_observation(self):
        with self.assertRaises(ValueError):
            p.constitute_finding(
                self.registry, observation="", exploration_context="ctx",
                supporting_evidence=["x"], created_by="me", created_at="2026-09-29T12:00:00Z")


class FindingToHypothesisTransitionTests(unittest.TestCase):
    """The transition is the only place the frozen contract is imposed. It is
    two explicit, decoupled steps and requires ZERO change to the Hypothesis
    contract -- the FINDING| provenance entry is already accepted."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.findings = root / "findings.json"
        self.hypotheses = root / "hypotheses.json"

    def test_finding_becomes_a_hypothesis_traceable_both_ways(self):
        finding = p.constitute_finding(
            self.findings,
            observation="ETH/BTC ratio above its own average kept rising in 2025",
            exploration_context="explored ETH/BTC daily ratio vs its 20-day average, 2025",
            supporting_evidence=["experiment-results-pair-eth-btc.json"],
            created_by="oscar+claude-analysis", created_at="2026-09-29T12:00:00Z")

        # Step 1: constitute the Hypothesis, carrying the Finding in provenance.
        # No change to constitute_hypothesis -- the provenance list already
        # accepts an arbitrary distinct FINDING| entry alongside the legacy
        # DISCOVERY| literal.
        hypothesis = p.constitute_hypothesis(
            self.hypotheses,
            description="For ETH-USD, mean t+1 return when close>SMA3 exceeds otherwise",
            target_metric="m", expected_direction="INCREASE",
            constraints={"period": {"start_utc": "2025-01-01T00:00:00Z",
                                    "end_exclusive_utc": "2026-01-01T00:00:00Z"},
                         "universe": ["ETH-USD"],
                         "variables": ["close", "sma_close_3", "forward_return_1d"]},
            acceptance_criterion={"metric": "m", "comparison": "GT", "threshold": "0",
                                  "expected_direction": "INCREASE"},
            creation_timestamp="2026-09-29T13:00:00Z", status="CONSTITUTED",
            created_by="oscar+claude-analysis",
            provenance=["SNAPSHOT_SHA256|" + "a" * 64, "DISCOVERY|artifacts/live-run-1",
                        "FINDING|" + finding["finding_id"].removeprefix("FINDING|")],
            system_version="0.1.0", code_revision="0" * 40)

        self.assertIn(finding["finding_id"], hypothesis["creation_context"]["provenance"])

        # Step 2: record the reverse link on the Finding.
        promoted = p.promote_finding(
            self.findings, finding["finding_id"],
            hypothesis_id=hypothesis["hypothesis_id"], at="2026-09-29T13:00:01Z")
        self.assertEqual(promoted["status"], p.FINDING_STATUS_PROMOTED)
        self.assertIn(hypothesis["hypothesis_id"], promoted["history"][-1]["note"])


if __name__ == "__main__":
    unittest.main()
