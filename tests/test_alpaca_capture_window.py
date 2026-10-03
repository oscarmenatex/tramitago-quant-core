"""Where a capture begins and ends, tested with a network that is faithful to the contract.

THE BUG THIS PINS. `before_start[-warmup_periods]` and `at_or_after_end[horizon - 1]`
index a list from a count, and Python reads -0 as 0 and -1 as the last element. With
warmup zero the capture began at the FIRST day of a 400-day calendar window instead of
the evaluable start, thirteen months early. Nothing had ever asked for warmup zero --
every runner wanted two or three rows -- so it stayed invisible until the spec engine
captured auxiliary series that need none. XYLD's first real run died on it with a
coverage mismatch beginning 2014-12-02, a year before the window.
"""

import json
import unittest
from datetime import date, timedelta
from urllib.parse import urlparse, parse_qs

from tramitago_quant_core.data.alpaca_equity_series import capture_alpaca_equity_bars

START, END = "2020-01-06T00:00:00Z", "2020-03-02T00:00:00Z"      # a Monday to a Monday


def _weekdays(first, last):
    day, out = date.fromisoformat(first), []
    while day <= date.fromisoformat(last):
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


class FakeAlpaca:
    """Answers the calendar and bars endpoints for weekdays, and records every request."""

    def __init__(self):
        self.requests = []

    def __call__(self, url, headers, timeout):
        parsed = urlparse(url)
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        self.requests.append((parsed.path, query))
        if parsed.path.endswith("/calendar"):
            days = _weekdays(query["start"], query["end"])
            return json.dumps([{"date": d, "open": "09:30", "close": "16:00"}
                               for d in days]).encode()
        days = _weekdays(query["start"], query["end"])
        bars = [{"t": f"{d}T05:00:00Z", "o": 100, "h": 101, "l": 99, "c": 100.5, "v": 1000}
                for d in days]
        return json.dumps({"bars": bars, "next_page_token": None}).encode()


def _capture(warmup, horizon=1, transport=None):
    transport = transport or FakeAlpaca()
    result = capture_alpaca_equity_bars(
        symbol="TEST", evaluable_start_utc=START, evaluable_end_exclusive_utc=END,
        warmup_periods=warmup, horizon=horizon, acquired_at="2026-10-03T12:00:00.000000Z",
        credential_injector=lambda headers: headers, transport=transport)
    return result, transport


class WarmupZeroTests(unittest.TestCase):
    def test_no_warmup_begins_at_the_evaluable_start_not_thirteen_months_earlier(self):
        (rows, capture, _), transport = _capture(0)
        self.assertEqual(capture["capture_period"]["start_utc"], "2020-01-06T00:00:00Z")
        self.assertEqual(rows[0]["timestamp"][:10], "2020-01-06")

    def test_the_bars_request_starts_where_the_capture_does(self):
        # The symptom was a request for bars from 2014-12-02, outside what the feed
        # serves, so the request itself is what must be right.
        _, transport = _capture(0)
        bar_requests = [q for path, q in transport.requests if "bars" in path]
        self.assertEqual(bar_requests[0]["start"], "2020-01-06")

    def test_the_old_expression_is_the_one_that_misreads_zero(self):
        # The trap, shown in isolation so nobody reintroduces it by tidying.
        trading_days = ["a", "b", "c", "d"]
        self.assertEqual(trading_days[-0], "a")           # the FRONT, not the back
        self.assertEqual(trading_days[-1], "d")

    def test_two_rows_of_warmup_still_begin_two_trading_days_early(self):
        # The behaviour every sealed capture relied on, unchanged.
        (rows, capture, _), _ = _capture(2)
        self.assertEqual(capture["capture_period"]["start_utc"], "2020-01-02T00:00:00Z")
        self.assertEqual(rows[0]["timestamp"][:10], "2020-01-02")

    def test_warmup_and_none_differ_by_exactly_the_warmup_rows(self):
        (zero, _, _), _ = _capture(0)
        (three, _, _), _ = _capture(3)
        self.assertEqual(len(three) - len(zero), 3)


class HorizonTests(unittest.TestCase):
    def test_one_forward_bar_ends_at_the_first_trading_day_at_or_after_the_end(self):
        (rows, capture, _), _ = _capture(0, horizon=1)
        self.assertEqual(rows[-1]["timestamp"][:10], "2020-03-02")

    def test_a_longer_horizon_captures_that_many_forward_bars(self):
        (one, _, _), _ = _capture(0, horizon=1)
        (three, _, _), _ = _capture(0, horizon=3)
        self.assertEqual(len(three) - len(one), 2)


class RefusalTests(unittest.TestCase):
    """A count that selects from the wrong end is refused rather than obeyed."""

    def test_a_negative_warmup_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            _capture(-1)
        self.assertIn("non-negative", str(caught.exception))

    def test_horizon_zero_is_refused_because_it_would_index_from_the_back(self):
        # at_or_after_end[0 - 1] is the LAST day of the window.
        with self.assertRaises(ValueError) as caught:
            _capture(0, horizon=0)
        self.assertIn("at least one bar", str(caught.exception))

    def test_a_non_integer_count_is_refused(self):
        for bad in (1.5, "2", None, True):
            with self.assertRaises(ValueError):
                _capture(bad)
        for bad in (1.5, "1", None, True):
            with self.assertRaises(ValueError):
                _capture(0, horizon=bad)


if __name__ == "__main__":
    unittest.main()
