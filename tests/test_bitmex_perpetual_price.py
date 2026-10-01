"""BitMEX capture: the deepest perpetual series, and the two traps in it.

Hyperliquid could not reach a crisis, and the conclusion drawn from that -- "the
tail is unmeasurable" -- was wrong for an avoidable reason: one venue mistaken
for all venues. These prove the capture is sealed, that the timestamp
realignment happens (the trap that silently inflated a measurement by ten
times), and that the adverse direction for a carry is the perpetual trading
ABOVE spot, not any large move.
"""

import json
import unittest

import pipeline as p
from tramitago_quant_core.data.bitmex_perpetual_price import (
    capture_bitmex_perpetual_price, verified_bitmex_perpetual_price_capture,
    carry_adverse_excursion, BITMEX_PERPETUAL_INTERVALS,
)

START = "2020-03-12T00:00:00Z"
END = "2020-03-12T05:00:00Z"


def _buckets(closes, start_iso=START, interval=3600):
    """BitMEX stamps a bucket with its CLOSE, so a bar covering 00:00-01:00 is
    labelled 01:00. The fixture reproduces that, which is what the parse undoes."""
    start = p.epoch(start_iso)
    return [{"timestamp": p.iso(start + (index + 1) * interval).replace("Z", ".000Z"),
             "symbol": "XBTUSD", "close": close, "volume": 1000}
            for index, close in enumerate(closes)]


def _transport(payload):
    def get(url, headers, timeout):
        return json.dumps(payload).encode(), {"Date": "x", "Content-Type": "application/json"}
    return get


class CaptureTests(unittest.TestCase):
    def test_a_capture_is_sealed_and_independently_re_derivable(self):
        series, capture, raw = capture_bitmex_perpetual_price(
            "XBTUSD", START, END, "2026-09-30T00:00:00Z",
            transport=_transport(_buckets([100, 101, 102, 103, 104])))
        self.assertEqual(len(series), 5)
        self.assertEqual(verified_bitmex_perpetual_price_capture(raw, capture), series)

    def test_the_capture_declares_the_contract_as_inverse(self):
        # XBTUSD is margined in BTC, so a crash erodes the collateral backing a
        # short. The basis transfers to a linear venue; the liquidation does not.
        _, capture, _ = capture_bitmex_perpetual_price(
            "XBTUSD", START, END, "2026-09-30T00:00:00Z",
            transport=_transport(_buckets([100, 101, 102, 103, 104])))
        self.assertEqual(capture["margin_kind"], "INVERSE")
        self.assertIn("realigned", capture["timestamp_convention"])

    def test_a_tampered_response_fails_verification(self):
        _, capture, raw = capture_bitmex_perpetual_price(
            "XBTUSD", START, END, "2026-09-30T00:00:00Z",
            transport=_transport(_buckets([100, 101, 102, 103, 104])))
        broken = json.loads(raw)
        broken["responses"][0]["response_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            verified_bitmex_perpetual_price_capture(p.encoded(broken), capture)

    def test_a_bucket_with_no_traded_close_is_refused(self):
        empty = _buckets([100, 101, 102, 103, 104])
        empty[2]["close"] = None
        with self.assertRaises(ValueError):
            capture_bitmex_perpetual_price(
                "XBTUSD", START, END, "2026-09-30T00:00:00Z", transport=_transport(empty))

    def test_a_bucket_with_no_volume_is_refused(self):
        silent = _buckets([100, 101, 102, 103, 104])
        silent[1]["volume"] = 0
        with self.assertRaises(ValueError):
            capture_bitmex_perpetual_price(
                "XBTUSD", START, END, "2026-09-30T00:00:00Z", transport=_transport(silent))


class TimestampAlignmentTests(unittest.TestCase):
    """The trap that inflated a real measurement by a factor of ten.

    BitMEX labels by bucket close, Coinbase by candle open. Pairing them naively
    shifts one series a full bar: measured on a calm period the naive pairing
    reported a 0.31% mean absolute basis and the aligned one 0.03%.
    """

    def test_a_bucket_is_keyed_at_its_opening_instant(self):
        series, _, _ = capture_bitmex_perpetual_price(
            "XBTUSD", START, END, "2026-09-30T00:00:00Z",
            transport=_transport(_buckets([100, 101, 102, 103, 104])))
        # The bar stamped 01:00 by BitMEX covers 00:00-01:00 and must key at 00:00.
        self.assertEqual(series["2020-03-12T00:00:00Z"], 100.0)
        self.assertNotIn("2020-03-12T05:00:00Z", series)

    def test_a_period_not_aligned_to_the_bin_size_is_refused(self):
        with self.assertRaises(ValueError):
            capture_bitmex_perpetual_price(
                "XBTUSD", "2020-03-12T00:30:00Z", "2020-03-12T05:30:00Z",
                "2026-09-30T00:00:00Z", transport=_transport([]))

    def test_an_unsupported_bin_size_is_refused(self):
        with self.assertRaises(ValueError):
            capture_bitmex_perpetual_price(
                "XBTUSD", START, END, "2026-09-30T00:00:00Z", bin_size="4h",
                transport=_transport([]))


class CarryDirectionTests(unittest.TestCase):
    """A crash is favourable to a carry. Measuring "the largest move" measures
    the wrong thing."""

    def test_a_liquidation_cascade_is_favourable_not_adverse(self):
        # March 2020 shape: the perpetual fell far BELOW spot as longs were
        # liquidated. A carry is short the perpetual, so it gained.
        crash = {"t0": 0.001, "t1": -0.05, "t2": -0.1278, "t3": -0.02}
        result = carry_adverse_excursion(crash)
        self.assertAlmostEqual(result["worst_adverse_excursion"], 0.001)
        self.assertAlmostEqual(result["best_favourable_excursion"], -0.1278)

    def test_the_adverse_direction_is_the_perpetual_above_spot(self):
        squeeze = {"t0": 0.0005, "t1": 0.0045, "t2": 0.0020}
        result = carry_adverse_excursion(squeeze)
        self.assertAlmostEqual(result["worst_adverse_excursion"], 0.0045)

    def test_the_excursion_is_measured_from_the_entry_basis(self):
        # Entering at a wide basis means less room before the loss is realised.
        series = {"t0": 0.002, "t1": 0.006}
        flat = carry_adverse_excursion(series, entry_basis=0.0)
        entered = carry_adverse_excursion(series, entry_basis=0.004)
        self.assertAlmostEqual(flat["worst_adverse_excursion"], 0.006)
        self.assertAlmostEqual(entered["worst_adverse_excursion"], 0.002)

    def test_an_empty_series_is_refused(self):
        with self.assertRaises(ValueError):
            carry_adverse_excursion({})


if __name__ == "__main__":
    unittest.main()
