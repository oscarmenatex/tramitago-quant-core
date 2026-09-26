"""Focused proof for M2.4-T2: Knowledge preservation and query (DOC-004 §12).

Preserves hypothesis, methodology, results, interpretation, and
limitations as a structured, query-able, permanent record -- a
rejected or inconclusive disposition is preserved exactly like an
accepted one (P-004-004) -- and never touches operational state.
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
HYPOTHESIS_ID = "HYPOTHESIS|3345b100-db15-4aa2-976d-dbd797cbc4a3"
EXPERIMENT_ID = "EXPERIMENT|23000efa-a18e-44cb-9a64-993cfb65200d"
EXPERIMENT_SEAL = ("EXPERIMENT_VERSION|EXPERIMENT|23000efa-a18e-44cb-9a64-993cfb65200d|1|"
                   "b89a7a41531bed017d0cf96be1644f761ba5868a78ee6898b0a250d47357257a")
EXECUTION_REVISION = "a" * 40
MATERIALIZATION_REVISION = "b" * 40
MATERIALIZED_AT = "2026-09-25T12:00:00Z"
DISPOSED_AT = "2026-09-26T09:00:00Z"
DISPOSITION_REVISION = "d" * 40
PRESERVED_AT = "2026-09-26T10:00:00Z"
KNOWLEDGE_REVISION = "f" * 40
LIMITATIONS = ["Single BTC-USD instrument", "One independent evaluation period, not walk-forward"]
INTERPRETATION = "Preserved for future reuse regardless of the disposition outcome."


class M24T2KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.legacy_results = self.directory / "experiment-results.json"
        self.executions = self.directory / "research-executions.json"
        self.results = self.directory / "research-results.json"
        self.dispositions = self.directory / "dispositions.json"
        self.knowledge = self.directory / "knowledge.json"

    def disposition(self):
        legacy = p.execute_experiment_result(
            self.legacy_results, experiment_registry_path=EXPERIMENT_REGISTRY,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY,
            experiment_id=EXPERIMENT_ID, experiment_version=1,
            experiment_record_id=EXPERIMENT_SEAL, execution_code_revision=EXECUTION_REVISION)
        execution = p.constitute_research_execution(
            self.executions, legacy_result_registry_path=self.legacy_results,
            legacy_result_id=legacy["result_id"], legacy_result_record_id=legacy["record_id"],
            experiment_registry_path=EXPERIMENT_REGISTRY,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY,
            materialized_at=MATERIALIZED_AT,
            materialization_code_revision=MATERIALIZATION_REVISION)
        result = p.constitute_research_result(
            self.results, research_execution_registry_path=self.executions,
            research_execution_id=execution["execution_id"],
            research_execution_record_id=execution["record_id"],
            legacy_result_registry_path=self.legacy_results,
            experiment_registry_path=EXPERIMENT_REGISTRY,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY,
            materialized_at=MATERIALIZED_AT,
            materialization_code_revision=MATERIALIZATION_REVISION)
        return p.constitute_disposition(
            self.dispositions, research_result_registry_path=self.results,
            research_result_id=result["research_result_id"],
            research_result_record_id=result["record_id"],
            research_execution_registry_path=self.executions,
            legacy_result_registry_path=self.legacy_results,
            experiment_registry_path=EXPERIMENT_REGISTRY,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY,
            disposed_at=DISPOSED_AT, disposition_code_revision=DISPOSITION_REVISION)

    def constitute_knowledge(self, disposition, *, knowledge=None,
                             disposition_record_id=None, interpretation=INTERPRETATION,
                             limitations=LIMITATIONS, preserved_at=PRESERVED_AT,
                             knowledge_code_revision=KNOWLEDGE_REVISION):
        return p.constitute_knowledge_record(
            knowledge or self.knowledge, disposition_registry_path=self.dispositions,
            disposition_id=disposition["disposition_id"],
            disposition_record_id=(
                disposition["record_id"] if disposition_record_id is None
                else disposition_record_id),
            research_result_registry_path=self.results,
            research_execution_registry_path=self.executions,
            legacy_result_registry_path=self.legacy_results,
            experiment_registry_path=EXPERIMENT_REGISTRY,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY,
            interpretation=interpretation, limitations=limitations, preserved_at=preserved_at,
            knowledge_code_revision=knowledge_code_revision)

    def verify_knowledge(self, knowledge_id, *, knowledge=None):
        return p.verified_knowledge(
            knowledge or self.knowledge, knowledge_id,
            disposition_registry_path=self.dispositions,
            research_result_registry_path=self.results,
            research_execution_registry_path=self.executions,
            legacy_result_registry_path=self.legacy_results,
            experiment_registry_path=EXPERIMENT_REGISTRY,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY)

    def test_preserves_hypothesis_methodology_results_interpretation_and_limitations(self):
        disposition = self.disposition()
        experiment = p.verified_experiment_conditions(
            EXPERIMENT_REGISTRY, EXPERIMENT_ID, 1,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY)
        record = self.constitute_knowledge(disposition)

        self.assertEqual(self.verify_knowledge(record["knowledge_id"]), record)
        self.assertTrue(record["knowledge_id"].startswith("KNOWLEDGE|"))
        self.assertTrue(record["record_id"].startswith("KNOWLEDGE_RECORD|"))
        self.assertEqual(record["hypothesis"]["hypothesis_id"], HYPOTHESIS_ID)
        self.assertEqual(record["hypothesis"]["acceptance_criterion"],
                         disposition["criterion_applied"])
        self.assertEqual(record["methodology"], experiment["conditions"])
        self.assertEqual(record["results"], {
            "scientific_result": disposition["scientific_result"],
            "outcome": disposition["outcome"], "outcome_reason": disposition["outcome_reason"]})
        self.assertEqual(record["interpretation"], INTERPRETATION)
        self.assertEqual(record["limitations"], LIMITATIONS)
        self.assertEqual(record["status"], p.KNOWLEDGE_STATUS)

    def test_preserves_a_rejected_disposition_exactly_like_an_accepted_one(self):
        """A REJECTED (or INCONCLUSIVE) outcome must validate and persist with no
        special-casing -- P-004-004 treats a negative result as a fully valid,
        query-able piece of knowledge, not a discard-worthy failure."""
        hypothesis_reference = {
            "hypothesis_id": HYPOTHESIS_ID, "version": 1,
            "record_id": "HYPOTHESIS_VERSION|synthetic|1|" + "0" * 64,
            "system_version": "1.0.0", "code_revision": "a" * 40,
        }
        references = {
            "disposition": {"disposition_id": "DISPOSITION|" + "1" * 64,
                            "record_id": "DISPOSITION_RECORD|synthetic|" + "2" * 64},
            "research_result": {"research_result_id": "RESEARCH_RESULT|" + "3" * 64,
                                "record_id": "RESEARCH_RESULT_RECORD|synthetic|" + "4" * 64},
            "hypothesis": hypothesis_reference,
            "experiment": {"experiment_id": EXPERIMENT_ID, "version": 1,
                          "record_id": "EXPERIMENT_VERSION|synthetic|1|" + "5" * 64},
        }
        criterion = {"metric": "m", "comparison": "GT", "threshold": "0",
                    "expected_direction": "INCREASE"}
        hypothesis_snapshot = {
            "hypothesis_id": HYPOTHESIS_ID, "version": 1,
            "record_id": hypothesis_reference["record_id"], "description": "synthetic",
            "target_metric": "m", "expected_direction": "INCREASE",
            "acceptance_criterion": criterion,
        }
        results = {
            "scientific_result": {"criterion": criterion, "criterion_result": "NOT_MET",
                                  "metric": -0.1},
            "outcome": "REJECTED", "outcome_reason": None,
        }
        materialization = p._knowledge_materialization(PRESERVED_AT, KNOWLEDGE_REVISION)
        knowledge_id = p._knowledge_id(references)
        record = p._knowledge_record(
            knowledge_id, references, hypothesis_snapshot, {"synthetic": "methodology"},
            results, "Rejected, preserved for future reuse.", ["synthetic limitation"],
            materialization)

        self.assertTrue(p._knowledge_record_is_valid(record))
        self.assertEqual(p._persist_knowledge_record(self.knowledge, record), record)
        queried = p.query_knowledge(self.knowledge, outcome="REJECTED")
        self.assertEqual([item["knowledge_id"] for item in queried], [knowledge_id])

    def test_query_filters_by_hypothesis_and_outcome_without_mutating_anything(self):
        disposition = self.disposition()
        record = self.constitute_knowledge(disposition)
        before = self.knowledge.read_bytes()

        by_hypothesis = p.query_knowledge(self.knowledge, hypothesis_id=HYPOTHESIS_ID)
        self.assertEqual([item["knowledge_id"] for item in by_hypothesis], [record["knowledge_id"]])
        by_outcome = p.query_knowledge(self.knowledge, outcome=disposition["outcome"])
        self.assertEqual([item["knowledge_id"] for item in by_outcome], [record["knowledge_id"]])
        self.assertEqual(p.query_knowledge(self.knowledge, hypothesis_id="HYPOTHESIS|" + "0" * 32), [])
        self.assertEqual(self.knowledge.read_bytes(), before)

    def test_query_returns_empty_list_when_nothing_has_been_preserved_yet(self):
        self.assertEqual(p.query_knowledge(self.knowledge), [])
        self.assertFalse(self.knowledge.exists())

    def test_reloads_exactly_from_a_second_process(self):
        disposition = self.disposition()
        record = self.constitute_knowledge(disposition)
        command = (
            "import json, pipeline as p; r=p.verified_knowledge("
            + repr(str(self.knowledge)) + ", " + repr(record["knowledge_id"]) + ", "
            "disposition_registry_path=" + repr(str(self.dispositions)) + ", "
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
        self.assertEqual(json.loads(process.stdout), record)

    def test_is_idempotent_and_ignores_a_later_preservation_attempt(self):
        disposition = self.disposition()
        first = self.constitute_knowledge(disposition, preserved_at=PRESERVED_AT)
        second = self.constitute_knowledge(
            disposition, preserved_at="2026-09-27T10:00:00Z",
            knowledge_code_revision="1" * 40)
        self.assertEqual(first, second)

    def test_rejects_a_wrong_disposition_seal_before_persisting(self):
        disposition = self.disposition()
        with self.assertRaisesRegex(ValueError, "seal"):
            self.constitute_knowledge(
                disposition, disposition_record_id=disposition["record_id"] + "-x")
        self.assertFalse(self.knowledge.exists())

    def test_rejects_missing_or_duplicate_limitations_and_missing_interpretation(self):
        disposition = self.disposition()
        with self.assertRaisesRegex(ValueError, "limitations"):
            self.constitute_knowledge(disposition, limitations=[])
        with self.assertRaisesRegex(ValueError, "limitations"):
            self.constitute_knowledge(disposition, limitations=["same", "same"])
        with self.assertRaisesRegex(ValueError, "interpretation"):
            self.constitute_knowledge(disposition, interpretation="")
        self.assertFalse(self.knowledge.exists())

    def test_rejects_a_tampered_knowledge_registry(self):
        disposition = self.disposition()
        record = self.constitute_knowledge(disposition)
        altered = json.loads(self.knowledge.read_bytes())
        altered["records"][0]["limitations"] = ["tampered"]
        self.knowledge.write_bytes(p.encoded(altered))
        with self.assertRaisesRegex(ValueError, "invalid"):
            self.verify_knowledge(record["knowledge_id"])

    def test_load_rejects_a_malformed_identity(self):
        with self.assertRaisesRegex(ValueError, "valid"):
            p.load_knowledge(self.knowledge, "not-a-valid-id")


if __name__ == "__main__":
    unittest.main()
