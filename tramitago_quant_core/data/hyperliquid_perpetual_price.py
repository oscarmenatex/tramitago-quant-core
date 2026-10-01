"""CAP-001 Data extension (2026-09-30): Hyperliquid perpetual price history --
the input the carry tail model said it did not have.

`carry_tail.py` can compute liquidation exactly but cannot estimate the basis
distribution, because the sealed evidence holds funding rates and SPOT closes
and no perpetual prices at all. Every stress scenario was therefore a declared
assumption. This module captures the missing leg so the basis becomes an
observed quantity instead of a guess.

WHAT THIS PRICE IS, and is not: `candleSnapshot` returns TRADED prices. Margin
and liquidation are computed against the venue's MARK price, which blends the
oracle and the book and is not published historically. The traded close is the
standard proxy for basis measurement and is what every public basis series uses,
but it is a proxy: in exactly the disorderly moments the tail model cares about,
mark and traded price diverge most. A basis measured here UNDERSTATES the
liquidation stress of a violent moment, and that caveat travels with the number
via `basis_price_is_traded_not_mark()`.

Same sealed-capture contract as every other provider here: raw responses stored
and hash-sealed, independently re-verifiable with no live network. This module
is the only place that knows Hyperliquid's candle request and response shape.

Deliberately NOT done: filling a missing day. A gap in the perpetual series is
exactly the condition under which the basis matters most, so imputing one would
erase the observation the tail model exists to make. Coverage fails closed, the
same policy acquisition.py applies to Coinbase.
"""

import base64
import json
import math
from urllib.request import Request, urlopen

from tramitago_quant_core.shared.util import digest, encoded, epoch, iso

HYPERLIQUID_PERPETUAL_PRICE_SCHEMA_VERSION = "1"
HYPERLIQUID_PERPETUAL_PRICE_CAPTURE_KIND = "hyperliquid-perpetual-price"
HYPERLIQUID_PERPETUAL_PRICE_SOURCE = (
    "Hyperliquid public perpetual daily candles (traded price, not mark)")
HYPERLIQUID_PERPETUAL_PRICE_MAX_CANDLES_PER_REQUEST = 5000
HYPERLIQUID_PERPETUAL_PRICE_REQUEST_HEADERS = {
    "User-Agent": "TramitaGO-Quant-Core/0.1",
    "Accept": "application/json",
    "Content-Type": "application/json",
}
# Required subset rather than an exact field set: the raw bytes are what is
# sealed, so re-verification re-parses them identically whatever extra fields
# the venue adds later. Rejecting an unknown extra would only break capture.
_CANDLE_REQUIRED_FIELDS = {"t", "o", "h", "l", "c"}


def _headers():
    return dict(HYPERLIQUID_PERPETUAL_PRICE_REQUEST_HEADERS)


def _endpoint():
    return "https://api.hyperliquid.xyz/info"


def _body(coin, start_ms, end_ms):
    """The exact request payload, canonically encoded so the sealed capture can
    reproduce it byte for byte."""
    return encoded({"type": "candleSnapshot",
                    "req": {"coin": coin, "interval": "1d",
                            "startTime": start_ms, "endTime": end_ms}})


def _live_post(url, body, headers, timeout_seconds):
    request = Request(url, data=body, headers=headers)
    with urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read(8_000_001)
        if len(raw) > 8_000_000:
            raise ValueError("Response exceeds bounded capture size")
        return raw, {key: response.headers.get(key) for key in ("Date", "Content-Type")}


def _response(transport, url, body):
    result = (transport or _live_post)(url, body, _headers(), 30)
    if isinstance(result, tuple) and len(result) == 2:
        raw, headers = result
    else:
        raw, headers = result, {}
    if not isinstance(raw, bytes) or not isinstance(headers, dict):
        raise ValueError("Hyperliquid perpetual price transport returned an invalid response")
    return raw, headers


def _parse(raw, coin):
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid Hyperliquid perpetual price response: " + str(exc)) from exc
    if not isinstance(payload, list):
        raise ValueError("Malformed Hyperliquid perpetual price response")
    candles = []
    for item in payload:
        if not isinstance(item, dict) or not _CANDLE_REQUIRED_FIELDS.issubset(item):
            raise ValueError("Malformed Hyperliquid perpetual candle")
        if not isinstance(item["t"], int) or item["t"] <= 0:
            raise ValueError("Invalid Hyperliquid perpetual candle timestamp")
        if "s" in item and item["s"] != coin:
            raise ValueError("Hyperliquid perpetual candle is for another coin")
        values = {}
        for field in ("o", "h", "l", "c"):
            try:
                value = float(item[field])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Non-numeric Hyperliquid perpetual {field}") from exc
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"Non-positive Hyperliquid perpetual {field}")
            values[field] = value
        candles.append({"timestamp": item["t"] // 1000, "open": values["o"],
                        "high": values["h"], "low": values["l"], "close": values["c"]})
    return candles


def _daily_close_series(candles, start, end):
    """One close per UTC day, failing closed unless every day is present.

    A missing day is not filled: a gap is precisely the condition under which
    the basis matters, so imputing one would erase the observation.
    """
    by_day = {}
    for candle in candles:
        if not start <= candle["timestamp"] < end:
            continue
        day = (candle["timestamp"] // 86400) * 86400
        if day in by_day and by_day[day] != candle["close"]:
            raise ValueError("Hyperliquid perpetual series has conflicting closes for " + iso(day))
        by_day[day] = candle["close"]
    expected = {iso(value) for value in range(start, end, 86400)}
    series = {iso(day): close for day, close in by_day.items()}
    if set(series) != expected:
        missing = sorted(expected - set(series))
        raise ValueError("Hyperliquid perpetual price coverage is incomplete; missing "
                         + ", ".join(missing[:5]))
    return series


def _capture_content(coin, capture_period, acquired_at, responses):
    content = {
        "schema_version": HYPERLIQUID_PERPETUAL_PRICE_SCHEMA_VERSION,
        "kind": HYPERLIQUID_PERPETUAL_PRICE_CAPTURE_KIND,
        "source": HYPERLIQUID_PERPETUAL_PRICE_SOURCE,
        "coin": coin,
        "capture_period": capture_period,
        "acquired_at": acquired_at,
        "responses": responses,
    }
    return {**content,
            "capture_id": "HYPERLIQUID_PERPETUAL_PRICE_CAPTURE|" + digest(encoded(content))}


def _raw_content(stored):
    return encoded({"schema_version": HYPERLIQUID_PERPETUAL_PRICE_SCHEMA_VERSION,
                    "responses": stored})


def capture_hyperliquid_perpetual_price(coin, start_utc, end_exclusive_utc, acquired_at,
                                        *, transport=None):
    """Capture and seal the perpetual daily close series.

    Returns (series, capture, raw) where series maps ISO day -> close, matching
    the shape coinbase_close_series returns for spot so the two can be paired
    without either side knowing about the other.
    """
    start, end = epoch(start_utc), epoch(end_exclusive_utc)
    if end <= start:
        raise ValueError("Capture period is empty")
    capture_period = {"start_utc": iso(start), "end_exclusive_utc": iso(end)}

    responses, stored, candles = [], [], []
    sequence, cursor = 1, start
    while cursor < end:
        window_end = min(cursor + HYPERLIQUID_PERPETUAL_PRICE_MAX_CANDLES_PER_REQUEST * 86400,
                         end)
        body = _body(coin, cursor * 1000, window_end * 1000)
        raw, headers = _response(transport, _endpoint(), body)
        response_sha256 = digest(raw)
        responses.append({"sequence": sequence, "url": _endpoint(),
                          "request_body_sha256": digest(body),
                          "start_ms": cursor * 1000, "end_ms": window_end * 1000,
                          "response_sha256": response_sha256,
                          "response_headers": headers})
        stored.append({"sequence": sequence, "response_sha256": response_sha256,
                       "request_body_base64": base64.b64encode(body).decode("ascii"),
                       "response_base64": base64.b64encode(raw).decode("ascii")})
        candles.extend(_parse(raw, coin))
        cursor, sequence = window_end, sequence + 1

    series = _daily_close_series(candles, start, end)
    capture = _capture_content(coin, capture_period, acquired_at, responses)
    return series, capture, _raw_content(stored)


def verified_hyperliquid_perpetual_price_capture(raw_bytes, capture):
    """Independently re-derive the series from the sealed bytes.

    Signature (raw_bytes, capture) -> series is the auxiliary-source verifier
    contract, so this plugs into the dataset layer like any other provider.
    """
    if not isinstance(capture, dict):
        raise ValueError("Hyperliquid perpetual price capture is invalid")
    keys = ("schema_version", "kind", "source", "coin", "capture_period",
            "acquired_at", "responses")
    content = {key: capture[key] for key in keys if key in capture}
    if (set(capture) != set(content) | {"capture_id"}
            or capture.get("schema_version") != HYPERLIQUID_PERPETUAL_PRICE_SCHEMA_VERSION
            or capture.get("kind") != HYPERLIQUID_PERPETUAL_PRICE_CAPTURE_KIND
            or capture.get("source") != HYPERLIQUID_PERPETUAL_PRICE_SOURCE
            or capture.get("capture_id")
            != "HYPERLIQUID_PERPETUAL_PRICE_CAPTURE|" + digest(encoded(content))
            or not isinstance(capture.get("responses"), list)):
        raise ValueError("Hyperliquid perpetual price capture metadata is invalid")
    try:
        raw = json.loads(raw_bytes)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Hyperliquid perpetual price raw capture is invalid") from error
    if (not isinstance(raw, dict) or set(raw) != {"schema_version", "responses"}
            or raw.get("schema_version") != HYPERLIQUID_PERPETUAL_PRICE_SCHEMA_VERSION
            or not isinstance(raw.get("responses"), list)
            or len(raw["responses"]) != len(capture["responses"])):
        raise ValueError("Hyperliquid perpetual price raw capture is incomplete")

    candles = []
    for number, (meta, item) in enumerate(zip(capture["responses"], raw["responses"]), 1):
        if (not isinstance(meta, dict) or not isinstance(item, dict)
                or meta.get("sequence") != number or item.get("sequence") != number
                or meta["response_sha256"] != item["response_sha256"]):
            raise ValueError("Hyperliquid perpetual price request identity is invalid")
        try:
            response = base64.b64decode(item["response_base64"].encode("ascii"), validate=True)
            body = base64.b64decode(item["request_body_base64"].encode("ascii"), validate=True)
        except (ValueError, UnicodeError) as error:
            raise ValueError("Hyperliquid perpetual price response is invalid") from error
        if (digest(response) != meta["response_sha256"]
                or digest(body) != meta["request_body_sha256"]):
            raise ValueError("Hyperliquid perpetual price response hash is invalid")
        candles.extend(_parse(response, capture["coin"]))

    period = capture["capture_period"]
    return _daily_close_series(candles, epoch(period["start_utc"]),
                               epoch(period["end_exclusive_utc"]))


def basis_series(spot_series, perpetual_series):
    """The observed basis per day: (perp - spot) / spot.

    Positive means the perpetual trades above spot, which is the condition a
    cash-and-carry is paid for. Requires both series to cover exactly the same
    days -- a basis computed over a partial overlap would silently describe a
    different period than it claims to.
    """
    if set(spot_series) != set(perpetual_series):
        raise ValueError("Spot and perpetual series must cover exactly the same days")
    if not spot_series:
        raise ValueError("Series are empty")
    basis = {}
    for day, spot in spot_series.items():
        if spot <= 0:
            raise ValueError("Non-positive spot close on " + day)
        basis[day] = (perpetual_series[day] - spot) / spot
    return basis


def basis_stress_observed(basis):
    """What the observed basis actually did -- the input carry_tail was missing.

    `worst_adverse` is the largest move AGAINST a cash-and-carry, which is the
    basis WIDENING (perp rising further above spot), since that is what loses
    money on the short perp leg. The largest single-day CHANGE is reported
    separately because a gap is what liquidates, not a level.
    """
    if not basis:
        raise ValueError("Basis series is empty")
    days = sorted(basis)
    levels = [basis[day] for day in days]
    changes = [levels[index] - levels[index - 1] for index in range(1, len(levels))]
    return {
        "days": len(days),
        "period": [days[0], days[-1]],
        "mean": math.fsum(levels) / len(levels),
        "max_level": max(levels),
        "min_level": min(levels),
        "worst_adverse_daily_change": max(changes) if changes else None,
        "worst_favourable_daily_change": min(changes) if changes else None,
        "note": ("worst_adverse_daily_change is the stress a cash-and-carry must "
                 "survive: the basis widening against the short perp leg in one day"),
    }


def basis_price_is_traded_not_mark():
    """The caveat that must travel with any basis computed from this source."""
    return {
        "price_kind": "traded close",
        "margin_uses": "mark price (oracle-blended, not published historically)",
        "consequence": ("a basis measured from traded prices UNDERSTATES liquidation "
                        "stress in disorderly moments, which are exactly the moments "
                        "the tail model is about"),
    }
