"""CAP-006 Portfolio: how much of a thing fits.

Vía 7.D was closed on a carry whose worst fold drew down 15.77% against a 15%
limit -- an excess of 0.77 points, with the position at ONE HUNDRED PERCENT of
capital. The limit is a portfolio limit and it was applied to a position, because
the platform could not express the difference.
"""

import math
import unittest

import pipeline as p
from tramitago_quant_core.risk.portfolio import (
    portfolio_allocation, verified_portfolio_allocation, portfolio_returns,
    portfolio_drawdown, maximum_admissible_weight, allocation_summary,
)


def _allocation(weights=None, cash="0", source="declared for this test"):
    # `is None`, not a truth test: an EMPTY allocation is one of the things being
    # refused, and `weights or default` would quietly substitute the default for it.
    return portfolio_allocation(weights={"carry": "0.30"} if weights is None else weights,
                                cash_return_per_period=cash, source=source)


def _series(pattern, count):
    return [pattern[index % len(pattern)] for index in range(count)]


class DeclarationTests(unittest.TestCase):
    def test_leverage_is_refused_rather_than_inferred(self):
        with self.assertRaises(ValueError) as caught:
            _allocation({"a": "0.7", "b": "0.5"})
        self.assertIn("leverage", str(caught.exception))

    def test_the_remainder_is_cash_and_is_recorded(self):
        allocation = _allocation({"carry": "0.30"})
        self.assertEqual(float(allocation["cash_weight"]), 0.70)

    def test_an_allocation_must_say_where_its_weights_came_from(self):
        with self.assertRaises(ValueError):
            _allocation(source="")

    def test_a_tampered_allocation_fails_verification(self):
        allocation = dict(_allocation())
        allocation["weights"] = {"carry": "0.90"}
        with self.assertRaises(ValueError):
            verified_portfolio_allocation(allocation)

    def test_invalid_weights_are_refused(self):
        for weights in ({}, {"a": "1.5"}, {"a": "-0.1"}, {"": "0.5"}, {"a": 0.5}):
            with self.assertRaises(ValueError):
                _allocation(weights)


class CombinationTests(unittest.TestCase):
    def test_a_position_at_a_weight_scales_its_returns(self):
        allocation = _allocation({"carry": "0.30"})
        returns = portfolio_returns(allocation, {"carry": [0.01, -0.02]})
        self.assertAlmostEqual(returns[0], 0.003)
        self.assertAlmostEqual(returns[1], -0.006)

    def test_cash_earns_its_declared_rate_on_its_own_weight(self):
        allocation = _allocation({"carry": "0.50"}, cash="0.0001")
        returns = portfolio_returns(allocation, {"carry": [0.0]})
        self.assertAlmostEqual(returns[0], 0.00005)

    def test_an_allocation_and_its_series_must_correspond(self):
        allocation = _allocation({"carry": "0.30"})
        for supplied in ({"other": [0.01]}, {"carry": [0.01], "extra": [0.01]}):
            with self.assertRaises(ValueError):
                portfolio_returns(allocation, supplied)

    def test_misaligned_series_are_refused(self):
        allocation = _allocation({"a": "0.3", "b": "0.3"})
        with self.assertRaises(ValueError):
            portfolio_returns(allocation, {"a": [0.01, 0.01], "b": [0.01]})


class DrawdownTests(unittest.TestCase):
    """The one thing this module refuses to let anyone do is add drawdowns."""

    def test_losses_at_the_same_time_compound(self):
        allocation = _allocation({"a": "0.5", "b": "0.5"})
        together = portfolio_drawdown(portfolio_returns(
            allocation, {"a": [-0.10, 0.0], "b": [-0.10, 0.0]}))
        self.assertAlmostEqual(together, 0.10)

    def test_losses_at_different_times_partly_cancel(self):
        allocation = _allocation({"a": "0.5", "b": "0.5"})
        offset = portfolio_drawdown(portfolio_returns(
            allocation, {"a": [-0.10, 0.10], "b": [0.10, -0.10]}))
        self.assertLess(offset, 0.05)

    def test_the_sum_of_position_drawdowns_is_not_the_portfolio_drawdown(self):
        allocation = _allocation({"a": "0.5", "b": "0.5"})
        series = {"a": [-0.10, 0.10], "b": [0.10, -0.10]}
        summary = allocation_summary(allocation, series)
        self.assertLess(float(summary["portfolio_drawdown"]),
                        float(summary["sum_of_position_drawdowns"]))
        self.assertIn("NOT the sum", summary["note"])

    def test_a_drawdown_needs_returns(self):
        with self.assertRaises(ValueError):
            portfolio_drawdown([])


class CapacityTests(unittest.TestCase):
    def test_a_position_within_the_limit_at_full_weight_fits_entirely(self):
        self.assertEqual(
            maximum_admissible_weight(_series([0.001, -0.001], 100), drawdown_limit="0.15"),
            1.0)

    def test_a_position_that_breaches_the_limit_gets_a_smaller_weight(self):
        series = [0.004] * 20 + [-0.05] * 6
        weight = maximum_admissible_weight(series, drawdown_limit="0.15")
        self.assertLess(weight, 1.0)
        self.assertGreater(weight, 0.0)

    def test_the_weight_it_returns_actually_respects_the_limit(self):
        series = [0.004] * 20 + [-0.05] * 6
        weight = maximum_admissible_weight(series, drawdown_limit="0.15")
        allocation = _allocation({"position": f"{weight:.9f}"})
        self.assertLessEqual(
            portfolio_drawdown(portfolio_returns(allocation, {"position": series})), 0.15)

    def test_a_larger_limit_admits_a_larger_weight(self):
        series = [0.004] * 20 + [-0.05] * 6
        self.assertGreater(maximum_admissible_weight(series, drawdown_limit="0.30"),
                           maximum_admissible_weight(series, drawdown_limit="0.10"))

    def test_bisection_not_division_because_cash_earns_something(self):
        # With a non-zero cash return the portfolio drawdown is not proportional
        # to the weight, so limit / position_drawdown would be wrong.
        series = [0.004] * 20 + [-0.05] * 6
        plain = maximum_admissible_weight(series, drawdown_limit="0.15")
        paying = maximum_admissible_weight(series, drawdown_limit="0.15",
                                           cash_return_per_period="0.0005")
        self.assertNotEqual(plain, paying)

    def test_an_invalid_limit_is_refused(self):
        for limit in ("0", "1", "1.5", "-0.1"):
            with self.assertRaises(ValueError):
                maximum_admissible_weight([0.01], drawdown_limit=limit)


class CarryTests(unittest.TestCase):
    """The question the closure of vía 7.D could not ask."""

    def test_the_carry_at_full_weight_breaches_and_at_a_third_does_not(self):
        # 15.77% at 100% of capital, against a declared 15% limit.
        series = _series([0.004] * 30 + [-0.17], 120)
        full = portfolio_drawdown(portfolio_returns(
            _allocation({"carry": "1"}), {"carry": series}))
        third = portfolio_drawdown(portfolio_returns(
            _allocation({"carry": "0.33"}), {"carry": series}))
        self.assertGreater(full, 0.15)
        self.assertLess(third, 0.15)

    def test_scaling_a_position_does_not_change_the_sign_of_any_period(self):
        # Which is why a weight can rescue a drawdown gate and can never rescue a
        # consistency one: scaling preserves every sign.
        series = _series([0.004, -0.002, 0.001], 60)
        scaled = portfolio_returns(_allocation({"carry": "0.25"}), {"carry": series})
        self.assertTrue(all(math.copysign(1, a) == math.copysign(1, b)
                            for a, b in zip(series, scaled) if a))


if __name__ == "__main__":
    unittest.main()
