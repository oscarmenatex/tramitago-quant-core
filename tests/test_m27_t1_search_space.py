"""Focused proof for M2.7-T1: the bounded, pre-justified search space for
autonomous hypothesis generation (Etapa 2.7, R-2.7-001)."""

import unittest

import pipeline as p


class M27T1SearchSpaceTests(unittest.TestCase):
    def test_search_space_is_exactly_eight_strategies(self):
        strategies = p.hypothesis_generation_search_space()
        self.assertEqual(len(strategies), 8)

    def test_search_space_is_the_declared_sma_and_momentum_families(self):
        strategies = p.hypothesis_generation_search_space()
        sma = [s for s in strategies if s["strategy_id"] == "SMA_CROSSOVER"]
        momentum = [s for s in strategies if s["strategy_id"] == "MOMENTUM_CROSSOVER"]
        self.assertEqual(len(sma), 4)
        self.assertEqual(len(momentum), 4)
        self.assertEqual(sorted(s["parameters"]["window"] for s in sma), [3, 5, 7, 10])
        self.assertEqual(sorted(s["parameters"]["lookback"] for s in momentum), [3, 5, 7, 10])

    def test_search_space_has_no_duplicate_strategies(self):
        strategies = p.hypothesis_generation_search_space()
        identities = [(s["strategy_id"], tuple(sorted(s["parameters"].items())))
                     for s in strategies]
        self.assertEqual(len(identities), len(set(identities)))

    def test_search_space_is_deterministic_across_calls(self):
        first = p.hypothesis_generation_search_space()
        second = p.hypothesis_generation_search_space()
        first_identity = [(s["strategy_id"], s["parameters"], s["column_name"],
                          s["required_inputs"]) for s in first]
        second_identity = [(s["strategy_id"], s["parameters"], s["column_name"],
                           s["required_inputs"]) for s in second]
        self.assertEqual(first_identity, second_identity)

    def test_declared_instrument_is_the_single_already_investigated_default(self):
        self.assertEqual(p.HYPOTHESIS_GENERATION_INSTRUMENT, "BTC-USD")


if __name__ == "__main__":
    unittest.main()
