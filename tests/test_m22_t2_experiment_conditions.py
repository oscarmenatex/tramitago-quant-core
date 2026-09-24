"""Focused proof for M2.2-T2 immutable experiment conditions."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pipeline as p


ROOT = Path(__file__).resolve().parents[1]
HYPOTHESIS_REGISTRY = ROOT / "artifacts" / "research" / "hypotheses.json"
DATASET_DIRECTORY = (ROOT / "artifacts" / "research" / "datasets"
                     / "3345b100-db15-4aa2-976d-dbd797cbc4a3" / "v1-final")
HYPOTHESIS_ID = "HYPOTHESIS|3345b100-db15-4aa2-976d-dbd797cbc4a3"
DATASET_ID = ("HISTORICAL_HYPOTHESIS_DATASET|"
              "4d2f8ad3473b82e3e380fa09a765dd0b6e8d38ea9a2d786e59391a286b11ccbf")


class M22T2ExperimentConditionsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.registry = Path(self.temporary.name) / "experiments.json"

    def constitute(self):
        return p.constitute_experiment_conditions(
            self.registry, hypothesis_registry_path=HYPOTHESIS_REGISTRY,
            dataset_directory=DATASET_DIRECTORY, hypothesis_id=HYPOTHESIS_ID,
            hypothesis_version=1, dataset_id=DATASET_ID,
            created_at="2026-09-24T12:00:00Z", revision_reason="INITIAL_CONDITIONS_FIXED")

    def test_constitutes_reloads_and_fixes_all_conditions_without_execution(self):
        record = self.constitute()
        reloaded = p.verified_experiment_conditions(
            self.registry, record["experiment_id"], 1,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY)
        conditions = reloaded["conditions"]

        self.assertEqual(reloaded, record)
        self.assertTrue(record["experiment_id"].startswith("EXPERIMENT|"))
        self.assertTrue(record["record_id"].startswith("EXPERIMENT_VERSION|"))
        self.assertEqual(record["version"], 1)
        self.assertEqual(record["references"]["hypothesis"], {
            "hypothesis_id": HYPOTHESIS_ID, "version": 1,
            "record_id": "HYPOTHESIS_VERSION|HYPOTHESIS|3345b100-db15-4aa2-976d-dbd797cbc4a3|1|18a92fa7153e8fb0477a3ba0ca9b7d0fcf5deb0f8ea3ca484c2296065ad94f57",
            "system_version": "0.1.0",
            "code_revision": "00f40857faf517aca9939f6c5f66395eec09f8ee",
        })
        self.assertEqual(record["references"]["dataset"]["dataset_id"], DATASET_ID)
        self.assertEqual(conditions["instrument"], "BTC-USD")
        self.assertEqual(conditions["frequency_seconds"], 86400)
        self.assertEqual(conditions["sma"], {"source": "close", "window": 3})
        self.assertEqual(conditions["outcome"], {
            "name": "return_t+1", "formula": "(close_t+1 / close_t) - 1"})
        self.assertEqual(conditions["acceptance_criterion"], {
            "metric": "mean_forward_return_1d(close_t > sma_close_3) - mean_forward_return_1d(close_t <= sma_close_3)",
            "comparison": "GT", "threshold": "0", "expected_direction": "INCREASE"})
        self.assertEqual(conditions["temporal_split"]["independent_evaluation_period"], {
            "start_utc": "2025-01-01T00:00:00Z",
            "end_exclusive_utc": "2026-01-01T00:00:00Z"})
        self.assertEqual(conditions["population"]["support_rows"], [
            {"timestamp": "2024-12-30T00:00:00Z", "row_role": "SUPPORT_SMA3_WARMUP"},
            {"timestamp": "2024-12-31T00:00:00Z", "row_role": "SUPPORT_SMA3_WARMUP"},
            {"timestamp": "2026-01-01T00:00:00Z", "row_role": "SUPPORT_FORWARD_RETURN"},
        ])
        self.assertEqual(conditions["costs"], {
            "declaration": p.EXPERIMENT_COSTS_NOT_APPLICABLE})
        self.assertEqual(conditions["execution_scope"], {
            "metric_calculated": False, "experiment_executed": False,
            "backtest_executed": False})

    def test_reloads_exactly_from_a_second_process(self):
        record = self.constitute()
        command = (
            "import json, pipeline as p; r=p.load_experiment_conditions("
            + repr(str(self.registry)) + ", " + repr(record["experiment_id"]) + ", 1); "
            "print(json.dumps(r, sort_keys=True))")
        process = subprocess.run(
            [sys.executable, "-B", "-c", command], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout), record)

    def test_rejects_missing_or_incompatible_references_and_retroactive_tampering(self):
        with self.assertRaisesRegex(ValueError, "Dataset identity"):
            p.constitute_experiment_conditions(
                self.registry, hypothesis_registry_path=HYPOTHESIS_REGISTRY,
                dataset_directory=DATASET_DIRECTORY, hypothesis_id=HYPOTHESIS_ID,
                hypothesis_version=1, dataset_id=DATASET_ID + "-wrong",
                created_at="2026-09-24T12:00:00Z", revision_reason="INVALID_DATASET")
        with self.assertRaisesRegex(ValueError, "not registered"):
            p.constitute_experiment_conditions(
                self.registry, hypothesis_registry_path=HYPOTHESIS_REGISTRY,
                dataset_directory=DATASET_DIRECTORY,
                hypothesis_id="HYPOTHESIS|00000000-0000-0000-0000-000000000000",
                hypothesis_version=1, dataset_id=DATASET_ID,
                created_at="2026-09-24T12:00:00Z", revision_reason="MISSING_HYPOTHESIS")
        record = self.constitute()
        tampered = json.loads(self.registry.read_bytes())
        tampered["experiments"][0]["conditions"]["sma"]["window"] = 4
        self.registry.write_bytes(p.encoded(tampered))
        with self.assertRaisesRegex(ValueError, "invalid"):
            p.load_experiment_conditions(self.registry, record["experiment_id"], 1)

    def test_rejects_second_same_identity_version_and_preserves_v1_bytes_for_v2(self):
        first = self.constitute()
        first_bytes = p.encoded(copy.deepcopy(first))
        with self.assertRaisesRegex(ValueError, "already exist"):
            p._persist_experiment_conditions(self.registry, first)
        conflicting = p._experiment_record(
            first["experiment_id"], 1, first["references"], first["conditions"],
            first["created_at"], first["status"], "CONFLICTING_SECOND_DEFINITION")
        with self.assertRaisesRegex(ValueError, "already exist"):
            p._persist_experiment_conditions(self.registry, conflicting)
        second = p.revise_experiment_conditions(
            self.registry, first["experiment_id"], hypothesis_registry_path=HYPOTHESIS_REGISTRY,
            dataset_directory=DATASET_DIRECTORY, hypothesis_id=HYPOTHESIS_ID,
            hypothesis_version=1, dataset_id=DATASET_ID,
            created_at="2026-09-24T12:00:01Z", revision_reason="TEST_VERSION_TWO")
        self.assertEqual(second["version"], 2)
        self.assertEqual(p.encoded(p.load_experiment_conditions(
            self.registry, first["experiment_id"], 1)), first_bytes)


if __name__ == "__main__":
    unittest.main()
