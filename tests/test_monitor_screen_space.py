"""The monitor screen candidate space: it must be a space that could be sealed."""

import copy
import json
import unittest
from pathlib import Path

from tramitago_quant_core.research.monitor_screen_space import (
    validate_space, chance_of_passing, expected_false_passes, FORMS, STATUS_DRAFT)

SPACE_FILE = Path(__file__).resolve().parents[1] / "scripts" / "research" / "monitor_screen" / "space.json"


def _space():
    return json.loads(SPACE_FILE.read_text(encoding="utf-8"))


class TheRepositorySpaceTests(unittest.TestCase):
    def test_the_drafted_space_validates(self):
        space = validate_space(_space())
        self.assertEqual(space["status"], STATUS_DRAFT)

    def test_it_is_not_yet_sealed_and_says_so(self):
        self.assertEqual(_space()["status"], "DRAFT_FOR_REVIEW_NOT_SEALED")

    def test_every_candidate_has_a_mechanism_and_no_direction_of_its_own(self):
        for candidate in _space()["candidates"]:
            self.assertGreaterEqual(len(candidate["mechanism"].split()), 12, candidate["id"])
            self.assertEqual(set(candidate),
                             {"id", "variable", "form", "exposure", "mechanism"})

    def test_the_multiplicity_includes_what_was_already_tested(self):
        space = _space()
        self.assertEqual(space["multiplicity"]["scanned"], 14)
        self.assertEqual(space["multiplicity"]["total_tested_on_this_question"], 19)

    def test_the_expected_number_of_chance_passes_is_stated_and_not_small(self):
        # 14 candidates at 5 of 7 folds is about three false passes by chance alone: passing the
        # discovery rule is therefore not evidence, which is why the holdout exists.
        self.assertAlmostEqual(expected_false_passes(_space()), 14 * 29 / 128, places=9)
        self.assertGreater(expected_false_passes(_space()), 3.0)


class ChanceTests(unittest.TestCase):
    def test_the_chance_of_an_unrelated_variable_passing_the_fold_rule(self):
        self.assertAlmostEqual(chance_of_passing(7), 29 / 128, places=12)      # 5 of 7
        self.assertAlmostEqual(chance_of_passing(6), 7 / 64, places=12)        # 5 of 6
        self.assertAlmostEqual(chance_of_passing(5), 6 / 32, places=12)        # 4 of 5

    def test_more_folds_never_make_a_chance_pass_easier_than_a_coin(self):
        for folds in range(5, 12):
            self.assertLess(chance_of_passing(folds), 0.5)


class RefusalTests(unittest.TestCase):
    def _refuse(self, change, fragment):
        space = _space()
        change(space)
        with self.assertRaises(ValueError) as caught:
            validate_space(space)
        self.assertIn(fragment, str(caught.exception))

    def test_a_holdout_inside_the_discovery_window_is_refused(self):
        self._refuse(lambda s: s["windows"]["holdout"].update(start_utc="2022-01-01T00:00:00Z"),
                     "holdout")

    def test_a_pass_rule_different_from_the_engines_is_refused(self):
        for change in ({"consistency_threshold": "0.60"}, {"minimum_usable_folds": 3},
                       {"minimum_trigger_days_per_fold": 5}):
            self._refuse(lambda s, c=change: s["pass_rule"].update(c), "engine")

    def test_fewer_folds_than_the_minimum_usable_is_refused(self):
        self._refuse(lambda s: s.update(folds=4), "folds")

    def test_a_candidate_without_a_mechanism_is_refused(self):
        self._refuse(lambda s: s["candidates"][0].update(mechanism="it works well"),
                     "no mechanism")

    def test_a_candidate_with_its_own_direction_or_threshold_is_refused(self):
        self._refuse(lambda s: s["candidates"][0].update(direction="BOTH"), "direction")
        self._refuse(lambda s: s["candidates"][0].update(threshold="0.5"), "threshold")

    def test_the_same_candidate_twice_is_refused(self):
        def duplicate(space):
            clone = copy.deepcopy(space["candidates"][0])
            clone["id"] = "C99"
            space["candidates"].append(clone)
            space["multiplicity"]["scanned"] = len(space["candidates"])
            space["multiplicity"]["total_tested_on_this_question"] = \
                space["multiplicity"]["scanned"] + space["multiplicity"]["also_counted_from_the_ledger"]
        self._refuse(duplicate, "twice")

    def test_a_form_that_is_not_declared_is_refused(self):
        self._refuse(lambda s: s["candidates"][0].update(form="ABOVE_90TH_PERCENTILE"), "form")

    def test_a_variable_that_is_revised_or_not_daily_is_refused(self):
        self._refuse(lambda s: s["variables"][0]["validity"].update(revisions="restated"),
                     "never revised")
        self._refuse(lambda s: s["variables"][0]["validity"].update(frequency="weekly"),
                     "daily")

    def test_a_variable_or_exposure_nobody_uses_is_refused(self):
        def orphan(space):
            space["variables"].append(copy.deepcopy(space["variables"][0]) | {"id": "ORPHAN"})
        self._refuse(orphan, "used by a candidate")

    def test_a_multiplicity_that_forgets_the_ledger_is_refused(self):
        self._refuse(lambda s: s["multiplicity"].update(total_tested_on_this_question=14),
                     "multiplicity")

    def test_a_direction_searched_both_ways_is_refused(self):
        self._refuse(lambda s: s.update(adverse_direction="either sign is a finding"),
                     "never searched both ways")

    def test_unknown_or_missing_top_level_keys_are_refused(self):
        self._refuse(lambda s: s.update(extra="x"), "unknown")
        self._refuse(lambda s: s.pop("ledger_prior"), "missing")

    def test_the_forms_are_exactly_the_declared_ones(self):
        space = _space()
        self.assertEqual(set(space["state_forms"]), set(FORMS))


if __name__ == "__main__":
    unittest.main()
