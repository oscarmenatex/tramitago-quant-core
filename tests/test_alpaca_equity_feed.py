"""The equity bar feed, which decides what windows this platform can measure at all.

WHY THIS EXISTS. The equity risk premium Hypothesis declared a window of
2018-01-02 to 2026-10-01, because the net Sharpe gate is a LOWER bound and a
bound is a statement about sample size as much as about effect. The capture
refused, and the probe said why:

    feed=iex   earliest 2018-11-01   644 of 2198 NYSE sessions missing
    feed=sip   earliest 2016-01-04     0 of 2198 sessions missing

IEX carries a few percent of consolidated volume. Its first fully covered
window starts 2020-07-24 -- four months AFTER the March 2020 crash -- so
adopting it would have removed the worst tail of the equity risk premium and
kept the entire recovery, inflating the very quantity being measured. SIP is the
consolidated tape.

THE DEFAULT DELIBERATELY STAYS IEX. The request URL is part of the capture's own
content and therefore of its capture_id, so changing the default would silently
invalidate every equity capture already sealed. The feed is chosen per capture
and travels into the seal on its own.

Before this, nothing in the suite touched alpaca_equity_series at all.
"""

import unittest

from tramitago_quant_core.data.alpaca_equity_series import (
    _alpaca_bars_url, ALPACA_DEFAULT_FEED, ALPACA_FEED_IEX, ALPACA_FEED_SIP, ALPACA_FEEDS)


class FeedSelectionTests(unittest.TestCase):
    def test_the_default_is_unchanged_so_sealed_captures_reproduce(self):
        self.assertEqual(ALPACA_DEFAULT_FEED, ALPACA_FEED_IEX)
        self.assertIn("feed=iex", _alpaca_bars_url("SPY", "2024-01-02", "2024-06-01"))

    def test_the_chosen_feed_reaches_the_url(self):
        url = _alpaca_bars_url("SPY", "2024-01-02", "2024-06-01", None, ALPACA_FEED_SIP)
        self.assertIn("feed=sip", url)
        self.assertNotIn("feed=iex", url)

    def test_the_feed_survives_pagination(self):
        # Pagination is where a per-request parameter is easiest to drop, and a
        # capture whose later pages silently changed feed would be worse than
        # one that failed outright.
        url = _alpaca_bars_url("SPY", "2024-01-02", "2024-06-01", "tok", ALPACA_FEED_SIP)
        self.assertIn("feed=sip", url)
        self.assertIn("page_token=tok", url)

    def test_an_unknown_feed_is_refused_rather_than_passed_through(self):
        # The dangerous case is not a crash, it is silence: the API may ignore
        # an unrecognised parameter and serve its default, leaving a sealed
        # capture that records one feed in its URL and holds another's bars.
        for feed in ("nasdaq", "SIP", "iex ", "", None, 1, True):
            with self.assertRaises(ValueError):
                _alpaca_bars_url("SPY", "2024-01-02", "2024-06-01", None, feed)

    def test_every_declared_feed_is_actually_accepted(self):
        # A constant nobody can pass would be decoration.
        for feed in ALPACA_FEEDS:
            self.assertIn(f"feed={feed}",
                          _alpaca_bars_url("SPY", "2024-01-02", "2024-06-01", None, feed))

    def test_the_adjustment_stays_total_return_whatever_the_feed(self):
        # A premium measured on price alone is not the premium. This is the one
        # parameter that would corrupt the measurement silently rather than
        # loudly, so it is pinned next to the feed.
        for feed in ALPACA_FEEDS:
            self.assertIn("adjustment=all",
                          _alpaca_bars_url("SPY", "2024-01-02", "2024-06-01", None, feed))


if __name__ == "__main__":
    unittest.main()
