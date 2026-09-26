"""Focused proof for M2.6-T1/T2/T3: walk-forward validation over an
already-sealed Hypothesis Dataset and Experiment.

Purely additive to M2.2/M2.3/M2.4: never mutates the dataset, never
redefines the Hypothesis's acceptance criterion (HY-AC-002), and never
rewrites the single-sample Disposition already issued for it (M2.4-T1).
"""

import json
from pathlib import Path
import tempfile
import unittest

import pipeline as p


ROOT = Path(__file__).resolve().parents[1]
HYPOTHESIS_REGISTRY = ROOT / "artifacts" / "research" / "hypotheses.json"
HYPOTHESIS_ID = "HYPOTHESIS|3345b100-db15-4aa2-976d-dbd797cbc4a3"
EXPERIMENT_REGISTRY = ROOT / "artifacts" / "research" / "experiments.json"
DATASET_DIRECTORY = (ROOT / "artifacts" / "research" / "datasets"
                     / "3345b100-db15-4aa2-976d-dbd797cbc4a3" / "v1-final")
EXPERIMENT_ID = "EXPERIMENT|23000efa-a18e-44cb-9a64-993cfb65200d"
EXPERIMENT_SEAL = ("EXPERIMENT_VERSION|EXPERIMENT|23000efa-a18e-44cb-9a64-993cfb65200d|1|"
                   "b89a7a41531bed017d0cf96be1644f761ba5868a78ee6898b0a250d47357257a")
FOLD_COUNT = 5  # 365 evaluable days / 5 == 73, an exact divisor
PARTITIONED_AT = "2026-09-27T09:00:00Z"
PARTITION_REVISION = "a" * 40
COMPUTED_AT = "2026-09-27T09:10:00Z"
COMPUTATION_REVISION = "b" * 40
VALIDATED_AT = "2026-09-27T09:20:00Z"
VALIDATION_REVISION = "c" * 40


class M26WalkForwardValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.partitions = self.directory / "walk-forward-partitions.json"
        self.fold_results = self.directory / "walk-forward-fold-results.json"
        self.validations = self.directory / "statistical-validations.json"

    def partition(self, *, fold_count=FOLD_COUNT):
        return p.constitute_walk_forward_partition(
            self.partitions, dataset_directory=DATASET_DIRECTORY,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, fold_count=fold_count,
            partitioned_at=PARTITIONED_AT, partition_code_revision=PARTITION_REVISION)

    def fold_result(self, partition, fold_index):
        return p.constitute_walk_forward_fold_result(
            self.fold_results, partition_registry_path=self.partitions,
            partition_id=partition["partition_id"], partition_record_id=partition["record_id"],
            fold_index=fold_index, experiment_registry_path=EXPERIMENT_REGISTRY,
            experiment_id=EXPERIMENT_ID, experiment_version=1,
            experiment_record_id=EXPERIMENT_SEAL, hypothesis_registry_path=HYPOTHESIS_REGISTRY,
            dataset_directory=DATASET_DIRECTORY, computed_at=COMPUTED_AT,
            computation_code_revision=COMPUTATION_REVISION)

    def all_fold_results(self, partition):
        return [self.fold_result(partition, index) for index in range(partition["fold_count"])]

    def validation(self, partition, *, minimum_folds_required=FOLD_COUNT,
                   consistency_threshold="0.6", validated_at=VALIDATED_AT,
                   validation_code_revision=VALIDATION_REVISION):
        return p.constitute_statistical_validation(
            self.validations, partition_registry_path=self.partitions,
            partition_id=partition["partition_id"], partition_record_id=partition["record_id"],
            fold_result_registry_path=self.fold_results,
            experiment_registry_path=EXPERIMENT_REGISTRY,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY,
            minimum_folds_required=minimum_folds_required,
            consistency_threshold=consistency_threshold, validated_at=validated_at,
            validation_code_revision=validation_code_revision)

    # -- M2.6-T1 -----------------------------------------------------------

    def test_partitions_into_contiguous_gapless_folds_covering_the_full_period(self):
        record = self.partition()
        self.assertEqual(self.verify_partition(record), record)
        self.assertEqual(record["fold_count"], FOLD_COUNT)
        self.assertEqual(len(record["folds"]), FOLD_COUNT)
        self.assertEqual(record["folds"][0]["start_utc"], "2025-01-01T00:00:00Z")
        self.assertEqual(record["folds"][-1]["end_exclusive_utc"], "2026-01-01T00:00:00Z")
        for previous, following in zip(record["folds"], record["folds"][1:]):
            self.assertEqual(previous["end_exclusive_utc"], following["start_utc"])

    def verify_partition(self, record):
        return p.verified_walk_forward_partition(
            self.partitions, record["partition_id"], dataset_directory=DATASET_DIRECTORY,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY)

    def test_rejects_a_fold_count_that_does_not_evenly_divide_the_period(self):
        with self.assertRaisesRegex(ValueError, "evenly divide"):
            self.partition(fold_count=4)
        self.assertFalse(self.partitions.exists())

    def test_partition_is_idempotent(self):
        first = self.partition()
        second = self.partition()
        self.assertEqual(first, second)

    def test_never_writes_to_the_dataset(self):
        before = {path: path.read_bytes() for path in DATASET_DIRECTORY.rglob("*") if path.is_file()}
        self.partition()
        after = {path: path.read_bytes() for path in DATASET_DIRECTORY.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    # -- M2.6-T2 -----------------------------------------------------------

    def test_computes_every_fold_against_the_declared_baseline_and_reloads_exactly(self):
        partition = self.partition()
        results = self.all_fold_results(partition)

        self.assertEqual(len(results), FOLD_COUNT)
        total_observations = 0
        for index, result in enumerate(results):
            self.assertEqual(
                p.verified_walk_forward_fold_result(
                    self.fold_results, result["fold_result_id"],
                    partition_registry_path=self.partitions,
                    experiment_registry_path=EXPERIMENT_REGISTRY,
                    hypothesis_registry_path=HYPOTHESIS_REGISTRY,
                    dataset_directory=DATASET_DIRECTORY),
                result)
            self.assertEqual(result["fold_index"], index)
            self.assertEqual(result["fold_period"], partition["folds"][index])
            evaluation = result["evaluation"]
            self.assertEqual(evaluation["baseline"]["rule"],
                             "BUY_AND_HOLD_MEAN_FORWARD_RETURN_1D")
            self.assertIn(evaluation["criterion_result"], {"MET", "NOT_MET", "INCONCLUSIVE"})
            total_observations += evaluation["observation_count"]
        self.assertEqual(total_observations, 365)

    def test_fold_result_is_idempotent(self):
        partition = self.partition()
        first = self.fold_result(partition, 0)
        second = self.fold_result(partition, 0)
        self.assertEqual(first, second)

    def test_rejects_a_wrong_partition_seal_before_persisting_a_fold_result(self):
        partition = self.partition()
        with self.assertRaisesRegex(ValueError, "seal"):
            p.constitute_walk_forward_fold_result(
                self.fold_results, partition_registry_path=self.partitions,
                partition_id=partition["partition_id"],
                partition_record_id=partition["record_id"] + "-x", fold_index=0,
                experiment_registry_path=EXPERIMENT_REGISTRY, experiment_id=EXPERIMENT_ID,
                experiment_version=1, experiment_record_id=EXPERIMENT_SEAL,
                hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY,
                computed_at=COMPUTED_AT, computation_code_revision=COMPUTATION_REVISION)
        self.assertFalse(self.fold_results.exists())

    def test_rejects_a_fold_index_out_of_range(self):
        partition = self.partition()
        with self.assertRaisesRegex(ValueError, "out of range"):
            self.fold_result(partition, FOLD_COUNT)

    # -- M2.6-T3 -----------------------------------------------------------

    def test_aggregates_all_folds_into_a_traceable_never_unclassified_verdict(self):
        partition = self.partition()
        self.all_fold_results(partition)
        record = self.validation(partition)

        self.assertEqual(
            p.verified_statistical_validation(
                self.validations, record["validation_id"],
                partition_registry_path=self.partitions,
                fold_result_registry_path=self.fold_results,
                experiment_registry_path=EXPERIMENT_REGISTRY,
                hypothesis_registry_path=HYPOTHESIS_REGISTRY,
                dataset_directory=DATASET_DIRECTORY),
            record)
        self.assertIn(record["outcome"], p.STATISTICAL_VALIDATION_OUTCOMES)
        self.assertEqual(len(record["fold_summaries"]), FOLD_COUNT)
        if record["outcome"] == "VALIDATED":
            self.assertIsNone(record["outcome_reason"])
            self.assertIsNotNone(record["consistency_ratio"])
        else:
            self.assertIsNotNone(record["outcome_reason"])

    def test_requires_every_fold_to_have_a_result_first(self):
        partition = self.partition()
        self.fold_result(partition, 0)  # only one of five
        with self.assertRaisesRegex(ValueError, "Not every fold"):
            self.validation(partition)
        self.assertFalse(self.validations.exists())

    def test_is_idempotent_and_ignores_a_later_validation_attempt(self):
        partition = self.partition()
        self.all_fold_results(partition)
        first = self.validation(partition, validated_at=VALIDATED_AT)
        second = self.validation(
            partition, validated_at="2026-09-28T09:20:00Z", validation_code_revision="d" * 40)
        self.assertEqual(first, second)

    def test_does_not_rewrite_the_single_sample_disposition_already_issued(self):
        """M2.6-T3's hard constraint: additive only, never touches M2.4-T1."""
        legacy_results = self.directory / "experiment-results.json"
        executions = self.directory / "research-executions.json"
        research_results = self.directory / "research-results.json"
        dispositions = self.directory / "dispositions.json"

        legacy = p.execute_experiment_result(
            legacy_results, experiment_registry_path=EXPERIMENT_REGISTRY,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY,
            experiment_id=EXPERIMENT_ID, experiment_version=1,
            experiment_record_id=EXPERIMENT_SEAL, execution_code_revision="e" * 40)
        execution = p.constitute_research_execution(
            executions, legacy_result_registry_path=legacy_results,
            legacy_result_id=legacy["result_id"], legacy_result_record_id=legacy["record_id"],
            experiment_registry_path=EXPERIMENT_REGISTRY,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY,
            materialized_at="2026-09-27T08:00:00Z", materialization_code_revision="f" * 40)
        research_result = p.constitute_research_result(
            research_results, research_execution_registry_path=executions,
            research_execution_id=execution["execution_id"],
            research_execution_record_id=execution["record_id"],
            legacy_result_registry_path=legacy_results, experiment_registry_path=EXPERIMENT_REGISTRY,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY,
            materialized_at="2026-09-27T08:00:00Z", materialization_code_revision="f" * 40)
        disposition = p.constitute_disposition(
            dispositions, research_result_registry_path=research_results,
            research_result_id=research_result["research_result_id"],
            research_result_record_id=research_result["record_id"],
            research_execution_registry_path=executions, legacy_result_registry_path=legacy_results,
            experiment_registry_path=EXPERIMENT_REGISTRY,
            hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY,
            disposed_at="2026-09-27T08:30:00Z", disposition_code_revision="1" * 40)
        before = dispositions.read_bytes()

        partition = self.partition()
        self.all_fold_results(partition)
        self.validation(partition)

        self.assertEqual(dispositions.read_bytes(), before)
        self.assertEqual(
            p.verified_disposition(
                dispositions, disposition["disposition_id"],
                research_result_registry_path=research_results,
                research_execution_registry_path=executions,
                legacy_result_registry_path=legacy_results,
                experiment_registry_path=EXPERIMENT_REGISTRY,
                hypothesis_registry_path=HYPOTHESIS_REGISTRY, dataset_directory=DATASET_DIRECTORY),
            disposition)

    def test_query_filters_by_outcome_and_starts_empty(self):
        self.assertEqual(p.query_statistical_validation(self.validations), [])
        partition = self.partition()
        self.all_fold_results(partition)
        record = self.validation(partition)
        matching = p.query_statistical_validation(self.validations, outcome=record["outcome"])
        self.assertEqual([item["validation_id"] for item in matching], [record["validation_id"]])

    def test_load_rejects_malformed_identities(self):
        with self.assertRaisesRegex(ValueError, "valid"):
            p.load_walk_forward_partition(self.partitions, "not-a-valid-id")
        with self.assertRaisesRegex(ValueError, "valid"):
            p.load_walk_forward_fold_result(self.fold_results, "not-a-valid-id")
        with self.assertRaisesRegex(ValueError, "valid"):
            p.load_statistical_validation(self.validations, "not-a-valid-id")

    # -- outcome classification, unit-tested directly for full coverage ----

    def test_outcome_is_insufficient_evidence_below_the_declared_minimum(self):
        summaries = [
            {"fold_index": 0, "period": {}, "criterion_result": "INCONCLUSIVE",
             "metric": None, "beats_baseline": None},
            {"fold_index": 1, "period": {}, "criterion_result": "MET",
             "metric": 0.1, "beats_baseline": True},
        ]
        outcome, reason, ratio = p._statistical_validation_outcome(summaries, 2, "0.5")
        self.assertEqual(outcome, "INSUFFICIENT_EVIDENCE")
        self.assertEqual(reason, "USABLE_FOLDS_BELOW_MINIMUM")
        self.assertIsNone(ratio)

    def test_outcome_is_validated_at_or_above_the_threshold(self):
        summaries = [
            {"fold_index": i, "period": {}, "criterion_result": "MET",
             "metric": 0.1, "beats_baseline": True}
            for i in range(3)
        ] + [{"fold_index": 3, "period": {}, "criterion_result": "NOT_MET",
             "metric": -0.1, "beats_baseline": False}]
        outcome, reason, ratio = p._statistical_validation_outcome(summaries, 2, "0.75")
        self.assertEqual(outcome, "VALIDATED")
        self.assertIsNone(reason)
        self.assertEqual(ratio, 0.75)

    def test_outcome_is_not_validated_below_the_threshold(self):
        summaries = [
            {"fold_index": 0, "period": {}, "criterion_result": "MET",
             "metric": 0.1, "beats_baseline": True},
            {"fold_index": 1, "period": {}, "criterion_result": "NOT_MET",
             "metric": -0.1, "beats_baseline": False},
        ]
        outcome, reason, ratio = p._statistical_validation_outcome(summaries, 2, "0.9")
        self.assertEqual(outcome, "NOT_VALIDATED")
        self.assertEqual(reason, "CONSISTENCY_BELOW_THRESHOLD")
        self.assertEqual(ratio, 0.5)


if __name__ == "__main__":
    unittest.main()
