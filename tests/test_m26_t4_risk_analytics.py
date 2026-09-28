"""M2.6-T4: Risk Analytics -- descriptive Sharpe ratio, annualized
volatility, and max drawdown over the daily returns a long-only
implementation of the Hypothesis's own criterion would have captured.

Purely descriptive (DOC-003 §8: report the risk side of "Retorno
esperado / Riesgo asumido"): never changes the acceptance criterion,
never gates the walk-forward verdict, never blocks or authorizes
anything. Distinct from Risk Control (Etapa 4.5), which requires real
capital and a Broker Adapter -- this requires neither.
"""

import math
import unittest

import pipeline as p


def make_evidence(returns_by_group):
    """returns_by_group: list of (group, return_t_plus_1) pairs."""
    return [{"group": group, "return_t_plus_1": value} for group, value in returns_by_group]


class M26T4RiskAnalyticsTests(unittest.TestCase):
    def test_strategy_returns_are_long_only_flat_when_not_upper(self):
        evidence = make_evidence([
            ("UPPER", 0.02), ("LOWER_OR_EQUAL", 0.05), ("UPPER", -0.01),
        ])
        returns = p._risk_analytics_strategy_returns(evidence)
        self.assertEqual(returns, [0.02, 0.0, -0.01])

    def test_sharpe_ratio_matches_hand_computed_value(self):
        returns = [0.01, -0.01, 0.02, -0.02, 0.01]
        mean = math.fsum(returns) / len(returns)
        variance = math.fsum((r - mean) ** 2 for r in returns) / len(returns)
        expected = (mean / math.sqrt(variance)) * math.sqrt(365)
        self.assertAlmostEqual(p._risk_analytics_sharpe_ratio(returns), expected, places=12)

    def test_sharpe_ratio_is_none_without_variance_or_data(self):
        self.assertIsNone(p._risk_analytics_sharpe_ratio([]))
        self.assertIsNone(p._risk_analytics_sharpe_ratio([0.0, 0.0, 0.0]))

    def test_annualized_volatility_matches_hand_computed_value(self):
        returns = [0.03, -0.01, 0.02]
        mean = math.fsum(returns) / len(returns)
        variance = math.fsum((r - mean) ** 2 for r in returns) / len(returns)
        expected = math.sqrt(variance) * math.sqrt(365)
        self.assertAlmostEqual(
            p._risk_analytics_annualized_volatility(returns), expected, places=12)

    def test_max_drawdown_of_a_monotonically_rising_series_is_zero(self):
        returns = [0.01, 0.02, 0.03]
        self.assertEqual(p._risk_analytics_max_drawdown(returns), 0.0)

    def test_max_drawdown_matches_hand_computed_peak_to_trough(self):
        # equity: 1.10 -> 0.99 -> 1.089 ; peak 1.10, trough 0.99 -> drawdown 0.1
        returns = [0.10, -0.10, 0.10]
        equity = [1.0]
        for r in returns:
            equity.append(equity[-1] * (1 + r))
        peak = 0.0
        expected = 0.0
        for value in equity:
            peak = max(peak, value)
            expected = max(expected, (peak - value) / peak)
        self.assertAlmostEqual(p._risk_analytics_max_drawdown(returns), expected, places=12)
        self.assertAlmostEqual(p._risk_analytics_max_drawdown(returns), 0.1, places=6)

    def test_max_drawdown_of_empty_series_is_zero(self):
        self.assertEqual(p._risk_analytics_max_drawdown([]), 0.0)

    def test_summary_reports_the_fixed_descriptive_rule(self):
        evidence = make_evidence([("UPPER", 0.01), ("LOWER_OR_EQUAL", 0.02), ("UPPER", -0.01)])
        summary = p._risk_analytics_summary(evidence)
        self.assertEqual(summary["rule"], p.RISK_ANALYTICS_RULE)
        self.assertEqual(set(summary), {"rule", "sharpe_ratio", "annualized_volatility", "max_drawdown"})

    def test_never_gates_the_walk_forward_verdict_end_to_end(self):
        """Real, end-to-end proof: the same walk-forward pipeline used
        throughout M2.6 now carries risk_analytics on every fold and an
        aggregate risk_analytics_summary on the Statistical Validation,
        without changing VALIDATED/NOT_VALIDATED/INSUFFICIENT_EVIDENCE."""
        import tempfile
        from pathlib import Path

        root = Path(".").resolve()
        hypothesis_registry = root / "artifacts/research/hypotheses.json"
        experiment_registry = root / "artifacts/research/experiments.json"
        dataset_directory = (root / "artifacts/research/datasets"
                             / "3345b100-db15-4aa2-976d-dbd797cbc4a3/v1-final")
        experiment_id = "EXPERIMENT|23000efa-a18e-44cb-9a64-993cfb65200d"
        experiment_seal = ("EXPERIMENT_VERSION|EXPERIMENT|23000efa-a18e-44cb-9a64-993cfb65200d|1|"
                           "b89a7a41531bed017d0cf96be1644f761ba5868a78ee6898b0a250d47357257a")

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            partitions, folds, validations = d / "p.json", d / "f.json", d / "v.json"
            partition = p.constitute_walk_forward_partition(
                partitions, dataset_directory=dataset_directory,
                hypothesis_registry_path=hypothesis_registry, fold_count=5,
                partitioned_at="2026-09-28T09:00:00Z", partition_code_revision="a" * 40)
            for index in range(5):
                fold_result = p.constitute_walk_forward_fold_result(
                    folds, partition_registry_path=partitions,
                    partition_id=partition["partition_id"],
                    partition_record_id=partition["record_id"], fold_index=index,
                    experiment_registry_path=experiment_registry, experiment_id=experiment_id,
                    experiment_version=1, experiment_record_id=experiment_seal,
                    hypothesis_registry_path=hypothesis_registry,
                    dataset_directory=dataset_directory, computed_at="2026-09-28T09:10:00Z",
                    computation_code_revision="b" * 40)
                self.assertIn("risk_analytics", fold_result["evaluation"])
                self.assertEqual(
                    fold_result["evaluation"]["risk_analytics"]["rule"], p.RISK_ANALYTICS_RULE)

            validation = p.constitute_statistical_validation(
                validations, partition_registry_path=partitions,
                partition_id=partition["partition_id"],
                partition_record_id=partition["record_id"], fold_result_registry_path=folds,
                experiment_registry_path=experiment_registry,
                hypothesis_registry_path=hypothesis_registry,
                dataset_directory=dataset_directory, minimum_folds_required=5,
                consistency_threshold="0.6", validated_at="2026-09-28T09:20:00Z",
                validation_code_revision="c" * 40)
            self.assertIn(validation["outcome"], p.STATISTICAL_VALIDATION_OUTCOMES)
            self.assertIn("risk_analytics_summary", validation)
            self.assertEqual(
                set(validation["risk_analytics_summary"]),
                {"sharpe_ratio_min", "sharpe_ratio_max", "sharpe_ratio_mean",
                 "max_drawdown_worst"})

            reloaded = p.verified_statistical_validation(
                validations, validation["validation_id"], partition_registry_path=partitions,
                fold_result_registry_path=folds, experiment_registry_path=experiment_registry,
                hypothesis_registry_path=hypothesis_registry, dataset_directory=dataset_directory)
            self.assertEqual(reloaded, validation)


if __name__ == "__main__":
    unittest.main()
