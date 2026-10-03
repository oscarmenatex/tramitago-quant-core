"""The orchestration the twenty-three per-asset scripts each re-implemented, tested.

None of those scripts could be tested because credentials, an ssh hop and a network
sat in the middle of every one of them. Here the only two things that touch the
outside world are injected, so the whole path -- refuse an unanswered Hypothesis,
capture and seal the dataset, capture the auxiliary series, judge, seal -- runs
against synthetic bars in a temporary directory.
"""

import json
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tramitago_quant_core.research.hypothesis import constitute_hypothesis
from tramitago_quant_core.research.premium_runner import run_spec, WARMUP, HORIZON

CODE = "a" * 40
NOW = "2026-10-03T12:00:00.123456Z"
PERIOD = ("2020-01-06T00:00:00Z", "2024-01-02T00:00:00Z")      # about four years


def _weekdays(start="2019-12-01", stop="2024-03-01"):
    day, out = datetime.fromisoformat(start).replace(tzinfo=timezone.utc), []
    end = datetime.fromisoformat(stop).replace(tzinfo=timezone.utc)
    while day < end:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


class FakeIO:
    """Synthetic bars and a synthetic FRED, counting every call that reaches out."""

    def __init__(self, annual_distribution_yield=0.09, leak_key=False):
        self.days = _weekdays()
        self.yield_ = annual_distribution_yield
        self.leak_key = leak_key
        self.bar_calls, self.fred_calls = [], []
        self._raw = {}

    def _raw_closes(self, symbol):
        if symbol not in self._raw:
            rng, price, closes = random.Random(sum(map(ord, symbol))), 100.0, []
            for _ in self.days:
                price *= 1 + rng.gauss(0.0004, 0.008)
                closes.append(price)
            self._raw[symbol] = closes
        return self._raw[symbol]

    def capture_bars(self, *, symbol, start, end, warmup, horizon, acquired_at, adjustment):
        self.bar_calls.append((symbol, adjustment))
        stamps = [day.isoformat().replace("+00:00", "Z") for day in self.days]
        first = next(i for i, s in enumerate(stamps) if s[:10] >= start[:10])
        last = max(i for i, s in enumerate(stamps) if s[:10] < end[:10])
        chosen = range(first - warmup, last + 1 + horizon)
        raw = self._raw_closes(symbol)
        n = len(self.days)
        rows = []
        for i in chosen:
            close = raw[i]
            if adjustment != "raw":
                # Back-adjusted for distributions: earlier prices scaled DOWN, so the
                # adjusted-to-raw ratio rises toward one by the end of the series.
                close *= (1 + self.yield_) ** (-(n - 1 - i) / 252)
            rows.append({"instrument": symbol, "timestamp": stamps[i], "open": close,
                         "high": close * 1.002, "low": close * 0.998, "close": close,
                         "volume": 1_000_000.0})
        capture = {"kind": "alpaca-equity-bars", "schema_version": "1", "symbol": symbol,
                   "source": "SYNTHETIC test bars", "capture_period": {
                       "start_utc": rows[0]["timestamp"],
                       "end_exclusive_utc": rows[-1]["timestamp"]}}
        return rows, capture, b"{}"

    def relay_fred(self, series_id, start, end):
        self.fred_calls.append(series_id)
        rng = random.Random(7)
        body = {"observations": [
            {"date": day.date().isoformat(), "value": f"{rng.uniform(10, 30):.2f}"}
            for day in self.days if start <= day.date().isoformat() <= end]}
        if self.leak_key:
            body["api_key"] = "SECRET"
        return json.dumps(body).encode("utf-8")


def _spec(hypothesis_id, monitor=True, checks=True):
    spec = {
        "slug": "test-premium", "hypothesis_id": hypothesis_id,
        "instrument": {"provider": "alpaca_equity", "symbol": "TEST"},
        "cost": {"commission": "0", "half_spread": "0.0001", "slippage": "0.00002",
                 "legs": 1, "source": "declared for a test and not measured"},
        "sizing": {"drawdown_limit": "0.15"},
        "bootstrap": {"resamples": 100},
        "level_claim": {"folds": 7, "consistency_threshold": "0.70",
                        "adverse_threshold": "-0.015", "minimum_adverse_episodes": 1},
        "survival": {"counterparty": "investors shedding a risk they are mandated to shed",
                     "why_they_accept_losing": "they are buying certainty rather than losing",
                     "what_would_end_it": "the risk ceasing to be disliked by anyone at all"},
        "decision": {"build_cost": "0", "life_years": "3", "tranche": "1000"}}
    if monitor:
        spec["monitor"] = {"kind": "implied_minus_realised", "variable": "implied minus realised",
                           "implied": "VIXCLS", "underlying": "SPY", "window": 21, "floor": "0",
                           "source": "a synthetic series used only in a test"}
        spec["controls"] = [{"kind": "underlying_forward_returns", "name": "SPY"}]
    if checks:
        spec["checks"] = [{"kind": "distribution_adjustment",
                           "expected_annual_yield": ["0.04", "0.16"]}]
    return spec


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.paths = {"hypotheses": self.root / "hypotheses.json",
                      "pre_declarations": self.root / "pre-declarations.json",
                      "datasets": self.root / "datasets", "checks": self.root / "checks",
                      "level_claims": self.root, "admissions": self.root / "admissions.json"}

    def _hypothesis(self, created="2026-09-01T00:00:00.123456Z", period=PERIOD):
        # Created BEFORE the pre-declaration contract came into force, so the guard
        # does not apply to it. The guard has its own test below.
        record = constitute_hypothesis(
            self.paths["hypotheses"],
            description="A held-position Hypothesis used only by this test, declared before "
                        "any bar was captured.",
            target_metric="net_sharpe(TEST held continuously, net of execution cost)",
            expected_direction="INCREASE",
            constraints={"period": {"start_utc": period[0], "end_exclusive_utc": period[1]},
                         "universe": ["TEST"],
                         "variables": ["close", "forward_return_1d", "sma_close_3"]},
            acceptance_criterion={"metric": "net_sharpe(TEST held continuously, net of "
                                            "execution cost)", "comparison": "GE",
                                  "threshold": "0.50", "expected_direction": "INCREASE"},
            creation_timestamp=created, status="PROPOSED", created_by="test",
            provenance=["TEST|an identifier that names where this came from"],
            code_revision=CODE, system_version="0.1.0")
        return record["hypothesis_id"]

    def test_the_whole_path_runs_and_seals_what_it_should(self):
        hid, io = self._hypothesis(), FakeIO()
        result = run_spec(_spec(hid), io=io, paths=self.paths, now=NOW, code_revision=CODE)
        self.assertIsNone(result["void"])
        self.assertTrue((self.paths["datasets"] / "test_premium" / "manifest.json").exists())
        for name in ("raw_bars.closes.json", "underlying_bars.closes.json",
                     "VIXCLS.capture.json", "VIXCLS.raw.json"):
            self.assertTrue((self.paths["checks"] / "test_premium" / name).exists(), name)
        self.assertEqual(result["distribution_check"]["status"], "OK")
        self.assertIn("monitor", result)
        self.assertIn("SPY", result["controls"])
        self.assertTrue((self.root / "level-claim-validations-test-premium.json").exists())
        self.assertIn("validation_id", result["sealed"])

    def test_a_validated_claim_reaches_admission_and_an_unvalidated_one_does_not(self):
        hid = self._hypothesis()
        result = run_spec(_spec(hid), io=FakeIO(), paths=self.paths, now=NOW,
                          code_revision=CODE)
        validated = result["level_claim"]["outcome"] == "VALIDATED"
        self.assertEqual("admission_id" in result["sealed"], validated)
        self.assertEqual(self.paths["admissions"].exists(), validated)

    def test_a_second_run_re_fetches_nothing_because_the_dataset_is_the_evidence(self):
        hid = self._hypothesis()
        run_spec(_spec(hid), io=FakeIO(), paths=self.paths, now=NOW, code_revision=CODE)
        second_io = FakeIO()
        again = run_spec(_spec(hid), io=second_io, paths=self.paths, now=NOW,
                         code_revision=CODE, seal=False)
        self.assertEqual(second_io.bar_calls, [])
        self.assertEqual(second_io.fred_calls, [])
        self.assertTrue(again["resumed"])

    def test_omitted_distributions_void_the_measurement_and_seal_no_judgement(self):
        hid = self._hypothesis()
        result = run_spec(_spec(hid), io=FakeIO(annual_distribution_yield=0.0),
                          paths=self.paths, now=NOW, code_revision=CODE)
        self.assertIn("outside the declared range", result["void"])
        self.assertNotIn("level_claim", result)
        self.assertFalse((self.root / "level-claim-validations-test-premium.json").exists())
        self.assertFalse(self.paths["admissions"].exists())
        # The captured data stays: it is what was observed.
        self.assertTrue((self.paths["datasets"] / "test_premium" / "manifest.json").exists())

    def test_seal_false_judges_without_writing_a_verdict(self):
        hid = self._hypothesis()
        result = run_spec(_spec(hid), io=FakeIO(), paths=self.paths, now=NOW,
                          code_revision=CODE, seal=False)
        self.assertIn("level_claim", result)
        self.assertEqual(result["sealed"], {})
        self.assertFalse((self.root / "level-claim-validations-test-premium.json").exists())

    def test_a_hypothesis_that_has_not_answered_its_seven_questions_costs_no_network_call(self):
        hid = self._hypothesis(created="2026-10-04T00:00:00.123456Z")
        io = FakeIO()
        with self.assertRaises(ValueError) as caught:
            run_spec(_spec(hid), io=io, paths=self.paths, now=NOW, code_revision=CODE)
        self.assertIn("seven", str(caught.exception).lower())
        self.assertEqual(io.bar_calls, [])
        self.assertEqual(io.fred_calls, [])

    def test_a_response_that_echoes_a_credential_is_refused_before_it_is_sealed(self):
        hid = self._hypothesis()
        with self.assertRaises(ValueError) as caught:
            run_spec(_spec(hid), io=FakeIO(leak_key=True), paths=self.paths, now=NOW,
                     code_revision=CODE)
        self.assertIn("key", str(caught.exception))
        self.assertFalse((self.paths["checks"] / "test_premium" / "VIXCLS.raw.json").exists())

    def test_a_window_shorter_than_three_years_is_refused_and_nothing_is_sealed(self):
        hid = self._hypothesis(period=("2020-01-06T00:00:00Z", "2021-01-04T00:00:00Z"))
        with self.assertRaises(ValueError) as caught:
            run_spec(_spec(hid), io=FakeIO(), paths=self.paths, now=NOW, code_revision=CODE)
        self.assertIn("REFUSED", str(caught.exception))
        self.assertFalse((self.paths["datasets"] / "test_premium").exists())

    def test_a_hypothesis_missing_from_the_registry_is_refused(self):
        self._hypothesis()
        with self.assertRaises(ValueError):
            run_spec(_spec("HYPOTHESIS|not-in-the-registry"), io=FakeIO(), paths=self.paths,
                     now=NOW, code_revision=CODE)

    def test_a_spec_without_a_monitor_or_checks_needs_no_auxiliary_capture(self):
        hid, io = self._hypothesis(), FakeIO()
        result = run_spec(_spec(hid, monitor=False, checks=False), io=io, paths=self.paths,
                          now=NOW, code_revision=CODE, seal=False)
        self.assertEqual(io.fred_calls, [])
        self.assertEqual([call[0] for call in io.bar_calls], ["TEST"])
        self.assertNotIn("monitor", result)

    def test_the_dataset_contract_warmup_and_horizon_are_the_ones_the_runner_asks_for(self):
        self.assertEqual((WARMUP, HORIZON), (2, 1))


if __name__ == "__main__":
    unittest.main()
