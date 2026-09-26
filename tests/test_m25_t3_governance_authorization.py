"""Focused proof for M2.5-T3: Governance authorizes/rejects a next configuration.

Verifies the operational configuration owner's own boundary
(load_forward_paper_configuration) before deciding, registers
AUTHORIZED or REJECTED, creates a new configuration version only when
authorized, and demonstrates in an isolated environment that a later
cycle uses exclusively the authorized configuration -- while the prior
(T9-like) session, configuration, ledger, and receipts remain intact.
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
STARTED = "2026-09-14T00:00:00Z"
LINKED_AT = "2026-09-26T09:00:00Z"
LINKING_REVISION = "c" * 40
PROPOSED_AT = "2026-09-26T09:30:00Z"
RECOMMENDATION_REVISION = "5" * 40
DECIDED_AT = "2026-09-26T10:00:00Z"
DECISION_REVISION = "7" * 40
ONLY_COMPLETED_LIMITS = {"acceptable_terminal_results": ["COMPLETED"]}


class M25T3GovernanceAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.case = self._make_case(self.root / "prior", "m25-t3-prior")
        self.evidence = self.root / "paper-evidence.json"
        self.recommendations = self.root / "recommendations.json"
        self.authorizations = self.root / "authorizations.json"

    def _make_case(self, root, tag, *, processing="2026-09-26T00:15:00Z"):
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
            "session_id": "PAPER_SESSION|" + tag + "|" + root.name,
            "processing": processing,
        }
        p.prepare_forward_paper_invocation(
            case["session"], case["configuration"], case["invocation"],
            case["session_id"], STARTED, processing)
        p.prepare_forward_paper_activation_policy(case["policy"], case["configuration"])
        return case

    def _t4_path_args(self, case):
        return [
            str(case["policy"]), str(case["configuration"]), str(case["session"]),
            str(case["invocation"]), str(case["ledger"]), str(case["receipts"]),
            str(case["dataset"]), str(case["selection"]), str(case["fixture"]),
            str(case["acceptance"]), str(case["indicator"]), str(case["cycle"]),
            str(case["result"]), str(case["output"]),
        ]

    def _fake_m12(self, case, terminal):
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

    def _complete_and_recommend(self, case, *, terminal="COMPLETED",
                                limits=ONLY_COMPLETED_LIMITS, evidence_registry=None,
                                recommendation_registry=None):
        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=self._fake_m12(case, terminal)):
            p.attempt_forward_paper_activation(
                *self._t4_path_args(case), case["processing"], str(uuid.uuid4()))
        evidence = p.constitute_paper_evidence(
            evidence_registry or self.evidence, policy_path=case["policy"],
            configuration_path=case["configuration"], ledger_directory=case["ledger"],
            receipt_directory=case["receipts"], linked_at=LINKED_AT,
            linking_code_revision=LINKING_REVISION)
        recommendation = p.constitute_recommendation(
            recommendation_registry or self.recommendations,
            paper_evidence_registry_path=evidence_registry or self.evidence,
            evidence_id=evidence["evidence_id"], evidence_record_id=evidence["record_id"],
            policy_path=case["policy"], configuration_path=case["configuration"],
            receipt_directory=case["receipts"], limits=limits, proposed_at=PROPOSED_AT,
            recommendation_code_revision=RECOMMENDATION_REVISION)
        return evidence, recommendation

    def authorize(self, case, recommendation, *, recommendation_record_id=None,
                 decided_at=DECIDED_AT, decision_code_revision=DECISION_REVISION,
                 authorizations=None):
        return p.constitute_governance_authorization(
            authorizations or self.authorizations,
            recommendation_registry_path=self.recommendations,
            recommendation_id=recommendation["recommendation_id"],
            recommendation_record_id=(
                recommendation["record_id"] if recommendation_record_id is None
                else recommendation_record_id),
            paper_evidence_registry_path=self.evidence, policy_path=case["policy"],
            configuration_path=case["configuration"], receipt_directory=case["receipts"],
            session_path=case["session"], decided_at=decided_at,
            decision_code_revision=decision_code_revision)

    def verify(self, case, authorization_id, *, authorizations=None):
        return p.verified_governance_authorization(
            authorizations or self.authorizations, authorization_id,
            recommendation_registry_path=self.recommendations,
            paper_evidence_registry_path=self.evidence, policy_path=case["policy"],
            configuration_path=case["configuration"], receipt_directory=case["receipts"],
            session_path=case["session"])

    def test_authorizes_a_recommended_proposal_as_configuration_version_one(self):
        _, recommendation = self._complete_and_recommend(self.case)
        record = self.authorize(self.case, recommendation)

        self.assertEqual(self.verify(self.case, record["authorization_id"]), record)
        self.assertTrue(record["authorization_id"].startswith("GOVERNANCE_AUTHORIZATION|"))
        self.assertTrue(record["record_id"].startswith("GOVERNANCE_AUTHORIZATION_RECORD|"))
        self.assertEqual(record["outcome"], "AUTHORIZED")
        self.assertIsNone(record["outcome_reason"])
        self.assertEqual(record["configuration_version"], 1)
        self.assertEqual(record["configuration"], p.forward_paper_configuration())
        self.assertEqual(record["status"], p.GOVERNANCE_AUTHORIZATION_STATUS)

    def test_rejects_insufficient_evidence_and_creates_no_version(self):
        narrow_limits = {"acceptable_terminal_results": ["BLOCKED"]}
        _, recommendation = self._complete_and_recommend(self.case, limits=narrow_limits)
        self.assertEqual(recommendation["outcome"], "INSUFFICIENT_EVIDENCE")
        record = self.authorize(self.case, recommendation)
        self.assertEqual(record["outcome"], "REJECTED")
        self.assertEqual(record["outcome_reason"], "RECOMMENDATION_INSUFFICIENT_EVIDENCE")
        self.assertIsNone(record["configuration_version"])

    def test_rejects_a_not_recommended_proposal_and_creates_no_version(self):
        broader = {"acceptable_terminal_results": ["COMPLETED", "RECOVERABLE_ERROR"]}
        _, recommendation = self._complete_and_recommend(
            self.case, terminal="RECOVERABLE_ERROR", limits=broader)
        self.assertEqual(recommendation["outcome"], "NOT_RECOMMENDED")
        record = self.authorize(self.case, recommendation)
        self.assertEqual(record["outcome"], "REJECTED")
        self.assertEqual(record["outcome_reason"], "RECOMMENDATION_NOT_RECOMMENDED")
        self.assertIsNone(record["configuration_version"])

    def test_a_second_authorized_recommendation_becomes_version_two(self):
        _, first_recommendation = self._complete_and_recommend(self.case)
        first = self.authorize(self.case, first_recommendation)
        self.assertEqual(first["configuration_version"], 1)

        second_case = self._make_case(
            self.root / "second-slot", "m25-t3-second", processing="2026-09-27T00:15:00Z")
        _, second_recommendation = self._complete_and_recommend(second_case)
        second = self.authorize(second_case, second_recommendation)
        self.assertEqual(second["configuration_version"], 2)
        self.assertEqual(second["configuration_id"], first["configuration_id"])

    def test_never_writes_to_session_configuration_ledger_or_receipts(self):
        _, recommendation = self._complete_and_recommend(self.case)
        watched = [self.case["session"], self.case["configuration"]]
        watched += sorted(self.case["ledger"].rglob("*")) if self.case["ledger"].exists() else []
        watched += sorted(self.case["receipts"].rglob("*")) if self.case["receipts"].exists() else []
        before = {path: path.read_bytes() for path in watched if path.is_file()}
        self.authorize(self.case, recommendation)
        after = {path: path.read_bytes() for path in watched if path.is_file()}
        self.assertEqual(before, after)

    def test_reloads_exactly_from_a_second_process(self):
        _, recommendation = self._complete_and_recommend(self.case)
        record = self.authorize(self.case, recommendation)
        case = self.case
        command = (
            "import json, pipeline as p; r=p.verified_governance_authorization("
            + repr(str(self.authorizations)) + ", " + repr(record["authorization_id"]) + ", "
            "recommendation_registry_path=" + repr(str(self.recommendations)) + ", "
            "paper_evidence_registry_path=" + repr(str(self.evidence)) + ", "
            "policy_path=" + repr(str(case["policy"])) + ", "
            "configuration_path=" + repr(str(case["configuration"])) + ", "
            "receipt_directory=" + repr(str(case["receipts"])) + ", "
            "session_path=" + repr(str(case["session"])) + "); "
            "print(json.dumps(r, sort_keys=True))")
        process = subprocess.run(
            [sys.executable, "-B", "-c", command], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout), record)

    def test_is_idempotent_and_ignores_a_later_decision_attempt(self):
        _, recommendation = self._complete_and_recommend(self.case)
        first = self.authorize(self.case, recommendation, decided_at=DECIDED_AT)
        second = self.authorize(
            self.case, recommendation, decided_at="2026-09-27T10:00:00Z",
            decision_code_revision="8" * 40)
        self.assertEqual(first, second)

    def test_rejects_a_wrong_recommendation_seal_before_persisting(self):
        _, recommendation = self._complete_and_recommend(self.case)
        with self.assertRaisesRegex(ValueError, "seal"):
            self.authorize(
                self.case, recommendation,
                recommendation_record_id=recommendation["record_id"] + "-x")
        self.assertFalse(self.authorizations.exists())

    def test_rejects_a_tampered_authorizations_registry(self):
        _, recommendation = self._complete_and_recommend(self.case)
        record = self.authorize(self.case, recommendation)
        altered = json.loads(self.authorizations.read_bytes())
        altered["authorizations"][0]["configuration_version"] = 99
        self.authorizations.write_bytes(p.encoded(altered))
        with self.assertRaisesRegex(ValueError, "invalid"):
            self.verify(self.case, record["authorization_id"])

    def test_query_filters_by_outcome_and_configuration_and_starts_empty(self):
        self.assertEqual(p.query_governance_authorization(self.authorizations), [])
        _, recommendation = self._complete_and_recommend(self.case)
        record = self.authorize(self.case, recommendation)
        matching = p.query_governance_authorization(self.authorizations, outcome="AUTHORIZED")
        self.assertEqual([item["authorization_id"] for item in matching],
                         [record["authorization_id"]])
        self.assertEqual(
            p.query_governance_authorization(self.authorizations, outcome="REJECTED"), [])
        self.assertEqual(len(p.query_governance_authorization(
            self.authorizations, configuration_id=record["configuration_id"])), 1)

    def test_load_rejects_a_malformed_identity(self):
        with self.assertRaisesRegex(ValueError, "valid"):
            p.load_governance_authorization(self.authorizations, "not-a-valid-id")

    def test_isolated_next_cycle_uses_exclusively_the_authorized_configuration(self):
        """M2.5-T3's hard deliverable: a later cycle, in isolation, uses
        exclusively the authorized configuration, while the prior
        (T9-like) session/configuration/ledger/receipts stay untouched."""
        _, recommendation = self._complete_and_recommend(self.case)
        authorization = self.authorize(self.case, recommendation)
        self.assertEqual(authorization["outcome"], "AUTHORIZED")

        prior_watched = [self.case["session"], self.case["configuration"]]
        prior_watched += sorted(self.case["ledger"].rglob("*"))
        prior_watched += sorted(self.case["receipts"].rglob("*"))
        prior_before = {path: path.read_bytes() for path in prior_watched if path.is_file()}

        isolated_root = self.root / "isolated-next-cycle"
        isolated_case = self._make_case(
            isolated_root, "m25-t3-isolated", processing="2026-10-01T00:15:00Z")
        isolated_configuration = p.load_forward_paper_configuration(
            isolated_case["session"], isolated_case["configuration"])
        self.assertEqual(isolated_configuration["configuration_id"],
                         authorization["configuration_id"])

        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=self._fake_m12(isolated_case, "COMPLETED")):
            attempt = p.attempt_forward_paper_activation(
                *self._t4_path_args(isolated_case), isolated_case["processing"], str(uuid.uuid4()))
        self.assertEqual(attempt["status"], "PASS")
        cycle_configuration = p.load_forward_paper_configuration(
            isolated_case["session"], isolated_case["configuration"])
        self.assertEqual(cycle_configuration["configuration_id"],
                         authorization["configuration_id"])

        prior_after = {path: path.read_bytes() for path in prior_watched if path.is_file()}
        self.assertEqual(prior_before, prior_after)
        self.assertNotEqual(isolated_case["session"].resolve(), self.case["session"].resolve())


if __name__ == "__main__":
    unittest.main()
