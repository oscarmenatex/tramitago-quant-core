"""BitMEX funding history: the only funding source that reaches back to a squeeze.

Hyperliquid's history starts 2023-11, after every crisis worth testing, which is
why the first level-claim verdict on the carry was blind to its own tail. These
cover the sealed-capture contract and the two shape differences that matter: the
eight-hour settlement period, and that the period is CHECKED rather than assumed.
"""

import base64
import json
import unittest

import pipeline as p
from tramitago_quant_core.data.bitmex_funding_rate import (
    capture_bitmex_funding_rate, verified_bitmex_funding_rate_capture,
    BITMEX_FUNDING_RATE_INTERVAL_SECONDS, BITMEX_FUNDING_PAYMENTS_PER_DAY,
    _parse, _daily_series, _interval_seconds,
)

SYMBOL = "XBTUSD"
EIGHT_HOURS = "2000-01-01T08:00:00.000Z"


def _event(timestamp, rate, interval=EIGHT_HOURS):
    return {"timestamp": timestamp, "symbol": SYMBOL, "fundingInterval": interval,
            "fundingRate": rate, "fundingRateDaily": rate * 3}


def _day(date, rates, interval=EIGHT_HOURS):
    return [_event(f"{date}T{hour:02d}:00:00.000Z", rate, interval)
            for hour, rate in zip((4, 12, 20), rates)]


def _transport(pages):
    """Serve prepared pages in order, then empty ones -- the venue's own
    termination signal."""
    served = []

    def transport(url, headers, timeout):
        index = len(served)
        served.append(url)
        payload = pages[index] if index < len(pages) else []
        return json.dumps(payload).encode("utf-8"), {"Content-Type": "application/json"}

    transport.served = served
    return transport


class IntervalTests(unittest.TestCase):
    def test_the_interval_is_read_from_the_response(self):
        self.assertEqual(_interval_seconds(EIGHT_HOURS), BITMEX_FUNDING_RATE_INTERVAL_SECONDS)
        self.assertEqual(_interval_seconds("2000-01-01T01:00:00.000Z"), 3600)

    def test_a_venue_that_changed_its_cadence_is_refused(self):
        # Otherwise the same stored rate would silently mean something else and
        # rescale a sealed measurement with nothing noticing.
        with self.assertRaises(ValueError) as caught:
            _parse(json.dumps(_day("2020-03-12", [0.0001] * 3,
                                   interval="2000-01-01T01:00:00.000Z")).encode(), SYMBOL)
        self.assertIn("eight hours", str(caught.exception))

    def test_three_payments_a_day_is_what_a_caller_declares(self):
        self.assertEqual(BITMEX_FUNDING_PAYMENTS_PER_DAY, 3)
        self.assertEqual(86400 // BITMEX_FUNDING_RATE_INTERVAL_SECONDS,
                         BITMEX_FUNDING_PAYMENTS_PER_DAY)


class ParseTests(unittest.TestCase):
    def test_a_foreign_symbol_is_refused(self):
        payload = _day("2020-03-12", [0.0001] * 3)
        payload[0]["symbol"] = "ETHUSD"
        with self.assertRaises(ValueError):
            _parse(json.dumps(payload).encode(), SYMBOL)

    def test_an_unexpected_field_is_refused(self):
        payload = _day("2020-03-12", [0.0001] * 3)
        payload[0]["extra"] = 1
        with self.assertRaises(ValueError):
            _parse(json.dumps(payload).encode(), SYMBOL)

    def test_a_non_finite_rate_is_refused(self):
        with self.assertRaises(ValueError):
            _parse(b'[{"timestamp":"2020-03-12T04:00:00.000Z","symbol":"XBTUSD",'
                   b'"fundingInterval":"2000-01-01T08:00:00.000Z","fundingRate":NaN,'
                   b'"fundingRateDaily":0}]', SYMBOL)

    def test_the_venues_own_daily_field_is_ignored(self):
        # Taking it would make the series depend on a convenience number whose
        # definition the venue can change -- the shape of the 24x defect.
        payload = _day("2020-03-12", [0.0003] * 3)
        for item in payload:
            item["fundingRateDaily"] = 99.0
        events = _parse(json.dumps(payload).encode(), SYMBOL)
        self.assertEqual([event["funding_rate"] for event in events], [0.0003] * 3)


class DailySeriesTests(unittest.TestCase):
    def test_the_day_is_the_mean_of_its_rates(self):
        events = _parse(json.dumps(_day("2020-03-12", [0.0001, 0.0002, 0.0003])).encode(), SYMBOL)
        series = _daily_series(events, p.epoch("2020-03-12T00:00:00Z"),
                               p.epoch("2020-03-13T00:00:00Z"))
        self.assertAlmostEqual(series["2020-03-12T00:00:00Z"], 0.0002)

    def test_a_missing_day_fails_closed_rather_than_imputing(self):
        events = _parse(json.dumps(_day("2020-03-12", [0.0001] * 3)).encode(), SYMBOL)
        with self.assertRaises(ValueError):
            _daily_series(events, p.epoch("2020-03-12T00:00:00Z"),
                          p.epoch("2020-03-14T00:00:00Z"))


class CaptureTests(unittest.TestCase):
    def _two_days(self):
        return _day("2020-03-12", [-0.00375] * 3) + _day("2020-03-13", [-0.00375] * 3)

    def test_a_capture_re_verifies_from_stored_bytes_alone(self):
        transport = _transport([self._two_days()])
        series, capture, raw = capture_bitmex_funding_rate(
            SYMBOL, "2020-03-12T00:00:00Z", "2020-03-14T00:00:00Z",
            "2026-10-01T00:00:00Z", transport=transport)
        self.assertEqual(verified_bitmex_funding_rate_capture(raw, capture), series)
        self.assertAlmostEqual(series["2020-03-12T00:00:00Z"], -0.00375)

    def test_pagination_walks_forward_from_the_last_record(self):
        transport = _transport([_day("2020-03-12", [-0.00375] * 3),
                                _day("2020-03-13", [-0.00375] * 3)])
        series, capture, raw = capture_bitmex_funding_rate(
            SYMBOL, "2020-03-12T00:00:00Z", "2020-03-14T00:00:00Z",
            "2026-10-01T00:00:00Z", transport=transport)
        self.assertEqual(len(capture["responses"]), 3)   # two pages, then the empty one
        self.assertIn("2020-03-12T20%3A00%3A01", capture["responses"][1]["url"])
        self.assertEqual(verified_bitmex_funding_rate_capture(raw, capture), series)

    def test_an_empty_first_page_is_no_coverage_not_an_empty_series(self):
        with self.assertRaises(ValueError):
            capture_bitmex_funding_rate(SYMBOL, "2016-01-01T00:00:00Z", "2016-01-02T00:00:00Z",
                                        "2026-10-01T00:00:00Z", transport=_transport([]))

    def test_a_tampered_response_fails_verification(self):
        transport = _transport([self._two_days()])
        _, capture, raw = capture_bitmex_funding_rate(
            SYMBOL, "2020-03-12T00:00:00Z", "2020-03-14T00:00:00Z",
            "2026-10-01T00:00:00Z", transport=transport)
        payload = json.loads(raw)
        tampered = _day("2020-03-12", [0.01] * 3) + _day("2020-03-13", [0.01] * 3)
        payload["captures"][0]["response_base64"] = base64.b64encode(
            json.dumps(tampered).encode()).decode("ascii")
        with self.assertRaises(ValueError):
            verified_bitmex_funding_rate_capture(p.encoded(payload), capture)

    def test_an_altered_capture_identity_fails_verification(self):
        transport = _transport([self._two_days()])
        _, capture, raw = capture_bitmex_funding_rate(
            SYMBOL, "2020-03-12T00:00:00Z", "2020-03-14T00:00:00Z",
            "2026-10-01T00:00:00Z", transport=transport)
        with self.assertRaises(ValueError):
            verified_bitmex_funding_rate_capture(raw, {**capture, "symbol": "ETHUSD"})


if __name__ == "__main__":
    unittest.main()
