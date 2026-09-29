"""Focused proof for intraday_range_strategy: the sixth Strategy family and
the first to read `high`/`low` at all. Every Strategy before it used only
`close`, `volume` or `funding_rate`, leaving half the OHLCV record
untouched. Classifies by whether a day's own close-normalized intraday
range exceeds its trailing average -- a volatility-expansion hypothesis,
never direction and never activity level."""

import math
import unittest

import pipeline as p


def rows_with_ranges(spreads, close=100.0):
    """Each row has the same close, so (high - low) / close is exactly
    spread / close -- makes the expected indicator hand-checkable."""
    return [{"high": close + spread / 2, "low": close - spread / 2,
             "close": close, "volume": 10.0} for spread in spreads]


class IntradayRangeStrategyTests(unittest.TestCase):
    def test_contract_shape(self):
        strategy = p.intraday_range_strategy(20)
        self.assertEqual(strategy["strategy_id"], "INTRADAY_RANGE")
        self.assertEqual(strategy["parameters"], {"window": 20})
        self.assertEqual(strategy["required_inputs"],
                         {"variables": ["high", "low", "close"], "warmup_periods": 20})
        self.assertEqual(strategy["column_name"], "range_avg_20")
        self.assertEqual(strategy["indicator_name"], "RANGE20")
        self.assertEqual(strategy["upper_group_description"],
                         "(high_t - low_t) / close_t > RANGE20_t")
        self.assertEqual(strategy["lower_or_equal_group_description"],
                         "(high_t - low_t) / close_t <= RANGE20_t")

    def test_classifies_by_range_expansion_not_by_close(self):
        spreads = [2.0] * 5 + [10.0]  # calm days, then one wide-range day
        strategy = p.intraday_range_strategy(5)
        classified = p._strategy_classify_rows(strategy, rows_with_ranges(spreads))

        for index in range(5):
            self.assertIsNone(classified[index]["group"])
            self.assertIsNone(classified[index]["range_avg_5"])
        self.assertAlmostEqual(classified[5]["range_avg_5"], 2.0 / 100.0)
        self.assertEqual(classified[5]["group"], "UPPER")

    def test_narrow_day_after_wide_days_is_lower(self):
        spreads = [10.0] * 5 + [1.0]
        strategy = p.intraday_range_strategy(5)
        classified = p._strategy_classify_rows(strategy, rows_with_ranges(spreads))
        self.assertAlmostEqual(classified[5]["range_avg_5"], 10.0 / 100.0)
        self.assertEqual(classified[5]["group"], "LOWER_OR_EQUAL")

    def test_normalization_makes_the_indicator_price_level_independent(self):
        """The same relative range at two very different price levels must
        produce the same indicator value -- this is why the raw high-low
        spread is divided by close."""
        strategy = p.intraday_range_strategy(3)
        cheap = p._strategy_classify_rows(strategy, rows_with_ranges([2.0] * 4, close=100.0))
        expensive = p._strategy_classify_rows(strategy, rows_with_ranges([200.0] * 4, close=10000.0))
        self.assertAlmostEqual(cheap[3]["range_avg_3"], expensive[3]["range_avg_3"])

    def test_rejects_invalid_window(self):
        with self.assertRaises(ValueError):
            p.intraday_range_strategy(1)
        with self.assertRaises(ValueError):
            p.intraday_range_strategy(True)


if __name__ == "__main__":
    unittest.main()
