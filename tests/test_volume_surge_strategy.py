"""Focused proof for volume_surge_strategy: a third Strategy family,
structurally unrelated to SMA_CROSSOVER/MOMENTUM_CROSSOVER, proposed
2026-09-28 after 9 real price-lag variants all showed a full-sample effect
an order of magnitude smaller than the instrument's own daily volatility.
Classifies by trailing average VOLUME, never by `close`."""

import math
import unittest

import pipeline as p


def synthetic_volumes():
    # Arbitrary, deterministic, no real market data involved.
    return [10.0, 12.0, 9.0, 30.0, 11.0, 8.0, 9.5, 40.0, 15.0, 10.0,
            9.0, 11.0, 50.0, 8.5, 9.0]


def rows_from_volumes(volumes):
    return [{"volume": value, "close": 100.0} for value in volumes]


class VolumeSurgeStrategyTests(unittest.TestCase):
    def test_contract_shape(self):
        strategy = p.volume_surge_strategy(5)
        self.assertEqual(strategy["strategy_id"], "VOLUME_SURGE")
        self.assertEqual(strategy["parameters"], {"window": 5})
        self.assertEqual(strategy["required_inputs"], {"variables": ["volume"], "warmup_periods": 5})
        self.assertEqual(strategy["column_name"], "volume_avg_5")
        self.assertEqual(strategy["indicator_name"], "VOLSURGE5")
        self.assertEqual(strategy["upper_group_description"], "volume_t > VOLSURGE5_t")
        self.assertEqual(strategy["lower_or_equal_group_description"], "volume_t <= VOLSURGE5_t")

    def test_classifies_by_trailing_average_volume_not_by_close(self):
        volumes = synthetic_volumes()
        strategy = p.volume_surge_strategy(5)
        classified = p._strategy_classify_rows(strategy, rows_from_volumes(volumes))

        for index, row in enumerate(classified):
            if index < 5:
                self.assertIsNone(row["group"])
                self.assertIsNone(row["volume_avg_5"])
                continue
            expected_avg = math.fsum(volumes[index - 5:index]) / 5
            expected_group = "UPPER" if volumes[index] > expected_avg else "LOWER_OR_EQUAL"
            self.assertAlmostEqual(row["volume_avg_5"], expected_avg)
            self.assertEqual(row["group"], expected_group)

    def test_rejects_invalid_window(self):
        with self.assertRaises(ValueError):
            p.volume_surge_strategy(1)
        with self.assertRaises(ValueError):
            p.volume_surge_strategy(True)


if __name__ == "__main__":
    unittest.main()
