"""M4.1 proof of concept: a Strategy contract agnostic to how any given
strategy computes its signal.

Demonstrates two things, without touching any M2.x production function or
real committed research data:
  1. sma_crossover_strategy(3) run through the generic classifier reproduces
     exactly the same indicator values and UPPER/LOWER_OR_EQUAL grouping
     that pipeline.py's real production code (`indicators()` and
     `_experiment_result_dataset_rows`'s independent recomputation) already
     produce today -- proving the contract is a faithful generalization,
     not a different behavior.
  2. momentum_crossover_strategy (a structurally different strategy: no
     averaging, a single lagged close) runs through the SAME generic
     classifier with zero code changes -- proving the rest of the system
     never needs to know which strategy is in use.
"""

import math
import unittest

import pipeline as p


def synthetic_closes():
    # Arbitrary, deterministic, no real market data involved.
    return [100.0, 102.0, 101.0, 105.0, 103.0, 98.0, 99.0, 108.0, 110.0, 107.0,
            106.0, 111.0, 109.0, 104.0, 112.0]


def rows_from_closes(closes):
    return [{"close": value} for value in closes]


class M41StrategyContractTests(unittest.TestCase):
    def test_sma_crossover_matches_production_indicators_function(self):
        """sma_crossover_strategy(3) must reproduce indicators()'s own math
        exactly -- same warmup, same values, same finite-number guarantee."""
        closes = synthetic_closes()
        rows = [{"close": c, "timestamp": f"T{i}", "instrument": "BTC-USD",
                 "open": c, "high": c, "low": c, "volume": 1.0}
                for i, c in enumerate(closes)]

        production = p.indicators(rows)
        strategy = p.sma_crossover_strategy(3)
        classified = p._strategy_classify_rows(strategy, rows_from_closes(closes))

        self.assertEqual(strategy["required_inputs"], {"variables": ["close"], "warmup_periods": 2})
        self.assertEqual(strategy["column_name"], "sma_close_3")

        for prod_row, gen_row in zip(production, classified):
            self.assertEqual(prod_row["sma_close_3"], gen_row["sma_close_3"])

    def test_sma_crossover_grouping_matches_production_result_classification(self):
        """Production code classifies UPPER/LOWER_OR_EQUAL as
        close_t > sma (see _experiment_result_dataset_rows). The generic
        classifier must agree exactly, row for row."""
        closes = synthetic_closes()
        strategy = p.sma_crossover_strategy(3)
        classified = p._strategy_classify_rows(strategy, rows_from_closes(closes))

        for index, row in enumerate(classified):
            if index < 2:
                self.assertIsNone(row["group"])
                continue
            expected_sma = math.fsum(c / 3 for c in closes[index - 2:index + 1])
            expected_group = "UPPER" if closes[index] > expected_sma else "LOWER_OR_EQUAL"
            self.assertEqual(row["group"], expected_group)

    def test_a_structurally_different_strategy_needs_no_new_classifier_code(self):
        """momentum_crossover_strategy computes its signal completely
        differently (a single lagged close, no averaging) but plugs into
        the exact same _strategy_classify_rows used above."""
        closes = synthetic_closes()
        strategy = p.momentum_crossover_strategy(5)
        classified = p._strategy_classify_rows(strategy, rows_from_closes(closes))

        self.assertEqual(strategy["required_inputs"], {"variables": ["close"], "warmup_periods": 5})
        self.assertEqual(strategy["column_name"], "close_lag_5")

        for index, row in enumerate(classified):
            if index < 5:
                self.assertIsNone(row["group"])
                self.assertIsNone(row["close_lag_5"])
                continue
            lagged = closes[index - 5]
            self.assertEqual(row["close_lag_5"], lagged)
            expected_group = "UPPER" if closes[index] > lagged else "LOWER_OR_EQUAL"
            self.assertEqual(row["group"], expected_group)

    def test_classifier_never_reads_strategy_id_or_parameters(self):
        """Structural proof of agnosticism: strip strategy_id/parameters
        after construction and the classifier still works identically,
        since it only ever reads required_inputs and compute()."""
        closes = synthetic_closes()
        strategy = p.sma_crossover_strategy(3)
        stripped = {k: v for k, v in strategy.items() if k not in ("strategy_id", "parameters")}

        with_full = p._strategy_classify_rows(strategy, rows_from_closes(closes))
        with_stripped = p._strategy_classify_rows(stripped, rows_from_closes(closes))
        self.assertEqual(with_full, with_stripped)

    def test_rejects_invalid_strategy_parameters(self):
        with self.assertRaises(ValueError):
            p.sma_crossover_strategy(1)
        with self.assertRaises(ValueError):
            p.sma_crossover_strategy(True)
        with self.assertRaises(ValueError):
            p.momentum_crossover_strategy(0)
        with self.assertRaises(ValueError):
            p.momentum_crossover_strategy(-1)


if __name__ == "__main__":
    unittest.main()
