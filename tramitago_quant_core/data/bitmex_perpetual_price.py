"""CAP-001 Data extension (2026-09-30): BitMEX XBTUSD perpetual price history --
the deepest perpetual series that exists, and the only one reaching a crisis.

Hyperliquid could not answer the carry tail question: its hourly candles reach
~180 days, 4-hourly ~2 years, and everything before 2023-02-26 is oracle
backfill with zero trades. No market-structure crisis falls inside that. The
conclusion drawn from it -- "the tail is unmeasurable" -- was wrong, and wrong
for an avoidable reason: one venue was mistaken for all venues.

BitMEX serves XBTUSD from 2016 at one-minute resolution with real volume, so
March 2020, the 2021 bull runs, LUNA and FTX are all observable.

TWO TRAPS THIS MODULE EXISTS TO CLOSE:

  TIMESTAMP ALIGNMENT. BitMEX labels a bucket with the time it CLOSED; Coinbase
  labels a candle with the time it OPENED. Pairing them naively shifts one
  series by a full bucket. Measured on a calm period, the naive pairing reports
  a mean absolute basis of 0.31% and the aligned pairing 0.03% -- a factor of
  ten of pure artefact. The interval is subtracted here, once, so no caller can
  forget.

  INVERSE CONTRACT. XBTUSD is quoted in USD but MARGINED AND SETTLED IN BTC.
  When BTC falls, the collateral backing a short falls with it, a compounding
  effect a linear USDT-margined perpetual does not have. The basis measured
  here transfers to a linear venue; the liquidation translation does NOT, and
  is worse here. Declared in the capture so it cannot be read off silently.

RATE LIMIT: BitMEX allows roughly 30 unauthenticated requests per minute. A
multi-year capture at fine resolution needs throttling by the caller; this
module does not sleep on its own, because a module that hides a delay makes a
capture look cheaper than it is.
"""

import base64
import json
import math
import urllib.parse
from urllib.request import Request, urlopen

from tramitago_quant_core.shared.util import digest, encoded, epoch, iso

BITMEX_PERPETUAL_PRICE_SCHEMA_VERSION = "1"
BITMEX_PERPETUAL_PRICE_CAPTURE_KIND = "bitmex-perpetual-price"
BITMEX_PERPETUAL_PRICE_SOURCE = "BitMEX public bucketed trades (traded price, not mark)"
BITMEX_PERPETUAL_MAX_ROWS_PER_REQUEST = 1000
BITMEX_PERPETUAL_INTERVALS = {"1m": 60, "5m": 300, "1h": 3600, "1d": 86400}

# Margin currency decides whether a crash erodes the collateral backing a short.
BITMEX_INVERSE_SYMBOLS = ("XBTUSD",)

BITMEX_REQUEST_HEADERS = {
    "User-Agent": "TramitaGO-Quant-Core/0.1",
    "Accept": "application/json",
}


def _endpoint(symbol, bin_size, start_utc, end_exclusive_utc):
    return ("https://www.bitmex.com/api/v1/trade/bucketed?"
            + urllib.parse.urlencode({
                "binSize": bin_size, "symbol": symbol, "partial": "false",
                "count": BITMEX_PERPETUAL_MAX_ROWS_PER_REQUEST,
                "startTime": start_utc, "endTime": end_exclusive_utc}))


def _live_get(url, headers, timeout_seconds):
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read(8_000_001)
        if len(raw) > 8_000_000:
            raise ValueError("Response exceeds bounded capture size")
        return raw, {key: response.headers.get(key) for key in ("Date", "Content-Type")}


def _response(transport, url):
    result = (transport or _live_get)(url, dict(BITMEX_REQUEST_HEADERS), 40)
    if isinstance(result, tuple) and len(result) == 2:
        raw, headers = result
    else:
        raw, headers = result, {}
    if not isinstance(raw, bytes) or not isinstance(headers, dict):
        raise ValueError("BitMEX transport returned an invalid response")
    return raw, headers


def _parse(raw, symbol, interval_seconds):
    """Parse buckets, REALIGNING the timestamp from close to open.

    This is the correction the module exists for. BitMEX stamps a bucket with
    its closing instant, so a 1h bucket labelled 01:00 covers 00:00-01:00 and
    must be keyed at 00:00 to sit alongside a Coinbase candle for the same hour.
    """
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid BitMEX response: " + str(exc)) from exc
    if not isinstance(payload, list):
        raise ValueError("Malformed BitMEX response")
    buckets = []
    for item in payload:
        if not isinstance(item, dict) or "timestamp" not in item or "close" not in item:
            raise ValueError("Malformed BitMEX bucket")
        if item.get("symbol") not in (None, symbol):
            raise ValueError("BitMEX bucket is for another symbol")
        stamp = item["timestamp"]
        if not isinstance(stamp, str):
            raise ValueError("Invalid BitMEX bucket timestamp")
        closed_at = epoch(stamp.replace(".000Z", "Z"))
        close = item["close"]
        if close is None:
            # An empty bucket has no traded price. Refused rather than carried
            # forward: a basis against an invented price is not an observation.
            raise ValueError("BitMEX bucket has no traded close at " + iso(closed_at))
        try:
            close = float(close)
        except (TypeError, ValueError) as exc:
            raise ValueError("Non-numeric BitMEX close") from exc
        if not math.isfinite(close) or close <= 0:
            raise ValueError("Non-positive BitMEX close")
        volume = item.get("volume")
        if volume is not None and float(volume) <= 0:
            raise ValueError("BitMEX bucket has no volume at " + iso(closed_at))
        buckets.append({"timestamp": closed_at - interval_seconds, "close": close})
    return buckets


def _close_series(buckets, start, end, interval_seconds):
    series = {}
    for bucket in buckets:
        if not start <= bucket["timestamp"] < end:
            continue
        key = iso((bucket["timestamp"] // interval_seconds) * interval_seconds)
        if key in series and series[key] != bucket["close"]:
            raise ValueError("BitMEX series has conflicting closes for " + key)
        series[key] = bucket["close"]
    expected = {iso(value) for value in range(start, end, interval_seconds)}
    if set(series) != expected:
        missing = sorted(expected - set(series))
        raise ValueError("BitMEX coverage is incomplete; missing " + ", ".join(missing[:5]))
    return series


def _capture_content(symbol, bin_size, capture_period, acquired_at, responses):
    content = {
        "schema_version": BITMEX_PERPETUAL_PRICE_SCHEMA_VERSION,
        "kind": BITMEX_PERPETUAL_PRICE_CAPTURE_KIND,
        "source": BITMEX_PERPETUAL_PRICE_SOURCE,
        "symbol": symbol,
        "margin_kind": ("INVERSE" if symbol in BITMEX_INVERSE_SYMBOLS else "LINEAR"),
        "bin_size": bin_size,
        "timestamp_convention": "bucket close, realigned to open on parse",
        "capture_period": capture_period,
        "acquired_at": acquired_at,
        "responses": responses,
    }
    return {**content, "capture_id": "BITMEX_PERPETUAL_PRICE_CAPTURE|" + digest(encoded(content))}


def capture_bitmex_perpetual_price(symbol, start_utc, end_exclusive_utc, acquired_at,
                                   *, bin_size="1h", transport=None):
    """Capture and seal the perpetual close series, aligned to interval opens.

    Returns (series, capture, raw) with the same shape the other providers
    return, so the basis can be computed without either side knowing about the
    other.
    """
    if bin_size not in BITMEX_PERPETUAL_INTERVALS:
        raise ValueError("Bin size must be one of " + ", ".join(sorted(BITMEX_PERPETUAL_INTERVALS)))
    interval_seconds = BITMEX_PERPETUAL_INTERVALS[bin_size]
    start, end = epoch(start_utc), epoch(end_exclusive_utc)
    if end <= start:
        raise ValueError("Capture period is empty")
    if start % interval_seconds or end % interval_seconds:
        raise ValueError("Capture period must align to the bin size")
    capture_period = {"start_utc": iso(start), "end_exclusive_utc": iso(end)}

    responses, stored, buckets = [], [], []
    sequence, cursor = 1, start
    while cursor < end:
        window_end = min(cursor + BITMEX_PERPETUAL_MAX_ROWS_PER_REQUEST * interval_seconds, end)
        # BitMEX stamps buckets at close, so the request window is shifted to
        # match: asking for [cursor, window_end) of OPENS means asking the venue
        # for the closes one interval later.
        url = _endpoint(symbol, bin_size, iso(cursor + interval_seconds),
                        iso(window_end + interval_seconds))
        raw, headers = _response(transport, url)
        response_sha256 = digest(raw)
        responses.append({"sequence": sequence, "url": url,
                          "response_sha256": response_sha256, "response_headers": headers})
        stored.append({"sequence": sequence, "response_sha256": response_sha256,
                       "response_base64": base64.b64encode(raw).decode("ascii")})
        buckets.extend(_parse(raw, symbol, interval_seconds))
        cursor, sequence = window_end, sequence + 1

    series = _close_series(buckets, start, end, interval_seconds)
    capture = _capture_content(symbol, bin_size, capture_period, acquired_at, responses)
    raw_content = encoded({"schema_version": BITMEX_PERPETUAL_PRICE_SCHEMA_VERSION,
                           "responses": stored})
    return series, capture, raw_content


def verified_bitmex_perpetual_price_capture(raw_bytes, capture):
    """Independently re-derive the series from the sealed bytes."""
    if not isinstance(capture, dict):
        raise ValueError("BitMEX capture is invalid")
    keys = ("schema_version", "kind", "source", "symbol", "margin_kind", "bin_size",
            "timestamp_convention", "capture_period", "acquired_at", "responses")
    content = {key: capture[key] for key in keys if key in capture}
    if (set(capture) != set(content) | {"capture_id"}
            or capture.get("schema_version") != BITMEX_PERPETUAL_PRICE_SCHEMA_VERSION
            or capture.get("kind") != BITMEX_PERPETUAL_PRICE_CAPTURE_KIND
            or capture.get("source") != BITMEX_PERPETUAL_PRICE_SOURCE
            or capture.get("bin_size") not in BITMEX_PERPETUAL_INTERVALS
            or capture.get("capture_id")
            != "BITMEX_PERPETUAL_PRICE_CAPTURE|" + digest(encoded(content))
            or not isinstance(capture.get("responses"), list)):
        raise ValueError("BitMEX capture metadata is invalid")
    try:
        raw = json.loads(raw_bytes)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("BitMEX raw capture is invalid") from error
    if (not isinstance(raw, dict) or set(raw) != {"schema_version", "responses"}
            or len(raw["responses"]) != len(capture["responses"])):
        raise ValueError("BitMEX raw capture is incomplete")

    interval_seconds = BITMEX_PERPETUAL_INTERVALS[capture["bin_size"]]
    buckets = []
    for number, (meta, item) in enumerate(zip(capture["responses"], raw["responses"]), 1):
        if (meta.get("sequence") != number or item.get("sequence") != number
                or meta["response_sha256"] != item["response_sha256"]):
            raise ValueError("BitMEX request identity is invalid")
        try:
            response = base64.b64decode(item["response_base64"].encode("ascii"), validate=True)
        except (ValueError, UnicodeError) as error:
            raise ValueError("BitMEX response is invalid") from error
        if digest(response) != meta["response_sha256"]:
            raise ValueError("BitMEX response hash is invalid")
        buckets.extend(_parse(response, capture["symbol"], interval_seconds))

    period = capture["capture_period"]
    return _close_series(buckets, epoch(period["start_utc"]),
                         epoch(period["end_exclusive_utc"]), interval_seconds)


def carry_adverse_excursion(basis, *, entry_basis=0.0):
    """The stress a CASH-AND-CARRY actually faces, which is not any large move.

    A carry is long spot and short perpetual, so it LOSES when the perpetual
    pulls ABOVE spot -- a positive basis. A crash drives the perpetual BELOW
    spot as longs are liquidated, which is favourable. Measuring "the largest
    basis move" therefore measures the wrong thing: March 2020 shows a -12.78%
    excursion that a carry holder would have profited from.

    What matters is how far the basis travels ADVERSELY from where the position
    was entered, since that is the unrealised loss on the short leg.
    """
    if not basis:
        raise ValueError("Basis series is empty")
    levels = list(basis.values())
    excursions = [level - entry_basis for level in levels]
    worst = max(excursions)
    return {
        "observations": len(levels),
        "entry_basis": entry_basis,
        "worst_adverse_excursion": worst,
        "best_favourable_excursion": min(excursions),
        "max_level": max(levels),
        "min_level": min(levels),
        "note": ("adverse means the perpetual trading ABOVE spot; a liquidation "
                 "cascade drives it below and is favourable to a carry"),
    }
