"""Order books for both legs of a carry, sealed, and what crossing one costs.

THE SHORT LEG, AND WHY IT IS NOT ON TESTNET. The request was to build the carry's
short leg on Hyperliquid testnet. Measured before writing anything:

    mainnet   spread 1.29 bp   top-10 ask depth 28.851 BTC
    testnet   spread 0.12 bp   top-10 ask depth  0.330 BTC

Testnet shows a TIGHTER spread with ninety times less depth. It is a fiction, and
measuring execution cost there would produce a number better than reality -- the
exact failure mode that makes a paper engine's slippage a floor rather than an
estimate. Testnet is for exercising order MECHANICS, which is a different
question from what a fill costs. So the books captured here are live mainnet, and
the module refuses a testnet host for cost work rather than quietly accepting it.

WHY A BOOK RATHER THAN AN ORDER. A fill needs a signed transaction and therefore
a wallet key, which this agent does not handle and this project has no dependency
to produce. But the two components that decide the carry's breakeven -- the
spread, and the impact of crossing for a given size -- are both readable from a
public book with no credential at all. And for impact a real book is STRONGER
evidence than a paper fill: walking actual depth is a measurement, while a paper
engine's slippage is an assumption wearing a number.

WHAT A BOOK SNAPSHOT CANNOT TELL YOU, stated so the record cannot be over-read:
it is one instant. It has no queue position, so it prices crossing and never
resting. It assumes the book does not move while the order executes, which for a
size small against the visible depth is nearly true and for a large one is not.
`levels_consumed` and `depth_exhausted` are reported for exactly that reason: a
walk that ran out of book has measured nothing except that the book was too thin.

Both venues live here rather than in two provider modules because, unlike the
price and funding captures, nothing downstream needs to stay venue-agnostic --
a carry's runner knows perfectly well which leg is which.
"""

import base64
import json
import math
from urllib.request import Request, urlopen

from tramitago_quant_core.shared.util import digest, encoded, _explicit_utc

ORDER_BOOK_CAPTURE_SCHEMA_VERSION = "1"
ORDER_BOOK_CAPTURE_KIND = "order-book-snapshot"

HYPERLIQUID_MAINNET = "api.hyperliquid.xyz"
HYPERLIQUID_TESTNET = "api.hyperliquid-testnet.xyz"
COINBASE_HOST = "api.exchange.coinbase.com"

VENUE_HYPERLIQUID_PERPETUAL = "HYPERLIQUID_PERPETUAL"
VENUE_COINBASE_SPOT = "COINBASE_SPOT"

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"

_HEADERS = {"User-Agent": "TramitaGO-Quant-Core/0.1", "Accept": "application/json",
            "Content-Type": "application/json"}

_MAX_RESPONSE_BYTES = 8_000_000


def _headers():
    return dict(_HEADERS)


def _live_request(url, body, headers, timeout_seconds):
    request = Request(url, data=body, headers=headers,
                      method="POST" if body is not None else "GET")
    with urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ValueError("Response exceeds bounded capture size")
        return raw, {key: response.headers.get(key) for key in ("Date", "Content-Type")}


def _response(transport, url, body):
    result = (transport or _live_request)(url, body, _headers(), 30)
    if isinstance(result, tuple) and len(result) == 2:
        raw, headers = result
    else:
        raw, headers = result, {}
    if not isinstance(raw, bytes) or not isinstance(headers, dict):
        raise ValueError("Order book transport returned an invalid response")
    return raw, headers


def _levels(pairs, descending):
    """Price/size pairs, validated and ordered as the side they belong to.

    The order is RE-DERIVED rather than trusted: a venue returning a book out of
    order would otherwise be walked in the wrong sequence and report a cost that
    is too low, which is the direction a measurement must never be wrong in.
    """
    levels = []
    for price, size in pairs:
        price, size = float(price), float(size)
        if not math.isfinite(price) or not math.isfinite(size) or price <= 0 or size <= 0:
            raise ValueError("Order book level must have a positive finite price and size")
        levels.append((price, size))
    if not levels:
        raise ValueError("Order book side is empty")
    return sorted(levels, key=lambda level: level[0], reverse=descending)


def _hyperliquid_parse(raw, coin):
    payload = json.loads(raw)
    if (not isinstance(payload, dict) or payload.get("coin") != coin
            or not isinstance(payload.get("levels"), list) or len(payload["levels"]) != 2):
        raise ValueError("Malformed Hyperliquid order book response")
    bids, asks = payload["levels"]
    return (_levels([(item["px"], item["sz"]) for item in bids], descending=True),
            _levels([(item["px"], item["sz"]) for item in asks], descending=False))


def _coinbase_parse(raw, product):
    payload = json.loads(raw)
    if (not isinstance(payload, dict)
            or not isinstance(payload.get("bids"), list)
            or not isinstance(payload.get("asks"), list)):
        raise ValueError("Malformed Coinbase order book response")
    return (_levels([(item[0], item[1]) for item in payload["bids"]], descending=True),
            _levels([(item[0], item[1]) for item in payload["asks"]], descending=False))


_PARSERS = {VENUE_HYPERLIQUID_PERPETUAL: _hyperliquid_parse,
            VENUE_COINBASE_SPOT: _coinbase_parse}


def _capture_content(venue, instrument, url, acquired_at, response_sha256, response_headers):
    content = {
        "schema_version": ORDER_BOOK_CAPTURE_SCHEMA_VERSION,
        "kind": ORDER_BOOK_CAPTURE_KIND,
        "venue": venue,
        "instrument": instrument,
        "url": url,
        "acquired_at": acquired_at,
        "response_sha256": response_sha256,
        "response_headers": response_headers,
    }
    return {**content, "capture_id": "ORDER_BOOK_CAPTURE|" + digest(encoded(content))}


def capture_order_book(venue, instrument, acquired_at, *, transport=None, host=None):
    """Capture one book snapshot and seal the bytes it came from.

    REFUSES A TESTNET HOST. A book whose depth is ninety times thinner than the
    one you would actually trade is not a cheaper measurement of the same thing;
    it is a measurement of something else. Passing it explicitly as `host` is the
    only way to get one, and even then the venue label records it.
    """
    if venue not in _PARSERS:
        raise ValueError("Unknown order book venue")
    if not _explicit_utc(acquired_at):
        raise ValueError("Acquisition time must be canonical UTC")
    if not isinstance(instrument, str) or not instrument:
        raise ValueError("An instrument is required")

    if venue == VENUE_HYPERLIQUID_PERPETUAL:
        host = host or HYPERLIQUID_MAINNET
        if host == HYPERLIQUID_TESTNET:
            raise ValueError(
                "Hyperliquid testnet is not a cost measurement: its book is ninety times "
                "thinner than mainnet and quotes a tighter spread, so a cost derived from "
                "it is better than reality. Use it for order mechanics, never for what a "
                "fill costs.")
        url = f"https://{host}/info"
        body = encoded({"type": "l2Book", "coin": instrument})
    else:
        host = host or COINBASE_HOST
        url = f"https://{host}/products/{instrument}/book?level=2"
        body = None

    raw, headers = _response(transport, url, body)
    bids, asks = _PARSERS[venue](raw, instrument)
    capture = _capture_content(venue, instrument, url, acquired_at, digest(raw), headers)
    stored = encoded({"schema_version": ORDER_BOOK_CAPTURE_SCHEMA_VERSION,
                      "response_base64": base64.b64encode(raw).decode("ascii")})
    return {"bids": bids, "asks": asks}, capture, stored


def verified_order_book_capture(raw_bytes, capture):
    """Reproduce the book from the stored bytes alone, re-deriving the capture's
    identity rather than trusting it."""
    if not isinstance(capture, dict):
        raise ValueError("Order book capture is invalid")
    content = {key: capture[key] for key in (
        "schema_version", "kind", "venue", "instrument", "url", "acquired_at",
        "response_sha256", "response_headers") if key in capture}
    if (set(capture) != set(content) | {"capture_id"}
            or capture.get("schema_version") != ORDER_BOOK_CAPTURE_SCHEMA_VERSION
            or capture.get("kind") != ORDER_BOOK_CAPTURE_KIND
            or capture.get("venue") not in _PARSERS
            or capture.get("capture_id") != "ORDER_BOOK_CAPTURE|" + digest(encoded(content))):
        raise ValueError("Order book capture metadata is invalid")
    try:
        stored = json.loads(raw_bytes)
        raw = base64.b64decode(stored["response_base64"].encode("ascii"), validate=True)
    except (ValueError, KeyError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Order book raw capture is invalid") from error
    if digest(raw) != capture["response_sha256"]:
        raise ValueError("Order book capture response integrity is invalid")
    bids, asks = _PARSERS[capture["venue"]](raw, capture["instrument"])
    return {"bids": bids, "asks": asks}


def mid_price(book):
    best_bid, best_ask = book["bids"][0][0], book["asks"][0][0]
    if best_bid >= best_ask:
        raise ValueError("Crossed book is not a measurement")
    return (best_bid + best_ask) / 2


def half_spread_rate(book):
    best_bid, best_ask = book["bids"][0][0], book["asks"][0][0]
    return (best_ask - best_bid) / 2 / mid_price(book)


def walk_book(book, side, notional):
    """What crossing this book for `notional` would cost, right now.

    Returns the volume-weighted price actually paid, the cost against mid as a
    fraction, how many levels it took, and whether the book ran out. That last
    one matters more than it looks: a walk that exhausted the visible depth has
    measured nothing except that the book was too thin, and reporting its VWAP as
    a cost would understate by an unbounded amount.
    """
    if side not in (SIDE_BUY, SIDE_SELL):
        raise ValueError("Side must be BUY or SELL")
    if not isinstance(notional, (int, float)) or isinstance(notional, bool) \
            or not math.isfinite(notional) or notional <= 0:
        raise ValueError("Notional must be finite and positive")

    levels = book["asks"] if side == SIDE_BUY else book["bids"]
    mid = mid_price(book)
    remaining = float(notional)
    spent, quantity, consumed = 0.0, 0.0, 0
    for price, size in levels:
        consumed += 1
        available = price * size
        take = min(available, remaining)
        spent += take
        quantity += take / price
        remaining -= take
        if remaining <= 0:
            break

    exhausted = remaining > 0
    vwap = spent / quantity if quantity else None
    # Always a COST: paying above mid on a buy, receiving below it on a sell.
    cost = ((vwap - mid) / mid if side == SIDE_BUY else (mid - vwap) / mid) if vwap else None
    return {
        "side": side, "notional": notional, "mid": mid, "vwap": vwap,
        "cost_rate": cost, "levels_consumed": consumed,
        "quantity": quantity, "filled_notional": spent,
        "depth_exhausted": exhausted,
    }
