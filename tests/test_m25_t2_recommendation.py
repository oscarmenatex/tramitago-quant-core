"""Focused proof for M2.5-T2: produce a traceable, effect-free recommendation.

Relates PAPER Evidence and caller-supplied limits to an evaluation,
registering RECOMMENDED / NOT_RECOMMENDED / INSUFFICIENT_EVIDENCE --
never leaving evidence unclassified -- and proves Knowledge changes no
configuration by itself.
"""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

import pipeline as p


ROOT = Path(__file__).resolve().parents[1]
PROCESSING = "2026-09-26T00:15:00Z"
STARTED = "2026-09-14T00:00:00Z"
LINKED_AT = "2026-09-26T09:00:00Z"
LINKING_REVISION = "c" * 40
PROPOSED_AT = "2026-09-26T09:30:00Z"
RECOMMENDATION_REVISION = "5" * 40
ONLY_COMPLETED_LIMITS = {"acceptable_terminal_results": ["COMPLETED"]}
BROADER_LIMITS = {"acceptable_terminal_results": ["COMPLETED", "BLOCKED", "RECOVERABLE_ERROR"]}


class M25T2RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.case = self._make_case(self.root / "case")
        self.evidence = self.root / "paper-evidence.json"
        self.recommendations = self.root / "recommendations.json"

    def _make_case(self, root):
        root.mkdir(parents=True, exist_ok=True)
        case = {
            "session": root / "session" / "state.json",
            "configuration": root / "configuration.json",
            "invocation": root / "invocation.json",
            "policy": root / "policy.json",
            "ledger": root / "activation-ledger",
            "receipts": root / "activation-receipts",
            "dataset": root / "dataset.json",
            "selection": root / "selection.json",
            "fixture": root / "fixture.json",
            "acceptance": root / "acceptance.json",
            "indicator": root / "indicator.json",
            "cycle": root / "cycle.json",
            "result": root / "m12-invocation-result.json",
            "output": root / "output",
            "session_id": "PAPER_SESSION|m25-t2-recommendation|" + root.name,
        }
        p.prepare_forward_paper_invocation(
            case["session"], case["configuration"], case["invocation"],
            case["session_id"], STARTED, PROCESSING)
        p.prepare_forward_paper_activation_policy(case["policy"], case["configuration"])
        return case

    def _t4_path_args(self):
        case = self.case
        return [
            str(case["policy"]), str(case["configuration"]), str(case["session"]),
            str(case["invocation"]), str(case["ledger"]), str(case["receipts"]),
            str(case["dataset"]), str(case["selection"]), str(case["fixture"]),
            str(case["acceptance"]), str(case["indicator"]), str(case["cycle"]),
            str(case["result"]), str(case["output"]),
        ]

    def _fake_m12(self, terminal):
        case = self.case

        def fake(*args, **kwargs):
            preparation = p.load_forward_paper_preparation(
                case["session"], case["configuration"], case["invocation"])
            content = {
                "configuration_id": preparation["configuration"]["configuration_id"],
                "canonical_cycle_id": None, "dataset_id": None,
                "evidence": {"test_double": True},
                "invocation_id": preparation["invocation"]["invocation_id"],
                "mode": "FORWARD_PAPER",
                "processing_instant_utc":
                    preparation["invocation"]["processing_instant_utc"],
                "reason": "deterministic M1.2 public-entrypoint double",
                "selection_receipt_id": None,
                "session_id": preparation["session"]["session_id"],
                "terminal_result": terminal,
            }
            record = {
                "schema_version": p.FORWARD_PAPER_INVOCATION_RESULT_SCHEMA_VERSION,
                **content,
                "invocation_result_id": "FORWARD_PAPER_INVOCATION_RESULT|"
                + p.digest(p.encoded(content)),
            }
            case["result"].write_bytes(p.encoded(record))
            return {"status": "PASS", **record}
        return fake

    def link_completed_evidence(self):
        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=self._fake_m12("COMPLETED")):
            p.attempt_forward_paper_activation(
                *self._t4_path_args(), PROCESSING, str(uuid.uuid4()))
        case = self.case
        return p.constitute_paper_evidence(
            self.evidence, policy_path=case["policy"],
            configuration_path=case["configuration"], ledger_directory=case["ledger"],
            receipt_directory=case["receipts"], linked_at=LINKED_AT,
            linking_code_revision=LINKING_REVISION)

    def recommend(self, evidence, *, limits=ONLY_COMPLETED_LIMITS,
                  evidence_record_id=None, proposed_at=PROPOSED_AT,
                  recommendation_code_revision=RECOMMENDATION_REVISION,
                  recommendations=None):
        case = self.case
        return p.constitute_recommendation(
            recommendations or self.recommendations, paper_evidence_registry_path=self.evidence,
            evidence_id=evidence["evidence_id"],
            evidence_record_id=(
                evidence["record_id"] if evidence_record_id is None else evidence_record_id),
            policy_path=case["policy"], configuration_path=case["configuration"],
            receipt_directory=case["receipts"], limits=limits, proposed_at=proposed_at,
            recommendation_code_revision=recommendation_code_revision)

    def verify(self, recommendation_id, *, recommendations=None):
        case = self.case
        return p.verified_recommendation(
            recommendations or self.recommendations, recommendation_id,
            paper_evidence_registry_path=self.evidence, policy_path=case["policy"],
            configuration_path=case["configuration"], receipt_directory=case["receipts"])

    def test_recommends_when_the_completed_receipt_is_within_limits(self):
        evidence = self.link_completed_evidence()
        record = self.recommend(evidence)

        self.assertEqual(self.verify(record["recommendation_id"]), record)
        self.assertTrue(record["recommendation_id"].startswith("RECOMMENDATION|"))
        self.assertTrue(record["record_id"].startswith("RECOMMENDATION_RECORD|"))
        self.assertEqual(record["reference"]["paper_evidence"],
                         {"evidence_id": evidence["evidence_id"], "record_id": evidence["record_id"]})
        self.assertEqual(record["limits"], ONLY_COMPLETED_LIMITS)
        self.assertEqual(record["evidence_snapshot"], evidence["snapshot"])
        self.assertEqual(record["outcome"], "RECOMMENDED")
        self.assertIsNone(record["outcome_reason"])
        self.assertEqual(record["status"], p.RECOMMENDATION_STATUS)

    def test_declares_insufficient_evidence_when_outside_limits(self):
        evidence = self.link_completed_evidence()
        narrow_limits = {"acceptable_terminal_results": ["BLOCKED"]}
        record = self.recommend(evidence, limits=narrow_limits)
        self.assertEqual(record["outcome"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(record["outcome_reason"], "TERMINAL_RESULT_OUTSIDE_LIMITS")

    def test_not_recommended_for_a_within_limits_error_terminal_result(self):
        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=self._fake_m12("RECOVERABLE_ERROR")):
            p.attempt_forward_paper_activation(*self._t4_path_args(), PROCESSING, str(uuid.uuid4()))
        case = self.case
        evidence = p.constitute_paper_evidence(
            self.evidence, policy_path=case["policy"], configuration_path=case["configuration"],
            ledger_directory=case["ledger"], receipt_directory=case["receipts"],
            linked_at=LINKED_AT, linking_code_revision=LINKING_REVISION)

        record = self.recommend(evidence, limits=BROADER_LIMITS)
        self.assertEqual(record["outcome"], "NOT_RECOMMENDED")
        self.assertEqual(record["outcome_reason"], "TERMINAL_RESULT_RECOVERABLE_ERROR")

    def test_never_writes_to_session_configuration_or_policy(self):
        evidence = self.link_completed_evidence()
        watched = [self.case["session"], self.case["configuration"], self.case["policy"]]
        before = {path: path.read_bytes() for path in watched}
        self.recommend(evidence)
        after = {path: path.read_bytes() for path in watched}
        self.assertEqual(before, after)

    def test_reloads_exactly_from_a_second_process(self):
        evidence = self.link_completed_evidence()
        record = self.recommend(evidence)
        case = self.case
        command = (
            "import json, pipeline as p; r=p.verified_recommendation("
            + repr(str(self.recommendations)) + ", " + repr(record["recommendation_id"]) + ", "
            "paper_evidence_registry_path=" + repr(str(self.evidence)) + ", "
            "policy_path=" + repr(str(case["policy"])) + ", "
            "configuration_path=" + repr(str(case["configuration"])) + ", "
            "receipt_directory=" + repr(str(case["receipts"])) + "); "
            "print(json.dumps(r, sort_keys=True))")
        process = subprocess.run(
            [sys.executable, "-B", "-c", command], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout), record)

    def test_is_idempotent_and_ignores_a_later_proposal_attempt(self):
        evidence = self.link_completed_evidence()
        first = self.recommend(evidence, proposed_at=PROPOSED_AT)
        second = self.recommend(
            evidence, proposed_at="2026-09-27T09:30:00Z", recommendation_code_revision="6" * 40)
        self.assertEqual(first, second)

    def test_different_limits_over_the_same_evidence_are_distinct_recommendations(self):
        evidence = self.link_completed_evidence()
        broad = self.recommend(evidence, limits=BROADER_LIMITS)
        narrow = self.recommend(evidence, limits=ONLY_COMPLETED_LIMITS)
        self.assertNotEqual(broad["recommendation_id"], narrow["recommendation_id"])
        self.assertEqual(broad["outcome"], "RECOMMENDED")
        self.assertEqual(narrow["outcome"], "RECOMMENDED")

    def test_rejects_a_wrong_evidence_seal_before_persisting(self):
        evidence = self.link_completed_evidence()
        with self.assertRaisesRegex(ValueError, "seal"):
            self.recommend(evidence, evidence_record_id=evidence["record_id"] + "-x")
        self.assertFalse(self.recommendations.exists())

    def test_rejects_invalid_limits(self):
        evidence = self.link_completed_evidence()
        with self.assertRaisesRegex(ValueError, "limits"):
            self.recommend(evidence, limits={"acceptable_terminal_results": []})
        with self.assertRaisesRegex(ValueError, "limits"):
            self.recommend(evidence, limits={"acceptable_terminal_results": ["NOT_A_RESULT"]})
        self.assertFalse(self.recommendations.exists())

    def test_rejects_a_tampered_recommendations_registry(self):
        evidence = self.link_completed_evidence()
        record = self.recommend(evidence)
        altered = json.loads(self.recommendations.read_bytes())
        altered["recommendations"][0]["outcome"] = "NOT_RECOMMENDED"
        altered["recommendations"][0]["outcome_reason"] = "TAMPERED"
        self.recommendations.write_bytes(p.encoded(altered))
        with self.assertRaisesRegex(ValueError, "invalid"):
            self.verify(record["recommendation_id"])

    def test_query_filters_by_outcome_and_returns_empty_before_anything_proposed(self):
        self.assertEqual(p.query_recommendation(self.recommendations), [])
        evidence = self.link_completed_evidence()
        record = self.recommend(evidence)
        matching = p.query_recommendation(self.recommendations, outcome="RECOMMENDED")
        self.assertEqual([item["recommendation_id"] for item in matching],
                         [record["recommendation_id"]])
        self.assertEqual(p.query_recommendation(self.recommendations, outcome="NOT_RECOMMENDED"), [])

    def test_load_rejects_a_malformed_identity(self):
        with self.assertRaisesRegex(ValueError, "valid"):
            p.load_recommendation(self.recommendations, "not-a-valid-id")


if __name__ == "__main__":
    unittest.main()
