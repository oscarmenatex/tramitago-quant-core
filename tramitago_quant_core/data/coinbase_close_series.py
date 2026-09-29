"""CAP-001 Data extension (vía 7.C, 2026-09-29): a SECOND Coinbase
instrument's daily closes, captured and sealed as an auxiliary data source.

A relative-value Hypothesis needs both legs of a pair. The primary leg
travels the normal Hypothesis Dataset path; this module supplies the
second one through the same Etapa 2.8 auxiliary fusion point that
funding rate already uses -- real captured prices, hash-sealed and
independently re-verifiable, never synthesized.

Self-contained by the same convention every other provider module follows
(binance/okx/hyperliquid): it owns its own URL construction and parsing
rather than importing from research/, which would invert the data ->
research layering.
"""

import base64
import json
import math
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tramitago_quant_core.shared.util import digest, encoded, epoch, iso
from tramitago_quant_core.data.acquisition import _coinbase_public_request_headers

COINBASE_CLOSE_SERIES_CAPTURE_SCHEMA_VERSION = "1"
COINBASE_CLOSE_SERIES_SOURCE = "Coinbase Exchange public candles (pair leg)"
COINBASE_CLOSE_SERIES_MAX_CANDLES_PER_REQUEST = 300


def _coinbase_close_series_endpoint(instrument):
    return f"https://api.exchange.coinbase.com/products/{instrument}/candles"


def _coinbase_close_series_url(instrument, start_utc, end_exclusive_utc):
    # Coinbase's `end` candle bound is inclusive; the contract stays half-open,
    # so request the final included candle rather than its successor.
    endpoint_end = iso(epoch(end_exclusive_utc) - 86400)
    return _coinbase_close_series_endpoint(instrument) + "?" + urlencode({
        "granularity": 86400, "start": start_utc, "end": endpoint_end})


def _coinbase_close_series_windows(start, end):
    width = COINBASE_CLOSE_SERIES_MAX_CANDLES_PER_REQUEST * 86400
    return [(iso(left), iso(min(left + width, end))) for left in range(start, end, width)]


def _coinbase_close_series_live_get(url, headers, timeout_seconds):
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Response exceeds bounded capture size")
        return raw, {key: response.headers.get(key) for key in ("Date", "Content-Type")}


def _coinbase_close_series_response(transport, url):
    response = (transport or _coinbase_close_series_live_get)(
        url, _coinbase_public_request_headers(), 30)
    if isinstance(response, tuple) and len(response) == 2:
        raw, headers = response
    else:
        raw, headers = response, {}
    if not isinstance(raw, bytes) or not isinstance(headers, dict):
        raise ValueError("Coinbase close series transport returned an invalid response")
    return raw, headers


def _coinbase_close_series_parse(raw):
    """Coinbase candles are [time, low, high, open, close, volume]."""
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid Coinbase close series response: " + str(exc)) from exc
    if not isinstance(payload, list):
        raise ValueError("Malformed Coinbase close series response")
    rows = []
    for item in payload:
        if (not isinstance(item, list) or len(item) != 6
                or not isinstance(item[0], int)
                or not all(isinstance(value, (int, float)) for value in item[1:])):
            raise ValueError("Malformed Coinbase close series candle")
        close = float(item[4])
        if not math.isfinite(close) or close <= 0:
            raise ValueError("Invalid Coinbase close price")
        if item[0] % 86400:
            raise ValueError("Coinbase close series candle is not aligned to a daily boundary")
        rows.append({"timestamp": item[0], "close": close})
    return rows


def _coinbase_close_series_daily(rows, start, end):
    """One close per UTC day, failing closed unless every day is present --
    mirroring acquisition.py's "reject entire dataset; no imputation"."""
    by_day = {}
    for row in rows:
        if not start <= row["timestamp"] < end:
            continue
        day = iso(row["timestamp"])
        if day in by_day and by_day[day] != row["close"]:
            raise ValueError("Coinbase close series has conflicting duplicates")
        by_day[day] = row["close"]
    expected = {iso(value) for value in range(start, end, 86400)}
    if set(by_day) != expected:
        raise ValueError("Coinbase close series coverage is incomplete for the capture period")
    return by_day


def _coinbase_close_series_capture_content(instrument, capture_period, acquired_at, responses):
    content = {
        "schema_version": COINBASE_CLOSE_SERIES_CAPTURE_SCHEMA_VERSION,
        "kind": "coinbase-close-series-capture",
        "source": COINBASE_CLOSE_SERIES_SOURCE,
        "instrument": instrument,
        "capture_period": capture_period,
        "acquired_at": acquired_at,
        "responses": responses,
    }
    return {**content,
            "capture_id": "COINBASE_CLOSE_SERIES_CAPTURE|" + digest(encoded(content))}


def _coinbase_close_series_raw_content(captures):
    return {"schema_version": COINBASE_CLOSE_SERIES_CAPTURE_SCHEMA_VERSION,
            "captures": captures}


def capture_coinbase_close_series(instrument, start_utc, end_exclusive_utc, acquired_at, *,
                                  transport=None):
    """Capture and seal every response byte-for-byte, then reduce to one
    close per day. Windows are a pure function of the requested bounds
    (unlike the cursor-paginated funding providers), so verification
    recomputes them directly."""
    start, end = epoch(start_utc), epoch(end_exclusive_utc)
    responses, stored, rows = [], [], []
    for sequence, (window_start, window_end) in enumerate(
            _coinbase_close_series_windows(start, end), 1):
        url = _coinbase_close_series_url(instrument, window_start, window_end)
        response, headers = _coinbase_close_series_response(transport, url)
        response_sha256 = digest(response)
        responses.append({
            "sequence": sequence, "url": url, "start_utc": window_start,
            "end_exclusive_utc": window_end, "response_sha256": response_sha256,
            "response_headers": headers,
        })
        stored.append({
            "sequence": sequence, "response_sha256": response_sha256,
            "response_base64": base64.b64encode(response).decode("ascii"),
        })
        rows.extend(_coinbase_close_series_parse(response))
    capture_period = {"start_utc": start_utc, "end_exclusive_utc": end_exclusive_utc}
    capture = _coinbase_close_series_capture_content(
        instrument, capture_period, acquired_at, responses)
    raw = encoded(_coinbase_close_series_raw_content(stored))
    return _coinbase_close_series_daily(rows, start, end), capture, raw


def verified_coinbase_close_series_capture(raw_bytes, capture):
    """Independently reproduce the daily close series from stored raw bytes,
    never trusting the persisted metadata alone. Signature
    (raw_bytes, capture) -> series is the generic auxiliary verifier
    contract historical_dataset.py calls without knowing the provider."""
    if not isinstance(capture, dict):
        raise ValueError("Coinbase close series capture is invalid")
    responses = capture.get("responses")
    content = {key: capture[key] for key in (
        "schema_version", "kind", "source", "instrument", "capture_period", "acquired_at",
        "responses") if key in capture}
    if (set(capture) != set(content) | {"capture_id"}
            or capture.get("schema_version") != COINBASE_CLOSE_SERIES_CAPTURE_SCHEMA_VERSION
            or capture.get("kind") != "coinbase-close-series-capture"
            or capture.get("source") != COINBASE_CLOSE_SERIES_SOURCE
            or capture.get("capture_id")
            != "COINBASE_CLOSE_SERIES_CAPTURE|" + digest(encoded(content))
            or not isinstance(responses, list)):
        raise ValueError("Coinbase close series capture metadata is invalid")
    try:
        raw = json.loads(raw_bytes)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Coinbase close series raw capture is invalid") from error
    if (not isinstance(raw, dict) or set(raw) != {"schema_version", "captures"}
            or raw.get("schema_version") != COINBASE_CLOSE_SERIES_CAPTURE_SCHEMA_VERSION
            or not isinstance(raw.get("captures"), list)
            or len(raw["captures"]) != len(responses)):
        raise ValueError("Coinbase close series raw capture is incomplete")
    capture_period = capture["capture_period"]
    start, end = epoch(capture_period["start_utc"]), epoch(capture_period["end_exclusive_utc"])
    expected_windows = _coinbase_close_series_windows(start, end)
    if len(responses) != len(expected_windows):
        raise ValueError("Coinbase close series capture has an incompatible request count")
    rows = []
    for number, ((window_start, window_end), metadata, stored) in enumerate(
            zip(expected_windows, responses, raw["captures"]), 1):
        fields = {"sequence", "url", "start_utc", "end_exclusive_utc", "response_sha256",
                  "response_headers"}
        raw_fields = {"sequence", "response_sha256", "response_base64"}
        if (not isinstance(metadata, dict) or set(metadata) != fields
                or not isinstance(stored, dict) or set(stored) != raw_fields
                or metadata["sequence"] != number or stored["sequence"] != number
                or metadata["start_utc"] != window_start
                or metadata["end_exclusive_utc"] != window_end
                or metadata["url"] != _coinbase_close_series_url(
                    capture["instrument"], window_start, window_end)
                or metadata["response_sha256"] != stored["response_sha256"]
                or not isinstance(metadata["response_headers"], dict)):
            raise ValueError("Coinbase close series capture request identity is invalid")
        try:
            response = base64.b64decode(stored["response_base64"].encode("ascii"), validate=True)
        except (ValueError, UnicodeError) as error:
            raise ValueError("Coinbase close series capture response is invalid") from error
        if digest(response) != metadata["response_sha256"]:
            raise ValueError("Coinbase close series capture response integrity is invalid")
        rows.extend(_coinbase_close_series_parse(response))
    return _coinbase_close_series_daily(rows, start, end)
