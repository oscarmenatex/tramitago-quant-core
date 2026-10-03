"""Seven questions a Hypothesis must answer before any data is captured.

MEASURED, across 47 sealed Hypotheses: 2 name who pays, and both were written
on 2026-10-02. The multiplicity audit shows what the other 45 bought -- the
pre-declared effect came back positive on 78 of 189 usable folds, 0.413 against
a coin's 0.500, which is 2.40 sigma the WRONG WAY, with a mean fold metric of
-0.0011 and a best p-value of 0.0625 against a Bonferroni alpha of 0.00135.

Three Hypotheses died on a dimension that was never in their declaration: the
carry on turnover, the equity premium on monitorability, the credit premium on
effect size. Each was knowable before a bar was captured.
"""

import json
import tempfile
import unittest
from pathlib import Path

from tramitago_quant_core.research.pre_declaration import (
    pre_declaration, verified_pre_declaration, constitute_pre_declaration,
    load_pre_declaration, require_pre_declaration, QUESTIONS, CLAIM_PREMIUM,
    MINIMUM_PLAUSIBLE_POINT_SHARPE, PRE_DECLARATION_IN_FORCE_SINCE,
)

ANSWERS = dict(
    hypothesis_id="HYPOTHESIS|probe",
    payer="levered long holders who pay continuously to maintain their exposure",
    why_they_keep_paying="they are buying directional exposure and the funding is the "
                         "price of the leverage, small against the move they want",
    crossings_per_year="2",
    net_level_claimed="the net return of holding the position continuously, after costs on both legs",
    monitor_variable="the VIX term structure", monitor_publisher="CBOE, published daily",
    required_point_sharpe="0.54", plausible_point_sharpe="0.70",
    plausibility_source="published long-run estimates of the variance risk premium run "
                        "between 0.6 and 0.9 on a decade of daily data",
    claim_class=CLAIM_PREMIUM, window_years="8.7",
    adverse_episode_in_window="the February 2018 volatility spike and the March 2020 crash",
    source="answered in writing before any bar of either leg was captured, from the literature",
)


class ContractTests(unittest.TestCase):
    def test_it_answers_exactly_the_seven_questions(self):
        record = verified_pre_declaration(pre_declaration(**ANSWERS))
        for question in QUESTIONS:
            self.assertIn(question, record)

    def test_altering_an_answer_breaks_the_identity(self):
        record = pre_declaration(**ANSWERS)
        tampered = json.loads(json.dumps(record))
        tampered["turnover"]["crossings_per_year"] = "400"
        with self.assertRaises(ValueError):
            verified_pre_declaration(tampered)


class RefusalTests(unittest.TestCase):
    """Each refusal is a Hypothesis this project actually paid for."""

    def test_an_effect_below_its_own_gate_is_refused_outright(self):
        # The credit premium cost two days. Its plausible Sharpe was known from
        # the literature to run 0.3-0.5 against a bar near 0.54, and it came in
        # at 0.19. Capturing it was spending to confirm a refutation already
        # available in writing.
        with self.assertRaises(ValueError) as caught:
            pre_declaration(**{**ANSWERS, "plausible_point_sharpe": "0.40"})
        self.assertIn("cannot clear its own gate", str(caught.exception))

    def test_nobody_may_declare_a_bar_below_what_the_gate_needs(self):
        # Otherwise the question answers itself by lowering its own threshold.
        with self.assertRaises(ValueError):
            pre_declaration(**{**ANSWERS, "required_point_sharpe": "0.20"})

    def test_the_floor_is_the_one_the_corrected_gate_implies(self):
        self.assertEqual(MINIMUM_PLAUSIBLE_POINT_SHARPE, "0.54")

    def test_a_gesture_is_not_an_answer(self):
        # 45 of 47 Hypotheses answered "who pays" in zero words. Two is not an
        # improvement worth recording.
        for field in ("payer", "why_they_keep_paying", "net_level_claimed"):
            with self.assertRaises(ValueError):
                pre_declaration(**{**ANSWERS, field: "the market"})

    def test_a_mispricing_may_not_be_filed_as_something_else(self):
        with self.assertRaises(ValueError):
            pre_declaration(**{**ANSWERS, "claim_class": "ARBITRAGE"})

    def test_turnover_and_window_must_be_numbers(self):
        for field in ("crossings_per_year", "window_years"):
            with self.assertRaises(ValueError):
                pre_declaration(**{**ANSWERS, field: "low"})

    def test_a_negative_turnover_or_an_empty_window_is_refused(self):
        with self.assertRaises(ValueError):
            pre_declaration(**{**ANSWERS, "crossings_per_year": "-1"})
        with self.assertRaises(ValueError):
            pre_declaration(**{**ANSWERS, "window_years": "0"})


class GuardTests(unittest.TestCase):
    """Enforced where the money is spent, and derived from the timestamp so the
    47 sealed before this contract existed are unaffected by construction."""

    def setUp(self):
        self.registry = Path(tempfile.mkdtemp()) / "pre-declarations.json"

    def test_a_hypothesis_predating_the_contract_is_untouched(self):
        old = {"hypothesis_id": "HYPOTHESIS|old",
               "creation_timestamp": "2026-09-28T10:30:00.123456Z"}
        self.assertIsNone(require_pre_declaration(old, self.registry))

    def test_a_hypothesis_created_after_it_must_have_answered(self):
        new = {"hypothesis_id": "HYPOTHESIS|new",
               "creation_timestamp": "2026-10-04T10:30:00.123456Z"}
        with self.assertRaises(ValueError) as caught:
            require_pre_declaration(new, self.registry)
        self.assertIn("seven questions", str(caught.exception).lower())

    def test_the_guard_passes_once_the_questions_are_answered(self):
        sealed = constitute_pre_declaration(
            self.registry, pre_declaration(**{**ANSWERS, "hypothesis_id": "HYPOTHESIS|new"}),
            declared_at="2026-10-04T10:00:00.123456Z", code_revision="a" * 40)
        new = {"hypothesis_id": "HYPOTHESIS|new",
               "creation_timestamp": "2026-10-04T10:30:00.123456Z"}
        self.assertIsNotNone(require_pre_declaration(new, self.registry))
        self.assertEqual(sealed["hypothesis_id"], "HYPOTHESIS|new")

    def test_re_running_the_declaring_script_is_not_a_second_hypothesis(self):
        record = pre_declaration(**ANSWERS)
        first = constitute_pre_declaration(self.registry, record,
                                           declared_at="2026-10-04T10:00:00.123456Z",
                                           code_revision="a" * 40)
        second = constitute_pre_declaration(self.registry, record,
                                            declared_at="2026-10-04T11:00:00.123456Z",
                                            code_revision="b" * 40)
        self.assertEqual(first["pre_declaration_id"], second["pre_declaration_id"])
        self.assertEqual(
            len(json.loads(self.registry.read_bytes())["pre_declarations"]), 1)

    def test_the_contract_came_into_force_on_a_declared_date(self):
        self.assertEqual(PRE_DECLARATION_IN_FORCE_SINCE, "2026-10-03T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
