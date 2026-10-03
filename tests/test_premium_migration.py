"""The three held-position Hypotheses, migrated from their own runners to specs.

SPY, the credit premium and SVXY each had a runner of their own, and the three
shared 52 to 61 percent of their executable code. They now run through ONE engine
from a JSON spec each, and this is the proof that nothing changed: the whole path,
`run_spec` on the repository's real sealed artifacts, with a network that raises if
anything so much as touches it, must reproduce every figure and every gate state of
the admission record already sealed for each.

WHAT THIS CAUGHT BEFORE IT WAS WRITTEN. The monitor inputs the old runners read --
the VIX curve for SVXY, Baa spreads for the credit premium -- had never been
persisted: the runners sealed their FRED captures in memory only, so an admission
citing a link of 2 of 5 folds could not be re-derived from the repository. The engine
persists what it captures, so the migration also closed that gap, and re-fetching the
series today reproduced the sealed trigger counts exactly (173 days for SVXY, none
for the credit premium).
"""

import json
import unittest
from pathlib import Path

from tramitago_quant_core.research.premium_engine import validate_spec
from tramitago_quant_core.research.premium_runner import run_spec

REPO = Path(__file__).resolve().parents[1]
ARTIFACTS = REPO / "artifacts" / "research"
SPECS = REPO / "scripts" / "research" / "specs"
PATHS = {"hypotheses": ARTIFACTS / "hypotheses.json",
         "pre_declarations": ARTIFACTS / "pre-declarations.json",
         "datasets": ARTIFACTS / "datasets", "checks": ARTIFACTS / "checks",
         "level_claims": ARTIFACTS, "admissions": ARTIFACTS / "admissions.json"}
CODE, NOW = "a" * 40, "2026-10-03T18:00:00.000000Z"


class NoNetwork:
    """Anything reaching out is a failure: the artifacts are sealed, so the run must
    be reproducible from the repository alone."""

    def capture_bars(self, **kwargs):
        raise AssertionError(f"a bar capture was requested for {kwargs.get('symbol')}")

    def relay_fred(self, series_id, start, end):
        raise AssertionError(f"a FRED fetch was requested for {series_id}")


def _spec(name):
    return json.loads((SPECS / f"{name}.json").read_text(encoding="utf-8"))


def _sealed_gate_states(hypothesis_id):
    path = ARTIFACTS / "admissions.json"
    records = [r for r in json.loads(path.read_bytes())["admissions"]
               if r["hypothesis_id"].endswith(hypothesis_id.split("|", 1)[1])
               or r["hypothesis_id"] == hypothesis_id]
    return [gate["state"] for gate in records[-1]["gates"]] if records else None


def _available(name):
    spec = _spec(name)
    slug_dir = spec["slug"].replace("-", "_")
    return (ARTIFACTS / "datasets" / slug_dir / "manifest.json").exists()


class MigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = {}
        for name in ("spy", "svxy", "credit-lqd-ief"):
            if not _available(name):
                continue
            spec = _spec(name)
            cls.results[name] = run_spec(spec, io=NoNetwork(), paths=PATHS, now=NOW,
                                         code_revision=CODE, seal=False)

    def _result(self, name):
        if name not in self.results:
            self.skipTest(f"the sealed {name} artifacts are not in this checkout")
        return self.results[name]

    def test_every_migrated_spec_is_valid_and_names_a_real_hypothesis(self):
        registry = {h["hypothesis_id"] for h in
                    json.loads(PATHS["hypotheses"].read_bytes())["hypotheses"]}
        for name in ("spy", "svxy", "credit-lqd-ief"):
            spec = _spec(name)
            self.assertTrue(validate_spec(spec))
            self.assertIn(spec["hypothesis_id"], registry, name)

    def test_spy_reproduces_its_sealed_figures_offline(self):
        r = self._result("spy")
        self.assertTrue(r["resumed"])
        self.assertAlmostEqual(r["weight"], 0.280, places=3)
        self.assertAlmostEqual(r["sharpe_point"], 0.8125, places=4)
        self.assertAlmostEqual(r["sharpe_bound"], 0.2695, places=4)
        self.assertAlmostEqual(r["drawdown_bound"], 0.149976, places=6)
        self.assertEqual(r["level_claim"]["outcome"], "VALIDATED")
        self.assertAlmostEqual(float(r["level_claim"]["consistency"]), 0.857, places=3)

    def test_svxy_reproduces_its_sealed_figures_and_its_monitor_offline(self):
        r = self._result("svxy")
        self.assertAlmostEqual(r["weight"], 0.109, places=3)
        self.assertAlmostEqual(r["sharpe_point"], 0.5021, places=4)
        self.assertAlmostEqual(r["sharpe_bound"], -0.0445, places=4)
        self.assertAlmostEqual(r["drawdown_bound"], 0.149716, places=6)
        monitor = r["monitor"]
        # The trigger count recomputed from series fetched AGAIN today equals the
        # one the original run saw: FRED served the same data.
        self.assertEqual(monitor["trigger_days"], 173)
        self.assertEqual((monitor["link_met"], monitor["link_usable"]), (2, 5))
        self.assertEqual(monitor["link_outcome"], "REACHABLE_AND_FAILED")

    def test_the_credit_pair_reproduces_its_sealed_figures_and_its_monitor_offline(self):
        r = self._result("credit-lqd-ief")
        self.assertAlmostEqual(r["weight"], 0.396, places=3)
        self.assertAlmostEqual(r["sharpe_point"], 0.1875, places=4)
        self.assertAlmostEqual(r["sharpe_bound"], -0.3374, places=4)
        self.assertAlmostEqual(r["drawdown_bound"], 0.149982, places=6)
        self.assertAlmostEqual(float(r["level_claim"]["consistency"]), 0.714, places=3)
        monitor = r["monitor"]
        self.assertEqual(monitor["trigger_days"], 0)
        self.assertEqual(monitor["link_outcome"], "UNREACHABLE")
        self.assertEqual(monitor["link_form"], "M2_IDENTITY")

    def test_the_gate_states_match_every_sealed_admission(self):
        # The state of all six gates, in order, against the record sealed for each.
        for name in ("spy", "svxy", "credit-lqd-ief"):
            r = self._result(name)
            sealed = _sealed_gate_states(_spec(name)["hypothesis_id"])
            self.assertIsNotNone(sealed, name)
            self.assertEqual([gate["state"] for gate in r["gates"]], sealed, name)

    def test_the_identity_form_is_closed_for_svxy_whatever_its_spec_offers(self):
        # M2_AVAILABILITY|04a35abd. The spec declares an identity and the empirical
        # link was reachable and failed, so R3 FAILS -- exactly what the first
        # runner got wrong for eight hours.
        r = self._result("svxy")
        self.assertEqual(
            [g["state"] for g in r["gates"] if "monitorability" in g["gate"]], ["FAILED"])

    def test_the_adverse_threshold_scales_with_the_weight_for_every_migrated_spec(self):
        for name, base in (("spy", -0.02), ("svxy", -0.03), ("credit-lqd-ief", -0.01)):
            r = self._result(name)
            scaled = float(r["level_claim"]["claim"]["adverse_period_threshold"])
            self.assertAlmostEqual(scaled, base * r["weight"], places=6, msg=name)

    def test_the_credit_pair_cannot_be_priced_as_one_leg(self):
        # A pair priced as one leg would understate its cost by half while reading as
        # an ordinary spec, so the engine refuses the mismatch outright.
        spec = _spec("credit-lqd-ief")
        self.assertEqual(spec["cost"]["legs"], 2)
        spec["cost"]["legs"] = 1
        with self.assertRaises(ValueError) as caught:
            validate_spec(spec)
        self.assertIn("cost.legs must be 2", str(caught.exception))

    def test_a_single_instrument_cannot_be_priced_as_two(self):
        spec = _spec("spy")
        spec["cost"]["legs"] = 2
        with self.assertRaises(ValueError):
            validate_spec(spec)

if __name__ == "__main__":
    unittest.main()
