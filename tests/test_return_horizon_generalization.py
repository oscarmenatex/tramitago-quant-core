"""Focused proof for the return-horizon generalization (Etapa 2.7, proposed
2026-09-28 after diagnosing that 11 real hypotheses at horizon=1 all showed
a full-sample effect inside the noise floor -- maybe the effect takes
longer than one day to manifest).

historical_dataset.py/experiment.py/walk_forward.py's forward-return
distance now accepts an explicit `horizon` (defaulting to 1, the exact
distance every already-sealed real artifact was built with). This test
proves the generalization itself with synthetic data end to end
(Hypothesis -> Dataset -> Experiment -> Result), not the real market
result (that is a separate, real run).
"""

import json
import math
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pipeline as p


class ReturnHorizonGeneralizationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.hyp_registry = self.root / "hypotheses.json"

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

    def test_forward_column_name_and_support_policy_at_default_and_nondefault_horizon(self):
        self.assertEqual(p._hypothesis_dataset_forward_column(1), "forward_return_1d")
        self.assertEqual(p._hypothesis_dataset_forward_column(3), "forward_return_3d")
        strategy = p.sma_crossover_strategy(3)
        self.assertEqual(
            p._hypothesis_dataset_support_policy(strategy, 1),
            "exactly two SMA3 warm-up rows before and one forward-return row after the "
            "evaluable period")
        self.assertEqual(
            p._hypothesis_dataset_support_policy(strategy, 3),
            "exactly two SMA3 warm-up rows before and three forward-return rows after the "
            "evaluable period")

    def test_horizon_3_chain_reproduces_end_to_end_and_detects_tampering(self):
        strategy = p.sma_crossover_strategy(3)
        horizon = 3
        forward_column = p._hypothesis_dataset_forward_column(horizon)
        constraints = {
            "variables": ["close", "sma_close_3", forward_column],
            "period": {"start_utc": "2025-01-01T00:00:00Z", "end_exclusive_utc": "2026-01-01T00:00:00Z"},
            "universe": ["BTC-USD"],
        }
        acceptance_criterion = {
            "metric": "m", "comparison": "GT", "threshold": "0", "expected_direction": "INCREASE",
        }
        hypothesis = p.constitute_hypothesis(
            self.hyp_registry, description="test horizon", target_metric="m",
            expected_direction="INCREASE", constraints=constraints,
            acceptance_criterion=acceptance_criterion, creation_timestamp="2026-09-28T00:00:00Z",
            status="CONSTITUTED", created_by="test",
            provenance=["SNAPSHOT_SHA256|" + "a" * 64, "DISCOVERY|artifacts/live-run-1"],
            system_version="0.1.0", code_revision="0" * 40)

        output = self.root / "dataset"
        manifest = p.create_hypothesis_dataset(
            self.hyp_registry, hypothesis["hypothesis_id"], 1, output,
            transport=self._transport(), acquired_at="2026-09-28T00:00:00Z",
            strategy=strategy, horizon=horizon)
        self.assertIn(forward_column, manifest["columns"])
        reloaded = p.verified_hypothesis_dataset(
            output, self.hyp_registry, strategy=strategy, horizon=horizon)
        self.assertEqual(reloaded, manifest)

        exp_registry = self.root / "experiments.json"
        exp_record = p.constitute_experiment_conditions(
            exp_registry, hypothesis_registry_path=self.hyp_registry, dataset_directory=output,
            hypothesis_id=hypothesis["hypothesis_id"], hypothesis_version=1,
            dataset_id=manifest["dataset_id"], created_at="2026-09-28T00:00:00Z",
            revision_reason="TEST_HORIZON", strategy=strategy, horizon=horizon)
        self.assertEqual(exp_record["conditions"]["outcome"],
                         {"name": "return_t+3", "formula": "(close_t+3 / close_t) - 1"})
        self.assertEqual(exp_record["conditions"]["forward_horizon"], 3)
        reloaded_exp = p.verified_experiment_conditions(
            exp_registry, exp_record["experiment_id"], 1,
            hypothesis_registry_path=self.hyp_registry, dataset_directory=output)
        self.assertEqual(reloaded_exp, exp_record)

        res_registry = self.root / "results.json"
        res_record = p.execute_experiment_result(
            res_registry, experiment_registry_path=exp_registry,
            hypothesis_registry_path=self.hyp_registry, dataset_directory=output,
            experiment_id=exp_record["experiment_id"], experiment_version=1,
            experiment_record_id=exp_record["record_id"], execution_code_revision="a" * 40)
        reloaded_res = p.verified_experiment_result(
            res_registry, res_record["result_id"], experiment_registry_path=exp_registry,
            hypothesis_registry_path=self.hyp_registry, dataset_directory=output)
        self.assertEqual(reloaded_res, res_record)

        tampered = json.loads(res_registry.read_bytes())
        tampered["results"][0]["evidence"][0]["return_t_plus_1"] = 0.0
        res_registry.write_bytes(p.encoded(tampered))
        with self.assertRaises(ValueError):
            p.verified_experiment_result(
                res_registry, res_record["result_id"], experiment_registry_path=exp_registry,
                hypothesis_registry_path=self.hyp_registry, dataset_directory=output)

    def test_default_horizon_omits_the_additive_field(self):
        strategy = p.sma_crossover_strategy(3)
        constraints = {
            "variables": ["close", "sma_close_3", "forward_return_1d"],
            "period": {"start_utc": "2025-01-01T00:00:00Z", "end_exclusive_utc": "2026-01-01T00:00:00Z"},
            "universe": ["BTC-USD"],
        }
        hypothesis = {
            "hypothesis_id": "HYPOTHESIS|00000000-0000-0000-0000-000000000000",
            "version": 1, "record_id": "HYPOTHESIS_VERSION|x",
            "system_version": "0.1.0", "code_revision": "a" * 40,
            "acceptance_criterion": {
                "metric": "m", "comparison": "GT", "threshold": "0",
                "expected_direction": "INCREASE"},
            "target_metric": "m", "constraints": constraints,
            "creation_context": {"provenance": [
                "SNAPSHOT_SHA256|" + "a" * 64, "DISCOVERY|artifacts/live-run-1"]},
        }
        dataset = {
            "dataset_id": "HISTORICAL_HYPOTHESIS_DATASET|" + "b" * 64,
            "dataset_sha256": "c" * 64,
            "config": {"instrument": "BTC-USD", "frequency_seconds": 86400,
                      "evaluable_period": constraints["period"]},
            "identity": {"hypothesis_id": hypothesis["hypothesis_id"],
                        "hypothesis_version": hypothesis["version"]},
        }
        selection = {"support_rows": []}
        conditions = p._experiment_conditions(hypothesis, dataset, selection, strategy)
        self.assertNotIn("forward_horizon", conditions)
        self.assertEqual(conditions["outcome"],
                         {"name": "return_t+1", "formula": "(close_t+1 / close_t) - 1"})


if __name__ == "__main__":
    unittest.main()
