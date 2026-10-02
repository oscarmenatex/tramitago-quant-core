"""The carry exit rule with a RELATIVE threshold.

The absolute version classifies by whether funding is at or above ZERO, and the
"premium inverted" group holds 11 of 366 days in 2024 and 18 of 365 in 2025 --
one fold a year contains none of them, so the validation can only ever answer
INSUFFICIENT_EVIDENCE. funding_rate_surge_strategy documented that exact failure
for the funding SIGNAL and fixed it by comparing the series to its own history.
These cover carrying that fix across, and the one property that makes it worth
carrying: the split stops being degenerate.
"""

import unittest

import pipeline as p
from tramitago_quant_core.strategy_contract.strategy import (
    carry_funding_surge_strategy, carry_funding_threshold_strategy, _strategy_classify_rows,
)
from tramitago_quant_core.research.experiment import _EXPERIMENT_STRATEGY_CONSTRUCTORS

DAY = 86400


def _rows(fundings, start="2024-01-01T00:00:00Z"):
    """A series whose funding is almost always positive -- the structural fact
    that makes the absolute threshold degenerate."""
    base = p.epoch(start)
    return [{"timestamp": p.iso(base + index * DAY), "close": 100.0 + index,
             "perp_close": 100.5 + index, "funding_rate": value}
            for index, value in enumerate(fundings)]


def _split(strategy, rows):
    classified = _strategy_classify_rows(strategy, rows)
    groups = [row["group"] for row in classified if row["group"] is not None]
    return groups.count("UPPER"), groups.count("LOWER_OR_EQUAL")


# 95% positive, which is what BTC funding looks like.
_BIASED = [0.00001 if index % 20 else -0.00002 for index in range(200)]


class ClassBalanceTests(unittest.TestCase):
    """The whole reason this Strategy exists."""

    def test_the_absolute_threshold_is_degenerate_on_a_biased_series(self):
        upper, lower = _split(carry_funding_threshold_strategy(), _rows(_BIASED))
        self.assertLess(lower / (upper + lower), 0.10)

    def test_the_relative_threshold_is_not(self):
        upper, lower = _split(carry_funding_surge_strategy(7), _rows(_BIASED))
        self.assertGreater(min(upper, lower) / (upper + lower), 0.20)

    def test_more_data_does_not_rescue_the_absolute_one(self):
        # The split stays degenerate at any length: it is a property of the
        # series, not of the sample size.
        short = _split(carry_funding_threshold_strategy(), _rows(_BIASED[:60]))
        long = _split(carry_funding_threshold_strategy(), _rows(_BIASED * 5))
        self.assertAlmostEqual(short[1] / sum(short), long[1] / sum(long), places=1)


class ContractTests(unittest.TestCase):
    def test_it_declares_the_carry_outcome_not_a_price_change(self):
        strategy = carry_funding_surge_strategy(7)
        self.assertEqual(strategy["outcome"]["outcome_id"], "CARRY_RETURN")

    def test_the_payment_cadence_reaches_the_outcome_not_the_signal(self):
        rows = _rows(_BIASED)
        self.assertEqual(_split(carry_funding_surge_strategy(7), rows),
                         _split(carry_funding_surge_strategy(7, payments_per_period=24), rows))
        self.assertEqual(
            carry_funding_surge_strategy(7, payments_per_period=24)["outcome"]["parameters"]
            ["payments_per_period"], 24)

    def test_the_sealed_machinery_can_rebuild_it(self):
        strategy = carry_funding_surge_strategy(7, payments_per_period=24)
        rebuilt = _EXPERIMENT_STRATEGY_CONSTRUCTORS[strategy["strategy_id"]](
            strategy["parameters"])
        self.assertEqual(rebuilt["parameters"], strategy["parameters"])
        self.assertEqual(rebuilt["column_name"], strategy["column_name"])

    def test_the_default_cadence_stays_out_of_the_parameters(self):
        self.assertNotIn("payments_per_period", carry_funding_surge_strategy(7)["parameters"])

    def test_the_signal_reads_yesterday_against_the_days_before_it(self):
        # No lookahead: window+2 rows, the last is t and is never read.
        strategy = carry_funding_surge_strategy(3)
        rows = _rows([0.001, 0.001, 0.001, 0.009, 0.0])
        self.assertEqual(strategy["compute"](rows)["group"], "UPPER")
        self.assertAlmostEqual(strategy["compute"](rows)["indicator_value"], 0.001)

    def test_a_window_below_two_is_refused(self):
        for window in (1, 0, -3, True, 2.5):
            with self.assertRaises(ValueError):
                carry_funding_surge_strategy(window)


if __name__ == "__main__":
    unittest.main()
