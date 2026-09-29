"""Focused proof for funding_rate_surge_strategy and
hyperliquid_funding_rate.py (2026-09-29).

FUNDING_RATE_SIGN's own Knowledge Record documented why it could not be a
clean test: classifying by absolute SIGN cannot produce a balanced
partition when the series carries a near-constant bias (BTC funding was
positive on 93 of 96 days, leaving a walk-forward fold with an empty group
and the validation INSUFFICIENT_EVIDENCE). FUNDING_RATE_SURGE compares the
series against its OWN recent history instead -- the same fix
volume_surge_strategy already applies to volume -- which balances the split
by construction, and Hyperliquid supplies the full-year history the
earlier providers could not.
"""

import json
import unittest

import pipeline as p


def rows_from_funding(rates):
    return [{"close": 100.0, "funding_rate": r} for r in rates]


class FundingRateSurgeStrategyTests(unittest.TestCase):
    def test_contract_shape(self):
        strategy = p.funding_rate_surge_strategy(20)
        self.assertEqual(strategy["strategy_id"], "FUNDING_RATE_SURGE")
        self.assertEqual(strategy["parameters"], {"window": 20})
        self.assertEqual(strategy["required_inputs"],
                         {"variables": ["funding_rate"], "warmup_periods": 21})
        self.assertEqual(strategy["column_name"], "funding_rate_avg_20")
        self.assertEqual(strategy["indicator_name"], "FUNDSURGE20")
        self.assertEqual(strategy["upper_group_description"],
                         "funding_rate_t-1 > FUNDSURGE20_t")

    def test_reads_yesterday_never_today(self):
        """The signal at day t must depend on day t-1's funding and be blind
        to day t's own -- the lag-1 no-lookahead convention inherited from
        FUNDING_RATE_SIGN."""
        strategy = p.funding_rate_surge_strategy(3)
        base = [0.0001] * 3 + [0.0009, 0.0001]  # yesterday (index 3) is the surge
        classified = p._strategy_classify_rows(strategy, rows_from_funding(base))
        self.assertEqual(classified[4]["group"], "UPPER")

        moved = [0.0001] * 3 + [0.0001, 0.0009]  # same surge, but on TODAY instead
        moved_classified = p._strategy_classify_rows(strategy, rows_from_funding(moved))
        self.assertEqual(moved_classified[4]["group"], "LOWER_OR_EQUAL")

    def test_all_positive_series_still_splits_both_ways(self):
        """The whole point of the relative threshold: a series that never
        changes sign must still produce both groups, which the absolute-sign
        strategy could not."""
        rates = [0.0001, 0.0002, 0.0001, 0.0003, 0.0001, 0.0004, 0.0001, 0.0005]
        surge = p._strategy_classify_rows(p.funding_rate_surge_strategy(2),
                                          rows_from_funding(rates))
        groups = {row["group"] for row in surge if row["group"] is not None}
        self.assertEqual(groups, {"UPPER", "LOWER_OR_EQUAL"})

        sign = p._strategy_classify_rows(p.funding_rate_sign_strategy(),
                                         rows_from_funding(rates))
        sign_groups = {row["group"] for row in sign if row["group"] is not None}
        self.assertEqual(sign_groups, {"UPPER"})  # degenerate: one group only

    def test_rejects_invalid_window(self):
        with self.assertRaises(ValueError):
            p.funding_rate_surge_strategy(1)
        with self.assertRaises(ValueError):
            p.funding_rate_surge_strategy(True)


class HyperliquidFundingRateCaptureTests(unittest.TestCase):
    def _transport(self, days, page=500):
        """Simulates Hyperliquid's forward cursor pagination over an hourly
        grid, including the millisecond timestamp jitter the real API
        returns."""
        events = []
        for day in days:
            for hour in range(24):
                events.append({"coin": "BTC", "fundingRate": f"{0.00001 * (hour + 1):.8f}",
                               "premium": "0.0001", "time": (day + hour * 3600) * 1000 + 42})
        events.sort(key=lambda e: e["time"])

        def transport(_url, body, _headers, _timeout):
            request = json.loads(body)
            start, end = request["startTime"], request["endTime"]
            batch = [e for e in events if start <= e["time"] < end][:page]
            return json.dumps(batch).encode("utf-8"), {"Date": "fixture"}
        return transport

    def test_round_trip_and_daily_reduction(self):
        days = list(range(p.epoch("2025-01-01T00:00:00Z"), p.epoch("2025-01-08T00:00:00Z"), 86400))
        series, capture, raw = p.capture_hyperliquid_funding_rate(
            "BTC", "2025-01-01T00:00:00Z", "2025-01-08T00:00:00Z", "2026-09-29T00:00:00Z",
            transport=self._transport(days))
        self.assertEqual(len(series), 7)
        reloaded = p.verified_hyperliquid_funding_rate_capture(raw, capture)
        self.assertEqual(reloaded, series)

    def test_multi_page_round_trip_and_tamper_detection(self):
        days = list(range(p.epoch("2025-01-01T00:00:00Z"), p.epoch("2025-02-01T00:00:00Z"), 86400))
        series, capture, raw = p.capture_hyperliquid_funding_rate(
            "BTC", "2025-01-01T00:00:00Z", "2025-02-01T00:00:00Z", "2026-09-29T00:00:00Z",
            transport=self._transport(days, page=100))
        self.assertEqual(len(series), 31)
        self.assertGreater(len(capture["responses"]), 1)
        self.assertEqual(p.verified_hyperliquid_funding_rate_capture(raw, capture), series)

        tampered = json.loads(json.dumps(capture))
        tampered["responses"][0]["start_time_ms"] += 1000
        with self.assertRaises(ValueError):
            p.verified_hyperliquid_funding_rate_capture(raw, tampered)

        raw_payload = json.loads(raw)
        raw_payload["captures"][-1]["response_base64"] = raw_payload["captures"][0]["response_base64"]
        with self.assertRaises(ValueError):
            p.verified_hyperliquid_funding_rate_capture(
                json.dumps(raw_payload).encode("utf-8"), capture)

    def test_missing_day_fails_closed(self):
        days = list(range(p.epoch("2025-01-01T00:00:00Z"), p.epoch("2025-01-08T00:00:00Z"), 86400))
        del days[3]
        with self.assertRaises(ValueError):
            p.capture_hyperliquid_funding_rate(
                "BTC", "2025-01-01T00:00:00Z", "2025-01-08T00:00:00Z", "2026-09-29T00:00:00Z",
                transport=self._transport(days))


if __name__ == "__main__":
    unittest.main()
