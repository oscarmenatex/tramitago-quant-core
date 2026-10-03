"""The spec engine driven THROUGH the real Alpaca capture module, against a fake network.

WHY THIS EXISTS. The runner tests use an injected `FakeIO` whose capture_bars returns
synthetic rows directly, so they never touched capture_alpaca_equity_bars, the layer
that actually decides which dates a request covers. That is the layer XYLD's first real
run died in: `before_start[-0]` selected the front of a 400-day window and the capture
began thirteen months early. A test boundary placed ABOVE the module that fails proves
nothing about it.

Here the only thing faked is the network. The capture module, its calendar logic, its
coverage check, the warmup and horizon arithmetic, the adjustment and feed parameters
and the engine's own use of all of them run for real.
"""

import json
import random
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from tramitago_quant_core.data.alpaca_equity_series import capture_alpaca_equity_bars
from tramitago_quant_core.research.hypothesis import constitute_hypothesis
from tramitago_quant_core.research.premium_runner import run_spec

from test_premium_runner import _spec, CODE, NOW, PERIOD

SERIES_END = date(2024, 3, 1)


def _weekdays(first, last):
    day, out = date.fromisoformat(first), []
    while day <= date.fromisoformat(last):
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


class FakeMarket:
    """A fake Alpaca: calendar and bars for weekdays, with adjustment and feed honoured."""

    def __init__(self, distribution_yield=0.09, history_starts="2016-01-04"):
        self.requests = []
        self.yield_ = distribution_yield
        self.history_starts = date.fromisoformat(history_starts)

    def _raw_close(self, symbol, day):
        rng = random.Random(f"{symbol}|{day.isoformat()}")
        base = 100 * (1.0003 ** (day - date(2015, 1, 1)).days)
        return base * (1 + rng.uniform(-0.01, 0.01))

    def __call__(self, url, headers, timeout):
        parsed = urlparse(url)
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        self.requests.append((parsed.path, query))
        if parsed.path.endswith("/calendar"):
            days = _weekdays(query["start"], query["end"])
            return json.dumps([{"date": d.isoformat()} for d in days]).encode()
        symbol = parsed.path.split("/")[-2]
        days = [d for d in _weekdays(query["start"], query["end"])
                if d >= self.history_starts]
        bars = []
        for day in days:
            close = self._raw_close(symbol, day)
            if query["adjustment"] != "raw":
                # Back-adjusted: earlier prices scaled DOWN by later distributions.
                close *= (1 + self.yield_) ** (-(SERIES_END - day).days / 365)
            bars.append({"t": f"{day.isoformat()}T05:00:00Z", "o": close, "h": close * 1.002,
                         "l": close * 0.998, "c": close, "v": 1_000_000})
        return json.dumps({"bars": bars, "next_page_token": None}).encode()


class CaptureIO:
    """The engine's I/O, wired to the REAL capture module over the fake network."""

    def __init__(self, market):
        self.market, self.bar_calls, self.fred_calls = market, [], []

    def capture_bars(self, *, symbol, start, end, warmup, horizon, acquired_at, adjustment,
                     feed):
        self.bar_calls.append({"symbol": symbol, "warmup": warmup, "adjustment": adjustment,
                               "feed": feed})
        return capture_alpaca_equity_bars(
            symbol=symbol, evaluable_start_utc=start, evaluable_end_exclusive_utc=end,
            warmup_periods=warmup, horizon=horizon, acquired_at=acquired_at,
            credential_injector=lambda headers: headers, transport=self.market,
            feed=feed, adjustment=adjustment)

    def relay_fred(self, series_id, start, end):
        self.fred_calls.append(series_id)
        rng = random.Random(7)
        return json.dumps({"observations": [
            {"date": d.isoformat(), "value": f"{rng.uniform(10, 30):.2f}"}
            for d in _weekdays(start, end)]}).encode()


class ThroughTheRealCaptureTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.paths = {"hypotheses": self.root / "hypotheses.json",
                      "pre_declarations": self.root / "pre-declarations.json",
                      "datasets": self.root / "datasets", "checks": self.root / "checks",
                      "level_claims": self.root, "admissions": self.root / "admissions.json"}
        record = constitute_hypothesis(
            self.paths["hypotheses"],
            description="A held-position Hypothesis used only by this test, declared before "
                        "any bar was captured.",
            target_metric="net_sharpe(TEST held continuously, net of execution cost)",
            expected_direction="INCREASE",
            constraints={"period": {"start_utc": PERIOD[0], "end_exclusive_utc": PERIOD[1]},
                         "universe": ["TEST"],
                         "variables": ["close", "forward_return_1d", "sma_close_3"]},
            acceptance_criterion={"metric": "net_sharpe(TEST held continuously, net of "
                                            "execution cost)", "comparison": "GE",
                                  "threshold": "0.50", "expected_direction": "INCREASE"},
            creation_timestamp="2026-09-01T00:00:00.123456Z", status="PROPOSED",
            created_by="test", provenance=["TEST|an identifier that names where this came from"],
            code_revision=CODE, system_version="0.1.0")
        self.hypothesis_id = record["hypothesis_id"]

    def _spec(self, **kwargs):
        spec = _spec(self.hypothesis_id, **kwargs)
        spec["instrument"]["feed"] = "sip"
        return spec

    def test_the_full_path_runs_through_the_real_capture_module(self):
        io = CaptureIO(FakeMarket())
        result = run_spec(self._spec(), io=io, paths=self.paths, now=NOW, code_revision=CODE,
                          seal=False)
        self.assertIsNone(result["void"], result["void"])
        self.assertEqual(result["distribution_check"]["status"], "OK")
        self.assertIn("monitor", result)

    def test_auxiliary_captures_with_no_warmup_begin_at_the_window_not_before_it(self):
        # THE BUG. The raw bars and the monitor's underlying ask for warmup zero, and
        # the capture used to begin at the front of a 400-day calendar window.
        market = FakeMarket()
        io = CaptureIO(market)
        run_spec(self._spec(), io=io, paths=self.paths, now=NOW, code_revision=CODE,
                 seal=False)
        starts = [q["start"] for path, q in market.requests if "bars" in path]
        self.assertTrue(all(s >= "2019-12-01" for s in starts), starts)
        for call in io.bar_calls:
            if call["warmup"] == 0:
                self.assertEqual(call["adjustment"] in ("raw", "all"), True)

    def test_a_window_that_begins_just_after_the_feed_floor_is_captured_without_a_coverage_mismatch(self):
        # XYLD EXACTLY. The feed serves from 2016-01-04 and the window began 2016-01-06,
        # so a capture thirteen months early reached BEFORE the feed floor and failed
        # with a coverage mismatch. The test above passed on the old code because its
        # feed floor sat years before its window, which hid the bug: asking for too
        # much history is harmless until the history is not there.
        market = FakeMarket(history_starts="2019-06-03")      # window starts 2020-01-06
        io = CaptureIO(market)
        result = run_spec(self._spec(), io=io, paths=self.paths, now=NOW, code_revision=CODE,
                          seal=False)
        self.assertIsNone(result["void"], result["void"])
        raw_start = [q["start"] for path, q in market.requests
                     if "bars" in path and q["adjustment"] == "raw"][0]
        self.assertEqual(raw_start, "2020-01-06")

    def test_the_raw_capture_uses_the_raw_adjustment_and_the_specs_feed(self):
        market = FakeMarket()
        io = CaptureIO(market)
        run_spec(self._spec(), io=io, paths=self.paths, now=NOW, code_revision=CODE,
                 seal=False)
        raw_calls = [c for c in io.bar_calls if c["adjustment"] == "raw"]
        self.assertEqual(len(raw_calls), 1)
        self.assertEqual({c["feed"] for c in io.bar_calls}, {"sip"})
        requested = {q["adjustment"] for path, q in market.requests if "bars" in path}
        self.assertEqual(requested, {"all", "raw"})

    def test_a_feed_that_cannot_serve_the_window_fails_loudly_not_quietly(self):
        # What XYLD's first run showed: a request outside what the feed serves is a
        # coverage mismatch, never a shorter series passed off as the whole one.
        market = FakeMarket(history_starts="2021-06-01")
        with self.assertRaises(ValueError) as caught:
            run_spec(self._spec(), io=CaptureIO(market), paths=self.paths, now=NOW,
                     code_revision=CODE, seal=False)
        self.assertIn("coverage mismatch", str(caught.exception))

    def test_omitted_distributions_void_the_measurement_through_the_real_path(self):
        result = run_spec(self._spec(), io=CaptureIO(FakeMarket(distribution_yield=0.0)),
                          paths=self.paths, now=NOW, code_revision=CODE)
        self.assertIn("outside the declared range", result["void"])
        self.assertFalse((self.root / "level-claim-validations-test-premium.json").exists())


if __name__ == "__main__":
    unittest.main()
