"""The carry exit rule, expressed as something the Core can judge.

The destination is harvesting risk premia, whose defining clause is "stop when
the evidence stops supporting it". That exit rule lives today as a DECLARED
threshold in a monitoring contract -- a conjecture nobody tested. These cover
the Strategy and Outcome that turn it into a Hypothesis, and in particular the
two properties that decide whether the result would mean anything: the return is
a cash flow PLUS a spread mark, and the signal is not inside its own outcome.
"""

import unittest

import pipeline as p
from tramitago_quant_core.strategy_contract.outcome import carry_return_outcome
from tramitago_quant_core.strategy_contract.strategy import carry_funding_threshold_strategy


def _row(close, perp, funding):
    return {"close": close, "perp_close": perp, "funding_rate": funding}


class CarryOutcomeTests(unittest.TestCase):
    def test_the_return_is_funding_received_minus_the_basis_move(self):
        outcome = carry_return_outcome()
        # basis_t = 0, basis_t+1 = +1%. Funding over the interval = +0.03%.
        row = _row(100.0, 100.0, 0.0)
        future = _row(100.0, 101.0, 0.0003)
        self.assertAlmostEqual(outcome["compute"](row, future), 0.0003 - 0.01)

    def test_a_short_perpetual_gains_when_the_basis_narrows(self):
        # The perpetual falling toward spot is a liquidation cascade, and it is
        # FAVOURABLE to a carry. Measuring only funding would miss this entirely.
        outcome = carry_return_outcome()
        row = _row(100.0, 102.0, 0.0)
        future = _row(100.0, 100.0, 0.0)
        self.assertAlmostEqual(outcome["compute"](row, future), 0.02)

    def test_funding_alone_would_describe_a_position_that_cannot_lose(self):
        outcome = carry_return_outcome()
        row, future = _row(100.0, 100.0, 0.0), _row(100.0, 105.0, 0.0003)
        self.assertLess(outcome["compute"](row, future), 0)   # the spread mark dominates

    def test_a_non_positive_spot_is_refused(self):
        outcome = carry_return_outcome()
        with self.assertRaises(ValueError):
            outcome["compute"](_row(0.0, 100.0, 0.0), _row(100.0, 100.0, 0.0))

    def test_the_outcome_declares_the_three_variables_it_reads(self):
        outcome = carry_return_outcome()
        self.assertEqual(set(outcome["required_inputs"]["variables"]),
                         {"close", "funding_rate", "perp_close"})
        self.assertEqual(outcome["column"](1), "forward_carry_return_1d")


class NoLookaheadTests(unittest.TestCase):
    """The property that decides whether a positive result would mean anything."""

    def test_the_signal_reads_today_and_the_outcome_reads_tomorrow(self):
        # Taking both from the same row would put the signal inside its own
        # outcome and manufacture the result.
        strategy = carry_funding_threshold_strategy()
        outcome = strategy["outcome"]
        today, tomorrow = _row(100.0, 100.0, 0.0010), _row(100.0, 100.0, -0.0050)
        signal = strategy["compute"]([today, tomorrow])
        self.assertEqual(signal["indicator_value"], 0.0010)      # today's funding
        self.assertAlmostEqual(outcome["compute"](today, tomorrow), -0.0050)  # tomorrow's

    def test_the_classified_day_is_the_first_of_the_window(self):
        strategy = carry_funding_threshold_strategy()
        positive_today = strategy["compute"]([_row(100., 100., 0.001), _row(100., 100., -0.9)])
        self.assertEqual(positive_today["group"], "UPPER")


class ThresholdTests(unittest.TestCase):
    """Zero is the only parameter-free boundary, and that is the integrity."""

    def test_the_threshold_is_zero_and_declared_in_the_parameters(self):
        strategy = carry_funding_threshold_strategy()
        self.assertEqual(strategy["parameters"]["threshold"], "0")

    def test_being_paid_is_the_upper_group_and_paying_is_the_lower(self):
        strategy = carry_funding_threshold_strategy()
        paid = strategy["compute"]([_row(100., 100., 0.0002), _row(100., 100., 0.0)])
        paying = strategy["compute"]([_row(100., 100., -0.0002), _row(100., 100., 0.0)])
        self.assertEqual(paid["group"], "UPPER")
        self.assertEqual(paying["group"], "LOWER_OR_EQUAL")

    def test_exactly_zero_counts_as_still_being_paid(self):
        # Declared before execution so the boundary case is not decided later by
        # whichever side happens to look better.
        strategy = carry_funding_threshold_strategy()
        self.assertEqual(
            strategy["compute"]([_row(100., 100., 0.0), _row(100., 100., 0.0)])["group"],
            "UPPER")

    def test_a_non_finite_funding_rate_is_refused(self):
        strategy = carry_funding_threshold_strategy()
        with self.assertRaises(ValueError):
            strategy["compute"]([_row(100., 100., float("nan")), _row(100., 100., 0.0)])


class ContractShapeTests(unittest.TestCase):
    def test_the_strategy_declares_the_carry_outcome_rather_than_a_price_return(self):
        strategy = carry_funding_threshold_strategy()
        self.assertEqual(strategy["outcome"]["outcome_id"], "CARRY_RETURN")
        self.assertEqual(p.strategy_outcome(strategy)["outcome_id"], "CARRY_RETURN")

    def test_it_requires_both_auxiliary_series(self):
        strategy = carry_funding_threshold_strategy()
        self.assertEqual(set(strategy["required_inputs"]["variables"]),
                         {"funding_rate", "perp_close"})

    def test_a_window_of_the_wrong_length_is_refused(self):
        strategy = carry_funding_threshold_strategy()
        with self.assertRaises(ValueError):
            strategy["compute"]([_row(100., 100., 0.0)])


if __name__ == "__main__":
    unittest.main()
