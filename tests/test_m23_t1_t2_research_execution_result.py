"""Focused proof for M2.3-T1/T2: Research Execution and Research Result,

constituted post-hoc from the already-sealed M2.2-T3 legacy experiment result.
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


class M23T1T2ResearchExecutionResultTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.legacy_results = self.directory / "experiment-results.json"
        self.executions = self.directory / "research-executions.json"
        self.results = self.directory / "research-results.json"

    def legacy(self):
        return p.execute_experiment_result(
            self.legacy_results, experiment_registry_path=EXPERIMENT_REGISTRY,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY,
            experiment_id=EXPERIMENT_ID, experiment_version=1,
            experiment_record_id=EXPERIMENT_SEAL, execution_code_revision=EXECUTION_REVISION)

    def constitute_execution(self, legacy_record, *, legacy_results=None, executions=None,
                             materialized_at=MATERIALIZED_AT,
                             materialization_code_revision=MATERIALIZATION_REVISION,
                             legacy_result_record_id=None):
        return p.constitute_research_execution(
            executions or self.executions,
            legacy_result_registry_path=legacy_results or self.legacy_results,
            legacy_result_id=legacy_record["result_id"],
            legacy_result_record_id=(legacy_record["record_id"] if legacy_result_record_id is None
                                     else legacy_result_record_id),
            experiment_registry_path=EXPERIMENT_REGISTRY,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY,
            materialized_at=materialized_at,
            materialization_code_revision=materialization_code_revision)

    def verify_execution(self, execution_id, *, executions=None, legacy_results=None):
        return p.verified_research_execution(
            executions or self.executions, execution_id,
            legacy_result_registry_path=legacy_results or self.legacy_results,
            experiment_registry_path=EXPERIMENT_REGISTRY,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY)

    def constitute_result(self, execution_record, *, results=None, executions=None,
                          legacy_results=None, materialized_at=MATERIALIZED_AT,
                          materialization_code_revision=MATERIALIZATION_REVISION,
                          research_execution_record_id=None):
        return p.constitute_research_result(
            results or self.results,
            research_execution_registry_path=executions or self.executions,
            research_execution_id=execution_record["execution_id"],
            research_execution_record_id=(
                execution_record["record_id"] if research_execution_record_id is None
                else research_execution_record_id),
            legacy_result_registry_path=legacy_results or self.legacy_results,
            experiment_registry_path=EXPERIMENT_REGISTRY,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY,
            materialized_at=materialized_at,
            materialization_code_revision=materialization_code_revision)

    def verify_result(self, research_result_id, *, results=None, executions=None,
                      legacy_results=None):
        return p.verified_research_result(
            results or self.results,
            research_execution_registry_path=executions or self.executions,
            legacy_result_registry_path=legacy_results or self.legacy_results,
            experiment_registry_path=EXPERIMENT_REGISTRY,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY,
            research_result_id=research_result_id)

    def test_constitutes_execution_then_result_and_both_reload_exactly(self):
        legacy = self.legacy()
        execution = self.constitute_execution(legacy)
        self.assertEqual(self.verify_execution(execution["execution_id"]), execution)
        self.assertTrue(execution["execution_id"].startswith("RESEARCH_EXECUTION|"))
        self.assertTrue(execution["record_id"].startswith("RESEARCH_EXECUTION_RECORD|"))
        self.assertEqual(execution["references"]["legacy_result"],
                         {"result_id": legacy["result_id"], "record_id": legacy["record_id"]})
        self.assertEqual(execution["scientific_result"], legacy["evaluation"])
        self.assertEqual(execution["causal_evidence"], legacy["evidence"])
        self.assertEqual(execution["terminal_result"], legacy["evaluation"]["criterion_result"])
        self.assertEqual(execution["status"], p.RESEARCH_EXECUTION_STATUS)
        self.assertEqual(execution["materialization"]["provenance"],
                         p.RESEARCH_MATERIALIZATION_PROVENANCE)

        result = self.constitute_result(execution)
        self.assertEqual(self.verify_result(result["research_result_id"]), result)
        self.assertTrue(result["research_result_id"].startswith("RESEARCH_RESULT|"))
        self.assertTrue(result["record_id"].startswith("RESEARCH_RESULT_RECORD|"))
        self.assertEqual(result["references"]["research_execution"],
                         {"execution_id": execution["execution_id"],
                          "record_id": execution["record_id"]})
        self.assertEqual(result["references"]["legacy_result"],
                         {"result_id": legacy["result_id"], "record_id": legacy["record_id"]})
        self.assertEqual(result["scientific_result"], legacy["evaluation"])
        self.assertEqual(result["costs"], {
            "source_experiment": execution["references"]["experiment"],
            "declaration": p.EXPERIMENT_COSTS_NOT_APPLICABLE})
        self.assertEqual(result["status"], p.RESEARCH_RESULT_STATUS)

    def test_reloads_exactly_from_a_second_process(self):
        legacy = self.legacy()
        execution = self.constitute_execution(legacy)
        result = self.constitute_result(execution)
        command = (
            "import json, pipeline as p; r=p.verified_research_result("
            + repr(str(self.results)) + ", "
            "research_execution_registry_path=" + repr(str(self.executions)) + ", "
            "legacy_result_registry_path=" + repr(str(self.legacy_results)) + ", "
            "experiment_registry_path=" + repr(str(EXPERIMENT_REGISTRY)) + ", "
            "hypothesis_registry_path=" + repr(str(HYPOTHESIS_REGISTRY)) + ", "
            "dataset_directory=" + repr(str(DATASET_DIRECTORY)) + ", "
            "research_result_id=" + repr(result["research_result_id"]) + "); "
            "print(json.dumps(r, sort_keys=True))")
        process = subprocess.run(
            [sys.executable, "-B", "-c", command], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout), result)

    def test_is_idempotent_and_ignores_a_later_materialization_on_repeat(self):
        legacy = self.legacy()
        first_execution = self.constitute_execution(legacy, materialized_at=MATERIALIZED_AT)
        second_execution = self.constitute_execution(
            legacy, materialized_at="2026-09-26T00:00:00Z",
            materialization_code_revision="c" * 40)
        self.assertEqual(first_execution, second_execution)

        first_result = self.constitute_result(first_execution, materialized_at=MATERIALIZED_AT)
        second_result = self.constitute_result(
            first_execution, materialized_at="2026-09-26T00:00:00Z",
            materialization_code_revision="c" * 40)
        self.assertEqual(first_result, second_result)

    def test_rejects_a_wrong_legacy_seal_before_persisting_execution(self):
        legacy = self.legacy()
        with self.assertRaisesRegex(ValueError, "seal"):
            self.constitute_execution(legacy, legacy_result_record_id=legacy["record_id"] + "-x")
        self.assertFalse(self.executions.exists())

    def test_rejects_a_wrong_execution_seal_before_persisting_result(self):
        legacy = self.legacy()
        execution = self.constitute_execution(legacy)
        with self.assertRaisesRegex(ValueError, "seal"):
            self.constitute_result(
                execution, research_execution_record_id=execution["record_id"] + "-x")
        self.assertFalse(self.results.exists())

    def test_rejects_tampered_legacy_result_when_reverifying_execution(self):
        legacy = self.legacy()
        execution = self.constitute_execution(legacy)
        altered = json.loads(self.legacy_results.read_bytes())
        altered["results"][0]["evidence"][0]["return_t_plus_1"] = 0.0
        self.legacy_results.write_bytes(p.encoded(altered))
        with self.assertRaisesRegex(ValueError, "invalid"):
            self.verify_execution(execution["execution_id"])

    def test_rejects_a_tampered_research_execution_registry(self):
        legacy = self.legacy()
        execution = self.constitute_execution(legacy)
        altered = json.loads(self.executions.read_bytes())
        altered["executions"][0]["terminal_result"] = "INCONCLUSIVE"
        self.executions.write_bytes(p.encoded(altered))
        with self.assertRaisesRegex(ValueError, "invalid"):
            self.verify_execution(execution["execution_id"])

    def test_rejects_a_tampered_research_result_registry(self):
        legacy = self.legacy()
        execution = self.constitute_execution(legacy)
        result = self.constitute_result(execution)
        altered = json.loads(self.results.read_bytes())
        altered["results"][0]["costs"]["declaration"] = "tampered"
        self.results.write_bytes(p.encoded(altered))
        with self.assertRaisesRegex(ValueError, "invalid"):
            self.verify_result(result["research_result_id"])

    def test_load_rejects_a_malformed_identity(self):
        with self.assertRaisesRegex(ValueError, "valid"):
            p.load_research_execution(self.executions, "not-a-valid-id")
        with self.assertRaisesRegex(ValueError, "valid"):
            p.load_research_result(self.results, "not-a-valid-id")


if __name__ == "__main__":
    unittest.main()
