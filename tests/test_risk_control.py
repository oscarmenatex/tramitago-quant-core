"""Proof for CAP-005 Risk Control (Etapa 4.5, M4.5-T1, 2026-09-29): the
quantitative pre-execution limits DOC-011 §6 said were still missing.

Risk Control gates a proposed action against a DECLARED, parametric set of
limits (never the literals baked into pipeline.py's proposal check), and
emits the M3.1 vocabulary COMPLETED / BLOCKED. It applies both limits the
spec names: RL-008-002 total exposure and RL-008-001 max drawdown -- the
latter being exactly what the existing hardcoded proposal check lacked.
"""

import unittest

import pipeline as p


class RiskContractTests(unittest.TestCase):
    def test_a_contract_holds_declared_parametric_limits(self):
        contract = p.risk_contract(max_total_exposure_usd="50", max_drawdown_ratio=0.15)
        self.assertEqual(contract["max_total_exposure_usd"], "50")
        self.assertEqual(contract["max_drawdown_ratio"], 0.15)

    def test_contract_rejects_nonsense_limits(self):
        with self.assertRaises(ValueError):
            p.risk_contract(max_total_exposure_usd="0", max_drawdown_ratio=0.15)
        with self.assertRaises(ValueError):
            p.risk_contract(max_total_exposure_usd="-5", max_drawdown_ratio=0.15)
        with self.assertRaises(ValueError):
            p.risk_contract(max_total_exposure_usd="50", max_drawdown_ratio=0.0)
        with self.assertRaises(ValueError):
            p.risk_contract(max_total_exposure_usd="50", max_drawdown_ratio=1.5)
        with self.assertRaises(ValueError):
            p.risk_contract(max_total_exposure_usd=50.0, max_drawdown_ratio=0.15)  # float money

    def test_money_is_decimal_not_float(self):
        """0.1 + 0.2 must not leak into a risk limit. Exposure of exactly the
        ceiling is allowed; a cent over is not."""
        contract = p.risk_contract(max_total_exposure_usd="0.30", max_drawdown_ratio=0.5)
        at_ceiling = p.evaluate_risk_control(
            contract=contract, proposed_total_exposure_usd="0.30", equity_curve=[100.0])
        self.assertEqual(at_ceiling["outcome"], p.RISK_CONTROL_COMPLETED)
        over = p.evaluate_risk_control(
            contract=contract, proposed_total_exposure_usd="0.31", equity_curve=[100.0])
        self.assertEqual(over["outcome"], p.RISK_CONTROL_BLOCKED)


class RiskControlEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.contract = p.risk_contract(max_total_exposure_usd="50", max_drawdown_ratio=0.15)

    def test_within_both_limits_is_completed(self):
        result = p.evaluate_risk_control(
            contract=self.contract, proposed_total_exposure_usd="40",
            equity_curve=[100.0, 108.0, 104.0])  # ~3.7% drawdown, under 15%
        self.assertEqual(result["outcome"], p.RISK_CONTROL_COMPLETED)
        self.assertEqual(result["breaches"], [])

    def test_exposure_breach_alone_blocks(self):
        result = p.evaluate_risk_control(
            contract=self.contract, proposed_total_exposure_usd="60",
            equity_curve=[100.0, 101.0])
        self.assertEqual(result["outcome"], p.RISK_CONTROL_BLOCKED)
        self.assertEqual([b["limit"] for b in result["breaches"]],
                         [p.RISK_LIMIT_TOTAL_EXPOSURE])

    def test_drawdown_breach_alone_blocks(self):
        # peak 120 -> trough 96 = 20% drawdown, over the 15% ceiling
        result = p.evaluate_risk_control(
            contract=self.contract, proposed_total_exposure_usd="10",
            equity_curve=[100.0, 120.0, 96.0])
        self.assertEqual(result["outcome"], p.RISK_CONTROL_BLOCKED)
        self.assertEqual([b["limit"] for b in result["breaches"]],
                         [p.RISK_LIMIT_MAX_DRAWDOWN])
        self.assertAlmostEqual(result["breaches"][0]["observed"], 0.2)

    def test_both_limits_breached_names_both(self):
        result = p.evaluate_risk_control(
            contract=self.contract, proposed_total_exposure_usd="80",
            equity_curve=[100.0, 130.0, 90.0])
        self.assertEqual(result["outcome"], p.RISK_CONTROL_BLOCKED)
        self.assertEqual({b["limit"] for b in result["breaches"]},
                         {p.RISK_LIMIT_TOTAL_EXPOSURE, p.RISK_LIMIT_MAX_DRAWDOWN})

    def test_empty_equity_history_has_zero_drawdown(self):
        result = p.evaluate_risk_control(
            contract=self.contract, proposed_total_exposure_usd="10", equity_curve=[])
        self.assertEqual(result["outcome"], p.RISK_CONTROL_COMPLETED)
        self.assertEqual(result["evaluated"]["observed_max_drawdown_ratio"], 0.0)

    def test_drawdown_matches_the_m26t4_metric(self):
        """RL-008-001 uses the same peak-to-trough metric M2.6-T4 Risk
        Analytics reports -- a monotonically rising curve has zero drawdown,
        and the metric equals (peak - trough)/peak."""
        rising = p.evaluate_risk_control(
            contract=self.contract, proposed_total_exposure_usd="10",
            equity_curve=[10.0, 20.0, 30.0, 40.0])
        self.assertEqual(rising["evaluated"]["observed_max_drawdown_ratio"], 0.0)
        self.assertAlmostEqual(p._equity_max_drawdown([100.0, 50.0]), 0.5)

    def test_it_only_gates_never_sizes(self):
        """A COMPLETED result is a permission, not an order: it carries no
        sizing, no broker, no action -- only the outcome and what was checked."""
        result = p.evaluate_risk_control(
            contract=self.contract, proposed_total_exposure_usd="40",
            equity_curve=[100.0])
        self.assertEqual(set(result),
                         {"schema_version", "outcome", "evaluated", "limits", "breaches"})
        self.assertNotIn("order", result)
        self.assertNotIn("size", result)


if __name__ == "__main__":
    unittest.main()
