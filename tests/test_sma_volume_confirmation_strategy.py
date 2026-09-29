"""Focused proof for sma_volume_confirmation_strategy: the first COMBINED
Strategy, proposed 2026-09-29 after Nivel 1 of the hypothesis catalog
(period, liquidity, direction) all failed to help. Classifies UPPER only
when BOTH the SMA_CROSSOVER and VOLUME_SURGE conditions hold at once --
tests whether a conjunction of two individually noise-floor signals
carries information neither does alone."""

import math
import unittest

import pipeline as p


def synthetic_rows():
    # Arbitrary, deterministic, no real market data involved. 21 rows so
    # a warmup of max(3-1, 20)=20 leaves exactly one classified row.
    closes = [100.0 + i for i in range(21)]
    volumes = [10.0] * 20 + [50.0]  # last day's volume is a clear surge
    return [{"close": c, "volume": v} for c, v in zip(closes, volumes)]


class SmaVolumeConfirmationStrategyTests(unittest.TestCase):
    def test_contract_shape(self):
        strategy = p.sma_volume_confirmation_strategy(3, 20)
        self.assertEqual(strategy["strategy_id"], "SMA_VOLUME_CONFIRMATION")
        self.assertEqual(strategy["parameters"], {"sma_window": 3, "volume_window": 20})
        self.assertEqual(strategy["required_inputs"],
                         {"variables": ["close", "volume"], "warmup_periods": 20})
        self.assertEqual(strategy["column_name"], "sma_close_3_confirm_vol_20")
        self.assertEqual(strategy["indicator_name"], "SMA3CONFIRM20")
        self.assertEqual(strategy["upper_group_description"],
                         "close_t > SMA3_t AND volume_t > VOLAVG20_t")
        self.assertEqual(strategy["lower_or_equal_group_description"],
                         "NOT (close_t > SMA3_t AND volume_t > VOLAVG20_t)")

    def test_classifies_upper_only_when_both_conditions_hold(self):
        strategy = p.sma_volume_confirmation_strategy(3, 20)
        rows = synthetic_rows()
        classified = p._strategy_classify_rows(strategy, rows)

        for index in range(20):
            self.assertIsNone(classified[index]["group"])
        # close rises monotonically -> close_t > SMA3_t holds; volume surges
        # on the last day -> both conditions hold -> UPPER.
        self.assertEqual(classified[20]["group"], "UPPER")
        expected_sma = math.fsum(rows[i]["close"] / 3 for i in range(18, 21))
        self.assertAlmostEqual(classified[20]["sma_close_3_confirm_vol_20"], expected_sma)

    def test_price_condition_alone_is_not_enough(self):
        strategy = p.sma_volume_confirmation_strategy(3, 20)
        rows = synthetic_rows()
        rows[20]["volume"] = 10.0  # no volume surge this time
        classified = p._strategy_classify_rows(strategy, rows)
        self.assertEqual(classified[20]["group"], "LOWER_OR_EQUAL")

    def test_volume_condition_alone_is_not_enough(self):
        strategy = p.sma_volume_confirmation_strategy(3, 20)
        rows = synthetic_rows()
        rows[20]["close"] = 50.0  # price drops sharply, no longer above its SMA
        classified = p._strategy_classify_rows(strategy, rows)
        self.assertEqual(classified[20]["group"], "LOWER_OR_EQUAL")

    def test_rejects_invalid_windows(self):
        with self.assertRaises(ValueError):
            p.sma_volume_confirmation_strategy(1, 20)
        with self.assertRaises(ValueError):
            p.sma_volume_confirmation_strategy(3, 1)
        with self.assertRaises(ValueError):
            p.sma_volume_confirmation_strategy(True, 20)


if __name__ == "__main__":
    unittest.main()
