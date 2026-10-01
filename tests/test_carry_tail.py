"""Carry tail: exact liquidation arithmetic, and honesty about the missing input.

The destination redefinition made the tail the deciding work. These prove the
arithmetic, and -- more importantly -- the two refusals: a stress scenario
cannot be declared without a source, and the collateral mode has no default,
because defaulting to the friendlier reading would hide the entire risk.
"""

import unittest

import pipeline as p


def _scenario(move="0.05"):
    return p.stress_scenario(
        adverse_move=move,
        source="DECLARED ASSUMPTION: no basis has been observed by this project")


class StressScenarioTests(unittest.TestCase):
    def test_a_scenario_cannot_be_declared_without_a_source(self):
        # Structural, not bureaucratic: the number is an assumption and must say
        # whose. A scenario sourced from nothing certifies its own guess.
        with self.assertRaises(ValueError) as caught:
            p.stress_scenario(adverse_move="0.05", source="   ")
        self.assertIn("never observed", str(caught.exception))

    def test_identity_reproduces_and_detects_a_softened_stress(self):
        scenario = _scenario("0.05")
        self.assertEqual(p.verified_stress_scenario(scenario), scenario)
        with self.assertRaises(ValueError):
            p.verified_stress_scenario({**scenario, "adverse_move": "0.001"})


class LiquidationArithmeticTests(unittest.TestCase):
    def test_the_threshold_is_exact(self):
        # 10x with 0.5% maintenance: 1/10 - 0.005 = 9.5%
        self.assertAlmostEqual(
            p.liquidation_move(leverage=10, maintenance_margin_rate="0.005"), 0.095)

    def test_higher_leverage_liquidates_sooner(self):
        low = p.liquidation_move(leverage=2, maintenance_margin_rate="0.005")
        high = p.liquidation_move(leverage=20, maintenance_margin_rate="0.005")
        self.assertGreater(low, high)

    def test_leverage_past_maintenance_margin_is_refused(self):
        with self.assertRaises(ValueError):
            p.liquidation_move(leverage=250, maintenance_margin_rate="0.005")


class CollateralModeTests(unittest.TestCase):
    """The structural fact that dominates, and needs no data."""

    def test_the_same_stress_reads_as_basis_or_price_depending_on_collateral(self):
        scenario = _scenario("0.05")
        shared_ok, shared = p.survives_stress(
            scenario=scenario, leverage=10, maintenance_margin_rate="0.005",
            collateral_mode=p.COLLATERAL_SHARED)
        separate_ok, separate = p.survives_stress(
            scenario=scenario, leverage=10, maintenance_margin_rate="0.005",
            collateral_mode=p.COLLATERAL_SEPARATE)
        self.assertIn("basis divergence", shared)
        self.assertIn("price move", separate)
        self.assertTrue(shared_ok and separate_ok)   # same threshold, different meaning

    def test_collateral_mode_has_no_default(self):
        # Defaulting to SHARED would quietly assume away the whole risk: a short
        # perp on another venue can be liquidated by a rally while the spot leg
        # sits profitable and unreachable.
        with self.assertRaises(ValueError):
            p.survives_stress(scenario=_scenario(), leverage=10,
                              maintenance_margin_rate="0.005", collateral_mode="MAYBE")

    def test_a_stress_beyond_the_threshold_liquidates(self):
        ok, detail = p.survives_stress(
            scenario=_scenario("0.20"), leverage=10,
            maintenance_margin_rate="0.005", collateral_mode=p.COLLATERAL_SEPARATE)
        self.assertFalse(ok)
        self.assertIn("liquidates", detail)


class LossTests(unittest.TestCase):
    def test_loss_is_the_margin_as_a_share_of_committed_capital(self):
        # 10x: margin 0.1 against 1.1 committed.
        self.assertAlmostEqual(
            p.loss_fraction_at_liquidation(leverage=10,
                                           collateral_mode=p.COLLATERAL_SHARED),
            0.1 / 1.1)

    def test_higher_leverage_loses_a_smaller_share_which_is_counterintuitive(self):
        # A thinner margin is simply less to lose. This is why loss-if-liquidated
        # must NOT be compared against a drawdown limit on its own: it makes low
        # leverage look dangerous when at low leverage that liquidation never
        # happens. loss_in_stress is the quantity the limit applies to.
        self.assertGreater(
            p.loss_fraction_at_liquidation(leverage=2, collateral_mode=p.COLLATERAL_SHARED),
            p.loss_fraction_at_liquidation(leverage=20, collateral_mode=p.COLLATERAL_SHARED))


class MaximumSafeLeverageTests(unittest.TestCase):
    def test_a_mild_stress_is_bound_by_survival_not_by_the_drawdown(self):
        # A 2% basis move costs well under the limit at any surviving leverage,
        # so what caps the size is the liquidation threshold.
        result = p.maximum_safe_leverage(
            scenario=_scenario("0.02"), maintenance_margin_rate="0.005",
            collateral_mode=p.COLLATERAL_SHARED, drawdown_limit="0.15")
        self.assertEqual(result["binding_constraint"], "STRESS")
        self.assertEqual(result["state_in_stress"], "SURVIVED")
        self.assertLessEqual(result["loss_in_stress"], 0.15)

    def test_a_stress_larger_than_the_drawdown_limit_is_bound_by_the_drawdown(self):
        # A 20% move against a 15% limit still sizes, because committed capital
        # includes the spot notional and dilutes the basis loss: at 2.9x the
        # commitment is 1.34 units, so 20% of one unit is 14.9% of it. Leverage
        # is capped where that dilution stops being enough -- DRAWDOWN binding,
        # not survival.
        result = p.maximum_safe_leverage(
            scenario=_scenario("0.20"), maintenance_margin_rate="0.005",
            collateral_mode=p.COLLATERAL_SHARED, drawdown_limit="0.15")
        self.assertEqual(result["binding_constraint"], "DRAWDOWN")
        self.assertLess(result["maximum_leverage"], 3.0)
        self.assertLessEqual(result["loss_in_stress"], 0.15)

    def test_survival_is_required_and_never_traded_off(self):
        # Being liquidated is not an outcome a favourable loss number excuses.
        result = p.maximum_safe_leverage(
            scenario=_scenario("0.30"), maintenance_margin_rate="0.005",
            collateral_mode=p.COLLATERAL_SEPARATE, drawdown_limit="0.99")
        self.assertIsNotNone(result["maximum_leverage"])
        self.assertEqual(result["state_in_stress"], "SURVIVED")

    def test_an_unsizeable_position_returns_none_rather_than_a_number(self):
        result = p.maximum_safe_leverage(
            scenario=_scenario("0.95"), maintenance_margin_rate="0.005",
            collateral_mode=p.COLLATERAL_SEPARATE, drawdown_limit="0.15")
        self.assertIsNone(result["maximum_leverage"])
        self.assertIn("cannot be sized safely", result["detail"])


class HonestyTests(unittest.TestCase):
    def test_the_module_reports_that_the_basis_is_unmeasured(self):
        # Returned as data so a caller can carry the caveat with the number,
        # rather than leaving it in a docstring nobody opens.
        state = p.basis_is_unmeasured()
        self.assertFalse(state["measured"])
        self.assertIn("perpetual mark price", state["missing"])
        self.assertIn("no result from this module is evidence about the tail",
                      state["consequence"])


if __name__ == "__main__":
    unittest.main()
