"""Focused proof for M2.7-T4: the autonomous orchestration that runs the
whole declared N=8 search space (M2.7-T1) end to end -- Hypothesis ->
Dataset -> Experiment -> Result -> Walk-Forward -> Statistical Validation
(Bonferroni-corrected for N=8, M2.7-T2) -> Batch (M2.7-T3) -- with
synthetic (no-network) candle data, so the test proves the wiring, not
the real market result (that is a separate, real run against Coinbase)."""

import json
import math
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pipeline as p


class M27T4AutonomousOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

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

    def _run(self):
        return p.run_hypothesis_generation_batch(
            hypothesis_registry_path=self.root / "hypotheses.json",
            dataset_root=self.root / "datasets",
            experiment_registry_path=self.root / "experiments.json",
            result_registry_path=self.root / "results.json",
            partition_registry_path=self.root / "partitions.json",
            fold_result_registry_path=self.root / "folds.json",
            validation_registry_path=self.root / "validations.json",
            batch_registry_path=self.root / "batches.json",
            created_at="2026-09-28T12:00:00Z", code_revision="e" * 40,
            transport=self._transport(), acquired_at="2026-09-28T12:00:00Z")

    def test_runs_the_entire_declared_search_space_and_seals_one_batch(self):
        batch = self._run()

        self.assertEqual(batch["search_space_id"], p.HYPOTHESIS_GENERATION_SEARCH_SPACE_ID)
        self.assertEqual(batch["instrument"], "BTC-USD")
        self.assertEqual(len(batch["members"]), 8)
        self.assertEqual(batch["summary"]["batch_size"], 8)
        self.assertEqual(
            batch["summary"]["validated_count"] + batch["summary"]["not_validated_count"]
            + batch["summary"]["insufficient_evidence_count"], 8)

        strategy_ids = sorted(member["strategy_id"] for member in batch["members"])
        self.assertEqual(strategy_ids, sorted(["SMA_CROSSOVER"] * 4 + ["MOMENTUM_CROSSOVER"] * 4))

        # Every Hypothesis is distinct (8 real, separately-sealed registrations).
        hypothesis_ids = {member["hypothesis_id"] for member in batch["members"]}
        self.assertEqual(len(hypothesis_ids), 8)

        reloaded = p.verified_hypothesis_generation_batch(
            self.root / "batches.json", batch["batch_id"],
            validation_registry_path=self.root / "validations.json",
            partition_registry_path=self.root / "partitions.json",
            fold_result_registry_path=self.root / "folds.json",
            experiment_registry_path=self.root / "experiments.json",
            hypothesis_registry_path=self.root / "hypotheses.json")
        self.assertEqual(reloaded, batch)

    def test_every_member_validation_used_this_batchs_own_bonferroni_correction(self):
        batch = self._run()
        validations = json.loads((self.root / "validations.json").read_bytes())["validations"]
        by_id = {item["validation_id"]: item for item in validations}
        for member in batch["members"]:
            validation = by_id[member["validation_id"]]
            self.assertIn("batch", validation)
            self.assertEqual(validation["batch"]["batch_size"], 8)
            self.assertAlmostEqual(
                validation["batch"]["bonferroni_corrected_alpha"], 0.05 / 8)

if __name__ == "__main__":
    unittest.main()
