"""The pre-declaration contract aligned with the candidate register.

PRE_DECLARATION_ALIGNMENT|0a659627. The register holds three verdicts and refuses
a candidate only when even the OPTIMISTIC end of its recalled effect range falls
below its bar. The contract refused whenever the single declared value, the
CONSERVATIVE end, fell below it. MEASURED from the register: the strict rule let
2 of 15 candidates be declared and the aligned one lets 12 through, ten of them
newly, four of those already measured, and none of the four was admitted.

Two measurements already sealed show why neither end is right alone. The S&P 500
was recalled at 0.40 to 0.80 against a bar of 0.500 and measured 0.81, so the
conservative end refuses a premium that clears. The credit premium was recalled at
0.20 to 0.50 and measured 0.19, so the optimistic end admits one that fails.
"""

import json
import unittest
from pathlib import Path

from tramitago_quant_core.research.pre_declaration import (
    pre_declaration, verified_pre_declaration, load_pre_declaration,
    POSITION_CLEARS, POSITION_STRADDLES, PRE_DECLARATION_SCHEMA_VERSION_RANGE,
)

BASE = dict(
    hypothesis_id="HYPOTHESIS|probe",
    payer="buyers of call options who pay for upside exposure the fund sells them",
    why_they_keep_paying="they want convex upside at limited cost and the sellers are paid "
                         "for taking the other side of that demand and bearing its crash risk",
    crossings_per_year="0.19",
    net_level_claimed="the net return of holding the fund continuously after execution costs, "
                      "not a difference between two groups of days",
    monitor_variable="VIX minus the 21-day realised volatility",
    monitor_publisher="CBOE publishes it daily and FRED distributes it",
    plausibility_source="a range recalled from published studies of the buy-write index, "
                        "never measured here",
    claim_class="PREMIUM", window_years="10.71",
    adverse_episode_in_window="the February 2018 spike and the March 2020 crash",
    source="answered in writing before any bar of the fund was captured")


def _range(low, high, **overrides):
    return pre_declaration(**{**BASE, "plausible_low": low, "plausible_high": high,
                              **overrides})


class AlignedRuleTests(unittest.TestCase):
    def test_a_range_straddling_the_bar_is_accepted_and_says_so(self):
        # XYLD: recalled 0.35 to 0.80 against a bar of 0.500. The old contract
        # refused it and the register called it UNCERTAIN.
        record = _range("0.35", "0.80")
        self.assertEqual(record["required_effect"]["position_against_bar"],
                         POSITION_STRADDLES)
        self.assertEqual(record["schema_version"], PRE_DECLARATION_SCHEMA_VERSION_RANGE)

    def test_a_range_whose_conservative_end_clears_is_marked_as_clearing(self):
        self.assertEqual(
            _range("0.60", "1.00")["required_effect"]["position_against_bar"],
            POSITION_CLEARS)

    def test_it_is_refused_only_when_even_the_optimistic_end_falls_short(self):
        # The term premium: 0.20 to 0.40 against 0.500. It cannot clear.
        with self.assertRaises(ValueError) as caught:
            _range("0.20", "0.40")
        self.assertIn("even at its optimistic end", str(caught.exception))

    def test_the_cases_that_showed_neither_end_is_right_alone(self):
        # SPY: recalled 0.40-0.80, measured 0.81. The conservative end would
        # have refused a premium that cleared.
        self.assertEqual(
            _range("0.40", "0.80")["required_effect"]["position_against_bar"],
            POSITION_STRADDLES)
        # Credit: recalled 0.20-0.50, measured 0.19. At the very edge of the
        # bar it is accepted as straddling, and it died anyway, which is the cost
        # of this rule and is recorded in the sealed alignment.
        self.assertEqual(
            _range("0.20", "0.50")["required_effect"]["position_against_bar"],
            POSITION_STRADDLES)

    def test_the_bar_is_still_derived_from_the_window_and_never_lowered(self):
        self.assertEqual(_range("0.60", "1.00")["required_effect"]["required_point_sharpe"],
                         "0.500")
        with self.assertRaises(ValueError):
            _range("0.60", "1.00", required_point_sharpe="0.20")

    def test_the_shorter_window_buys_a_higher_bar_that_the_range_must_still_meet(self):
        # 6.35 years is JEPI: the bar rises to about 0.635, and a range topping
        # out at 0.60 is refused whatever its conservative end.
        with self.assertRaises(ValueError):
            _range("0.30", "0.60", window_years="6.35",
                   available_window_years="6.35")


class ExactlyOneFormTests(unittest.TestCase):
    """A caller passing both would choose whichever the gate liked."""

    def test_both_forms_at_once_is_refused(self):
        with self.assertRaises(ValueError):
            pre_declaration(**{**BASE, "plausible_point_sharpe": "0.60",
                               "plausible_low": "0.35", "plausible_high": "0.80"})

    def test_neither_form_is_refused(self):
        with self.assertRaises(ValueError):
            pre_declaration(**BASE)

    def test_a_range_needs_both_ends_and_in_order(self):
        with self.assertRaises(ValueError):
            pre_declaration(**{**BASE, "plausible_low": "0.35"})
        with self.assertRaises(ValueError):
            _range("0.80", "0.35")

    def test_the_old_single_value_form_behaves_exactly_as_it_did(self):
        record = pre_declaration(**{**BASE, "plausible_point_sharpe": "0.60"})
        self.assertEqual(record["schema_version"], "1")
        self.assertEqual(record["required_effect"]["plausible_point_sharpe"], "0.60")
        self.assertNotIn("position_against_bar", record["required_effect"])
        with self.assertRaises(ValueError):
            pre_declaration(**{**BASE, "plausible_point_sharpe": "0.35"})


class VerificationTests(unittest.TestCase):
    def test_a_schema_2_record_verifies(self):
        self.assertTrue(verified_pre_declaration(_range("0.35", "0.80")))

    def test_altering_the_declared_range_breaks_the_identity(self):
        record = json.loads(json.dumps(_range("0.35", "0.80")))
        record["required_effect"]["plausible_high"] = "1.50"
        with self.assertRaises(ValueError):
            verified_pre_declaration(record)

    def test_every_record_already_sealed_still_verifies(self):
        # The regression this class exists for: schema 1 must reproduce.
        path = Path(__file__).resolve().parents[1] / "artifacts/research/pre-declarations.json"
        if not path.exists():
            self.skipTest("no sealed pre-declarations in this checkout")
        for item in json.loads(path.read_bytes())["pre_declarations"]:
            self.assertIsNotNone(load_pre_declaration(path, item["hypothesis_id"]))


if __name__ == "__main__":
    unittest.main()
