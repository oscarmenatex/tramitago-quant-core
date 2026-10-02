"""Order books for both legs of a carry, and what crossing one costs.

The request was to build the short leg on Hyperliquid testnet. Measured first:
mainnet quotes 1.29 bp with 28.851 BTC of top-10 depth, testnet quotes 0.12 bp
with 0.330. A tighter spread over ninety times less depth is not a cheaper
measurement of the same thing, so these cover the refusal as much as the
arithmetic.
"""

import json
import unittest

import pipeline as p
from tramitago_quant_core.data.order_book import (
    capture_order_book, verified_order_book_capture, walk_book, mid_price,
    half_spread_rate, VENUE_HYPERLIQUID_PERPETUAL, VENUE_COINBASE_SPOT,
    HYPERLIQUID_TESTNET, SIDE_BUY, SIDE_SELL,
)

AT = "2026-10-02T04:00:00Z"


def _hyperliquid_payload(bids, asks, coin="BTC"):
    return {"coin": coin,
            "levels": [[{"px": str(price), "sz": str(size), "n": 1} for price, size in bids],
                       [{"px": str(price), "sz": str(size), "n": 1} for price, size in asks]]}


def _coinbase_payload(bids, asks):
    return {"sequence": 1,
            "bids": [[str(price), str(size), 1] for price, size in bids],
            "asks": [[str(price), str(size), 1] for price, size in asks]}


def _transport(payload):
    def transport(url, body, headers, timeout):
        return json.dumps(payload).encode("utf-8"), {"Content-Type": "application/json"}
    return transport


def _book(bids=((99.0, 1.0), (98.0, 2.0)), asks=((101.0, 1.0), (102.0, 2.0))):
    return {"bids": sorted(bids, key=lambda level: level[0], reverse=True),
            "asks": sorted(asks, key=lambda level: level[0])}


class TestnetRefusalTests(unittest.TestCase):
    def test_a_testnet_book_is_refused_for_cost_work(self):
        with self.assertRaises(ValueError) as caught:
            capture_order_book(VENUE_HYPERLIQUID_PERPETUAL, "BTC", AT,
                               host=HYPERLIQUID_TESTNET,
                               transport=_transport(_hyperliquid_payload(
                                   [(99.0, 1.0)], [(101.0, 1.0)])))
        self.assertIn("ninety times thinner", str(caught.exception))
        self.assertIn("order mechanics", str(caught.exception))

    def test_mainnet_is_the_default_and_needs_no_argument(self):
        book, capture, _ = capture_order_book(
            VENUE_HYPERLIQUID_PERPETUAL, "BTC", AT,
            transport=_transport(_hyperliquid_payload([(99.0, 1.0)], [(101.0, 1.0)])))
        self.assertIn("api.hyperliquid.xyz", capture["url"])
        self.assertNotIn("testnet", capture["url"])


class CaptureTests(unittest.TestCase):
    def test_a_hyperliquid_capture_re_verifies_from_stored_bytes(self):
        payload = _hyperliquid_payload([(99.0, 1.0), (98.0, 3.0)], [(101.0, 2.0)])
        book, capture, raw = capture_order_book(
            VENUE_HYPERLIQUID_PERPETUAL, "BTC", AT, transport=_transport(payload))
        self.assertEqual(verified_order_book_capture(raw, capture), book)

    def test_a_coinbase_capture_re_verifies_from_stored_bytes(self):
        payload = _coinbase_payload([(99.0, 1.0)], [(101.0, 1.0), (102.0, 5.0)])
        book, capture, raw = capture_order_book(
            VENUE_COINBASE_SPOT, "BTC-USD", AT, transport=_transport(payload))
        self.assertEqual(verified_order_book_capture(raw, capture), book)

    def test_a_tampered_response_fails_verification(self):
        payload = _hyperliquid_payload([(99.0, 1.0)], [(101.0, 1.0)])
        _, capture, raw = capture_order_book(
            VENUE_HYPERLIQUID_PERPETUAL, "BTC", AT, transport=_transport(payload))
        stored = json.loads(raw)
        stored["response_base64"] = "eyJjb2luIjogIkJUQyJ9"
        with self.assertRaises(ValueError):
            verified_order_book_capture(p.encoded(stored), capture)

    def test_an_altered_capture_identity_fails_verification(self):
        payload = _hyperliquid_payload([(99.0, 1.0)], [(101.0, 1.0)])
        _, capture, raw = capture_order_book(
            VENUE_HYPERLIQUID_PERPETUAL, "BTC", AT, transport=_transport(payload))
        with self.assertRaises(ValueError):
            verified_order_book_capture(raw, {**capture, "instrument": "ETH"})

    def test_the_sides_are_reordered_rather_than_trusted(self):
        # A venue returning a book out of order would otherwise be walked in the
        # wrong sequence and report a cost that is too low.
        payload = _hyperliquid_payload([(98.0, 1.0), (99.0, 1.0)], [(102.0, 1.0), (101.0, 1.0)])
        book, _, _ = capture_order_book(VENUE_HYPERLIQUID_PERPETUAL, "BTC", AT,
                                        transport=_transport(payload))
        self.assertEqual(book["bids"][0][0], 99.0)
        self.assertEqual(book["asks"][0][0], 101.0)

    def test_an_empty_or_malformed_side_is_refused(self):
        for payload in (_hyperliquid_payload([], [(101.0, 1.0)]),
                        _hyperliquid_payload([(99.0, 1.0)], [(101.0, 0.0)]),
                        _hyperliquid_payload([(-1.0, 1.0)], [(101.0, 1.0)])):
            with self.assertRaises(ValueError):
                capture_order_book(VENUE_HYPERLIQUID_PERPETUAL, "BTC", AT,
                                   transport=_transport(payload))

    def test_an_unknown_venue_is_refused(self):
        with self.assertRaises(ValueError):
            capture_order_book("BINANCE", "BTC", AT, transport=_transport({}))


class SpreadTests(unittest.TestCase):
    def test_the_mid_and_half_spread(self):
        book = _book()
        self.assertEqual(mid_price(book), 100.0)
        self.assertAlmostEqual(half_spread_rate(book), 0.01)

    def test_a_crossed_book_is_not_a_measurement(self):
        with self.assertRaises(ValueError):
            mid_price({"bids": [(102.0, 1.0)], "asks": [(101.0, 1.0)]})


class WalkTests(unittest.TestCase):
    def test_a_size_inside_the_best_level_pays_only_the_half_spread(self):
        walk = walk_book(_book(), SIDE_BUY, 50.0)
        self.assertEqual(walk["levels_consumed"], 1)
        self.assertAlmostEqual(walk["cost_rate"], 0.01)
        self.assertFalse(walk["depth_exhausted"])

    def test_a_larger_size_walks_deeper_and_costs_more(self):
        small = walk_book(_book(), SIDE_BUY, 50.0)
        large = walk_book(_book(), SIDE_BUY, 250.0)
        self.assertGreater(large["levels_consumed"], small["levels_consumed"])
        self.assertGreater(large["cost_rate"], small["cost_rate"])

    def test_a_sell_walks_the_bids_and_is_also_a_cost(self):
        walk = walk_book(_book(), SIDE_SELL, 50.0)
        self.assertGreater(walk["cost_rate"], 0.0)
        self.assertLess(walk["vwap"], walk["mid"])

    def test_running_out_of_book_is_reported_not_hidden(self):
        # A walk that exhausted the visible depth has measured nothing except
        # that the book was too thin.
        walk = walk_book(_book(), SIDE_BUY, 1_000_000.0)
        self.assertTrue(walk["depth_exhausted"])
        self.assertLess(walk["filled_notional"], 1_000_000.0)

    def test_the_quantity_and_notional_agree_with_the_vwap(self):
        walk = walk_book(_book(), SIDE_BUY, 250.0)
        self.assertAlmostEqual(walk["vwap"] * walk["quantity"], walk["filled_notional"], places=6)

    def test_an_invalid_side_or_notional_is_refused(self):
        for side, notional in ((SIDE_BUY, 0.0), (SIDE_BUY, -1.0), ("LONG", 10.0)):
            with self.assertRaises(ValueError):
                walk_book(_book(), side, notional)


class MeasurementBridgeTests(unittest.TestCase):
    def test_a_walk_becomes_an_execution_measurement_labelled_BOOK(self):
        book = _book()
        walk = walk_book(book, SIDE_BUY, 50.0)
        measurement = p.measure_execution(
            side=SIDE_BUY, bid=book["bids"][0][0], ask=book["asks"][0][0],
            fill_price=walk["vwap"], filled_quantity=walk["quantity"], commission=0.0,
            fill_venue=p.FILL_BOOK, symbol="BTC", observed_at=AT)
        self.assertEqual(measurement["fill_venue"], p.FILL_BOOK)
        self.assertAlmostEqual(float(measurement["cost_per_side"]), walk["cost_rate"], places=9)

    def test_a_contract_built_from_books_says_what_a_book_cannot_tell_you(self):
        book = _book()
        walk = walk_book(book, SIDE_BUY, 50.0)
        measurement = p.measure_execution(
            side=SIDE_BUY, bid=book["bids"][0][0], ask=book["asks"][0][0],
            fill_price=walk["vwap"], filled_quantity=walk["quantity"], commission=0.0,
            fill_venue=p.FILL_BOOK, symbol="BTC", observed_at=AT)
        contract = p.measured_cost_contract([measurement], legs=2, source="test")
        self.assertIn("no queue position", contract["source"])


if __name__ == "__main__":
    unittest.main()
