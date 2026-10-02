"""Focused proof for M2.7-T3: the aggregate batch registry (R-2.7-003)
that audits every Strategy generated and tested in one batch, not just
the ones whose Statistical Validation came back VALIDATED.

Builds a small, real (synthetic-data, no network) N=2 batch end to end --
Hypothesis -> Dataset -> Experiment -> Result -> Walk-Forward Partition ->
Fold Results -> Statistical Validation (Bonferroni-corrected for N=2) ->
Batch -- to prove the registry independently reverifies its own summary
rather than trusting a passed-in count.
"""

import json
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import math
import tempfile
import unittest

import pipeline as p


class M27T3BatchRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.hyp_registry = self.root / "hypotheses.json"
        self.exp_registry = self.root / "experiments.json"
        self.result_registry = self.root / "results.json"
        self.partition_registry = self.root / "partitions.json"
        self.fold_registry = self.root / "folds.json"
        self.validation_registry = self.root / "validations.json"
        self.batch_registry = self.root / "batches.json"

    @staticmethod
    def _candle(ts):
        day = ts // 86400
        close = 100.0 + (day % 30) + 0.37 * math.sin(day / 5.0)
        return [ts, close - 1, close + 1, close - 0.5, close, 5.0]

    def _transport(self):
        def transport(url, _headers, _timeout):
            query = parse_qs(urlsplit(url).query)
            start = p.epoch(query["start"][0])
            end = p.epoch(query["end"][0])
            payload = [self._candle(t) for t in range(start, end + 86400, 86400)]
            return json.dumps(list(reversed(payload))).encode("utf-8"), {"Date": "fixture"}
        return transport

    def _build_member(self, strategy, column_name, sequence):
        constraints = {
            "variables": ["close", column_name, "forward_return_1d"],
            # 366 days = 6 folds x 61. Six is the fewest a search of TWO can be corrected
            # for: with five, the smallest attainable p-value is 0.03125 against a
            # bar of 0.025, so no outcome could pass.
            "period": {"start_utc": "2025-01-01T00:00:00Z", "end_exclusive_utc": "2026-01-02T00:00:00Z"},
            "universe": [p.HYPOTHESIS_GENERATION_INSTRUMENT],
        }
        acceptance_criterion = {
            "metric": "m", "comparison": "GT", "threshold": "0", "expected_direction": "INCREASE",
        }
        hypothesis = p.constitute_hypothesis(
            self.hyp_registry, description=f"batch member {sequence}", target_metric="m",
            expected_direction="INCREASE", constraints=constraints,
            acceptance_criterion=acceptance_criterion, creation_timestamp="2026-09-28T00:00:00Z",
            status="CONSTITUTED", created_by="test",
            provenance=["SNAPSHOT_SHA256|" + ("a" * 63 + str(sequence)),
                       "DISCOVERY|artifacts/live-run-1"],
            system_version="0.1.0", code_revision=(str(sequence) * 40)[:40])

        output = self.root / f"dataset-{sequence}"
        manifest = p.create_hypothesis_dataset(
            self.hyp_registry, hypothesis["hypothesis_id"], 1, output,
            transport=self._transport(), acquired_at="2026-09-28T00:00:00Z", strategy=strategy)

        exp_record = p.constitute_experiment_conditions(
            self.exp_registry, hypothesis_registry_path=self.hyp_registry,
            dataset_directory=output, hypothesis_id=hypothesis["hypothesis_id"],
            hypothesis_version=1, dataset_id=manifest["dataset_id"],
            created_at="2026-09-28T00:00:00Z", revision_reason="BATCH_MEMBER", strategy=strategy)

        p.execute_experiment_result(
            self.result_registry, experiment_registry_path=self.exp_registry,
            hypothesis_registry_path=self.hyp_registry, dataset_directory=output,
            experiment_id=exp_record["experiment_id"], experiment_version=1,
            experiment_record_id=exp_record["record_id"], execution_code_revision="a" * 40)

        partition = p.constitute_walk_forward_partition(
            self.partition_registry, dataset_directory=output,
            hypothesis_registry_path=self.hyp_registry, fold_count=6,
            partitioned_at="2026-09-28T09:00:00Z", partition_code_revision="a" * 40,
            strategy=strategy)

        for index in range(6):
            p.constitute_walk_forward_fold_result(
                self.fold_registry, partition_registry_path=self.partition_registry,
                partition_id=partition["partition_id"], partition_record_id=partition["record_id"],
                fold_index=index, experiment_registry_path=self.exp_registry,
                experiment_id=exp_record["experiment_id"], experiment_version=1,
                experiment_record_id=exp_record["record_id"],
                hypothesis_registry_path=self.hyp_registry, dataset_directory=output,
                computed_at="2026-09-28T09:10:00Z", computation_code_revision="b" * 40,
                strategy=strategy)

        validation = p.constitute_statistical_validation(
            self.validation_registry, partition_registry_path=self.partition_registry,
            partition_id=partition["partition_id"], partition_record_id=partition["record_id"],
            fold_result_registry_path=self.fold_registry,
            experiment_registry_path=self.exp_registry,
            hypothesis_registry_path=self.hyp_registry, dataset_directory=output,
            minimum_folds_required=6, consistency_threshold="0.7",
            validated_at="2026-09-28T09:20:00Z", validation_code_revision="c" * 40,
            batch_size=2, strategy=strategy)

        return {
            "strategy_id": strategy["strategy_id"],
            "parameters": strategy["parameters"],
            "hypothesis_id": hypothesis["hypothesis_id"],
            "hypothesis_version": 1,
            "validation_id": validation["validation_id"],
            "dataset_directory": str(output),
        }, validation

    def test_batch_constitutes_reloads_and_independently_reverifies_summary(self):
        member_1, validation_1 = self._build_member(p.sma_crossover_strategy(5), "sma_close_5", 1)
        member_2, validation_2 = self._build_member(p.momentum_crossover_strategy(7), "close_lag_7", 2)
        members = [member_1, member_2]

        batch = p.constitute_hypothesis_generation_batch(
            self.batch_registry, search_space_id="TEST_SEARCH_SPACE", instrument="BTC-USD",
            members=members, validation_registry_path=self.validation_registry,
            partition_registry_path=self.partition_registry,
            fold_result_registry_path=self.fold_registry,
            experiment_registry_path=self.exp_registry,
            hypothesis_registry_path=self.hyp_registry,
            created_at="2026-09-28T09:30:00Z", batch_code_revision="d" * 40)

        self.assertTrue(batch["batch_id"].startswith("HYPOTHESIS_GENERATION_BATCH|"))
        self.assertEqual(batch["summary"]["batch_size"], 2)
        self.assertEqual(
            batch["summary"]["validated_count"] + batch["summary"]["not_validated_count"]
            + batch["summary"]["insufficient_evidence_count"], 2)
        # Both members were corrected for N=2: base alpha 0.05 / 2 = 0.025.
        self.assertEqual(Decimal(str(validation_1["batch"]["bonferroni_corrected_alpha"])),
                         Decimal("0.025"))
        self.assertEqual(validation_2["batch"]["batch_size"], 2)

        reloaded = p.verified_hypothesis_generation_batch(
            self.batch_registry, batch["batch_id"],
            validation_registry_path=self.validation_registry,
            partition_registry_path=self.partition_registry,
            fold_result_registry_path=self.fold_registry,
            experiment_registry_path=self.exp_registry,
            hypothesis_registry_path=self.hyp_registry)
        self.assertEqual(reloaded, batch)

        # Idempotent: constituting the identical batch content again returns
        # the already-sealed record rather than raising or duplicating.
        again = p.constitute_hypothesis_generation_batch(
            self.batch_registry, search_space_id="TEST_SEARCH_SPACE", instrument="BTC-USD",
            members=members, validation_registry_path=self.validation_registry,
            partition_registry_path=self.partition_registry,
            fold_result_registry_path=self.fold_registry,
            experiment_registry_path=self.exp_registry,
            hypothesis_registry_path=self.hyp_registry,
            created_at="2026-09-28T09:30:00Z", batch_code_revision="d" * 40)
        self.assertEqual(again, batch)

    def test_rejects_a_member_whose_validation_was_not_corrected_for_this_batch_size(self):
        member_1, _ = self._build_member(p.sma_crossover_strategy(5), "sma_close_5", 3)
        with self.assertRaises(ValueError):
            p.constitute_hypothesis_generation_batch(
                self.batch_registry, search_space_id="TEST_SEARCH_SPACE_SINGLE",
                instrument="BTC-USD", members=[member_1],
                validation_registry_path=self.validation_registry,
                partition_registry_path=self.partition_registry,
                fold_result_registry_path=self.fold_registry,
                experiment_registry_path=self.exp_registry,
                hypothesis_registry_path=self.hyp_registry,
                created_at="2026-09-28T09:30:00Z", batch_code_revision="d" * 40)

    def test_rejects_tampered_summary_on_reload(self):
        member_1, _ = self._build_member(p.sma_crossover_strategy(3), "sma_close_3", 4)
        member_2, _ = self._build_member(p.momentum_crossover_strategy(3), "close_lag_3", 5)
        batch = p.constitute_hypothesis_generation_batch(
            self.batch_registry, search_space_id="TEST_SEARCH_SPACE_2", instrument="BTC-USD",
            members=[member_1, member_2], validation_registry_path=self.validation_registry,
            partition_registry_path=self.partition_registry,
            fold_result_registry_path=self.fold_registry,
            experiment_registry_path=self.exp_registry,
            hypothesis_registry_path=self.hyp_registry,
            created_at="2026-09-28T09:30:00Z", batch_code_revision="d" * 40)
        tampered = json.loads(self.batch_registry.read_bytes())
        for item in tampered["batches"]:
            if item["batch_id"] == batch["batch_id"]:
                item["summary"]["validated_count"] = 99
        self.batch_registry.write_bytes(p.encoded(tampered))
        with self.assertRaisesRegex(ValueError, "invalid"):
            p.verified_hypothesis_generation_batch(
                self.batch_registry, batch["batch_id"],
                validation_registry_path=self.validation_registry,
                partition_registry_path=self.partition_registry,
                fold_result_registry_path=self.fold_registry,
                experiment_registry_path=self.exp_registry,
                hypothesis_registry_path=self.hyp_registry)


if __name__ == "__main__":
    unittest.main()
