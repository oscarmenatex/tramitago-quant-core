"""CAP-001 Data extension (Etapa 2.8, M2.8-T2): Binance USDT-margined
futures public funding-rate history -- the first auxiliary data source
merged into a Hypothesis Dataset beyond Coinbase's own OHLCV candles.

Mirrors acquisition.py/historical_dataset.py's capture contract (every raw
response stored and hash-sealed, independently re-verifiable without live
network access) so the same rigor applies regardless of provider. This
module is the ONLY place that knows Binance's response shape -- the
historical_dataset.py core stays provider-agnostic (DOC-005 PA-005-002:
independencia de proveedor de datos; SS13: riesgo de "dependencia de un
unico proveedor"), receiving only a reduced daily series plus this
module's own sealed capture bytes.
"""

import base64
import json
import math
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tramitago_quant_core.shared.util import digest, encoded, epoch, iso

BINANCE_FUNDING_RATE_CAPTURE_SCHEMA_VERSION = "1"
BINANCE_FUNDING_RATE_SOURCE = "Binance USDT-M Futures public funding rate"
BINANCE_FUNDING_RATE_MAX_RECORDS_PER_REQUEST = 1000
BINANCE_FUNDING_RATE_INTERVAL_SECONDS = 28800
BINANCE_FUNDING_RATE_REQUEST_HEADERS = {
    "User-Agent": "TramitaGO-Quant-Core-Etapa2.8/0.1",
    "Accept": "application/json",
}


def _binance_funding_rate_headers():
    return dict(BINANCE_FUNDING_RATE_REQUEST_HEADERS)


def _binance_funding_rate_endpoint():
    return "https://fapi.binance.com/fapi/v1/fundingRate"


def _binance_funding_rate_url(symbol, start_utc, end_exclusive_utc):
    return _binance_funding_rate_endpoint() + "?" + urlencode({
        "symbol": symbol,
        "startTime": epoch(start_utc) * 1000,
        "endTime": epoch(end_exclusive_utc) * 1000 - 1,
        "limit": BINANCE_FUNDING_RATE_MAX_RECORDS_PER_REQUEST,
    })


def _binance_funding_rate_windows(start, end):
    width = BINANCE_FUNDING_RATE_MAX_RECORDS_PER_REQUEST * BINANCE_FUNDING_RATE_INTERVAL_SECONDS
    return [(iso(left), iso(min(left + width, end))) for left in range(start, end, width)]


def _binance_funding_rate_live_get(url, headers, timeout_seconds):
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Response exceeds bounded capture size")
        return raw, {key: response.headers.get(key) for key in ("Date", "Content-Type")}


def _binance_funding_rate_response(transport, url):
    response = (transport or _binance_funding_rate_live_get)(
        url, _binance_funding_rate_headers(), 30)
    if isinstance(response, tuple) and len(response) == 2:
        raw, headers = response
    else:
        raw, headers = response, {}
    if not isinstance(raw, bytes) or not isinstance(headers, dict):
        raise ValueError("Binance funding rate transport returned an invalid response")
    return raw, headers


def _binance_funding_rate_parse(raw):
    try:
        payload = json.loads(raw)
        if not isinstance(payload, list):
            raise ValueError("Expected a funding rate array")
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid Binance funding rate response: " + str(exc)) from exc
    events = []
    for item in payload:
        if (not isinstance(item, dict)
                or not isinstance(item.get("fundingTime"), int)
                or not isinstance(item.get("fundingRate"), str)):
            raise ValueError("Malformed Binance funding rate record")
        try:
            rate = float(item["fundingRate"])
        except ValueError as exc:
            raise ValueError("Non-numeric Binance funding rate") from exc
        if not math.isfinite(rate):
            raise ValueError("Non-finite Binance funding rate")
        if item["fundingTime"] % 1000:
            raise ValueError("Binance funding rate timestamp is not whole seconds")
        events.append({"timestamp": item["fundingTime"] // 1000, "funding_rate": rate})
    return events


def _binance_funding_rate_daily_series(events, start, end):
    """Reduce sub-daily funding events (every ~8h) into one daily mean per
    UTC day -- fails closed unless every day in [start, end) has at least
    one event, mirroring acquisition.py's "reject entire dataset; no
    imputation" policy."""
    by_day = {}
    for event in events:
        if not start <= event["timestamp"] < end:
            continue
        day = iso((event["timestamp"] // 86400) * 86400)
        by_day.setdefault(day, []).append(event["funding_rate"])
    expected_days = {iso(value) for value in range(start, end, 86400)}
    if set(by_day) != expected_days:
        raise ValueError("Binance funding rate coverage is incomplete for the capture period")
    return {day: math.fsum(values) / len(values) for day, values in by_day.items()}


def _binance_funding_rate_capture_content(symbol, capture_period, acquired_at, responses):
    content = {
        "schema_version": BINANCE_FUNDING_RATE_CAPTURE_SCHEMA_VERSION,
        "kind": "binance-funding-rate-capture",
        "source": BINANCE_FUNDING_RATE_SOURCE,
        "symbol": symbol,
        "capture_period": capture_period,
        "acquired_at": acquired_at,
        "responses": responses,
    }
    return {**content, "capture_id": "BINANCE_FUNDING_RATE_CAPTURE|" + digest(encoded(content))}


def _binance_funding_rate_raw_content(captures):
    return {"schema_version": BINANCE_FUNDING_RATE_CAPTURE_SCHEMA_VERSION, "captures": captures}


def capture_binance_funding_rate(symbol, start_utc, end_exclusive_utc, acquired_at, *,
                                 transport=None):
    """Capture and seal every funding-rate response byte-for-byte, then
    reduce to a daily mean series. Mirrors historical_dataset.py's
    Coinbase capture: raw responses are stored so verification never needs
    live network access again -- see verified_binance_funding_rate_capture.
    """
    start = epoch(start_utc)
    end = epoch(end_exclusive_utc)
    responses, stored, all_events = [], [], []
    for sequence, (window_start, window_end) in enumerate(
            _binance_funding_rate_windows(start, end), 1):
        url = _binance_funding_rate_url(symbol, window_start, window_end)
        response, headers = _binance_funding_rate_response(transport, url)
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
        all_events.extend(_binance_funding_rate_parse(response))
    capture_period = {"start_utc": start_utc, "end_exclusive_utc": end_exclusive_utc}
    capture = _binance_funding_rate_capture_content(symbol, capture_period, acquired_at, responses)
    raw = encoded(_binance_funding_rate_raw_content(stored))
    daily_series = _binance_funding_rate_daily_series(all_events, start, end)
    return daily_series, capture, raw


def verified_binance_funding_rate_capture(raw_bytes, capture):
    """Independently reproduce the daily series from stored raw bytes,
    never trusting the persisted capture metadata alone. Signature
    (raw_bytes, capture) -> daily_series is the generic "auxiliary
    verifier" contract historical_dataset.py calls without knowing it is
    Binance-specific."""
    if not isinstance(capture, dict):
        raise ValueError("Binance funding rate capture is invalid")
    responses = capture.get("responses")
    content = {key: capture[key] for key in (
        "schema_version", "kind", "source", "symbol", "capture_period", "acquired_at", "responses")
        if key in capture}
    if (set(capture) != set(content) | {"capture_id"}
            or capture.get("schema_version") != BINANCE_FUNDING_RATE_CAPTURE_SCHEMA_VERSION
            or capture.get("kind") != "binance-funding-rate-capture"
            or capture.get("source") != BINANCE_FUNDING_RATE_SOURCE
            or capture.get("capture_id") != "BINANCE_FUNDING_RATE_CAPTURE|" + digest(encoded(content))
            or not isinstance(responses, list)):
        raise ValueError("Binance funding rate capture metadata is invalid")
    try:
        raw = json.loads(raw_bytes)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Binance funding rate raw capture is invalid") from error
    if (not isinstance(raw, dict) or set(raw) != {"schema_version", "captures"}
            or raw.get("schema_version") != BINANCE_FUNDING_RATE_CAPTURE_SCHEMA_VERSION
            or not isinstance(raw.get("captures"), list)
            or len(raw["captures"]) != len(responses)):
        raise ValueError("Binance funding rate raw capture is incomplete")
    capture_period = capture["capture_period"]
    expected_windows = _binance_funding_rate_windows(
        epoch(capture_period["start_utc"]), epoch(capture_period["end_exclusive_utc"]))
    if len(responses) != len(expected_windows):
        raise ValueError("Binance funding rate capture has an incompatible request count")
    all_events = []
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
                or metadata["url"] != _binance_funding_rate_url(
                    capture["symbol"], window_start, window_end)
                or metadata["response_sha256"] != stored["response_sha256"]
                or not isinstance(metadata["response_headers"], dict)):
            raise ValueError("Binance funding rate capture request identity is invalid")
        try:
            response = base64.b64decode(stored["response_base64"].encode("ascii"), validate=True)
        except (ValueError, UnicodeError) as error:
            raise ValueError("Binance funding rate capture response is invalid") from error
        if digest(response) != metadata["response_sha256"]:
            raise ValueError("Binance funding rate capture response integrity is invalid")
        all_events.extend(_binance_funding_rate_parse(response))
    start = epoch(capture_period["start_utc"])
    end = epoch(capture_period["end_exclusive_utc"])
    return _binance_funding_rate_daily_series(all_events, start, end)
