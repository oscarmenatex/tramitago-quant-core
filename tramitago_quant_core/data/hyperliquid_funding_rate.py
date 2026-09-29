"""CAP-001 Data extension (Etapa 2.8, provider substitution 2026-09-29):
Hyperliquid perpetual funding-rate history -- the auxiliary data source
that finally makes a CLEAN funding-rate test possible.

Why a third provider: Binance has the depth but is geoblocked (HTTP 451)
from every network tried, including an Oracle Cloud VM; Bybit is blocked
by a CloudFront country rule; OKX is reachable but retains only ~3 months,
which forced the first funding-rate Hypothesis onto a short, severely
class-imbalanced window. Hyperliquid is a fully on-chain perpetuals DEX
with a public API and no geographic gating by design, and it serves
complete HOURLY funding history back beyond 2024 -- enough to test the
same full-year period every other real Hypothesis used.

Mirrors binance_funding_rate.py/okx_funding_rate.py's sealed-capture
contract (every raw response stored and hash-sealed, independently
re-verifiable without live network access). This module is the ONLY place
that knows Hyperliquid's request/response shape and its forward
cursor-based pagination -- historical_dataset.py stays provider-agnostic
(DOC-005 PA-005-002; SS13: riesgo de "dependencia de un unico proveedor").

Two shape differences from the earlier providers, both handled here and
invisible to the core: funding settles HOURLY (24 events per day, not 3),
and the reported timestamps carry a few milliseconds of jitter rather than
landing exactly on the hour, so whole-second timestamps are NOT required.
"""

import base64
import json
import math
from urllib.request import Request, urlopen

from tramitago_quant_core.shared.util import digest, encoded, epoch, iso

HYPERLIQUID_FUNDING_RATE_CAPTURE_SCHEMA_VERSION = "1"
HYPERLIQUID_FUNDING_RATE_SOURCE = "Hyperliquid public perpetual funding rate"
HYPERLIQUID_FUNDING_RATE_MAX_RECORDS_PER_REQUEST = 500
HYPERLIQUID_FUNDING_RATE_INTERVAL_SECONDS = 3600
HYPERLIQUID_FUNDING_RATE_REQUEST_HEADERS = {
    "User-Agent": "TramitaGO-Quant-Core-Etapa2.8/0.1",
    "Accept": "application/json",
    "Content-Type": "application/json",
}
_HYPERLIQUID_FUNDING_RATE_ITEM_FIELDS = {"coin", "fundingRate", "premium", "time"}


def _hyperliquid_funding_rate_headers():
    return dict(HYPERLIQUID_FUNDING_RATE_REQUEST_HEADERS)


def _hyperliquid_funding_rate_endpoint():
    return "https://api.hyperliquid.xyz/info"


def _hyperliquid_funding_rate_body(coin, start_ms, end_ms):
    """The exact request payload, canonically encoded so the sealed capture
    can reproduce it byte-for-byte."""
    return encoded({"type": "fundingHistory", "coin": coin,
                    "startTime": start_ms, "endTime": end_ms})


def _hyperliquid_funding_rate_live_post(url, body, headers, timeout_seconds):
    request = Request(url, data=body, headers=headers)
    with urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ValueError("Response exceeds bounded capture size")
        return raw, {key: response.headers.get(key) for key in ("Date", "Content-Type")}


def _hyperliquid_funding_rate_response(transport, url, body):
    response = (transport or _hyperliquid_funding_rate_live_post)(
        url, body, _hyperliquid_funding_rate_headers(), 30)
    if isinstance(response, tuple) and len(response) == 2:
        raw, headers = response
    else:
        raw, headers = response, {}
    if not isinstance(raw, bytes) or not isinstance(headers, dict):
        raise ValueError("Hyperliquid funding rate transport returned an invalid response")
    return raw, headers


def _hyperliquid_funding_rate_parse(raw, coin):
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid Hyperliquid funding rate response: " + str(exc)) from exc
    if not isinstance(payload, list):
        raise ValueError("Malformed Hyperliquid funding rate response")
    events = []
    for item in payload:
        if (not isinstance(item, dict) or set(item) != _HYPERLIQUID_FUNDING_RATE_ITEM_FIELDS
                or item.get("coin") != coin
                or not isinstance(item.get("time"), int) or item["time"] <= 0
                or not isinstance(item.get("fundingRate"), str)):
            raise ValueError("Malformed Hyperliquid funding rate record")
        try:
            rate = float(item["fundingRate"])
        except ValueError as exc:
            raise ValueError("Non-numeric Hyperliquid funding rate") from exc
        if not math.isfinite(rate):
            raise ValueError("Non-finite Hyperliquid funding rate")
        # Timestamps carry millisecond jitter (e.g. :00:00.054) -- floor to the
        # second rather than demanding whole seconds, unlike the 8h providers.
        events.append({"timestamp": item["time"] // 1000, "funding_rate": rate,
                       "time_ms": item["time"]})
    return events


def _hyperliquid_funding_rate_daily_series(events, start, end):
    """Reduce hourly funding events into one daily mean per UTC day -- fails
    closed unless every day in [start, end) is present, mirroring
    acquisition.py's "reject entire dataset; no imputation" policy."""
    by_day = {}
    for event in events:
        if not start <= event["timestamp"] < end:
            continue
        day_start = (event["timestamp"] // 86400) * 86400
        by_day.setdefault(day_start, []).append(event["funding_rate"])
    expected_days = {iso(value) for value in range(start, end, 86400)}
    by_day_iso = {iso(day): values for day, values in by_day.items()}
    if set(by_day_iso) != expected_days:
        raise ValueError("Hyperliquid funding rate coverage is incomplete for the capture period")
    return {day: math.fsum(values) / len(values) for day, values in by_day_iso.items()}


def _hyperliquid_funding_rate_capture_content(coin, capture_period, acquired_at, responses):
    content = {
        "schema_version": HYPERLIQUID_FUNDING_RATE_CAPTURE_SCHEMA_VERSION,
        "kind": "hyperliquid-funding-rate-capture",
        "source": HYPERLIQUID_FUNDING_RATE_SOURCE,
        "coin": coin,
        "capture_period": capture_period,
        "acquired_at": acquired_at,
        "responses": responses,
    }
    return {**content,
            "capture_id": "HYPERLIQUID_FUNDING_RATE_CAPTURE|" + digest(encoded(content))}


def _hyperliquid_funding_rate_raw_content(captures):
    return {"schema_version": HYPERLIQUID_FUNDING_RATE_CAPTURE_SCHEMA_VERSION,
            "captures": captures}


def capture_hyperliquid_funding_rate(coin, start_utc, end_exclusive_utc, acquired_at, *,
                                     transport=None):
    """Capture and seal every funding-rate response byte-for-byte, then
    reduce to a daily mean series. Hyperliquid paginates FORWARD: each
    request returns up to 500 hourly records from `startTime`, and the next
    request's `startTime` is the last record's own timestamp plus one
    millisecond -- content-dependent, not a pure function of the requested
    bounds. verified_hyperliquid_funding_rate_capture reproduces that exact
    chain from the sealed raw bytes alone.
    """
    start = epoch(start_utc)
    end = epoch(end_exclusive_utc)
    end_ms = end * 1000
    responses, stored, all_events = [], [], []
    cursor_ms = start * 1000
    sequence = 0
    while cursor_ms < end_ms:
        sequence += 1
        body = _hyperliquid_funding_rate_body(coin, cursor_ms, end_ms)
        response, headers = _hyperliquid_funding_rate_response(
            transport, _hyperliquid_funding_rate_endpoint(), body)
        response_sha256 = digest(response)
        responses.append({
            "sequence": sequence, "start_time_ms": cursor_ms, "end_time_ms": end_ms,
            "request_sha256": digest(body), "response_sha256": response_sha256,
            "response_headers": headers,
        })
        stored.append({
            "sequence": sequence, "response_sha256": response_sha256,
            "response_base64": base64.b64encode(response).decode("ascii"),
        })
        events = _hyperliquid_funding_rate_parse(response, coin)
        # Termination is derived from content, never from the page size: an
        # empty page means the range is exhausted. Deliberately NOT "a short
        # page is the last one" -- that would silently under-fetch if the
        # server ever returned fewer records than its documented maximum.
        if not events:
            if sequence == 1:
                raise ValueError(
                    "Hyperliquid funding rate history has no coverage for the period")
            break
        all_events.extend(events)
        cursor_ms = max(event["time_ms"] for event in events) + 1
    capture_period = {"start_utc": start_utc, "end_exclusive_utc": end_exclusive_utc}
    capture = _hyperliquid_funding_rate_capture_content(
        coin, capture_period, acquired_at, responses)
    raw = encoded(_hyperliquid_funding_rate_raw_content(stored))
    daily_series = _hyperliquid_funding_rate_daily_series(all_events, start, end)
    return daily_series, capture, raw


def verified_hyperliquid_funding_rate_capture(raw_bytes, capture):
    """Independently reproduce the daily series from stored raw bytes, never
    trusting the persisted capture metadata alone: each page's cursor is
    recomputed from the PRECEDING page's own parsed content, and the
    pagination's stopping point is re-derived rather than trusted.
    Signature (raw_bytes, capture) -> daily_series is the generic
    "auxiliary verifier" contract historical_dataset.py calls without
    knowing it is Hyperliquid-specific."""
    if not isinstance(capture, dict):
        raise ValueError("Hyperliquid funding rate capture is invalid")
    responses = capture.get("responses")
    content = {key: capture[key] for key in (
        "schema_version", "kind", "source", "coin", "capture_period", "acquired_at", "responses")
        if key in capture}
    if (set(capture) != set(content) | {"capture_id"}
            or capture.get("schema_version") != HYPERLIQUID_FUNDING_RATE_CAPTURE_SCHEMA_VERSION
            or capture.get("kind") != "hyperliquid-funding-rate-capture"
            or capture.get("source") != HYPERLIQUID_FUNDING_RATE_SOURCE
            or capture.get("capture_id")
            != "HYPERLIQUID_FUNDING_RATE_CAPTURE|" + digest(encoded(content))
            or not isinstance(responses, list) or not responses):
        raise ValueError("Hyperliquid funding rate capture metadata is invalid")
    try:
        raw = json.loads(raw_bytes)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Hyperliquid funding rate raw capture is invalid") from error
    if (not isinstance(raw, dict) or set(raw) != {"schema_version", "captures"}
            or raw.get("schema_version") != HYPERLIQUID_FUNDING_RATE_CAPTURE_SCHEMA_VERSION
            or not isinstance(raw.get("captures"), list)
            or len(raw["captures"]) != len(responses)):
        raise ValueError("Hyperliquid funding rate raw capture is incomplete")
    capture_period = capture["capture_period"]
    start = epoch(capture_period["start_utc"])
    end = epoch(capture_period["end_exclusive_utc"])
    end_ms = end * 1000
    coin = capture["coin"]
    all_events = []
    cursor_ms = start * 1000
    for number, (metadata, stored) in enumerate(zip(responses, raw["captures"]), 1):
        fields = {"sequence", "start_time_ms", "end_time_ms", "request_sha256",
                  "response_sha256", "response_headers"}
        raw_fields = {"sequence", "response_sha256", "response_base64"}
        if cursor_ms >= end_ms:
            raise ValueError("Hyperliquid funding rate capture has a superfluous request")
        expected_body = _hyperliquid_funding_rate_body(coin, cursor_ms, end_ms)
        if (not isinstance(metadata, dict) or set(metadata) != fields
                or not isinstance(stored, dict) or set(stored) != raw_fields
                or metadata["sequence"] != number or stored["sequence"] != number
                or metadata["start_time_ms"] != cursor_ms
                or metadata["end_time_ms"] != end_ms
                or metadata["request_sha256"] != digest(expected_body)
                or metadata["response_sha256"] != stored["response_sha256"]
                or not isinstance(metadata["response_headers"], dict)):
            raise ValueError("Hyperliquid funding rate capture request identity is invalid")
        try:
            response = base64.b64decode(stored["response_base64"].encode("ascii"), validate=True)
        except (ValueError, UnicodeError) as error:
            raise ValueError("Hyperliquid funding rate capture response is invalid") from error
        if digest(response) != metadata["response_sha256"]:
            raise ValueError("Hyperliquid funding rate capture response integrity is invalid")
        events = _hyperliquid_funding_rate_parse(response, coin)
        if not events:
            # An exhausted range terminates the chain; it can never be the
            # only request, and nothing may follow it.
            if number == 1 or number != len(responses):
                raise ValueError("Hyperliquid funding rate capture pagination is invalid")
            break
        all_events.extend(events)
        cursor_ms = max(event["time_ms"] for event in events) + 1
        if (cursor_ms >= end_ms) != (number == len(responses)):
            raise ValueError("Hyperliquid funding rate capture pagination is invalid")
    return _hyperliquid_funding_rate_daily_series(all_events, start, end)
