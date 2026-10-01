"""CAP-001 Data extension (2026-10-01): BitMEX perpetual funding history -- the
only funding source that reaches back to a squeeze.

WHY A FOURTH FUNDING PROVIDER. The carry was judged as a level claim and came
back INSUFFICIENT_EVIDENCE for one reason: across 2024-2025 no single day lost
more than 0.198%, against a +2.74% adverse basis excursion measured on BitMEX
during a squeeze. The window contained no episode of the kind that ends a carry,
so the premium was unjudged rather than refuted. Hyperliquid cannot fix that --
its history starts 2023-11, well after every crisis worth testing. BitMEX
XBTUSD has traded since 2016 and its funding history goes with it.

Mirrors the sealed-capture contract of hyperliquid_funding_rate.py exactly: every
raw response stored and hash-sealed, the pagination chain re-derived from the
stored bytes alone, and the daily series reproduced without network access. This
module is the ONLY place that knows BitMEX's funding request/response shape.

TWO SHAPE DIFFERENCES FROM HYPERLIQUID, both handled here:

  EIGHT-HOURLY, NOT HOURLY. BitMEX settles funding three times a day, so a
  caller measuring a carry declares `payments_per_period=3`, not 24. The daily
  series is the MEAN of the day's rates in both modules -- deliberately the same
  convention, because a per-venue convention is exactly how the funding leg came
  to be understated by a factor of 24 in the first place.

  THE RESPONSE CARRIES ITS OWN INTERVAL. `fundingInterval` states the settlement
  period, and it is CHECKED rather than assumed: a venue that silently changed
  its cadence would otherwise rescale a sealed measurement without anything
  noticing. A record whose interval is not eight hours is refused.

`fundingRateDaily` is deliberately IGNORED although the response offers it. It is
the venue's own arithmetic on a number this module already has, and taking it
would mean the daily series depends on a convenience field whose definition the
venue can change -- the same kind of implicit scaling the 24x defect was.
"""

import base64
import json
import math
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tramitago_quant_core.shared.util import digest, encoded, epoch, iso

BITMEX_FUNDING_RATE_CAPTURE_SCHEMA_VERSION = "1"
BITMEX_FUNDING_RATE_CAPTURE_KIND = "bitmex-funding-rate-capture"
BITMEX_FUNDING_RATE_SOURCE = "BitMEX public perpetual funding history"
BITMEX_FUNDING_RATE_MAX_RECORDS_PER_REQUEST = 500
BITMEX_FUNDING_RATE_INTERVAL_SECONDS = 8 * 3600
BITMEX_FUNDING_PAYMENTS_PER_DAY = 3
BITMEX_FUNDING_RATE_REQUEST_HEADERS = {
    "User-Agent": "TramitaGO-Quant-Core/0.1",
    "Accept": "application/json",
}
_ITEM_FIELDS = {"timestamp", "symbol", "fundingInterval", "fundingRate", "fundingRateDaily"}


def _headers():
    return dict(BITMEX_FUNDING_RATE_REQUEST_HEADERS)


def _endpoint(symbol, start_utc, end_exclusive_utc):
    """The exact URL, with parameters in a fixed order so the sealed capture can
    reproduce it byte for byte."""
    query = urlencode([
        ("symbol", symbol),
        ("startTime", start_utc),
        ("endTime", end_exclusive_utc),
        ("count", BITMEX_FUNDING_RATE_MAX_RECORDS_PER_REQUEST),
        ("reverse", "false"),
    ])
    return "https://www.bitmex.com/api/v1/funding?" + query


def _live_get(url, headers, timeout_seconds):
    with urlopen(Request(url, headers=headers), timeout=timeout_seconds) as response:
        raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ValueError("Response exceeds bounded capture size")
        return raw, {key: response.headers.get(key) for key in ("Date", "Content-Type")}


def _response(transport, url):
    result = (transport or _live_get)(url, _headers(), 30)
    if isinstance(result, tuple) and len(result) == 2:
        raw, headers = result
    else:
        raw, headers = result, {}
    if not isinstance(raw, bytes) or not isinstance(headers, dict):
        raise ValueError("BitMEX funding rate transport returned an invalid response")
    return raw, headers


def _interval_seconds(value):
    """`fundingInterval` is an ISO timestamp whose TIME OF DAY is the period --
    "2000-01-01T08:00:00.000Z" means eight hours. Parsed rather than assumed."""
    if not isinstance(value, str) or "T" not in value:
        raise ValueError("Malformed BitMEX funding interval")
    clock = value.split("T", 1)[1].rstrip("Z").split(".")[0]
    try:
        hours, minutes, seconds = (int(part) for part in clock.split(":"))
    except ValueError as error:
        raise ValueError("Malformed BitMEX funding interval") from error
    return hours * 3600 + minutes * 60 + seconds


def _parse(raw, symbol):
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError) as error:
        raise ValueError("Invalid BitMEX funding rate response: " + str(error)) from error
    if not isinstance(payload, list):
        raise ValueError("Malformed BitMEX funding rate response")
    events = []
    for item in payload:
        if (not isinstance(item, dict) or set(item) != _ITEM_FIELDS
                or item.get("symbol") != symbol
                or not isinstance(item.get("timestamp"), str)
                or not isinstance(item.get("fundingRate"), (int, float))
                or isinstance(item.get("fundingRate"), bool)):
            raise ValueError("Malformed BitMEX funding rate record")
        if _interval_seconds(item["fundingInterval"]) != BITMEX_FUNDING_RATE_INTERVAL_SECONDS:
            raise ValueError(
                "BitMEX funding interval is not eight hours; a venue that changed its "
                "cadence would rescale this measurement without anything noticing")
        rate = float(item["fundingRate"])
        if not math.isfinite(rate):
            raise ValueError("Non-finite BitMEX funding rate")
        events.append({"timestamp": epoch(item["timestamp"]), "funding_rate": rate})
    return events


def _daily_series(events, start, end):
    """One daily MEAN per UTC day -- the same convention hyperliquid_funding_rate
    uses, so the per-period scaling stays an explicit caller decision rather than
    a per-venue surprise. Fails closed unless every day in [start, end) is
    present: no imputation, matching acquisition.py's policy."""
    by_day = {}
    for event in events:
        if not start <= event["timestamp"] < end:
            continue
        by_day.setdefault((event["timestamp"] // 86400) * 86400, []).append(event["funding_rate"])
    by_day_iso = {iso(day): values for day, values in by_day.items()}
    if set(by_day_iso) != {iso(value) for value in range(start, end, 86400)}:
        raise ValueError("BitMEX funding rate coverage is incomplete for the capture period")
    return {day: math.fsum(values) / len(values) for day, values in by_day_iso.items()}


def _capture_content(symbol, capture_period, acquired_at, responses):
    content = {
        "schema_version": BITMEX_FUNDING_RATE_CAPTURE_SCHEMA_VERSION,
        "kind": BITMEX_FUNDING_RATE_CAPTURE_KIND,
        "source": BITMEX_FUNDING_RATE_SOURCE,
        "symbol": symbol,
        "capture_period": capture_period,
        "acquired_at": acquired_at,
        "responses": responses,
    }
    return {**content, "capture_id": "BITMEX_FUNDING_RATE_CAPTURE|" + digest(encoded(content))}


def _raw_content(captures):
    return {"schema_version": BITMEX_FUNDING_RATE_CAPTURE_SCHEMA_VERSION, "captures": captures}


def capture_bitmex_funding_rate(symbol, start_utc, end_exclusive_utc, acquired_at, *,
                                transport=None):
    """Capture and seal every funding response byte for byte, then reduce to a
    daily mean series.

    BitMEX paginates FORWARD by time: each request returns up to 500 ascending
    records from `startTime`, and the next request starts one second after the
    last record returned. The cursor is therefore content-dependent, exactly like
    Hyperliquid's, and `verified_bitmex_funding_rate_capture` reproduces the same
    chain from the sealed bytes alone.
    """
    start, end = epoch(start_utc), epoch(end_exclusive_utc)
    responses, stored, all_events = [], [], []
    cursor = start
    sequence = 0
    while cursor < end:
        sequence += 1
        url = _endpoint(symbol, iso(cursor), end_exclusive_utc)
        response, headers = _response(transport, url)
        response_sha256 = digest(response)
        responses.append({"sequence": sequence, "url": url,
                          "response_sha256": response_sha256, "response_headers": headers})
        stored.append({"sequence": sequence, "response_sha256": response_sha256,
                       "response_base64": base64.b64encode(response).decode("ascii")})
        events = _parse(response, symbol)
        # Termination comes from content, never from the page size: an empty page
        # means the range is exhausted. A short page is NOT treated as the last
        # one, which would silently under-fetch if the venue ever returned fewer
        # records than its documented maximum.
        if not events:
            if sequence == 1:
                raise ValueError("BitMEX funding history has no coverage for the period")
            break
        all_events.extend(events)
        cursor = max(event["timestamp"] for event in events) + 1
    capture = _capture_content(
        symbol, {"start_utc": start_utc, "end_exclusive_utc": end_exclusive_utc},
        acquired_at, responses)
    return _daily_series(all_events, start, end), capture, encoded(_raw_content(stored))


def verified_bitmex_funding_rate_capture(raw_bytes, capture):
    """Independently reproduce the daily series from the stored raw bytes alone,
    re-deriving each request's URL from the PRECEDING page's own parsed content
    rather than trusting the recorded one. Signature (raw_bytes, capture) ->
    daily_series is the generic auxiliary-verifier contract."""
    if not isinstance(capture, dict):
        raise ValueError("BitMEX funding rate capture is invalid")
    responses = capture.get("responses")
    content = {key: capture[key] for key in (
        "schema_version", "kind", "source", "symbol", "capture_period", "acquired_at",
        "responses") if key in capture}
    if (set(capture) != set(content) | {"capture_id"}
            or capture.get("schema_version") != BITMEX_FUNDING_RATE_CAPTURE_SCHEMA_VERSION
            or capture.get("kind") != BITMEX_FUNDING_RATE_CAPTURE_KIND
            or capture.get("source") != BITMEX_FUNDING_RATE_SOURCE
            or capture.get("capture_id")
            != "BITMEX_FUNDING_RATE_CAPTURE|" + digest(encoded(content))
            or not isinstance(responses, list) or not responses):
        raise ValueError("BitMEX funding rate capture metadata is invalid")
    try:
        raw = json.loads(raw_bytes)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("BitMEX funding rate raw capture is invalid") from error
    if (not isinstance(raw, dict) or set(raw) != {"schema_version", "captures"}
            or raw.get("schema_version") != BITMEX_FUNDING_RATE_CAPTURE_SCHEMA_VERSION
            or not isinstance(raw.get("captures"), list)
            or len(raw["captures"]) != len(responses)):
        raise ValueError("BitMEX funding rate raw capture is incomplete")

    period = capture["capture_period"]
    start, end = epoch(period["start_utc"]), epoch(period["end_exclusive_utc"])
    symbol = capture["symbol"]
    all_events = []
    cursor = start
    for number, (metadata, stored) in enumerate(zip(responses, raw["captures"]), 1):
        if cursor >= end:
            raise ValueError("BitMEX funding rate capture has a superfluous request")
        expected = _endpoint(symbol, iso(cursor), period["end_exclusive_utc"])
        if (not isinstance(metadata, dict)
                or set(metadata) != {"sequence", "url", "response_sha256", "response_headers"}
                or not isinstance(stored, dict)
                or set(stored) != {"sequence", "response_sha256", "response_base64"}
                or metadata["sequence"] != number or stored["sequence"] != number
                or metadata["url"] != expected
                or metadata["response_sha256"] != stored["response_sha256"]
                or not isinstance(metadata["response_headers"], dict)):
            raise ValueError("BitMEX funding rate capture request identity is invalid")
        try:
            response = base64.b64decode(stored["response_base64"].encode("ascii"), validate=True)
        except (ValueError, UnicodeError) as error:
            raise ValueError("BitMEX funding rate capture response is invalid") from error
        if digest(response) != metadata["response_sha256"]:
            raise ValueError("BitMEX funding rate capture response integrity is invalid")
        events = _parse(response, symbol)
        if not events:
            if number == 1 or number != len(responses):
                raise ValueError("BitMEX funding rate capture pagination is invalid")
            break
        all_events.extend(events)
        cursor = max(event["timestamp"] for event in events) + 1
        if (cursor >= end) != (number == len(responses)):
            raise ValueError("BitMEX funding rate capture pagination is invalid")
    return _daily_series(all_events, start, end)
