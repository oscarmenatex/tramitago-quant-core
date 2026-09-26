"""Focused proof for M2.4-T1: Strategy Evaluation disposition (HY-AC-006).

A disposition judges a Research Result against the Hypothesis's own,
predefined acceptance criterion -- never against a copy recomputed
elsewhere -- and classifies every result as ACCEPTED, REJECTED, or
INCONCLUSIVE (REQ-004-005), never leaving one unclassified. Issuing a
disposition never promotes anything to PAPER or LIVE.
"""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pipeline as p


ROOT = Path(__file__).resolve().parents[1]
HYPOTHESIS_REGISTRY = ROOT / "artifacts" / "research" / "hypotheses.json"
EXPERIMENT_REGISTRY = ROOT / "artifacts" / "research" / "experiments.json"
DATASET_DIRECTORY = (ROOT / "artifacts" / "research" / "datasets"
                     / "3345b100-db15-4aa2-976d-dbd797cbc4a3" / "v1-final")
EXPERIMENT_ID = "EXPERIMENT|23000efa-a18e-44cb-9a64-993cfb65200d"
EXPERIMENT_SEAL = ("EXPERIMENT_VERSION|EXPERIMENT|23000efa-a18e-44cb-9a64-993cfb65200d|1|"
                   "b89a7a41531bed017d0cf96be1644f761ba5868a78ee6898b0a250d47357257a")
EXECUTION_REVISION = "a" * 40
MATERIALIZATION_REVISION = "b" * 40
MATERIALIZED_AT = "2026-09-25T12:00:00Z"
DISPOSED_AT = "2026-09-26T09:00:00Z"
DISPOSITION_REVISION = "d" * 40


class M24T1DispositionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.legacy_results = self.directory / "experiment-results.json"
        self.executions = self.directory / "research-executions.json"
        self.results = self.directory / "research-results.json"
        self.dispositions = self.directory / "dispositions.json"

    def legacy(self):
        return p.execute_experiment_result(
            self.legacy_results, experiment_registry_path=EXPERIMENT_REGISTRY,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY,
            experiment_id=EXPERIMENT_ID, experiment_version=1,
            experiment_record_id=EXPERIMENT_SEAL, execution_code_revision=EXECUTION_REVISION)

    def result(self):
        legacy = self.legacy()
        execution = p.constitute_research_execution(
            self.executions, legacy_result_registry_path=self.legacy_results,
            legacy_result_id=legacy["result_id"], legacy_result_record_id=legacy["record_id"],
            experiment_registry_path=EXPERIMENT_REGISTRY,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY,
            materialized_at=MATERIALIZED_AT,
            materialization_code_revision=MATERIALIZATION_REVISION)
        return p.constitute_research_result(
            self.results, research_execution_registry_path=self.executions,
            research_execution_id=execution["execution_id"],
            research_execution_record_id=execution["record_id"],
            legacy_result_registry_path=self.legacy_results,
            experiment_registry_path=EXPERIMENT_REGISTRY,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY,
            materialized_at=MATERIALIZED_AT,
            materialization_code_revision=MATERIALIZATION_REVISION)

    def constitute_disposition(self, result_record, *, dispositions=None,
                               research_result_record_id=None, disposed_at=DISPOSED_AT,
                               disposition_code_revision=DISPOSITION_REVISION):
        return p.constitute_disposition(
            dispositions or self.dispositions,
            research_result_registry_path=self.results,
            research_result_id=result_record["research_result_id"],
            research_result_record_id=(
                result_record["record_id"] if research_result_record_id is None
                else research_result_record_id),
            research_execution_registry_path=self.executions,
            legacy_result_registry_path=self.legacy_results,
            experiment_registry_path=EXPERIMENT_REGISTRY,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY,
            disposed_at=disposed_at, disposition_code_revision=disposition_code_revision)

    def verify_disposition(self, disposition_id, *, dispositions=None):
        return p.verified_disposition(
            dispositions or self.dispositions, disposition_id,
            research_result_registry_path=self.results,
            research_execution_registry_path=self.executions,
            legacy_result_registry_path=self.legacy_results,
            experiment_registry_path=EXPERIMENT_REGISTRY,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY)

    def test_constitutes_a_disposition_traceable_to_hypothesis_criterion_and_evidence(self):
        result = self.result()
        hypothesis = p.load_hypothesis(HYPOTHESIS_REGISTRY, "HYPOTHESIS|3345b100-db15-4aa2-976d-dbd797cbc4a3", 1)
        disposition = self.constitute_disposition(result)

        self.assertEqual(self.verify_disposition(disposition["disposition_id"]), disposition)
        self.assertTrue(disposition["disposition_id"].startswith("DISPOSITION|"))
        self.assertTrue(disposition["record_id"].startswith("DISPOSITION_RECORD|"))
        self.assertEqual(disposition["references"]["research_result"],
                         {"research_result_id": result["research_result_id"],
                          "record_id": result["record_id"]})
        self.assertEqual(disposition["references"]["hypothesis"]["hypothesis_id"],
                         hypothesis["hypothesis_id"])
        self.assertEqual(disposition["criterion_applied"], hypothesis["acceptance_criterion"])
        self.assertEqual(disposition["scientific_result"], result["scientific_result"])
        self.assertIn(disposition["outcome"], p.DISPOSITION_OUTCOMES)
        self.assertEqual(disposition["status"], p.DISPOSITION_STATUS)

        expected_outcome = {"MET": "ACCEPTED", "NOT_MET": "REJECTED",
                           "INCONCLUSIVE": "INCONCLUSIVE"}[result["scientific_result"]["criterion_result"]]
        self.assertEqual(disposition["outcome"], expected_outcome)
        if expected_outcome == "INCONCLUSIVE":
            self.assertIsNotNone(disposition["outcome_reason"])
        else:
            self.assertIsNone(disposition["outcome_reason"])

    def test_reloads_exactly_from_a_second_process(self):
        result = self.result()
        disposition = self.constitute_disposition(result)
        command = (
            "import json, pipeline as p; r=p.verified_disposition("
            + repr(str(self.dispositions)) + ", "
            + repr(disposition["disposition_id"]) + ", "
            "research_result_registry_path=" + repr(str(self.results)) + ", "
            "research_execution_registry_path=" + repr(str(self.executions)) + ", "
            "legacy_result_registry_path=" + repr(str(self.legacy_results)) + ", "
            "experiment_registry_path=" + repr(str(EXPERIMENT_REGISTRY)) + ", "
            "hypothesis_registry_path=" + repr(str(HYPOTHESIS_REGISTRY)) + ", "
            "dataset_directory=" + repr(str(DATASET_DIRECTORY)) + "); "
            "print(json.dumps(r, sort_keys=True))")
        process = subprocess.run(
            [sys.executable, "-B", "-c", command], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout), disposition)

    def test_is_idempotent_and_ignores_a_later_materialization_on_repeat(self):
        result = self.result()
        first = self.constitute_disposition(result, disposed_at=DISPOSED_AT)
        second = self.constitute_disposition(
            result, disposed_at="2026-09-27T09:00:00Z",
            disposition_code_revision="e" * 40)
        self.assertEqual(first, second)

    def test_rejects_a_wrong_research_result_seal_before_persisting(self):
        result = self.result()
        with self.assertRaisesRegex(ValueError, "seal"):
            self.constitute_disposition(
                result, research_result_record_id=result["record_id"] + "-x")
        self.assertFalse(self.dispositions.exists())

    def test_rejects_a_tampered_dispositions_registry(self):
        result = self.result()
        disposition = self.constitute_disposition(result)
        altered = json.loads(self.dispositions.read_bytes())
        other = {"ACCEPTED", "REJECTED", "INCONCLUSIVE"} - {altered["dispositions"][0]["outcome"]}
        altered["dispositions"][0]["outcome"] = sorted(other)[0]
        self.dispositions.write_bytes(p.encoded(altered))
        with self.assertRaisesRegex(ValueError, "invalid"):
            self.verify_disposition(disposition["disposition_id"])

    def test_load_rejects_a_malformed_identity(self):
        with self.assertRaisesRegex(ValueError, "valid"):
            p.load_disposition(self.dispositions, "not-a-valid-id")

    def test_outcome_classifies_insufficient_evidence_as_inconclusive_never_unclassified(self):
        criterion = {"metric": "m", "comparison": "GT", "threshold": "0",
                    "expected_direction": "INCREASE"}
        insufficient = {"criterion": criterion, "criterion_result": "INCONCLUSIVE",
                        "inconclusive_reason": "GROUP_EMPTY_OR_NONFINITE", "metric": None}
        outcome, reason = p._disposition_outcome(criterion, insufficient)
        self.assertEqual(outcome, "INCONCLUSIVE")
        self.assertEqual(reason, "GROUP_EMPTY_OR_NONFINITE")

    def test_outcome_applies_the_hypothesis_comparator_both_directions(self):
        increase = {"metric": "m", "comparison": "GE", "threshold": "1",
                   "expected_direction": "INCREASE"}
        self.assertEqual(p._disposition_outcome(
            increase, {"criterion": increase, "criterion_result": "MET", "metric": 1.0}),
            ("ACCEPTED", None))
        self.assertEqual(p._disposition_outcome(
            increase, {"criterion": increase, "criterion_result": "NOT_MET", "metric": 0.5}),
            ("REJECTED", None))

        decrease = {"metric": "m", "comparison": "LT", "threshold": "0",
                   "expected_direction": "DECREASE"}
        self.assertEqual(p._disposition_outcome(
            decrease, {"criterion": decrease, "criterion_result": "MET", "metric": -0.1}),
            ("ACCEPTED", None))
        self.assertEqual(p._disposition_outcome(
            decrease, {"criterion": decrease, "criterion_result": "NOT_MET", "metric": 0.1}),
            ("REJECTED", None))


if __name__ == "__main__":
    unittest.main()
