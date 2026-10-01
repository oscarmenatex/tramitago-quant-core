"""Perpetual price capture: the leg that makes the basis an observation.

carry_tail.py could compute liquidation exactly but had no basis to compute it
against, so every stress scenario was a declared assumption. These prove the
capture is sealed and independently re-derivable, that a gap is refused rather
than filled, and that the stress a carry must survive is the basis WIDENING.
"""

import json
import unittest

import pipeline as p
from tramitago_quant_core.data.hyperliquid_perpetual_price import (
    capture_hyperliquid_perpetual_price, verified_hyperliquid_perpetual_price_capture,
    basis_series, basis_stress_observed, basis_price_is_traded_not_mark,
)

DAY = 86400
START = "2025-01-01T00:00:00Z"
END = "2025-01-06T00:00:00Z"


def _candles(closes, start_iso=START):
    start = p.epoch(start_iso)
    return [{"t": (start + index * DAY) * 1000, "T": (start + (index + 1) * DAY) * 1000,
             "s": "BTC", "i": "1d", "o": close, "h": close, "l": close, "c": close,
             "v": "10", "n": 5}
            for index, close in enumerate(closes)]


def _transport(payload):
    def post(url, body, headers, timeout):
        return json.dumps(payload).encode(), {"Date": "x", "Content-Type": "application/json"}
    return post


class CaptureTests(unittest.TestCase):
    def test_a_capture_is_sealed_and_independently_re_derivable(self):
        closes = ["100", "101", "102", "103", "104"]
        series, capture, raw = capture_hyperliquid_perpetual_price(
            "BTC", START, END, "2026-09-30T00:00:00Z", transport=_transport(_candles(closes)))
        self.assertEqual(len(series), 5)
        self.assertEqual(series["2025-01-01T00:00:00Z"], 100.0)
        # Re-derived from the sealed bytes alone, with no live network.
        self.assertEqual(verified_hyperliquid_perpetual_price_capture(raw, capture), series)

    def test_a_tampered_response_fails_verification(self):
        _, capture, raw = capture_hyperliquid_perpetual_price(
            "BTC", START, END, "2026-09-30T00:00:00Z",
            transport=_transport(_candles(["100", "101", "102", "103", "104"])))
        broken = json.loads(raw)
        broken["responses"][0]["response_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            verified_hyperliquid_perpetual_price_capture(p.encoded(broken), capture)

    def test_a_missing_day_is_refused_not_filled(self):
        # A gap is exactly when the basis matters, so imputing one would erase
        # the observation the tail model exists to make.
        partial = _candles(["100", "101", "102", "103", "104"])
        del partial[2]
        with self.assertRaises(ValueError) as caught:
            capture_hyperliquid_perpetual_price(
                "BTC", START, END, "2026-09-30T00:00:00Z", transport=_transport(partial))
        self.assertIn("coverage is incomplete", str(caught.exception))

    def test_a_candle_for_another_coin_is_refused(self):
        wrong = _candles(["100", "101", "102", "103", "104"])
        wrong[0]["s"] = "ETH"
        with self.assertRaises(ValueError):
            capture_hyperliquid_perpetual_price(
                "BTC", START, END, "2026-09-30T00:00:00Z", transport=_transport(wrong))

    def test_non_positive_prices_are_refused(self):
        bad = _candles(["100", "0", "102", "103", "104"])
        with self.assertRaises(ValueError):
            capture_hyperliquid_perpetual_price(
                "BTC", START, END, "2026-09-30T00:00:00Z", transport=_transport(bad))


class BackfillRefusalTests(unittest.TestCase):
    """Hyperliquid serves candles for dates BEFORE its perpetual traded.

    Real prices, volume 0, trades 0, derived from the oracle rather than its own
    book. BTC's first genuinely traded candle is 2023-02-26. A basis computed
    against an oracle price is (oracle - spot)/spot, and the oracle derives from
    spot, so it is near zero BY CONSTRUCTION -- an artefact that looks like a
    reassuringly tight basis and would poison any tail estimate built on it.
    """

    def test_a_candle_with_no_trades_is_refused(self):
        backfilled = _candles(["100", "101", "102", "103", "104"])
        backfilled[2]["n"] = 0
        backfilled[2]["v"] = "0"
        with self.assertRaises(ValueError) as caught:
            capture_hyperliquid_perpetual_price(
                "BTC", START, END, "2026-09-30T00:00:00Z", transport=_transport(backfilled))
        self.assertIn("no trades", str(caught.exception))
        self.assertIn("oracle backfill", str(caught.exception))

    def test_the_trade_count_is_required_not_optional(self):
        without = _candles(["100", "101", "102", "103", "104"])
        for candle in without:
            del candle["n"]
        with self.assertRaises(ValueError):
            capture_hyperliquid_perpetual_price(
                "BTC", START, END, "2026-09-30T00:00:00Z", transport=_transport(without))


class IntervalTests(unittest.TestCase):
    """Daily closes hide intraday gaps, and liquidation happens intraday."""

    def test_an_hourly_capture_seals_and_re_derives(self):
        start, end = "2025-01-01T00:00:00Z", "2025-01-01T05:00:00Z"
        hourly = [{"t": (p.epoch(start) + i * 3600) * 1000, "s": "BTC", "i": "1h",
                   "o": "100", "h": "100", "l": "100", "c": str(100 + i), "v": "1", "n": 7}
                  for i in range(5)]
        series, capture, raw = capture_hyperliquid_perpetual_price(
            "BTC", start, end, "2026-09-30T00:00:00Z", interval="1h",
            transport=_transport(hourly))
        self.assertEqual(len(series), 5)
        self.assertEqual(capture["interval"], "1h")
        self.assertEqual(verified_hyperliquid_perpetual_price_capture(raw, capture), series)

    def test_an_unsupported_interval_is_refused(self):
        with self.assertRaises(ValueError):
            capture_hyperliquid_perpetual_price(
                "BTC", START, END, "2026-09-30T00:00:00Z", interval="3h",
                transport=_transport([]))

    def test_a_period_not_aligned_to_the_interval_is_refused(self):
        with self.assertRaises(ValueError):
            capture_hyperliquid_perpetual_price(
                "BTC", "2025-01-01T00:30:00Z", "2025-01-02T00:30:00Z",
                "2026-09-30T00:00:00Z", interval="1h", transport=_transport([]))


class BasisTests(unittest.TestCase):
    def test_the_basis_is_perp_over_spot(self):
        spot = {"2025-01-01T00:00:00Z": 100.0, "2025-01-02T00:00:00Z": 100.0}
        perp = {"2025-01-01T00:00:00Z": 101.0, "2025-01-02T00:00:00Z": 99.0}
        basis = basis_series(spot, perp)
        self.assertAlmostEqual(basis["2025-01-01T00:00:00Z"], 0.01)
        self.assertAlmostEqual(basis["2025-01-02T00:00:00Z"], -0.01)

    def test_a_partial_overlap_is_refused(self):
        # A basis over a partial overlap silently describes a different period
        # than the one it claims.
        with self.assertRaises(ValueError):
            basis_series({"a": 1.0, "b": 1.0}, {"a": 1.0})

    def test_the_stress_is_the_basis_widening_against_the_short_leg(self):
        # A carry is long spot and short perp, so it loses when the perp pulls
        # further ABOVE spot. That widening is the number carry_tail needs.
        basis = {"2025-01-01T00:00:00Z": 0.001, "2025-01-02T00:00:00Z": 0.002,
                 "2025-01-03T00:00:00Z": 0.030, "2025-01-04T00:00:00Z": 0.004}
        stress = basis_stress_observed(basis)
        self.assertAlmostEqual(stress["worst_adverse_daily_change"], 0.028)
        self.assertAlmostEqual(stress["worst_favourable_daily_change"], -0.026)
        self.assertAlmostEqual(stress["max_level"], 0.030)
        self.assertEqual(stress["days"], 4)


class HonestyTests(unittest.TestCase):
    def test_the_traded_versus_mark_caveat_is_carried_as_data(self):
        caveat = basis_price_is_traded_not_mark()
        self.assertIn("mark price", caveat["margin_uses"])
        self.assertIn("UNDERSTATES", caveat["consequence"])


if __name__ == "__main__":
    unittest.main()
