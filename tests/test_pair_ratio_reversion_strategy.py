"""Focused proof for vía 7.C (2026-09-29): relative value -- the first
Hypothesis shape that predicts a SPREAD instead of one instrument's
direction.

Three things are genuinely new here and each is tested: the Outcome is
pluggable (what return is measured, declared by the Strategy), the second
leg is a real sealed capture rather than a synthetic series, and the
signal (read off the ratio) is deliberately distinct from the P&L (a
difference of two real returns).
"""

import json
import unittest

import pipeline as p


class OutcomeContractTests(unittest.TestCase):
    def test_close_return_is_the_default_and_reproduces_legacy_names(self):
        outcome = p.close_return_outcome()
        self.assertEqual(outcome["outcome_id"], "CLOSE_RETURN")
        self.assertEqual(outcome["column"](1), "forward_return_1d")
        self.assertEqual(outcome["column"](5), "forward_return_5d")
        self.assertEqual(outcome["name"](1), "return_t+1")
        self.assertEqual(outcome["formula"](1), "(close_t+1 / close_t) - 1")
        self.assertAlmostEqual(outcome["compute"]({"close": 100.0}, {"close": 110.0}), 0.1)

    def test_strategies_without_an_outcome_get_the_close_return(self):
        """Field presence is the discriminator -- every Strategy sealed
        before vía 7.C must keep measuring exactly what it always did."""
        for strategy in (p.sma_crossover_strategy(3), p.volume_surge_strategy(20),
                         p.funding_rate_surge_strategy(20), p.intraday_range_strategy(20)):
            self.assertEqual(p.strategy_outcome(strategy)["outcome_id"], "CLOSE_RETURN")

    def test_spread_return_is_the_difference_of_two_real_returns(self):
        outcome = p.spread_return_outcome("pair_close")
        self.assertEqual(outcome["outcome_id"], "SPREAD_RETURN")
        self.assertEqual(outcome["column"](1), "forward_spread_return_1d")
        row = {"close": 100.0, "pair_close": 50.0}
        future = {"close": 110.0, "pair_close": 52.5}   # +10% vs +5%
        self.assertAlmostEqual(outcome["compute"](row, future), 0.05)

    def test_spread_return_is_zero_when_both_legs_move_together(self):
        """The property that makes it market-neutral: a common move cancels."""
        outcome = p.spread_return_outcome("pair_close")
        row = {"close": 100.0, "pair_close": 50.0}
        future = {"close": 130.0, "pair_close": 65.0}   # both +30%
        self.assertAlmostEqual(outcome["compute"](row, future), 0.0)


class PairRatioReversionStrategyTests(unittest.TestCase):
    def test_contract_shape_and_declared_outcome(self):
        strategy = p.pair_ratio_reversion_strategy(20)
        self.assertEqual(strategy["strategy_id"], "PAIR_RATIO_REVERSION")
        self.assertEqual(strategy["required_inputs"],
                         {"variables": ["close", "pair_close"], "warmup_periods": 20})
        self.assertEqual(p.strategy_outcome(strategy)["outcome_id"], "SPREAD_RETURN")

    def test_classifies_by_the_ratio_not_by_either_leg_alone(self):
        """A ratio held constant while BOTH legs rise must not trigger: this
        is what separates relative value from directional prediction."""
        strategy = p.pair_ratio_reversion_strategy(3)
        flat_ratio = [{"close": 100.0 * (1.1 ** i), "pair_close": 50.0 * (1.1 ** i)}
                      for i in range(5)]
        classified = p._strategy_classify_rows(strategy, flat_ratio)
        for row in classified[3:]:
            self.assertEqual(row["group"], "LOWER_OR_EQUAL")  # ratio never exceeds its own mean

        widening = [{"close": 100.0, "pair_close": 50.0} for _ in range(4)]
        widening.append({"close": 130.0, "pair_close": 50.0})  # ratio jumps
        self.assertEqual(
            p._strategy_classify_rows(strategy, widening)[-1]["group"], "UPPER")

    def test_rejects_invalid_arguments(self):
        with self.assertRaises(ValueError):
            p.pair_ratio_reversion_strategy(1)
        with self.assertRaises(ValueError):
            p.pair_ratio_reversion_strategy(20, "")


class CoinbaseCloseSeriesCaptureTests(unittest.TestCase):
    def _transport(self, days):
        def transport(url, _headers, _timeout):
            from urllib.parse import parse_qs, urlsplit
            query = parse_qs(urlsplit(url).query)
            start, end = p.epoch(query["start"][0]), p.epoch(query["end"][0])
            # Coinbase returns [time, low, high, open, close, volume], newest first
            rows = [[t, 90.0, 110.0, 95.0, 100.0 + (t // 86400) % 7, 5.0]
                    for t in range(start, end + 86400, 86400)]
            return json.dumps(list(reversed(rows))).encode("utf-8"), {"Date": "fixture"}
        return transport

    def test_round_trip(self):
        series, capture, raw = p.capture_coinbase_close_series(
            "ETH-USD", "2025-01-01T00:00:00Z", "2025-01-08T00:00:00Z",
            "2026-09-29T00:00:00Z", transport=self._transport(7))
        self.assertEqual(len(series), 7)
        self.assertEqual(p.verified_coinbase_close_series_capture(raw, capture), series)

    def test_tampering_is_detected(self):
        series, capture, raw = p.capture_coinbase_close_series(
            "ETH-USD", "2025-01-01T00:00:00Z", "2025-01-08T00:00:00Z",
            "2026-09-29T00:00:00Z", transport=self._transport(7))
        tampered = json.loads(json.dumps(capture))
        tampered["responses"][0]["response_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            p.verified_coinbase_close_series_capture(raw, tampered)

    def test_missing_day_fails_closed(self):
        def gappy(url, _headers, _timeout):
            from urllib.parse import parse_qs, urlsplit
            query = parse_qs(urlsplit(url).query)
            start, end = p.epoch(query["start"][0]), p.epoch(query["end"][0])
            rows = [[t, 90.0, 110.0, 95.0, 100.0, 5.0]
                    for t in range(start, end + 86400, 86400)
                    if t != start + 3 * 86400]
            return json.dumps(rows).encode("utf-8"), {"Date": "fixture"}
        with self.assertRaises(ValueError):
            p.capture_coinbase_close_series(
                "ETH-USD", "2025-01-01T00:00:00Z", "2025-01-08T00:00:00Z",
                "2026-09-29T00:00:00Z", transport=gappy)


if __name__ == "__main__":
    unittest.main()
