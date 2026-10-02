"""A test corrected for N candidates must be passable at all.

CARRY_FUNDING_SURGE(30) was sealed over 5 folds corrected for 8 candidates,
passed 5 of 5 on a holdout, and came back NOT_VALIDATED at p=0.03125 against a
bar of 0.00625. No outcome whatsoever could have cleared it. The defect was
knowable before the run and nothing checked it.
"""

import unittest

import pipeline as p
from tramitago_quant_core.research.walk_forward import minimum_folds_for_batch


class MinimumFoldsTests(unittest.TestCase):
    def test_the_smallest_attainable_p_value_clears_the_corrected_bar(self):
        for batch in (1, 2, 5, 8, 17, 40, 100):
            folds = minimum_folds_for_batch(batch)
            self.assertLessEqual(0.5 ** folds, 0.05 / batch, batch)
            self.assertGreater(0.5 ** (folds - 1), 0.05 / batch, batch)

    def test_the_case_that_was_missed(self):
        # Eight candidates need eight folds; five were declared.
        self.assertEqual(minimum_folds_for_batch(8), 8)
        self.assertGreater(0.5 ** 5, 0.05 / 8)

    def test_a_wider_search_needs_more_folds(self):
        self.assertLess(minimum_folds_for_batch(8), minimum_folds_for_batch(100))

    def test_an_invalid_batch_size_is_refused(self):
        for value in (0, -1, True, 2.5, "8"):
            with self.assertRaises(ValueError):
                minimum_folds_for_batch(value)

    def test_it_is_reachable_from_the_facade(self):
        self.assertEqual(p.minimum_folds_for_batch(8), 8)


if __name__ == "__main__":
    unittest.main()
