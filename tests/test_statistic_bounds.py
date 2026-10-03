"""Adverse confidence bounds on a STATISTIC, not just on a mean.

The first admission review denied all three validated Hypotheses partly on §8.1,
including one whose realised worst-fold drawdown was 1.24% against a 15% limit.
It failed not because it was large but because nobody had produced a BOUND on it.
"""

import math
import unittest

import pipeline as p
from tramitago_quant_core.risk.statistic_bounds import (
    sharpe_ratio, max_drawdown, bootstrap_bound, net_sharpe_lower_bound,
    drawdown_upper_bound, BOUND_LOWER, BOUND_UPPER,
)


def _series(pattern, count):
    return [pattern[index % len(pattern)] for index in range(count)]


def _irregular(count=400):
    # Deterministic but not periodic: a periodic series makes the bootstrap
    # distribution a point mass and every assertion below vacuous.
    return [0.004 - 0.00002 * ((index * 37) % 500) for index in range(count)]


class StatisticTests(unittest.TestCase):
    def test_a_series_with_no_dispersion_has_no_sharpe(self):
        # None rather than infinity: reporting a huge number here would be the
        # most misleading thing this module could do.
        self.assertIsNone(sharpe_ratio([0.001] * 50))

    def test_a_series_too_short_has_no_sharpe(self):
        self.assertIsNone(sharpe_ratio([0.01]))

    def test_sharpe_rises_with_the_mean_and_falls_with_dispersion(self):
        calm = _series([0.002, 0.001], 200)
        wild = _series([0.012, -0.009], 200)
        self.assertGreater(sharpe_ratio(calm), sharpe_ratio(wild))

    def test_drawdown_is_zero_when_the_curve_never_declines(self):
        self.assertEqual(max_drawdown([0.01] * 20), 0.0)

    def test_drawdown_measures_peak_to_trough(self):
        self.assertAlmostEqual(max_drawdown([0.0, -0.10, 0.0]), 0.10)


class SideTests(unittest.TestCase):
    """Which tail is adverse depends on the gate, so it is stated not inferred."""

    def test_the_lower_bound_sits_below_the_point_estimate(self):
        returns = _irregular()
        self.assertLess(net_sharpe_lower_bound(returns), sharpe_ratio(returns))

    def test_the_upper_bound_sits_at_or_above_the_point_estimate(self):
        returns = _series([0.004] * 20 + [-0.03] * 3, 300)
        self.assertGreaterEqual(drawdown_upper_bound(returns), max_drawdown(returns))

    def test_the_two_sides_bracket_the_statistic(self):
        returns = _irregular()
        lower = bootstrap_bound(returns, sharpe_ratio, side=BOUND_LOWER)
        upper = bootstrap_bound(returns, sharpe_ratio, side=BOUND_UPPER)
        self.assertLess(lower, upper)

    def test_an_unknown_side_is_refused(self):
        with self.assertRaises(ValueError):
            bootstrap_bound(_irregular(), sharpe_ratio, side="EITHER")


class BlockTests(unittest.TestCase):
    """A drawdown is path-dependent, so the block length is not a detail.

    It is NOT true that longer blocks always give a more conservative bound, and
    asserting so is how this test first failed. Single-period resampling can
    CONCOCT runs of losses longer than the series ever contained (0.51 against
    0.36 on the series below), while a block as long as the data's own cycle
    makes every resample a copy of the original and reports only what already
    happened. Too short dissolves real runs; too long cannot find new ones.
    Neither end is safe, which is exactly why the length is a DECLARED, SEALED
    parameter rather than a default nobody examines."""

    def test_the_block_length_materially_changes_the_bound(self):
        returns = _series([0.004] * 16 + [-0.02] * 4, 400)
        bounds = {length: drawdown_upper_bound(returns, block_periods=length)
                  for length in (1, 5, 20)}
        self.assertGreater(max(bounds.values()) - min(bounds.values()), 0.05)

    def test_the_worst_bound_sits_at_neither_extreme(self):
        # Measured on this series: realised 0.3314, and the bound runs 0.5126 at
        # a block of 1, 0.6139 at 5, 0.3600 at 20. The most adverse answer is in
        # the MIDDLE, so there is no end of the range a cautious caller could
        # pick by reflex -- the length has to be declared on its own merits.
        returns = _series([0.004] * 16 + [-0.02] * 4, 400)
        bounds = {length: drawdown_upper_bound(returns, block_periods=length)
                  for length in (1, 5, 20)}
        worst = max(bounds, key=bounds.get)
        self.assertNotIn(worst, (min(bounds), max(bounds)))
        self.assertGreater(bounds[1], max_drawdown(returns))


class ReproducibilityTests(unittest.TestCase):
    def test_the_same_seed_gives_the_same_bound(self):
        returns = _irregular()
        self.assertEqual(net_sharpe_lower_bound(returns, seed=7),
                         net_sharpe_lower_bound(returns, seed=7))

    def test_the_bound_moves_with_the_seed_which_is_why_it_is_sealed(self):
        returns = _irregular()
        self.assertGreater(len({net_sharpe_lower_bound(returns, seed=seed)
                                for seed in range(8)}), 1)

    def test_a_stricter_confidence_gives_a_more_adverse_bound(self):
        returns = _irregular()
        self.assertLess(net_sharpe_lower_bound(returns, confidence="0.99", seed=3),
                        net_sharpe_lower_bound(returns, confidence="0.90", seed=3))


class RefusalTests(unittest.TestCase):
    def test_a_series_shorter_than_one_block_is_refused(self):
        with self.assertRaises(ValueError):
            bootstrap_bound([0.01, 0.02], sharpe_ratio, side=BOUND_LOWER, block_periods=5)

    def test_an_undefined_statistic_yields_no_bound_rather_than_a_number(self):
        # Every resample of a constant series has no Sharpe; counting those as
        # zero would pull the distribution toward a value the data never produced.
        self.assertIsNone(net_sharpe_lower_bound([0.001] * 200))

    def test_invalid_parameters_are_refused(self):
        returns = _irregular()
        for kwargs in ({"resamples": 0}, {"block_periods": 0}, {"seed": -1},
                       {"confidence": "0.4"}, {"confidence": "1.0"}):
            with self.assertRaises(ValueError):
                net_sharpe_lower_bound(returns, **kwargs)

    def test_it_is_reachable_from_the_facade(self):
        self.assertIsNotNone(p.net_sharpe_lower_bound(_irregular()))


if __name__ == "__main__":
    unittest.main()


class DrawdownSizingTests(unittest.TestCase):
    """Sizing must resolve against the quantity the GATE reads, not a cousin of it.

    CAP-006 resolves against the REALISED drawdown; §8.1 gates on the 95% upper
    bound. On SPY: realised 33.79%, bound 45.79%. CAP-006 returns 40.9%, at which
    the realised drawdown is exactly 15.00% and the bound is 21.31% -- sized,
    reading as compliant, still refused.
    """

    def _series(self):
        return _series([0.004] * 16 + [-0.02] * 4, 400)

    def test_the_weight_it_returns_actually_satisfies_the_bound(self):
        returns = self._series()
        weight = p.weight_within_drawdown_bound(returns, drawdown_limit="0.15")
        self.assertLessEqual(
            drawdown_upper_bound([v * weight for v in returns]), 0.15)

    def test_it_is_stricter_than_sizing_against_the_realised_drawdown(self):
        # The whole point: the bound exceeds the realised figure, so the weight
        # that satisfies the bound is SMALLER than the one CAP-006 returns.
        returns = self._series()
        self.assertLess(p.weight_within_drawdown_bound(returns, drawdown_limit="0.15"),
                        p.maximum_admissible_weight(returns, drawdown_limit="0.15"))

    def test_a_position_already_inside_the_limit_is_not_shrunk(self):
        calm = _series([0.001, 0.0005], 300)
        self.assertEqual(p.weight_within_drawdown_bound(calm, drawdown_limit="0.15"), 1.0)

    def test_invalid_limits_are_refused(self):
        for kwargs in ({"drawdown_limit": "0"}, {"drawdown_limit": "1"},
                       {"drawdown_limit": "0.15", "tolerance": "0"}):
            with self.assertRaises(ValueError):
                p.weight_within_drawdown_bound(self._series(), **kwargs)
