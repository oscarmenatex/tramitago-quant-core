"""CAP-001 Data extension (Etapa 2.8, M2.8-T2, provider substitution
2026-09-29): OKX public perpetual-swap funding-rate history -- the
auxiliary data source actually reachable for the FUNDING_RATE_SIGN
Hypothesis. Binance's own history has the depth (a full year) but is
geoblocked from every network tried (sandbox and an Oracle Cloud VM both
got HTTP 451); OKX's public history endpoint is reachable but only retains
roughly the last three months, so the real Hypothesis built on top of this
module necessarily uses that shorter window instead of the full-year
period every prior real Hypothesis used -- an explicit, documented
methodology deviation, not a silent one.

Mirrors binance_funding_rate.py's sealed-capture contract (every raw
response stored and hash-sealed, independently re-verifiable without live
network access) so the same rigor applies regardless of provider. This
module is the ONLY place that knows OKX's response shape and its
cursor-based (not fixed-window) pagination -- historical_dataset.py stays
provider-agnostic (DOC-005 PA-005-002; SS13: riesgo de "dependencia de un
unico proveedor").
"""

import base64
import json
import math
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tramitago_quant_core.shared.util import digest, encoded, epoch, iso

OKX_FUNDING_RATE_CAPTURE_SCHEMA_VERSION = "1"
OKX_FUNDING_RATE_SOURCE = "OKX public funding rate history"
OKX_FUNDING_RATE_MAX_RECORDS_PER_REQUEST = 100
OKX_FUNDING_RATE_INTERVAL_SECONDS = 28800
OKX_FUNDING_RATE_REQUEST_HEADERS = {
    "User-Agent": "TramitaGO-Quant-Core-Etapa2.8/0.1",
    "Accept": "application/json",
}
_OKX_FUNDING_RATE_ITEM_FIELDS = {
    "formulaType", "fundingRate", "fundingTime", "instId", "instType", "method", "realizedRate",
}


def _okx_funding_rate_headers():
    return dict(OKX_FUNDING_RATE_REQUEST_HEADERS)


def _okx_funding_rate_endpoint():
    return "https://www.okx.com/api/v5/public/funding-rate-history"


def _okx_funding_rate_url(inst_id, after_ms):
    return _okx_funding_rate_endpoint() + "?" + urlencode({
        "instId": inst_id, "limit": OKX_FUNDING_RATE_MAX_RECORDS_PER_REQUEST, "after": after_ms,
    })


def _okx_funding_rate_live_get(url, headers, timeout_seconds):
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Response exceeds bounded capture size")
        return raw, {key: response.headers.get(key) for key in ("Date", "Content-Type")}


def _okx_funding_rate_response(transport, url):
    response = (transport or _okx_funding_rate_live_get)(url, _okx_funding_rate_headers(), 30)
    if isinstance(response, tuple) and len(response) == 2:
        raw, headers = response
    else:
        raw, headers = response, {}
    if not isinstance(raw, bytes) or not isinstance(headers, dict):
        raise ValueError("OKX funding rate transport returned an invalid response")
    return raw, headers


def _okx_funding_rate_parse(raw, inst_id):
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid OKX funding rate response: " + str(exc)) from exc
    if (not isinstance(payload, dict) or set(payload) != {"code", "data", "msg"}
            or payload.get("code") != "0" or not isinstance(payload.get("data"), list)):
        raise ValueError("Malformed OKX funding rate response")
    events = []
    for item in payload["data"]:
        if (not isinstance(item, dict) or set(item) != _OKX_FUNDING_RATE_ITEM_FIELDS
                or item.get("instId") != inst_id
                or not isinstance(item.get("fundingTime"), str)
                or not isinstance(item.get("fundingRate"), str)):
            raise ValueError("Malformed OKX funding rate record")
        try:
            funding_time_ms = int(item["fundingTime"])
            rate = float(item["fundingRate"])
        except ValueError as exc:
            raise ValueError("Non-numeric OKX funding rate record") from exc
        if not math.isfinite(rate):
            raise ValueError("Non-finite OKX funding rate")
        if funding_time_ms % 1000:
            raise ValueError("OKX funding rate timestamp is not whole seconds")
        events.append({"timestamp": funding_time_ms // 1000, "funding_rate": rate})
    return events


def _okx_funding_rate_daily_series(events, start, end):
    """Reduce sub-daily funding events (every ~8h) into one daily mean per
    UTC day -- fails closed unless every day in [start, end) has at least
    one event, mirroring acquisition.py's "reject entire dataset; no
    imputation" policy."""
    by_day = {}
    for event in events:
        if not start <= event["timestamp"] < end:
            continue
        day_start = (event["timestamp"] // 86400) * 86400
        by_day.setdefault(day_start, []).append(event["funding_rate"])
    expected_days = {iso(value) for value in range(start, end, 86400)}
    by_day_iso = {iso(day): values for day, values in by_day.items()}
    if set(by_day_iso) != expected_days:
        raise ValueError("OKX funding rate coverage is incomplete for the capture period")
    return {day: math.fsum(values) / len(values) for day, values in by_day_iso.items()}


def _okx_funding_rate_capture_content(inst_id, capture_period, acquired_at, responses):
    content = {
        "schema_version": OKX_FUNDING_RATE_CAPTURE_SCHEMA_VERSION,
        "kind": "okx-funding-rate-capture",
        "source": OKX_FUNDING_RATE_SOURCE,
        "inst_id": inst_id,
        "capture_period": capture_period,
        "acquired_at": acquired_at,
        "responses": responses,
    }
    return {**content, "capture_id": "OKX_FUNDING_RATE_CAPTURE|" + digest(encoded(content))}


def _okx_funding_rate_raw_content(captures):
    return {"schema_version": OKX_FUNDING_RATE_CAPTURE_SCHEMA_VERSION, "captures": captures}


def capture_okx_funding_rate(inst_id, start_utc, end_exclusive_utc, acquired_at, *, transport=None):
    """Capture and seal every funding-rate response byte-for-byte, then
    reduce to a daily mean series. OKX's public history endpoint paginates
    backward via an "after" cursor (records strictly older than the given
    millisecond timestamp) rather than Binance's fixed time windows, so
    each subsequent request's cursor is the oldest record's own funding
    time from the PRECEDING response -- content-dependent, not a pure
    function of the requested bounds. verified_okx_funding_rate_capture
    reproduces this exact chain from the sealed raw bytes alone.
    """
    start = epoch(start_utc)
    end = epoch(end_exclusive_utc)
    responses, stored, all_events = [], [], []
    cursor_ms = end * 1000
    sequence = 0
    while True:
        sequence += 1
        url = _okx_funding_rate_url(inst_id, cursor_ms)
        response, headers = _okx_funding_rate_response(transport, url)
        response_sha256 = digest(response)
        responses.append({
            "sequence": sequence, "url": url, "after_ms": cursor_ms,
            "response_sha256": response_sha256, "response_headers": headers,
        })
        stored.append({
            "sequence": sequence, "response_sha256": response_sha256,
            "response_base64": base64.b64encode(response).decode("ascii"),
        })
        events = _okx_funding_rate_parse(response, inst_id)
        if not events:
            raise ValueError("OKX funding rate history has insufficient retention for the requested period")
        all_events.extend(events)
        oldest = min(event["timestamp"] for event in events)
        if oldest <= start:
            break
        cursor_ms = oldest * 1000
    capture_period = {"start_utc": start_utc, "end_exclusive_utc": end_exclusive_utc}
    capture = _okx_funding_rate_capture_content(inst_id, capture_period, acquired_at, responses)
    raw = encoded(_okx_funding_rate_raw_content(stored))
    daily_series = _okx_funding_rate_daily_series(all_events, start, end)
    return daily_series, capture, raw


def verified_okx_funding_rate_capture(raw_bytes, capture):
    """Independently reproduce the daily series from stored raw bytes,
    never trusting the persisted capture metadata alone: each page's
    "after" cursor is recomputed from the PRECEDING page's own parsed
    content, and the pagination's stopping point is re-derived rather than
    trusted. Signature (raw_bytes, capture) -> daily_series is the generic
    "auxiliary verifier" contract historical_dataset.py calls without
    knowing it is OKX-specific."""
    if not isinstance(capture, dict):
        raise ValueError("OKX funding rate capture is invalid")
    responses = capture.get("responses")
    content = {key: capture[key] for key in (
        "schema_version", "kind", "source", "inst_id", "capture_period", "acquired_at", "responses")
        if key in capture}
    if (set(capture) != set(content) | {"capture_id"}
            or capture.get("schema_version") != OKX_FUNDING_RATE_CAPTURE_SCHEMA_VERSION
            or capture.get("kind") != "okx-funding-rate-capture"
            or capture.get("source") != OKX_FUNDING_RATE_SOURCE
            or capture.get("capture_id") != "OKX_FUNDING_RATE_CAPTURE|" + digest(encoded(content))
            or not isinstance(responses, list)):
        raise ValueError("OKX funding rate capture metadata is invalid")
    try:
        raw = json.loads(raw_bytes)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("OKX funding rate raw capture is invalid") from error
    if (not isinstance(raw, dict) or set(raw) != {"schema_version", "captures"}
            or raw.get("schema_version") != OKX_FUNDING_RATE_CAPTURE_SCHEMA_VERSION
            or not isinstance(raw.get("captures"), list)
            or len(raw["captures"]) != len(responses)):
        raise ValueError("OKX funding rate raw capture is incomplete")
    capture_period = capture["capture_period"]
    start = epoch(capture_period["start_utc"])
    end = epoch(capture_period["end_exclusive_utc"])
    inst_id = capture["inst_id"]
    all_events = []
    cursor_ms = end * 1000
    for number, (metadata, stored) in enumerate(zip(responses, raw["captures"]), 1):
        fields = {"sequence", "url", "after_ms", "response_sha256", "response_headers"}
        raw_fields = {"sequence", "response_sha256", "response_base64"}
        expected_url = _okx_funding_rate_url(inst_id, cursor_ms)
        if (not isinstance(metadata, dict) or set(metadata) != fields
                or not isinstance(stored, dict) or set(stored) != raw_fields
                or metadata["sequence"] != number or stored["sequence"] != number
                or metadata["after_ms"] != cursor_ms
                or metadata["url"] != expected_url
                or metadata["response_sha256"] != stored["response_sha256"]
                or not isinstance(metadata["response_headers"], dict)):
            raise ValueError("OKX funding rate capture request identity is invalid")
        try:
            response = base64.b64decode(stored["response_base64"].encode("ascii"), validate=True)
        except (ValueError, UnicodeError) as error:
            raise ValueError("OKX funding rate capture response is invalid") from error
        if digest(response) != metadata["response_sha256"]:
            raise ValueError("OKX funding rate capture response integrity is invalid")
        events = _okx_funding_rate_parse(response, inst_id)
        if not events:
            raise ValueError("OKX funding rate capture response is empty")
        all_events.extend(events)
        oldest = min(event["timestamp"] for event in events)
        is_last = oldest <= start
        if is_last != (number == len(responses)):
            raise ValueError("OKX funding rate capture pagination is invalid")
        cursor_ms = oldest * 1000
    return _okx_funding_rate_daily_series(all_events, start, end)
