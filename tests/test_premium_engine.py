"""The engine judges a Hypothesis from a spec, and reproduces what was already sealed.

THE PROOF THAT MATTERS is the regression class below. Twenty-three research runners
each carried their own copy of this judgement and none had a test, so there was no
way to say that a refactor preserved anything. Run on the sealed SPY and SVXY
datasets the engine reproduces their weight, point Sharpe, lower bound, drawdown
bound and level-claim verdict to the last decimal -- which is a claim about numbers
already in the repository and not about the engine resembling the scripts.
"""

import copy
import csv
import unittest
from pathlib import Path

from tramitago_quant_core.governance.admission import (
    _monitorability_gate, GATE_PASSED, GATE_FAILED,
)
from tramitago_quant_core.research.premium_engine import (
    judge, validate_spec, distribution_check, _monitor_evidence,
)

ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts" / "research" / "datasets"
SURVIVAL = {"counterparty": "investors shedding a risk they are mandated to shed",
            "why_they_accept_losing": "they are buying certainty rather than losing",
            "what_would_end_it": "the risk ceasing to be disliked by anyone at all"}


def _spec(slug="x", hypothesis="HYPOTHESIS|probe", half="0.00005", slip="0.00001",
          adverse="-0.02", **extra):
    return {"slug": slug, "hypothesis_id": hypothesis,
            "instrument": {"provider": "alpaca_equity", "symbol": "TEST"},
            "cost": {"commission": "0", "half_spread": half, "slippage": slip, "legs": 1,
                     "source": "declared for a test and not measured"},
            "sizing": {"drawdown_limit": "0.15"},
            "level_claim": {"folds": 7, "consistency_threshold": "0.70",
                            "adverse_threshold": adverse, "minimum_adverse_episodes": 5},
            "survival": dict(SURVIVAL),
            "decision": {"build_cost": "0", "life_years": "3", "tranche": "1000"},
            **extra}


def _load(slug):
    path = ARTIFACTS / slug / "dataset.csv"
    if not path.exists():
        return None
    return list(csv.DictReader(path.open(encoding="utf-8")))


class SealedRegressionTests(unittest.TestCase):
    """The engine against numbers sealed before it existed."""

    @classmethod
    def setUpClass(cls):
        cls.spy_rows = _load("equity_risk_premium_spy")
        cls.svxy_rows = _load("volatility_premium_svxy")
        cls.spy = cls.svxy = None
        if cls.spy_rows:
            cls.spy = judge(_spec("equity-risk-premium-spy", "HYPOTHESIS|426cd26a-10ab-4c5e-"
                                  "ba5d-9e88510a4613"), cls.spy_rows)
        if cls.svxy_rows:
            cls.svxy = judge(_spec("volatility-premium-svxy", "HYPOTHESIS|205003fe-7b8d-4fc0-"
                                   "b30c-936a404cd7c0", half="0.0003", slip="0.0001",
                                   adverse="-0.03"), cls.svxy_rows)

    def test_spy_reproduces_its_sealed_figures(self):
        if self.spy is None:
            self.skipTest("the sealed SPY dataset is not in this checkout")
        self.assertAlmostEqual(self.spy["weight"], 0.280, places=3)
        self.assertAlmostEqual(self.spy["sharpe_point"], 0.8125, places=4)
        self.assertAlmostEqual(self.spy["sharpe_bound"], 0.2695, places=4)
        self.assertAlmostEqual(self.spy["drawdown_bound"], 0.149976, places=6)
        self.assertEqual(self.spy["level_claim"]["outcome"], "VALIDATED")
        self.assertAlmostEqual(float(self.spy["level_claim"]["consistency"]), 0.857, places=3)

    def test_svxy_reproduces_its_sealed_figures(self):
        if self.svxy is None:
            self.skipTest("the sealed SVXY dataset is not in this checkout")
        self.assertAlmostEqual(self.svxy["weight"], 0.109, places=3)
        self.assertAlmostEqual(self.svxy["sharpe_point"], 0.5021, places=4)
        self.assertAlmostEqual(self.svxy["sharpe_bound"], -0.0445, places=4)
        self.assertAlmostEqual(self.svxy["drawdown_bound"], 0.149716, places=6)
        self.assertAlmostEqual(float(self.svxy["level_claim"]["consistency"]), 0.714, places=3)

    def test_the_gate_states_match_the_sealed_admission_of_spy(self):
        # R3 is NOT_EVALUABLE because no monitor was declared, and everything else
        # passes: exactly what the sealed record says.
        if self.spy is None:
            self.skipTest("the sealed SPY dataset is not in this checkout")
        states = {gate["gate"].split(":")[0].split(" ")[0]: gate["state"]
                  for gate in self.spy["gates"]}
        self.assertEqual(sorted(set(states.values())), ["NOT_EVALUABLE", "PASSED"])
        self.assertEqual(states["R3"], "NOT_EVALUABLE")

    def test_the_gate_states_match_the_sealed_admission_of_svxy(self):
        # The Sharpe bound fails and nothing else does, with no monitor declared.
        if self.svxy is None:
            self.skipTest("the sealed SVXY dataset is not in this checkout")
        failed = [gate["gate"] for gate in self.svxy["gates"] if gate["state"] == "FAILED"]
        self.assertEqual(len(failed), 1)
        self.assertIn("Sharpe", failed[0])

    def test_the_adverse_threshold_scales_with_the_weight(self):
        # -2% was a stress day for the UNSIZED position; at a 28% weight the same
        # market event moves it -0.56%. Left absolute it reported a tail that was
        # entirely present as absent.
        if self.spy is None:
            self.skipTest("the sealed SPY dataset is not in this checkout")
        scaled = float(self.spy["level_claim"]["claim"]["adverse_period_threshold"])
        self.assertAlmostEqual(scaled, -0.02 * self.spy["weight"], places=6)


class SpecValidationTests(unittest.TestCase):
    """Strict on purpose: a typo in a field that decides a gate is how a threshold
    ends up silently defaulted."""

    def test_a_complete_spec_validates(self):
        self.assertTrue(validate_spec(_spec()))

    def test_an_unknown_top_level_key_is_a_typo_until_proven_otherwise(self):
        with self.assertRaises(ValueError) as caught:
            validate_spec(_spec(sizng={"drawdown_limit": "0.15"}))
        self.assertIn("sizng", str(caught.exception))

    def test_required_sections_may_not_be_omitted(self):
        for key in ("instrument", "cost", "sizing", "level_claim", "survival", "decision"):
            spec = _spec()
            del spec[key]
            with self.assertRaises(ValueError):
                validate_spec(spec)

    def test_survival_must_be_written_not_omitted(self):
        spec = _spec()
        spec["survival"]["why_they_accept_losing"] = "  "
        with self.assertRaises(ValueError):
            validate_spec(spec)

    def test_a_positive_adverse_threshold_is_refused(self):
        with self.assertRaises(ValueError):
            validate_spec(_spec(adverse="0.02"))

    def test_a_drawdown_limit_outside_zero_one_is_refused(self):
        for limit in ("0", "1", "1.5", "-0.1"):
            spec = _spec()
            spec["sizing"]["drawdown_limit"] = limit
            with self.assertRaises(ValueError):
                validate_spec(spec)

    def test_negative_costs_are_refused(self):
        with self.assertRaises(ValueError):
            validate_spec(_spec(half="-0.0001"))

    def test_an_unknown_monitor_kind_is_refused(self):
        with self.assertRaises(ValueError):
            validate_spec(_spec(monitor={"kind": "vibes", "floor": "0"}))

    def test_the_hypothesis_must_be_referenced(self):
        with self.assertRaises(ValueError):
            validate_spec(_spec(hypothesis="the premium"))


class DistributionCheckTests(unittest.TestCase):
    """A fund paying large monthly distributions can be mis-adjusted either way, and
    nothing in the adjusted series alone reveals it."""

    DAYS = [f"d{i:05d}" for i in range(2520)]          # ten years

    def _series(self, annual_yield):
        raw = {day: 100.0 * (1.0002 ** i) for i, day in enumerate(self.DAYS)}
        n = len(self.DAYS)
        adjusted = {day: raw[day] * ((1 + annual_yield) ** (-(n - 1 - i) / 252))
                    for i, day in enumerate(self.DAYS)}
        return adjusted, raw

    def test_a_realistic_adjustment_is_accepted(self):
        adjusted, raw = self._series(0.09)
        check = distribution_check(self.DAYS, adjusted, raw, ["0.04", "0.16"])
        self.assertEqual(check["status"], "OK")
        self.assertAlmostEqual(float(check["implied_annual_yield"]), 0.09, places=3)

    def test_distributions_that_were_omitted_void_the_measurement(self):
        # Adjusted equals raw: the Sharpe would be UNDERSTATED by roughly the yield.
        _, raw = self._series(0.09)
        check = distribution_check(self.DAYS, raw, raw, ["0.04", "0.16"])
        self.assertEqual(check["status"], "VOID")
        self.assertIn("outside the declared range", check["reason"])

    def test_distributions_that_were_double_counted_void_it_too(self):
        adjusted, raw = self._series(0.40)
        self.assertEqual(
            distribution_check(self.DAYS, adjusted, raw, ["0.04", "0.16"])["status"], "VOID")

    def test_a_ratio_that_falls_cannot_be_a_distribution_adjustment(self):
        adjusted, raw = self._series(0.09)
        adjusted[self.DAYS[1000]] *= 0.95
        check = distribution_check(self.DAYS, adjusted, raw, ["0.04", "0.16"])
        self.assertEqual(check["status"], "VOID")
        self.assertGreater(check["decreases"], 0)

    def test_too_few_overlapping_days_cannot_be_checked_at_all(self):
        adjusted, raw = self._series(0.09)
        check = distribution_check(self.DAYS[:100], adjusted, raw, ["0.04", "0.16"])
        self.assertEqual(check["status"], "VOID")


class VoidMeasurementTests(unittest.TestCase):
    def _rows(self):
        return [{"timestamp": f"2024-01-{(i % 28) + 1:02d}T00:00:00Z", "close": "100",
                 "volume": "1000000", "forward_return_1d": "0.0004"} for i in range(60)]

    def test_a_declared_check_with_no_raw_closes_judges_nothing(self):
        spec = _spec(checks=[{"kind": "distribution_adjustment",
                              "expected_annual_yield": ["0.04", "0.16"]}])
        spec["level_claim"]["folds"] = 3
        result = judge(spec, self._rows())
        self.assertIn("never examined", result["void"])
        self.assertNotIn("level_claim", result)

    def test_an_unknown_check_is_refused_not_skipped(self):
        spec = _spec(checks=[{"kind": "astrology"}])
        spec["level_claim"]["folds"] = 3
        with self.assertRaises(ValueError):
            judge(spec, self._rows(), raw_closes={})


class MonitorEvidenceTests(unittest.TestCase):
    """The identity form answers for evidence that cannot be gathered, never for
    evidence that was gathered and points the other way (M2_AVAILABILITY|04a35abd).
    This is the property the first volatility runner violated for eight hours."""

    IDENTITY = {"identity": "the roll is the slope", "parameter": "0",
                "parameter_source": "the sign change", "holds_for_range": "any curve shape"}

    def _spec(self, identity=True):
        monitor = {"variable": "VIX3M minus VIX", "floor": "0"}
        if identity:
            monitor["identity"] = self.IDENTITY
        return {"monitor": monitor}

    def _gate(self, link, identity=True):
        evidence, outcome, _ = _monitor_evidence(self._spec(identity), None, link, 0.0005, None)
        return evidence, outcome, _monitorability_gate({"monitor": evidence}, "4")["state"]

    def test_a_link_that_clears_is_the_empirical_form_and_passes(self):
        evidence, outcome, state = self._gate((4, 5))
        self.assertEqual(evidence["link_form"], "M1_EMPIRICAL")
        self.assertEqual(outcome, "REACHABLE_AND_MET")
        self.assertEqual(state, GATE_PASSED)

    def test_a_reachable_link_that_failed_closes_the_identity_even_when_offered(self):
        evidence, outcome, state = self._gate((2, 5), identity=True)
        self.assertEqual(outcome, "REACHABLE_AND_FAILED")
        self.assertEqual(state, GATE_FAILED)

    def test_an_unreachable_link_falls_back_to_the_identity_when_one_is_declared(self):
        evidence, outcome, state = self._gate((1, 2), identity=True)
        self.assertEqual(outcome, "UNREACHABLE")
        self.assertEqual(evidence["link_form"], "M2_IDENTITY")
        self.assertEqual(state, GATE_PASSED)

    def test_an_unreachable_link_with_no_identity_declared_is_not_admitted(self):
        # Section 11.1: INSUFFICIENT_EVIDENCE is not a pass.
        evidence, outcome, state = self._gate((1, 2), identity=False)
        self.assertEqual(state, GATE_FAILED)

    def test_five_usable_folds_is_the_floor_not_four(self):
        # With the trigger concentrated in one fold the link came back 1 of 1 = 1.0
        # and cleared the threshold trivially. Four of four is still not five.
        self.assertEqual(self._gate((4, 4))[1], "UNREACHABLE")
        self.assertEqual(self._gate((5, 5))[1], "REACHABLE_AND_MET")

    def test_a_loss_if_dead_is_never_negative(self):
        evidence, _, _ = _monitor_evidence(self._spec(), None, (4, 5), -0.0003, None)
        self.assertEqual(evidence["expected_daily_loss_if_dead"], "0.00000000")


if __name__ == "__main__":
    unittest.main()
