"""Monitors as data: how a trigger becomes daily flags, and how the link is tested.

Twenty-three research runners each carried their own copy of this logic and none had
a test. These are the properties that matter, pinned once.
"""

import math
import unittest

from tramitago_quant_core.research.monitors import (
    evaluate_monitor, fold_bounds, link_consistency, monitor_series_names,
    needs_underlying, KIND_NONE, KIND_LEVEL_BELOW, KIND_DIFFERENCE_BELOW,
    KIND_IMPLIED_MINUS_REALISED,
)

DAYS = [f"2024-01-{day:02d}" for day in range(1, 11)]


class KindTests(unittest.TestCase):
    def test_none_yields_no_monitor(self):
        self.assertIsNone(evaluate_monitor({"kind": KIND_NONE}, DAYS, {}))
        self.assertIsNone(evaluate_monitor({}, DAYS, {}))

    def test_an_unknown_kind_is_refused_rather_than_ignored(self):
        with self.assertRaises(ValueError):
            evaluate_monitor({"kind": "vibes", "floor": "0"}, DAYS, {})
        with self.assertRaises(ValueError):
            monitor_series_names({"kind": "vibes"})

    def test_a_level_fires_at_or_below_its_floor(self):
        series = {"S": {day: v for day, v in zip(DAYS, [2, 1, 0.5, 0.4, 3, 3, 3, 3, 3, 3])}}
        result = evaluate_monitor({"kind": KIND_LEVEL_BELOW, "series": "S", "floor": "0.5"},
                                  DAYS, series)
        self.assertEqual(result["flags"], [0, 0, 1, 1, 0, 0, 0, 0, 0, 0])
        self.assertEqual(result["trigger_days"], 2)

    def test_the_floor_itself_is_a_trigger(self):
        # At or below, not strictly below: the variable reaching the level where the
        # premium stops compensating IS the event.
        series = {"S": {DAYS[0]: 1.0}}
        flags = evaluate_monitor({"kind": KIND_LEVEL_BELOW, "series": "S", "floor": "1.0"},
                                 DAYS[:1], series)["flags"]
        self.assertEqual(flags, [1])

    def test_a_difference_fires_when_far_minus_near_reaches_the_floor(self):
        near = {day: 20.0 for day in DAYS}
        far = {day: (19.0 if i in (2, 3) else 22.0) for i, day in enumerate(DAYS)}
        result = evaluate_monitor(
            {"kind": KIND_DIFFERENCE_BELOW, "far": "FAR", "near": "NEAR", "floor": "0"},
            DAYS, {"FAR": far, "NEAR": near})
        self.assertEqual(result["flags"], [0, 0, 1, 1, 0, 0, 0, 0, 0, 0])

    def test_the_series_a_spec_reads_are_named_up_front(self):
        self.assertEqual(monitor_series_names(
            {"kind": KIND_DIFFERENCE_BELOW, "far": "FAR", "near": "NEAR"}), ["FAR", "NEAR"])
        self.assertEqual(monitor_series_names({"kind": KIND_NONE}), [])

    def test_only_the_realised_kind_needs_prices(self):
        self.assertTrue(needs_underlying({"kind": KIND_IMPLIED_MINUS_REALISED}))
        self.assertFalse(needs_underlying({"kind": KIND_LEVEL_BELOW}))


class MissingObservationTests(unittest.TestCase):
    """A missing day is not a day the monitor fired. Counting it as one would let a
    gappy series read as a monitor that fires often."""

    def test_a_missing_day_is_not_a_trigger_and_is_counted_apart(self):
        series = {"S": {DAYS[0]: 0.1, DAYS[1]: None, DAYS[2]: 0.1}}
        result = evaluate_monitor({"kind": KIND_LEVEL_BELOW, "series": "S", "floor": "0.5"},
                                  DAYS[:4], series)
        self.assertEqual(result["flags"], [1, 0, 1, 0])
        self.assertEqual(result["missing_days"], 2)
        self.assertEqual(result["observed_days"], 2)
        self.assertEqual(result["trigger_frequency"], 1.0)

    def test_a_difference_needs_both_legs(self):
        far = {DAYS[0]: 1.0, DAYS[1]: None}
        near = {DAYS[0]: None, DAYS[1]: 5.0}
        result = evaluate_monitor(
            {"kind": KIND_DIFFERENCE_BELOW, "far": "FAR", "near": "NEAR", "floor": "0"},
            DAYS[:2], {"FAR": far, "NEAR": near})
        self.assertEqual(result["flags"], [0, 0])
        self.assertEqual(result["observed_days"], 0)
        self.assertIsNone(result["trigger_frequency"])


class RealisedVolatilityTests(unittest.TestCase):
    """The variance premium measured against itself: implied minus realised."""

    def _prices(self, daily_move):
        days, price, out = [f"2024-02-{d:02d}" for d in range(1, 29)], 100.0, {}
        for i, day in enumerate(days):
            price *= math.exp(daily_move if i % 2 else -daily_move)
            out[day] = price
        return days, out

    def test_it_fires_when_realised_volatility_catches_the_implied(self):
        days, closes = self._prices(0.02)          # about 32 vol points annualised
        implied = {day: 15.0 for day in days}      # the market charges far less
        result = evaluate_monitor(
            {"kind": KIND_IMPLIED_MINUS_REALISED, "implied": "IV", "window": 5, "floor": "0"},
            days, {"IV": implied}, underlying=closes)
        self.assertGreater(result["trigger_days"], 0)

    def test_it_stays_quiet_when_implied_dwarfs_realised(self):
        days, closes = self._prices(0.002)
        implied = {day: 40.0 for day in days}
        result = evaluate_monitor(
            {"kind": KIND_IMPLIED_MINUS_REALISED, "implied": "IV", "window": 5, "floor": "0"},
            days, {"IV": implied}, underlying=closes)
        self.assertEqual(result["trigger_days"], 0)

    def test_the_first_window_has_no_value_and_is_missing_not_quiet(self):
        days, closes = self._prices(0.01)
        result = evaluate_monitor(
            {"kind": KIND_IMPLIED_MINUS_REALISED, "implied": "IV", "window": 21, "floor": "0"},
            days, {"IV": {day: 20.0 for day in days}}, underlying=closes)
        self.assertGreaterEqual(result["missing_days"], 21)

    def test_it_refuses_to_run_without_prices(self):
        with self.assertRaises(ValueError):
            evaluate_monitor({"kind": KIND_IMPLIED_MINUS_REALISED, "implied": "IV",
                              "floor": "0"}, DAYS, {"IV": {}})


class LinkConsistencyTests(unittest.TestCase):
    def test_the_folds_match_the_level_claims_split(self):
        # The same partition the claim is judged on, never one chosen to flatter the link.
        self.assertEqual(fold_bounds(10, 3), [(0, 3), (3, 6), (6, 10)])

    def test_a_fold_counts_when_triggered_days_are_worse_than_the_rest(self):
        returns = [-0.02] * 10 + [0.01] * 10
        flags = [1] * 10 + [0] * 10
        self.assertEqual(link_consistency([(0, 20)], returns, flags, minimum_days=10), (1, 1))

    def test_a_fold_where_triggered_days_were_better_does_not_count(self):
        returns = [0.03] * 10 + [0.0] * 10
        flags = [1] * 10 + [0] * 10
        self.assertEqual(link_consistency([(0, 20)], returns, flags, minimum_days=10), (0, 1))

    def test_a_fold_with_too_few_triggered_days_is_unusable_not_failed(self):
        # It could not be measured, and counting it against the link would let a
        # sparse trigger refute itself.
        returns = [-0.5] * 3 + [0.01] * 17
        flags = [1] * 3 + [0] * 17
        self.assertEqual(link_consistency([(0, 20)], returns, flags, minimum_days=10), (0, 0))

    def test_a_fold_with_no_quiet_days_is_unusable(self):
        self.assertEqual(
            link_consistency([(0, 12)], [0.01] * 12, [1] * 12, minimum_days=10), (0, 0))

    def test_none_returns_are_skipped_not_treated_as_zero(self):
        returns = [-0.02] * 10 + [None] * 5 + [0.01] * 10
        flags = [1] * 10 + [1] * 5 + [0] * 10
        self.assertEqual(link_consistency([(0, 25)], returns, flags, minimum_days=10), (1, 1))


if __name__ == "__main__":
    unittest.main()
