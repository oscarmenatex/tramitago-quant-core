"""The candidate space, sealed before one of them is picked.

Discovery seals its space before scanning so a Finding cannot hide how many it
beat. Nothing did the same for Hypotheses chosen by hand, and on 2026-10-03 that
cost something measurable: six premia sourced from recollection, ranked by
MONITORABILITY -- the failure mode that killed one of six, while size or returns
killed four.
"""

import json
import tempfile
import unittest
from pathlib import Path

from tramitago_quant_core.research.candidate_register import (
    candidate, candidate_verdict, rank_candidates, constitute_candidate_register,
    VERDICT_FEASIBLE, VERDICT_UNCERTAIN, VERDICT_BELOW_BAR, VERDICT_UNREACHABLE_DATA,
    VERDICT_UNOPERABLE,
    STATUS_UNTRIED, STATUS_MEASURED_DEAD,
)
from tramitago_quant_core.research.pre_declaration import CLAIM_PREMIUM

FIELDS = dict(
    claim_class=CLAIM_PREMIUM,
    payer="investors shedding a risk they are mandated or unwilling to bear",
    effect_source="a range recalled from published estimates, never measured here",
    data_source="Alpaca SIP", status=STATUS_UNTRIED)


def _entry(name, low, high, years):
    return candidate(name=name, effect_low=low, effect_high=high,
                     available_years=years, **FIELDS)


class VerdictTests(unittest.TestCase):
    """Neither end of a range is right alone, and the first version used only one."""

    def test_the_conservative_end_clearing_is_feasible(self):
        self.assertEqual(
            candidate_verdict(_entry("ample", "0.70", "1.00", "10.75"))["verdict"],
            VERDICT_FEASIBLE)

    def test_a_range_that_straddles_the_bar_is_uncertain_not_refused(self):
        # SPY's recalled range was 0.40 to 0.80 against a bar of 0.50, and it
        # MEASURED 0.81. Refusing on the conservative end would have turned away
        # a premium that then cleared its gate.
        verdict = candidate_verdict(_entry("spy", "0.40", "0.80", "10.75"))
        self.assertEqual(verdict["verdict"], VERDICT_UNCERTAIN)
        self.assertIn("straddles", verdict["detail"])

    def test_only_the_optimistic_end_failing_is_a_refusal(self):
        # The term premium: 0.20 to 0.40 against 0.50. It cannot clear, so
        # spending on it is waste rather than risk.
        self.assertEqual(
            candidate_verdict(_entry("term", "0.20", "0.40", "10.75"))["verdict"],
            VERDICT_BELOW_BAR)

    def test_the_bar_falls_as_the_available_window_grows(self):
        # The point-to-bound gap scales as one over the root of the sample, so
        # the SAME candidate is uncertain on eight years and feasible on
        # twenty-one. Ranking by effect size alone cannot see this.
        short = candidate_verdict(_entry("vrp", "0.50", "1.00", "8.57"))
        long = candidate_verdict(_entry("vrp", "0.50", "1.00", "21.00"))
        self.assertEqual(short["verdict"], VERDICT_UNCERTAIN)
        self.assertEqual(long["verdict"], VERDICT_FEASIBLE)
        self.assertGreater(short["required"], long["required"])

    def test_data_nobody_can_reach_is_its_own_verdict(self):
        entry = candidate(name="vix futures", effect_low="0.50", effect_high="1.00",
                          available_years="21.00", reachable_today=False,
                          **{**FIELDS, "data_source": "CBOE"})
        self.assertEqual(candidate_verdict(entry)["verdict"], VERDICT_UNREACHABLE_DATA)


class RankingTests(unittest.TestCase):
    def test_a_longer_window_beats_a_bigger_effect(self):
        # The mistake this module exists to stop: a large effect on a short
        # window is behind a smaller one on a long window, because the bar the
        # first must clear is higher.
        register = {"candidates": [_entry("short and big", "0.30", "0.60", "4.00"),
                                   _entry("long and modest", "0.30", "0.60", "12.00")]}
        self.assertEqual(rank_candidates(register)[0]["name"], "long and modest")

    def test_refusals_sort_below_open_questions(self):
        register = {"candidates": [_entry("refused", "0.10", "0.30", "10.75"),
                                   _entry("open", "0.40", "0.80", "10.75")]}
        order = [item["verdict"] for item in rank_candidates(register)]
        self.assertEqual(order, [VERDICT_UNCERTAIN, VERDICT_BELOW_BAR])


class SealingTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "registers.json"
        self.ok = dict(justification="the set this project is choosing from, declared "
                                     "before any one of them is picked",
                       declared_by="test", declared_at="2026-10-03T12:00:00.123456Z")

    def test_a_register_of_one_is_a_choice_already_made(self):
        with self.assertRaises(ValueError) as caught:
            constitute_candidate_register(
                self.path, candidates=[_entry("only", "0.5", "0.9", "10")], **self.ok)
        self.assertIn("already made", str(caught.exception))

    def test_the_same_candidate_may_not_be_named_twice(self):
        with self.assertRaises(ValueError):
            constitute_candidate_register(
                self.path, candidates=[_entry("same", "0.5", "0.9", "10"),
                                       _entry("same", "0.2", "0.4", "10")], **self.ok)

    def test_re_declaring_the_identical_space_returns_what_is_there(self):
        entries = [_entry("a", "0.5", "0.9", "10"), _entry("b", "0.2", "0.4", "10")]
        first = constitute_candidate_register(self.path, candidates=entries, **self.ok)
        second = constitute_candidate_register(self.path, candidates=entries, **self.ok)
        self.assertEqual(first["register_id"], second["register_id"])
        self.assertEqual(len(json.loads(self.path.read_bytes())["registers"]), 1)

    def test_an_inverted_effect_range_is_refused(self):
        with self.assertRaises(ValueError):
            _entry("inverted", "0.90", "0.20", "10")

    def test_a_mispricing_must_say_so_rather_than_pass_as_a_premium(self):
        with self.assertRaises(ValueError):
            candidate(name="x", effect_low="0.5", effect_high="0.9", available_years="10",
                      **{**FIELDS, "claim_class": "ARBITRAGE"})

    def test_a_dead_candidate_stays_in_the_register(self):
        # What was passed over, and what was tried and failed, are both part of
        # the record a later ranking has to be argued against.
        entry = candidate(name="dead", effect_low="0.2", effect_high="0.5",
                          available_years="10.75",
                          **{**FIELDS, "status": STATUS_MEASURED_DEAD})
        self.assertEqual(entry["status"], STATUS_MEASURED_DEAD)


if __name__ == "__main__":
    unittest.main()


class LotSizeTests(unittest.TestCase):
    """R4 checks the capacity CEILING and never the LOT, so a Hypothesis can
    pass it and be unoperable anyway.

    MEASURED 2026-10-03: at a $1,000 tranche and the 10.9% weight the drawdown
    limit derived for the same premium, the position is $109 of notional, while
    one mini VIX future at VIX 18 is $1,800 and one full contract is $18,000.
    No premium is large enough to fix an indivisible wrapper.
    """

    def test_a_lot_larger_than_the_tranche_is_refused_whatever_the_effect(self):
        entry = candidate(name="VX futures", effect_low="0.90", effect_high="1.50",
                          available_years="13.75", minimum_position_usd="1800", **FIELDS)
        verdict = candidate_verdict(entry)
        self.assertEqual(verdict["verdict"], VERDICT_UNOPERABLE)
        self.assertIn("whatever the premium measures", verdict["detail"])

    def test_an_expressible_lot_is_judged_on_its_effect_as_usual(self):
        entry = candidate(name="an ETF", effect_low="0.60", effect_high="1.00",
                          available_years="10.75", minimum_position_usd="20", **FIELDS)
        self.assertEqual(candidate_verdict(entry)["verdict"], VERDICT_FEASIBLE)

    def test_a_candidate_with_no_lot_worth_stating_is_unaffected(self):
        # An ETF share is not a lot size; a futures contract is.
        entry = _entry("an ETF", "0.60", "1.00", "10.75")
        self.assertNotIn("minimum_position_usd", entry)
        self.assertEqual(candidate_verdict(entry)["verdict"], VERDICT_FEASIBLE)

    def test_unoperable_sorts_below_every_open_question(self):
        register = {"candidates": [
            candidate(name="indivisible", effect_low="0.90", effect_high="1.50",
                      available_years="13.75", minimum_position_usd="1800", **FIELDS),
            _entry("modest but holdable", "0.30", "0.60", "10.75")]}
        self.assertEqual(rank_candidates(register)[0]["name"], "modest but holdable")
