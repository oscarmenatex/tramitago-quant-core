"""Focused proof for signal_portfolio_strategy (2026-09-29): the first
Strategy that evaluates a PORTFOLIO of weak signals rather than one signal
alone.

Motivation measured, not assumed: the project's >=70% fold-consistency bar
is a whole-strategy threshold being applied to individual components, and
no genuinely weak signal clears it alone. The eight price variants already
tested are redundant (mean pairwise correlation 0.753, effective breadth
1.28 of 8), but the four structurally different families are diverse
(0.131, effective breadth 2.87 of 4), so combining THEM is the version
worth testing.

The property that matters most here is equivalence: each vote must agree
exactly with the corresponding standalone Strategy's own decision, since
the composite reuses their formulas. If a vote ever diverged, the
composite would silently be testing something other than what it claims.
"""

import unittest

import pipeline as p


def synthetic_rows(n=40):
    """Deterministic, arbitrary, no real market data. Varied enough that
    every component flips both ways across the series."""
    rows = []
    for i in range(n):
        close = 100.0 + (i % 7) - 0.5 * (i % 3)
        spread = 1.0 + (i % 5)
        rows.append({
            "close": close,
            "high": close + spread / 2,
            "low": close - spread / 2,
            "volume": 10.0 + (i % 11),
            "funding_rate": 0.0001 * (1 + (i % 4)) - 0.00015 * (i % 2),
        })
    return rows


class SignalPortfolioStrategyTests(unittest.TestCase):
    def test_contract_shape(self):
        strategy = p.signal_portfolio_strategy(3, 20, 20, 20)
        self.assertEqual(strategy["strategy_id"], "SIGNAL_PORTFOLIO")
        self.assertEqual(strategy["parameters"], {
            "sma_window": 3, "volume_window": 20, "range_window": 20, "funding_window": 20})
        self.assertEqual(strategy["required_inputs"], {
            "variables": ["close", "volume", "high", "low", "funding_rate"],
            "warmup_periods": 21})
        self.assertEqual(strategy["column_name"], "portfolio_votes")

    def test_each_vote_matches_its_standalone_strategy_exactly(self):
        """The composite must not silently diverge from the components it
        claims to be combining."""
        rows = synthetic_rows()
        composite = p.signal_portfolio_strategy(3, 20, 20, 20)
        parts = {
            "sma": p.sma_crossover_strategy(3),
            "volume": p.volume_surge_strategy(20),
            "range": p.intraday_range_strategy(20),
            "funding": p.funding_rate_surge_strategy(20),
        }
        classified = {name: p._strategy_classify_rows(s, rows) for name, s in parts.items()}
        composite_rows = p._strategy_classify_rows(composite, rows)

        checked = 0
        for index, row in enumerate(composite_rows):
            if row["group"] is None:
                continue
            expected_votes = sum(
                1 for name in parts if classified[name][index]["group"] == "UPPER")
            self.assertEqual(row["portfolio_votes"], float(expected_votes),
                             f"vote count diverged at index {index}")
            self.assertEqual(row["group"], "UPPER" if expected_votes >= 3 else "LOWER_OR_EQUAL")
            checked += 1
        self.assertGreater(checked, 10, "not enough classified rows to be meaningful")

    def test_majority_rule_and_tie_direction(self):
        strategy = p.signal_portfolio_strategy(3, 20, 20, 20)
        rows = synthetic_rows()
        classified = [r for r in p._strategy_classify_rows(strategy, rows)
                      if r["group"] is not None]
        for row in classified:
            votes = row["portfolio_votes"]
            self.assertIn(votes, (0.0, 1.0, 2.0, 3.0, 4.0))
            # ties (2 of 4) must fall to LOWER_OR_EQUAL, matching the ">" convention
            if votes == 2.0:
                self.assertEqual(row["group"], "LOWER_OR_EQUAL")
            self.assertEqual(row["group"], "UPPER" if votes >= 3 else "LOWER_OR_EQUAL")

    def test_funding_vote_keeps_the_lag_1_convention(self):
        """The funding component must read yesterday, never today -- the
        no-lookahead convention inherited from FUNDING_RATE_SIGN."""
        strategy = p.signal_portfolio_strategy(3, 20, 20, 20)
        base = synthetic_rows()
        moved = [dict(r) for r in base]
        moved[-1]["funding_rate"] = 99.0  # an absurd value on TODAY only
        self.assertEqual(
            p._strategy_classify_rows(strategy, base)[-1]["portfolio_votes"],
            p._strategy_classify_rows(strategy, moved)[-1]["portfolio_votes"])

    def test_rejects_invalid_windows(self):
        for args in ((1, 20, 20, 20), (3, 1, 20, 20), (3, 20, 1, 20), (3, 20, 20, 1)):
            with self.assertRaises(ValueError):
                p.signal_portfolio_strategy(*args)


if __name__ == "__main__":
    unittest.main()
