"""The judge for a LEVEL claim: does holding this position pay, net of costs?

The comparative apparatus computes `upper_mean - lower_mean` and goes
INCONCLUSIVE when a group is empty, so it can only judge "returns differ between
these two groups". A risk premium is a claim about ONE position. These cover the
three refusals that make the judge worth having: never gross, never the mean, and
never a premium whose losses were not observed.
"""

import json
import math
import tempfile
import unittest
from pathlib import Path

import pipeline as p
from tramitago_quant_core.research.level_claim import (
    level_claim, verified_level_claim, adverse_period_frequency, adverse_mean_bound,
    evaluate_fold, level_claim_outcome, constitute_level_claim_validation,
    load_level_claim_validation, verified_level_claim_validation,
    FOLD_MET, FOLD_NOT_MET, FOLD_INCONCLUSIVE,
    OUTCOME_VALIDATED, OUTCOME_NOT_VALIDATED, OUTCOME_INSUFFICIENT,
    REASON_TAIL_NOT_COVERED, REASON_CONSISTENCY_BELOW, adverse_episodes,
    REASON_DRAWDOWN_EXCEEDED, REASON_FOLDS_BELOW_MINIMUM,
)

REVISION = "a" * 40


def _contract(commission="0.0005", half_spread="0.0002", slippage="0.0001", legs=2):
    return p.cost_contract(
        regime=p.COST_REGIME_HOLDING, commission_rate=commission,
        half_spread_rate=half_spread, slippage_rate=slippage,
        recurring_rate_per_period="0", legs=legs,
        source="test fee schedule")


def _claim(contract=None, **overrides):
    arguments = {
        "position_description": "long spot, short perpetual, equal notional",
        "cost_contract": contract if contract is not None else _contract(),
        "minimum_folds_required": 3,
        "consistency_threshold": "0.7",
        "minimum_adverse_episodes": 1,
        "adverse_period_threshold": "0",
        "maximum_drawdown": "0.15",
        "confidence_level": "0.95",
        "bootstrap_resamples": 400,
        "bootstrap_block_periods": 5,
        "bootstrap_seed": 0,
        "source": "declared for this test",
    }
    arguments.update(overrides)
    return level_claim(**arguments)


def _series(pattern, count):
    return [pattern[index % len(pattern)] for index in range(count)]


def _fold(claim, contract, index, returns, positions=None):
    positions = positions if positions is not None else [1] * len(returns)
    return evaluate_fold(claim, contract, fold_index=index,
                         period={"start_utc": "2024-01-01T00:00:00Z",
                                 "end_exclusive_utc": "2024-04-01T00:00:00Z"},
                         positions=positions, gross_returns=returns)


class ClaimDeclarationTests(unittest.TestCase):
    def test_a_claim_cannot_be_declared_without_a_cost_contract(self):
        # A level claim evaluated gross would be the thirty-fourth measurement of
        # a quantity nobody could trade.
        with self.assertRaises(TypeError):
            level_claim(position_description="x" * 10, minimum_folds_required=3,
                        consistency_threshold="0.7", minimum_adverse_episodes=1,
                        adverse_period_threshold="0", maximum_drawdown="0.15", source="s")

    def test_a_claim_requiring_no_adverse_episode_is_refused(self):
        # A window with no episode cannot judge a premium at all.
        with self.assertRaises(ValueError):
            _claim(minimum_adverse_episodes=0)

    def test_every_tunable_term_is_part_of_the_identity(self):
        # A claim re-declared with a friendlier seed is a DIFFERENT claim, which
        # is the only way re-rolling becomes visible.
        base = _claim()
        for field, value in (("bootstrap_seed", 7), ("confidence_level", "0.90"),
                             ("consistency_threshold", "0.5"), ("maximum_drawdown", "0.40"),
                             ("minimum_adverse_episodes", 5),
                             ("adverse_period_threshold", "-0.01"),
                             ("bootstrap_block_periods", 10)):
            self.assertNotEqual(_claim(**{field: value})["claim_id"], base["claim_id"], field)

    def test_a_tampered_claim_fails_verification(self):
        claim = dict(_claim())
        claim["maximum_drawdown"] = "0.90"
        with self.assertRaises(ValueError):
            verified_level_claim(claim)

    def test_a_foreign_cost_contract_is_refused_at_evaluation(self):
        claim = _claim(contract=_contract())
        with self.assertRaises(ValueError) as caught:
            _fold(claim, _contract(commission="0.002"), 0, _series([0.001, -0.002], 60))
        self.assertIn("not the one this level claim was sealed with", str(caught.exception))


class AdverseBoundTests(unittest.TestCase):
    def test_the_bound_is_below_the_mean(self):
        returns = _series([0.002, 0.001, 0.003, -0.004], 120)
        mean = math.fsum(returns) / len(returns)
        bound = adverse_mean_bound(returns, confidence_level="0.95", resamples=400,
                                   block_periods=5, seed=0)
        self.assertLess(bound, mean)

    def test_the_bound_is_reproducible_from_the_sealed_seed(self):
        returns = _series([0.002, -0.001, 0.004], 90)
        first = adverse_mean_bound(returns, confidence_level="0.95", resamples=400,
                                   block_periods=5, seed=11)
        again = adverse_mean_bound(returns, confidence_level="0.95", resamples=400,
                                   block_periods=5, seed=11)
        self.assertEqual(first, again)

    def test_the_bound_moves_with_the_seed_which_is_why_the_seed_is_sealed(self):
        # The risk the sealed seed exists to remove: if the seed were a caller's
        # choice, a claim sitting near zero could be re-rolled until its bound
        # cleared. Shown on a continuous series -- with only two distinct return
        # values the bootstrap distribution is coarse enough that neighbouring
        # seeds often land in the same bucket, which would make this flaky rather
        # than wrong.
        returns = [0.004 - 0.00001 * ((index * 37) % 500) for index in range(90)]
        bounds = {adverse_mean_bound(returns, confidence_level="0.95", resamples=400,
                                     block_periods=5, seed=seed)
                  for seed in range(8)}
        self.assertGreater(len(bounds), 1)

    def test_a_fat_left_tail_drags_the_bound_down(self):
        # "Small positive, small positive, ... occasionally catastrophic" is the
        # shape a normal approximation prices as though it did not exist.
        calm = _series([0.001], 200)
        # Same mean by construction, so only the SHAPE differs: nineteen quiet
        # periods paying a little more, then one that gives it all back.
        quiet = (0.001 * 20 + 0.019) / 19
        tailed = _series([quiet] * 19 + [-0.019], 200)
        self.assertAlmostEqual(math.fsum(calm) / 200, math.fsum(tailed) / 200, places=9)
        self.assertLess(
            adverse_mean_bound(tailed, confidence_level="0.95", resamples=400,
                               block_periods=5, seed=0),
            adverse_mean_bound(calm, confidence_level="0.95", resamples=400,
                               block_periods=5, seed=0))

    def test_a_series_shorter_than_one_block_has_no_bound(self):
        self.assertIsNone(adverse_mean_bound([0.01, 0.02], confidence_level="0.95",
                                             resamples=100, block_periods=5, seed=0))

    def test_a_higher_confidence_level_gives_a_lower_bound(self):
        returns = _series([0.003, -0.002, 0.001], 120)
        self.assertLessEqual(
            adverse_mean_bound(returns, confidence_level="0.99", resamples=400,
                               block_periods=5, seed=3),
            adverse_mean_bound(returns, confidence_level="0.90", resamples=400,
                               block_periods=5, seed=3))


class RepresentativenessTests(unittest.TestCase):
    """The decisive refusal: a premium whose losses were never observed."""

    def test_the_adverse_frequency_counts_losing_periods(self):
        self.assertAlmostEqual(adverse_period_frequency([0.01, -0.01, 0.01, 0.01]), 0.25)
        self.assertIsNone(adverse_period_frequency([]))

    def test_a_premium_that_never_lost_is_unjudged_not_validated(self):
        # And not NOT_VALIDATED either: "we did not observe the thing that kills
        # this" is not a refutation.
        contract = _contract(commission="0", half_spread="0", slippage="0")
        claim = _claim(contract=contract)
        folds = [_fold(claim, contract, index, _series([0.002], 90)) for index in range(5)]
        outcome, reason, consistency, adverse = level_claim_outcome(claim, folds)
        self.assertEqual(outcome, OUTCOME_INSUFFICIENT)
        self.assertEqual(reason, REASON_TAIL_NOT_COVERED)
        self.assertIsNone(consistency)

    def test_representativeness_is_checked_before_consistency(self):
        # A sample with no losses would otherwise pass on consistency alone,
        # which would be measuring that nothing went wrong.
        contract = _contract(commission="0", half_spread="0", slippage="0")
        claim = _claim(contract=contract, consistency_threshold="0.1")
        folds = [_fold(claim, contract, index, _series([0.002], 90)) for index in range(5)]
        self.assertEqual(level_claim_outcome(claim, folds)[1], REASON_TAIL_NOT_COVERED)

    def test_the_adverse_frequency_is_pooled_not_averaged(self):
        # Averaging would let a few loss-rich folds hide a majority with none.
        contract = _contract(commission="0", half_spread="0", slippage="0")
        claim = _claim(contract=contract)
        rich = _fold(claim, contract, 0, _series([0.002, -0.001], 200))
        thin = [_fold(claim, contract, index, _series([0.002], 20)) for index in range(1, 4)]
        _, _, _, adverse = level_claim_outcome(claim, [rich] + thin)
        pooled = (0.5 * 200) / (200 + 60)
        self.assertAlmostEqual(float(adverse), pooled, places=6)


class FoldTests(unittest.TestCase):
    def test_a_fold_never_held_is_inconclusive_not_refuting(self):
        # A sparse position must not be refuted by its own sparseness.
        contract = _contract()
        claim = _claim(contract=contract)
        fold = _fold(claim, contract, 0, _series([0.002], 60), positions=[0] * 60)
        self.assertEqual(fold["result"], FOLD_INCONCLUSIVE)
        self.assertEqual(fold["periods_held"], 0)

    def test_costs_are_charged_and_reported(self):
        contract = _contract()
        claim = _claim(contract=contract)
        fold = _fold(claim, contract, 0, _series([0.002, -0.001], 100))
        self.assertGreater(float(fold["costs"]["cost_total"]), 0.0)
        self.assertLess(float(fold["costs"]["net_total"]), float(fold["costs"]["gross_total"]))

    def test_costs_can_turn_a_positive_gross_fold_into_a_negative_one(self):
        # The whole reason net-only is not a precaution.
        expensive = _contract(commission="0.01", half_spread="0.01", slippage="0.01")
        claim = _claim(contract=expensive)
        fold = _fold(claim, expensive, 0, _series([0.002, -0.001], 100))
        self.assertEqual(fold["result"], FOLD_NOT_MET)
        self.assertLess(float(fold["mean_net_return"]), 0.0)


class OutcomeTests(unittest.TestCase):
    def _folds(self, claim, contract, pattern, count=5, periods=120):
        return [_fold(claim, contract, index, _series(pattern, periods))
                for index in range(count)]

    def test_a_premium_that_pays_through_observed_losses_validates(self):
        contract = _contract(commission="0.00001", half_spread="0", slippage="0")
        claim = _claim(contract=contract)
        folds = self._folds(claim, contract, [0.004, 0.004, 0.004, -0.001])
        outcome, reason, consistency, adverse = level_claim_outcome(claim, folds)
        self.assertEqual(outcome, OUTCOME_VALIDATED)
        self.assertIsNone(reason)
        self.assertGreater(float(adverse), 0.10)

    def test_too_few_usable_folds_is_insufficient(self):
        contract = _contract()
        claim = _claim(contract=contract, minimum_folds_required=5)
        folds = self._folds(claim, contract, [0.004, -0.001], count=2)
        self.assertEqual(level_claim_outcome(claim, folds)[1], REASON_FOLDS_BELOW_MINIMUM)

    def test_inconsistent_folds_are_refuted_not_left_unjudged(self):
        contract = _contract(commission="0.00001", half_spread="0", slippage="0")
        claim = _claim(contract=contract, consistency_threshold="0.9")
        good = self._folds(claim, contract, [0.004, 0.004, 0.004, -0.001], count=2)
        bad = [_fold(claim, contract, index, _series([-0.004, 0.001], 120))
               for index in range(2, 5)]
        outcome, reason, _, _ = level_claim_outcome(claim, good + bad)
        self.assertEqual(outcome, OUTCOME_NOT_VALIDATED)
        self.assertEqual(reason, REASON_CONSISTENCY_BELOW)

    def test_a_breached_drawdown_limit_refuses_a_consistent_premium(self):
        contract = _contract(commission="0.00001", half_spread="0", slippage="0")
        claim = _claim(contract=contract, maximum_drawdown="0.02")
        folds = self._folds(claim, contract, [0.004] * 20 + [-0.01] * 4)
        outcome, reason, _, _ = level_claim_outcome(claim, folds)
        self.assertEqual(outcome, OUTCOME_NOT_VALIDATED)
        self.assertEqual(reason, REASON_DRAWDOWN_EXCEEDED)

    def test_the_outcome_vocabulary_is_the_one_governance_already_consumes(self):
        self.assertTrue({OUTCOME_VALIDATED, OUTCOME_NOT_VALIDATED, OUTCOME_INSUFFICIENT}
                        <= p.STATISTICAL_VALIDATION_OUTCOMES)


class SealingTests(unittest.TestCase):
    def _sealed(self, tmp):
        contract = _contract(commission="0.00001", half_spread="0", slippage="0")
        claim = _claim(contract=contract)
        folds = [_fold(claim, contract, index, _series([0.004, 0.004, 0.004, -0.001], 120))
                 for index in range(5)]
        record = constitute_level_claim_validation(
            Path(tmp) / "level.json", claim=claim, folds=folds,
            validated_at="2026-10-01T00:00:00Z", validation_code_revision=REVISION)
        return record

    def test_a_sealed_verdict_round_trips_and_recomputes(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = self._sealed(tmp)
            path = Path(tmp) / "level.json"
            self.assertEqual(load_level_claim_validation(path, record["validation_id"]), record)
            self.assertEqual(verified_level_claim_validation(path, record["validation_id"]),
                             record)

    def test_re_sealing_later_keeps_the_first_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = self._sealed(tmp)
            again = self._sealed(tmp)
            self.assertEqual(first, again)
            self.assertEqual(
                len(json.loads((Path(tmp) / "level.json").read_bytes())["validations"]), 1)

    def test_an_edited_outcome_no_longer_reproduces(self):
        # Recomputing from the folds rather than trusting the stored verdict.
        with tempfile.TemporaryDirectory() as tmp:
            record = self._sealed(tmp)
            path = Path(tmp) / "level.json"
            raw = path.read_text("utf-8").replace(
                f'"outcome": "{record["outcome"]}"', '"outcome": "VALIDATED"'
            ).replace('"outcome_reason": null', '"outcome_reason": "edited"')
            path.write_text(raw, encoding="utf-8")
            with self.assertRaises(ValueError):
                load_level_claim_validation(path, record["validation_id"])

    def test_the_record_carries_the_claim_it_was_judged_under(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = self._sealed(tmp)
            self.assertEqual(record["claim"]["minimum_adverse_episodes"], 1)
            self.assertTrue(record["claim"]["cost_contract_id"].startswith("COST_CONTRACT|"))


if __name__ == "__main__":
    unittest.main()


class AdverseMagnitudeTests(unittest.TestCase):
    """Found by using the judge on the real carry: counting ANY loss as adverse
    let two placid years satisfy representativeness on daily noise, while the
    window held no episode of the kind that ends a carry."""

    def test_the_threshold_is_required_and_has_no_default(self):
        with self.assertRaises(TypeError):
            level_claim(position_description="x" * 10, cost_contract=_contract(),
                        minimum_folds_required=3, consistency_threshold="0.7",
                        minimum_adverse_episodes=1, maximum_drawdown="0.15",
                        source="s")

    def test_a_positive_threshold_is_refused(self):
        with self.assertRaises(ValueError):
            _claim(adverse_period_threshold="0.01")

    def test_magnitude_separates_noise_from_the_tail(self):
        noise = _series([0.002, -0.001], 100)
        self.assertAlmostEqual(adverse_period_frequency(noise, 0.0), 0.5)
        self.assertAlmostEqual(adverse_period_frequency(noise, -0.01), 0.0)

    def test_small_daily_losses_no_longer_satisfy_the_gate(self):
        contract = _contract(commission="0", half_spread="0", slippage="0")
        claim = _claim(contract=contract, adverse_period_threshold="-0.01")
        folds = [_fold(claim, contract, index, _series([0.002, -0.001], 120))
                 for index in range(5)]
        outcome, reason, _, adverse = level_claim_outcome(claim, folds)
        self.assertEqual(outcome, OUTCOME_INSUFFICIENT)
        self.assertEqual(reason, REASON_TAIL_NOT_COVERED)
        self.assertEqual(float(adverse), 0.0)

    def test_a_window_that_did_contain_the_tail_still_passes(self):
        contract = _contract(commission="0", half_spread="0", slippage="0")
        claim = _claim(contract=contract, adverse_period_threshold="-0.01",
                       minimum_adverse_episodes=1)
        folds = [_fold(claim, contract, index, _series([0.006] * 8 + [-0.02, -0.015], 120))
                 for index in range(5)]
        outcome, reason, _, adverse = level_claim_outcome(claim, folds)
        self.assertGreater(float(adverse), 0.10)
        self.assertNotEqual(reason, REASON_TAIL_NOT_COVERED)


class TailCoverageTests(unittest.TestCase):
    """Schema 1 asked for a FREQUENCY of adverse periods, which is the
    monitorability question. Applied to the measurement question it is
    unanswerable -- a tail is rare by definition -- and the consequence was
    measured: a window holding the March 2020 collapse and a placid one came back
    with the same verdict for the same reason."""

    def test_consecutive_losses_are_one_episode_not_several(self):
        # March 2020 cost 10.48% on the 12th and 2.21% on the 13th. Counting that
        # as two observations of the tail is the same overstatement in miniature
        # that counting daily noise as adverse was.
        self.assertEqual(adverse_episodes([0.01, -0.02, -0.03, 0.01], -0.01), 1)
        self.assertEqual(adverse_episodes([-0.02, 0.01, -0.03], -0.01), 2)
        self.assertEqual(adverse_episodes([0.01, -0.005], -0.01), 0)
        self.assertEqual(adverse_episodes([], -0.01), 0)

    def test_a_tail_too_rare_for_any_frequency_floor_still_counts_as_covered(self):
        # Two adverse days in 730 is 0.27% -- below every sane frequency floor,
        # and exactly what a tail looks like.
        contract = _contract(commission="0", half_spread="0", slippage="0")
        claim = _claim(contract=contract, adverse_period_threshold="-0.01",
                       minimum_adverse_episodes=1)
        folds = [_fold(claim, contract, index, _series([0.002], 120)) for index in range(4)]
        folds.append(_fold(claim, contract, 4,
                           _series([0.002], 118) + [-0.11, -0.03]))
        outcome, reason, consistency, _ = level_claim_outcome(claim, folds)
        self.assertNotEqual(reason, REASON_TAIL_NOT_COVERED)
        self.assertIsNotNone(consistency)

    def test_the_placid_window_is_still_refused(self):
        contract = _contract(commission="0", half_spread="0", slippage="0")
        claim = _claim(contract=contract, adverse_period_threshold="-0.01",
                       minimum_adverse_episodes=1)
        folds = [_fold(claim, contract, index, _series([0.002, -0.001], 120))
                 for index in range(5)]
        outcome, reason, _, _ = level_claim_outcome(claim, folds)
        self.assertEqual(outcome, OUTCOME_INSUFFICIENT)
        self.assertEqual(reason, REASON_TAIL_NOT_COVERED)

    def test_the_two_windows_are_now_distinguishable(self):
        # The property the separation buys, stated as the comparison that failed.
        contract = _contract(commission="0", half_spread="0", slippage="0")
        claim = _claim(contract=contract, adverse_period_threshold="-0.01",
                       minimum_adverse_episodes=1)
        placid = [_fold(claim, contract, i, _series([0.002, -0.001], 120)) for i in range(5)]
        with_tail = [_fold(claim, contract, i, _series([0.002, -0.001], 120)) for i in range(4)]
        with_tail.append(_fold(claim, contract, 4, _series([0.002, -0.001], 118) + [-0.11, -0.03]))
        self.assertNotEqual(level_claim_outcome(claim, placid)[1],
                            level_claim_outcome(claim, with_tail)[1])

    def test_the_frequency_survives_as_a_reported_number(self):
        # It moves to the monitoring contract as a condition of OPERATING, and
        # stays here only as something the record states.
        contract = _contract(commission="0", half_spread="0", slippage="0")
        claim = _claim(contract=contract)
        fold = _fold(claim, contract, 0, _series([0.002, -0.001], 100))
        self.assertAlmostEqual(float(fold["adverse_period_frequency"]), 0.5)
        self.assertIn("adverse_episodes", fold)

    def test_a_schema_1_claim_is_still_judged_by_the_rule_it_was_sealed_under(self):
        # Never re-adjudicated under a rule that did not exist when it was issued.
        contract = _contract(commission="0", half_spread="0", slippage="0")
        modern = _claim(contract=contract)
        legacy = dict(modern)
        del legacy["minimum_adverse_episodes"]
        legacy["schema_version"] = "1"
        legacy["minimum_adverse_period_frequency"] = "0.99"
        legacy["claim_id"] = "LEVEL_CLAIM|" + p.digest(p.encoded(
            {k: v for k, v in legacy.items() if k != "claim_id"}))
        folds = [_fold(modern, contract, index, _series([0.002, -0.001], 120))
                 for index in range(5)]
        self.assertEqual(level_claim_outcome(legacy, folds)[1],
                         "ADVERSE_PERIODS_BELOW_MINIMUM_FREQUENCY")
