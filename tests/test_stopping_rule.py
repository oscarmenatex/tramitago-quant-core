"""The search stopping rule, pre-declared 2026-09-30 and never once executed.

It turns "do we keep looking" from a question of mood into a measured statement.
These cover the exact bound, the minimum that stops an arithmetic artefact being
read as knowledge, and the reproduction of the three numbers the sealed
pre-declaration published -- which is the only check that matters for an
implementation of a rule written before it.
"""

import unittest

import pipeline as p
from tramitago_quant_core.governance.stopping_rule import (
    clopper_pearson_upper, population_status, STOPPING_RULE_MINIMUM_HYPOTHESES,
    REASON_BELOW_MINIMUM, REASON_BOUND_REACHES_THRESHOLD, REASON_EXHAUSTED,
)


class BoundTests(unittest.TestCase):
    def test_it_reproduces_the_sealed_pre_declaration(self):
        # The document of 2026-09-30 published these three and nothing in the
        # implementation may disagree with them.
        self.assertAlmostEqual(clopper_pearson_upper(42, 108), 0.472, places=3)
        self.assertAlmostEqual(clopper_pearson_upper(0, 6), 0.393, places=3)
        self.assertAlmostEqual(clopper_pearson_upper(0, 30), 0.095, places=3)

    def test_the_bound_is_above_the_observed_rate(self):
        for successes, trials in ((1, 10), (42, 108), (29, 31)):
            self.assertGreater(clopper_pearson_upper(successes, trials), successes / trials)

    def test_everything_observed_leaves_certainty_possible(self):
        self.assertEqual(clopper_pearson_upper(10, 10), 1.0)

    def test_more_evidence_tightens_the_bound_at_the_same_rate(self):
        self.assertGreater(clopper_pearson_upper(5, 10), clopper_pearson_upper(50, 100))

    def test_a_stricter_confidence_gives_a_looser_bound(self):
        # Being more certain means conceding more room, which keeps an axis open
        # for longer -- the right direction for a rule whose output is "stop".
        self.assertGreater(clopper_pearson_upper(10, 100, "0.99"),
                           clopper_pearson_upper(10, 100, "0.95"))

    def test_invalid_counts_are_refused(self):
        for successes, trials in ((5, 0), (-1, 10), (11, 10), (True, 10), (5, 2.5)):
            with self.assertRaises(ValueError):
                clopper_pearson_upper(successes, trials)


class PopulationTests(unittest.TestCase):
    def _status(self, **overrides):
        arguments = {"population": "test", "hypotheses": 10,
                     "passing_folds": 40, "usable_folds": 100}
        arguments.update(overrides)
        return population_status(**arguments)

    def test_a_population_whose_bound_falls_short_is_exhausted(self):
        status = self._status()
        self.assertTrue(status["exhausted"])
        self.assertEqual(status["reason"], REASON_EXHAUSTED)

    def test_a_population_whose_bound_still_reaches_the_bar_stays_open(self):
        status = self._status(passing_folds=29, usable_folds=31)
        self.assertFalse(status["exhausted"])
        self.assertEqual(status["reason"], REASON_BOUND_REACHES_THRESHOLD)

    def test_below_the_minimum_the_test_does_not_fire_however_bad_the_rate(self):
        # A single failed hypothesis already yields 0.393, which would close an
        # axis by arithmetic rather than by knowledge.
        status = self._status(hypotheses=1, passing_folds=0, usable_folds=6)
        self.assertFalse(status["exhausted"])
        self.assertEqual(status["reason"], REASON_BELOW_MINIMUM)

    def test_the_inapplicable_bound_is_still_reported_and_flagged(self):
        # Hiding it would make the artefact harder to see, not easier.
        status = self._status(hypotheses=1, passing_folds=0, usable_folds=6)
        self.assertAlmostEqual(float(status["upper_bound_95"]), 0.393, places=3)
        self.assertFalse(status["bound_is_applicable"])

    def test_the_minimum_is_five_as_declared(self):
        self.assertEqual(STOPPING_RULE_MINIMUM_HYPOTHESES, 5)
        self.assertTrue(self._status(hypotheses=5)["exhausted"])
        self.assertFalse(self._status(hypotheses=4)["exhausted"])

    def test_a_population_with_no_usable_folds_is_refused(self):
        with self.assertRaises(ValueError):
            self._status(usable_folds=0)

    def test_it_is_reachable_from_the_facade(self):
        self.assertAlmostEqual(p.clopper_pearson_upper(42, 108), 0.472, places=3)


if __name__ == "__main__":
    unittest.main()
