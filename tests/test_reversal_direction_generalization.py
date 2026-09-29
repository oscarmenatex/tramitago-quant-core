"""Focused proof for the acceptance-criterion direction generalization
(proposed 2026-09-29 after comparing all 12 prior real hypotheses' signed
metrics: 9 of 12 showed the "momentum" group underperforming the other
group, often by a larger margin than the 3 positive/noise cases --
suggesting short-term reversal, not continuation).

experiment.py's Experiment layer only ever accepted
comparison="GT"/threshold="0"/expected_direction="INCREASE", even though
hypothesis.py already validated the full GT/GE (INCREASE) and LT/LE
(DECREASE) space. This test proves the generalization itself with
synthetic data end to end (Hypothesis -> Dataset -> Experiment -> Result),
not the real market result (that is a separate, real run).
"""

import json
import math
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pipeline as p


class CriterionDirectionUnitTests(unittest.TestCase):
    def test_is_compatible_accepts_the_four_valid_pairings(self):
        for comparison, direction in [("GT", "INCREASE"), ("GE", "INCREASE"),
                                      ("LT", "DECREASE"), ("LE", "DECREASE")]:
            criterion = {"metric": "m", "comparison": comparison, "threshold": "0",
                        "expected_direction": direction}
            self.assertTrue(p._experiment_criterion_is_compatible(criterion, "m"), comparison)

    def test_is_compatible_rejects_mismatched_direction(self):
        criterion = {"metric": "m", "comparison": "GT", "threshold": "0",
                    "expected_direction": "DECREASE"}
        self.assertFalse(p._experiment_criterion_is_compatible(criterion, "m"))

    def test_criterion_result_derives_from_the_real_comparator(self):
        gt = {"metric": "m", "comparison": "GT", "threshold": "0", "expected_direction": "INCREASE"}
        lt = {"metric": "m", "comparison": "LT", "threshold": "0", "expected_direction": "DECREASE"}
        self.assertEqual(p._experiment_criterion_result(0.01, gt), "MET")
        self.assertEqual(p._experiment_criterion_result(-0.01, gt), "NOT_MET")
        self.assertEqual(p._experiment_criterion_result(-0.01, lt), "MET")
        self.assertEqual(p._experiment_criterion_result(0.01, lt), "NOT_MET")

    def test_long_group_flips_with_direction(self):
        increase = {"metric": "m", "comparison": "GT", "threshold": "0",
                   "expected_direction": "INCREASE"}
        decrease = {"metric": "m", "comparison": "LT", "threshold": "0",
                   "expected_direction": "DECREASE"}
        self.assertEqual(p._walk_forward_long_group(increase), "UPPER")
        self.assertEqual(p._walk_forward_long_group(decrease), "LOWER_OR_EQUAL")


class ReversalDirectionChainTests(unittest.TestCase):
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

    def _build_dataset(self, hypothesis):
        output = self.root / "dataset"
        p.create_hypothesis_dataset(
            self.hyp_registry, hypothesis["hypothesis_id"], 1, output,
            transport=self._transport(), acquired_at="2026-09-29T00:00:00Z",
            strategy=p.sma_crossover_strategy(3))
        return output

    def test_decrease_direction_chain_reproduces_and_detects_tampering(self):
        constraints = {
            "variables": ["close", "sma_close_3", "forward_return_1d"],
            "period": {"start_utc": "2025-01-01T00:00:00Z", "end_exclusive_utc": "2026-01-01T00:00:00Z"},
            "universe": ["BTC-USD"],
        }
        acceptance_criterion = {
            "metric": "m", "comparison": "LT", "threshold": "0", "expected_direction": "DECREASE",
        }
        hypothesis = p.constitute_hypothesis(
            self.hyp_registry, description="test reversal direction", target_metric="m",
            expected_direction="DECREASE", constraints=constraints,
            acceptance_criterion=acceptance_criterion, creation_timestamp="2026-09-29T00:00:00Z",
            status="CONSTITUTED", created_by="test",
            provenance=["SNAPSHOT_SHA256|" + "a" * 64, "DISCOVERY|artifacts/live-run-1"],
            system_version="0.1.0", code_revision="0" * 40)
        output = self._build_dataset(hypothesis)

        exp_registry = self.root / "experiments.json"
        exp_record = p.constitute_experiment_conditions(
            exp_registry, hypothesis_registry_path=self.hyp_registry, dataset_directory=output,
            hypothesis_id=hypothesis["hypothesis_id"], hypothesis_version=1,
            dataset_id=p.verified_hypothesis_dataset(output, self.hyp_registry)["dataset_id"],
            created_at="2026-09-29T00:00:00Z", revision_reason="TEST_REVERSAL")
        self.assertEqual(exp_record["conditions"]["acceptance_criterion"], acceptance_criterion)
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
        evaluation = res_record["evaluation"]
        expected_result = "MET" if evaluation["metric"] < 0 else "NOT_MET"
        self.assertEqual(evaluation["criterion_result"], expected_result)
        reloaded_res = p.verified_experiment_result(
            res_registry, res_record["result_id"], experiment_registry_path=exp_registry,
            hypothesis_registry_path=self.hyp_registry, dataset_directory=output)
        self.assertEqual(reloaded_res, res_record)

        tampered = json.loads(res_registry.read_bytes())
        tampered["results"][0]["evaluation"]["criterion_result"] = (
            "NOT_MET" if evaluation["criterion_result"] == "MET" else "MET")
        res_registry.write_bytes(p.encoded(tampered))
        with self.assertRaises(ValueError):
            p.verified_experiment_result(
                res_registry, res_record["result_id"], experiment_registry_path=exp_registry,
                hypothesis_registry_path=self.hyp_registry, dataset_directory=output)

    def test_increase_and_decrease_agree_on_the_same_underlying_metric(self):
        """The same data, evaluated once as INCREASE/GT and once as
        DECREASE/LT, must reach opposite MET/NOT_MET conclusions for the
        same signed metric -- proving the direction is read from the
        criterion, not silently re-derived some other way."""
        increase_criterion = {
            "metric": "m", "comparison": "GT", "threshold": "0", "expected_direction": "INCREASE"}
        decrease_criterion = {
            "metric": "m", "comparison": "LT", "threshold": "0", "expected_direction": "DECREASE"}
        for metric in (0.01, -0.01, 0.0):
            increase_result = p._experiment_criterion_result(metric, increase_criterion)
            decrease_result = p._experiment_criterion_result(metric, decrease_criterion)
            if metric == 0.0:
                self.assertEqual(increase_result, "NOT_MET")
                self.assertEqual(decrease_result, "NOT_MET")
            else:
                self.assertNotEqual(increase_result, decrease_result)


if __name__ == "__main__":
    unittest.main()
