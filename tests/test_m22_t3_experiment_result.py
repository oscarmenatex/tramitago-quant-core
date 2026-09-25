"""Focused proof for M2.2-T3 sealed experiment-result execution."""

import copy
import json
import math
from pathlib import Path
import shutil
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
DATASET_ID = ("HISTORICAL_HYPOTHESIS_DATASET|"
              "4d2f8ad3473b82e3e380fa09a765dd0b6e8d38ea9a2d786e59391a286b11ccbf")
EXPERIMENT_ID = "EXPERIMENT|23000efa-a18e-44cb-9a64-993cfb65200d"
EXPERIMENT_SEAL = ("EXPERIMENT_VERSION|EXPERIMENT|23000efa-a18e-44cb-9a64-993cfb65200d|1|"
                   "b89a7a41531bed017d0cf96be1644f761ba5868a78ee6898b0a250d47357257a")
EXECUTION_REVISION = "a" * 40


class M22T3ExperimentResultTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.results = self.directory / "experiment-results.json"

    def execute(self, *, experiment_registry=EXPERIMENT_REGISTRY,
                dataset_directory=DATASET_DIRECTORY, result_path=None):
        return p.execute_experiment_result(
            result_path or self.results, experiment_registry_path=experiment_registry,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=dataset_directory,
            experiment_id=EXPERIMENT_ID, experiment_version=1,
            experiment_record_id=EXPERIMENT_SEAL,
            execution_code_revision=EXECUTION_REVISION)

    def verify(self, result_id):
        return p.verified_experiment_result(
            self.results, result_id, experiment_registry_path=EXPERIMENT_REGISTRY,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY)

    def test_executes_the_sealed_definition_and_recalculates_the_aggregate(self):
        record = self.execute()
        reloaded = self.verify(record["result_id"])
        evaluation = record["evaluation"]
        evidence = record["evidence"]

        self.assertEqual(reloaded, record)
        self.assertTrue(record["result_id"].startswith("EXPERIMENT_RESULT|"))
        self.assertTrue(record["record_id"].startswith("EXPERIMENT_RESULT_RECORD|"))
        self.assertEqual(record["references"]["experiment"], {
            "experiment_id": EXPERIMENT_ID, "version": 1, "record_id": EXPERIMENT_SEAL})
        self.assertEqual(record["references"]["hypothesis"]["hypothesis_id"], HYPOTHESIS_ID)
        self.assertEqual(record["references"]["dataset"]["dataset_id"], DATASET_ID)
        self.assertEqual(evaluation["period"], {
            "start_utc": "2025-01-01T00:00:00Z",
            "end_exclusive_utc": "2026-01-01T00:00:00Z"})
        self.assertEqual(evaluation["observation_count"], 365)
        self.assertEqual(evaluation["groups"]["upper"]["count"]
                         + evaluation["groups"]["lower_or_equal"]["count"], 365)
        self.assertIn(evaluation["criterion_result"], {"MET", "NOT_MET"})
        self.assertEqual(evaluation["criterion"], {
            "metric": "mean_forward_return_1d(close_t > sma_close_3) - mean_forward_return_1d(close_t <= sma_close_3)",
            "comparison": "GT", "threshold": "0", "expected_direction": "INCREASE"})
        upper = [item["return_t_plus_1"] for item in evidence if item["group"] == "UPPER"]
        lower = [item["return_t_plus_1"] for item in evidence
                 if item["group"] == "LOWER_OR_EQUAL"]
        self.assertEqual(evaluation["groups"]["upper"]["mean_return_t_plus_1"],
                         math.fsum(upper) / len(upper))
        self.assertEqual(evaluation["groups"]["lower_or_equal"]["mean_return_t_plus_1"],
                         math.fsum(lower) / len(lower))
        self.assertEqual(evaluation["metric"],
                         math.fsum(upper) / len(upper) - math.fsum(lower) / len(lower))

    def test_reloads_exactly_from_a_second_process_and_is_idempotent(self):
        record = self.execute()
        original = self.results.read_bytes()
        repeated = self.execute()
        self.assertEqual(repeated, record)
        self.assertEqual(self.results.read_bytes(), original)
        command = (
            "import json, pipeline as p; r=p.verified_experiment_result("
            + repr(str(self.results)) + ", " + repr(record["result_id"]) + ", "
            "experiment_registry_path=" + repr(str(EXPERIMENT_REGISTRY)) + ", "
            "hypothesis_registry_path=" + repr(str(HYPOTHESIS_REGISTRY)) + ", "
            "dataset_directory=" + repr(str(DATASET_DIRECTORY)) + "); "
            "print(json.dumps(r, sort_keys=True))")
        process = subprocess.run(
            [sys.executable, "-B", "-c", command], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout), record)

    def test_evidence_excludes_support_and_uses_only_the_authorized_next_close(self):
        record = self.execute()
        evidence = record["evidence"]
        support = {"2024-12-30T00:00:00Z", "2024-12-31T00:00:00Z", "2026-01-01T00:00:00Z"}
        self.assertFalse(support & {item["timestamp"] for item in evidence})
        self.assertTrue(all(item["row_role"] == "EVALUATION" for item in evidence))
        self.assertTrue(all(
            item["next_timestamp"] == p.iso(p.epoch(item["timestamp"]) + 86400)
            for item in evidence))
        self.assertEqual(evidence[0]["timestamp"], "2025-01-01T00:00:00Z")
        self.assertEqual(evidence[-1]["timestamp"], "2025-12-31T00:00:00Z")
        self.assertEqual(evidence[-1]["next_timestamp"], "2026-01-01T00:00:00Z")

    def test_rejects_tampered_experiment_dataset_or_result_without_partial_output(self):
        tampered_experiments = self.directory / "experiments.json"
        altered = json.loads(EXPERIMENT_REGISTRY.read_bytes())
        altered["experiments"][0]["conditions"]["sma"]["window"] = 4
        tampered_experiments.write_bytes(p.encoded(altered))
        experiment_output = self.directory / "experiment-output.json"
        with self.assertRaisesRegex(ValueError, "invalid"):
            self.execute(experiment_registry=tampered_experiments, result_path=experiment_output)
        self.assertFalse(experiment_output.exists())

        tampered_dataset = self.directory / "dataset"
        shutil.copytree(DATASET_DIRECTORY, tampered_dataset)
        csv_path = tampered_dataset / "dataset.csv"
        csv_path.write_bytes(csv_path.read_bytes() + b"\n")
        dataset_output = self.directory / "dataset-output.json"
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.execute(dataset_directory=tampered_dataset, result_path=dataset_output)
        self.assertFalse(dataset_output.exists())

        record = self.execute()
        altered_result = json.loads(self.results.read_bytes())
        altered_result["results"][0]["evidence"][0]["return_t_plus_1"] = 0.0
        self.results.write_bytes(p.encoded(altered_result))
        with self.assertRaisesRegex(ValueError, "invalid"):
            self.verify(record["result_id"])

    def test_rejects_a_wrong_seal_before_persisting_any_result(self):
        with self.assertRaisesRegex(ValueError, "seal"):
            p.execute_experiment_result(
                self.results, experiment_registry_path=EXPERIMENT_REGISTRY,
                hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY,
                experiment_id=EXPERIMENT_ID, experiment_version=1,
                experiment_record_id=EXPERIMENT_SEAL + "-wrong",
                execution_code_revision=EXECUTION_REVISION)
        self.assertFalse(self.results.exists())


if __name__ == "__main__":
    unittest.main()
