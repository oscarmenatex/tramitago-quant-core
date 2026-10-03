"""CAP-001 Data extension (Nivel 5, 2026-09-30): daily equity OHLCV bars from
the Alpaca Data API v2.  Provides the PRIMARY data source for equity hypotheses
tested through the same M2.x research pipeline that crypto hypotheses use.

The module captures both the trading-day calendar (Alpaca Calendar API on
paper-api.alpaca.markets) and the OHLCV bars (Alpaca Data API on
data.alpaca.markets), seals all raw responses byte-for-byte, and verifies
coverage against the sealed calendar independently -- the same "fail closed,
no imputation" policy acquisition.py applies to Coinbase.

Authentication: same APCA-API-KEY-ID / APCA-API-SECRET-KEY injector as the
trading API.  The caller passes `credential_injector` (a callable that adds
auth headers), keeping secrets out of this module exactly as pipeline.py does
for PAPER/LIVE credentials.

Self-contained by the same convention as all other provider modules
(coinbase_close_series.py, hyperliquid_funding_rate.py, etc.).
"""

import base64
import json
import math
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tramitago_quant_core.shared.util import digest, encoded, epoch, iso

ALPACA_EQUITY_BARS_SCHEMA_VERSION = "1"
ALPACA_EQUITY_BARS_CAPTURE_KIND = "alpaca-equity-bars"
ALPACA_EQUITY_BARS_SOURCE = "Alpaca Data API v2 (daily equity bars, split-adjusted)"
ALPACA_CALENDAR_HOST = "paper-api.alpaca.markets"
ALPACA_DATA_HOST = "data.alpaca.markets"
# The free tier serves IEX, a few percent of consolidated volume. MEASURED on
# SPY 2026-10-02: over 2018-01-02..2026-10-01, IEX is missing 644 of 2198 NYSE
# sessions and its earliest bar is 2018-11-01, while SIP -- the consolidated
# tape -- covers all 2198 from 2016-01-04. The default stays IEX so every
# already-sealed capture reproduces byte for byte; the feed is carried into the
# sealed capture_id anyway, because the request URL is part of the capture's
# own content.
ALPACA_FEED_IEX = "iex"
ALPACA_FEED_SIP = "sip"
ALPACA_FEEDS = (ALPACA_FEED_IEX, ALPACA_FEED_SIP)
ALPACA_DEFAULT_FEED = ALPACA_FEED_IEX
ALPACA_BARS_MAX_LIMIT = 10000


# ── low-level HTTP ─────────────────────────────────────────────────────────────

def _alpaca_live_get(url, headers, timeout_seconds):
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read(10_000_001)
        if len(raw) > 10_000_000:
            raise ValueError("Response exceeds bounded capture size")
        return raw, {key: response.headers.get(key)
                     for key in ("Date", "Content-Type")}


def _alpaca_get(url, credential_injector, transport, timeout_seconds=30):
    get = transport or _alpaca_live_get
    result = get(url, credential_injector({"Accept": "application/json"}), timeout_seconds)
    if isinstance(result, tuple) and len(result) == 2:
        raw, headers = result
    else:
        raw, headers = result, {}
    if not isinstance(raw, bytes) or not isinstance(headers, dict):
        raise ValueError("Alpaca transport returned an invalid response")
    return raw, headers


# ── calendar API ───────────────────────────────────────────────────────────────

def _alpaca_calendar_url(start_date_str, end_date_str):
    return (f"https://{ALPACA_CALENDAR_HOST}/v2/calendar?"
            + urlencode({"start": start_date_str, "end": end_date_str}))


def _alpaca_calendar_parse(raw):
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid Alpaca calendar response: " + str(exc)) from exc
    if not isinstance(payload, list):
        raise ValueError("Malformed Alpaca calendar response")
    dates = []
    for item in payload:
        if not isinstance(item, dict) or "date" not in item:
            raise ValueError("Malformed Alpaca calendar entry")
        d = item["date"]
        try:
            datetime.strptime(d, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("Invalid Alpaca calendar date: " + d) from exc
        dates.append(d)
    return sorted(set(dates))  # sorted ISO date strings, deduplicated


def _dates_in_range(trading_dates, start_utc, end_exclusive_utc):
    """Filter trading dates to those within [start_utc, end_exclusive_utc)."""
    start_date = start_utc[:10]
    end_date = end_exclusive_utc[:10]
    return [d for d in trading_dates if start_date <= d < end_date]


# ── bars API ───────────────────────────────────────────────────────────────────

def _alpaca_bars_url(symbol, start_date, end_date, page_token=None,
                     feed=ALPACA_DEFAULT_FEED):
    if feed not in ALPACA_FEEDS:
        # Validated rather than passed through, because an unrecognised feed is
        # the dangerous case: the API may ignore the parameter and serve its
        # default, and the capture would then record SIP in nobody's mind while
        # holding IEX bars. A typo must fail here, not silently downgrade.
        raise ValueError(f"Unknown Alpaca feed {feed!r}; expected one of "
                         + ", ".join(ALPACA_FEEDS))
    params = {
        "timeframe": "1Day",
        "start": start_date,
        "end": end_date,
        "adjustment": "all",
        "feed": feed,
        "limit": ALPACA_BARS_MAX_LIMIT,
    }
    if page_token:
        params["page_token"] = page_token
    return f"https://{ALPACA_DATA_HOST}/v2/stocks/{symbol}/bars?" + urlencode(params)


def _alpaca_bars_parse(raw, symbol):
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid Alpaca bars response: " + str(exc)) from exc
    if not isinstance(payload, dict) or "bars" not in payload:
        raise ValueError("Malformed Alpaca bars response")
    bars = payload.get("bars") or []
    if not isinstance(bars, list):
        raise ValueError("Malformed Alpaca bars list")
    rows = []
    for item in bars:
        if not isinstance(item, dict) or "t" not in item:
            raise ValueError("Malformed Alpaca bar entry")
        try:
            dt = datetime.fromisoformat(item["t"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Invalid Alpaca bar timestamp: " + str(exc)) from exc
        normalized_ts = dt.strftime("%Y-%m-%dT00:00:00Z")
        for field in ("o", "h", "l", "c", "v"):
            val = item.get(field)
            if not isinstance(val, (int, float)) or not math.isfinite(float(val)):
                raise ValueError(f"Invalid Alpaca bar field {field}")
        close = float(item["c"])
        if close <= 0:
            raise ValueError("Non-positive Alpaca bar close")
        rows.append({
            "instrument": symbol,
            "timestamp": normalized_ts,
            "open": float(item["o"]),
            "high": float(item["h"]),
            "low": float(item["l"]),
            "close": close,
            "volume": float(item["v"]),
        })
    return rows, payload.get("next_page_token")


def _alpaca_bars_coverage(rows, trading_dates_in_period):
    """Verify every expected trading day has exactly one bar and vice versa."""
    by_day = {}
    for row in rows:
        day = row["timestamp"][:10]
        if day in by_day:
            raise ValueError("Alpaca bars have duplicate trading day: " + day)
        by_day[day] = row
    expected = set(trading_dates_in_period)
    actual = set(by_day)
    if expected != actual:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(
            f"Alpaca bars coverage mismatch: missing={missing[:5]}, extra={extra[:5]}")
    return [by_day[d] for d in sorted(by_day)]


# ── capture / verify ───────────────────────────────────────────────────────────

def _alpaca_equity_capture_content(symbol, capture_period, trading_dates, acquired_at,
                                   calendar_response_meta, bar_response_metas):
    content = {
        "schema_version": ALPACA_EQUITY_BARS_SCHEMA_VERSION,
        "kind": ALPACA_EQUITY_BARS_CAPTURE_KIND,
        "source": ALPACA_EQUITY_BARS_SOURCE,
        "symbol": symbol,
        "capture_period": capture_period,
        "trading_dates": trading_dates,
        "acquired_at": acquired_at,
        "calendar_response": calendar_response_meta,
        "bar_responses": bar_response_metas,
    }
    return {**content,
            "capture_id": "ALPACA_EQUITY_BARS_CAPTURE|" + digest(encoded(content))}


def _alpaca_equity_raw_content(calendar_stored, bar_stored):
    return {
        "schema_version": ALPACA_EQUITY_BARS_SCHEMA_VERSION,
        "calendar_raw": calendar_stored,
        "bar_raws": bar_stored,
    }


def capture_alpaca_equity_bars(symbol, evaluable_start_utc, evaluable_end_exclusive_utc,
                               warmup_periods, horizon, acquired_at, *,
                               credential_injector, transport=None,
                               feed=ALPACA_DEFAULT_FEED):
    """Capture and seal equity OHLCV bars from Alpaca, with calendar verification.

    Arguments
    ---------
    symbol               : Alpaca symbol, e.g. "SPY"
    evaluable_start_utc  : hypothesis evaluable period start (midnight UTC ISO8601)
    evaluable_end_exclusive_utc : hypothesis evaluable period end (exclusive, midnight UTC)
    warmup_periods       : number of TRADING DAYS of warmup before evaluable_start
    horizon              : number of TRADING DAYS of forward return after last evaluation row
    acquired_at          : ISO8601 UTC timestamp of acquisition
    credential_injector  : callable(headers_dict) -> headers_dict_with_auth

    Returns
    -------
    (rows, capture, raw)
      rows    : list of {instrument, timestamp, open, high, low, close, volume}
                sorted by timestamp (midnight UTC), trading days only
      capture : sealed capture dict (provider metadata, sha256s, capture_id)
      raw     : sealed raw bytes (JSON with base64-encoded responses)
    """
    evaluable_start_date = evaluable_start_utc[:10]
    evaluable_end_date = evaluable_end_exclusive_utc[:10]

    # Fetch calendar over a wide window (400 days before start, 30 after end) to
    # have enough trading days for warmup lookup without an extra round-trip.
    wide_start = (date.fromisoformat(evaluable_start_date) - timedelta(days=400)).isoformat()
    wide_end = (date.fromisoformat(evaluable_end_date) + timedelta(days=30)).isoformat()
    cal_url = _alpaca_calendar_url(wide_start, wide_end)
    cal_raw, cal_headers = _alpaca_get(cal_url, credential_injector, transport)
    all_trading_dates = _alpaca_calendar_parse(cal_raw)

    # Determine capture_period: warmup_periods trading days before evaluable_start,
    # horizon trading days at/after evaluable_end_exclusive.
    before_start = [d for d in all_trading_dates if d < evaluable_start_date]
    if len(before_start) < warmup_periods:
        raise ValueError(
            f"Not enough trading days in calendar for warmup "
            f"(need {warmup_periods}, found {len(before_start)})")
    capture_start_date = before_start[-warmup_periods]

    at_or_after_end = [d for d in all_trading_dates if d >= evaluable_end_date]
    if len(at_or_after_end) < horizon:
        raise ValueError(
            f"Not enough trading days in calendar for forward horizon "
            f"(need {horizon}, found {len(at_or_after_end)})")
    # The last evaluation row needs to look `horizon` bars forward in the sequence.
    # The last evaluation day is the last trading day strictly before evaluable_end.
    # We need `horizon` trading days from the first day AT OR AFTER evaluable_end.
    capture_end_date_inclusive = at_or_after_end[horizon - 1]  # horizon-th bar
    capture_end_exclusive_date = (
        date.fromisoformat(capture_end_date_inclusive) + timedelta(days=1)).isoformat()
    capture_period = {
        "start_utc": capture_start_date + "T00:00:00Z",
        "end_exclusive_utc": capture_end_exclusive_date + "T00:00:00Z",
    }
    trading_dates_in_capture = _dates_in_range(
        all_trading_dates, capture_period["start_utc"], capture_period["end_exclusive_utc"])

    # Fetch bars (paginated).
    bar_response_metas, bar_stored = [], []
    all_rows = []
    sequence, page_token = 1, None
    while True:
        url = _alpaca_bars_url(symbol, capture_start_date, capture_end_date_inclusive,
                               page_token, feed)
        bar_raw, bar_headers = _alpaca_get(url, credential_injector, transport)
        bar_sha = digest(bar_raw)
        bar_response_metas.append({
            "sequence": sequence, "url": url,
            "response_sha256": bar_sha, "response_headers": bar_headers,
        })
        bar_stored.append({
            "sequence": sequence, "response_sha256": bar_sha,
            "response_base64": base64.b64encode(bar_raw).decode("ascii"),
        })
        rows_page, next_token = _alpaca_bars_parse(bar_raw, symbol)
        all_rows.extend(rows_page)
        if not next_token:
            break
        page_token = next_token
        sequence += 1

    rows = _alpaca_bars_coverage(all_rows, trading_dates_in_capture)

    cal_sha = digest(cal_raw)
    calendar_response_meta = {"url": cal_url, "response_sha256": cal_sha,
                               "response_headers": cal_headers}
    calendar_stored = {"response_sha256": cal_sha,
                       "response_base64": base64.b64encode(cal_raw).decode("ascii")}

    capture = _alpaca_equity_capture_content(
        symbol, capture_period, trading_dates_in_capture,
        acquired_at, calendar_response_meta, bar_response_metas)
    raw = encoded(_alpaca_equity_raw_content(calendar_stored, bar_stored))
    return rows, capture, raw.encode("utf-8") if isinstance(raw, str) else raw


def verified_alpaca_equity_bars_capture(raw_bytes, capture):
    """Independently reproduce the OHLCV row list from sealed raw bytes.

    Signature (raw_bytes, capture) -> rows_list is the generic PRIMARY verifier
    contract.  Verifies calendar and bar integrity independently, then checks
    bar coverage against the sealed trading_dates.
    """
    if not isinstance(capture, dict):
        raise ValueError("Alpaca equity bars capture is invalid")
    content_keys = (
        "schema_version", "kind", "source", "symbol", "capture_period",
        "trading_dates", "acquired_at", "calendar_response", "bar_responses",
    )
    content = {key: capture[key] for key in content_keys if key in capture}
    if (set(capture) != set(content) | {"capture_id"}
            or capture.get("schema_version") != ALPACA_EQUITY_BARS_SCHEMA_VERSION
            or capture.get("kind") != ALPACA_EQUITY_BARS_CAPTURE_KIND
            or capture.get("source") != ALPACA_EQUITY_BARS_SOURCE
            or capture.get("capture_id")
            != "ALPACA_EQUITY_BARS_CAPTURE|" + digest(encoded(content))
            or not isinstance(capture.get("bar_responses"), list)
            or not isinstance(capture.get("trading_dates"), list)):
        raise ValueError("Alpaca equity bars capture metadata is invalid")
    try:
        raw = json.loads(raw_bytes)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Alpaca equity bars raw capture is invalid") from error
    bar_responses = capture["bar_responses"]
    if (not isinstance(raw, dict)
            or set(raw) != {"schema_version", "calendar_raw", "bar_raws"}
            or raw.get("schema_version") != ALPACA_EQUITY_BARS_SCHEMA_VERSION
            or not isinstance(raw.get("bar_raws"), list)
            or len(raw["bar_raws"]) != len(bar_responses)):
        raise ValueError("Alpaca equity bars raw capture is incomplete")

    # Verify calendar integrity.
    cal_meta = capture["calendar_response"]
    cal_stored = raw["calendar_raw"]
    if (not isinstance(cal_meta, dict) or not isinstance(cal_stored, dict)
            or "response_sha256" not in cal_meta or "response_sha256" not in cal_stored
            or cal_meta["response_sha256"] != cal_stored["response_sha256"]):
        raise ValueError("Alpaca equity bars calendar integrity is invalid")
    try:
        cal_raw = base64.b64decode(cal_stored["response_base64"].encode("ascii"), validate=True)
    except (ValueError, UnicodeError) as error:
        raise ValueError("Alpaca equity bars calendar response is invalid") from error
    if digest(cal_raw) != cal_meta["response_sha256"]:
        raise ValueError("Alpaca equity bars calendar response hash is invalid")
    _alpaca_calendar_parse(cal_raw)  # structural check only in verifier

    # Verify bar integrity and rebuild rows.
    symbol = capture["symbol"]
    all_rows = []
    for number, (meta, stored) in enumerate(zip(bar_responses, raw["bar_raws"]), 1):
        if (not isinstance(meta, dict) or not isinstance(stored, dict)
                or meta.get("sequence") != number or stored.get("sequence") != number
                or meta["response_sha256"] != stored["response_sha256"]):
            raise ValueError("Alpaca equity bars capture request identity is invalid")
        try:
            bar_raw = base64.b64decode(stored["response_base64"].encode("ascii"), validate=True)
        except (ValueError, UnicodeError) as error:
            raise ValueError("Alpaca equity bars response is invalid") from error
        if digest(bar_raw) != meta["response_sha256"]:
            raise ValueError("Alpaca equity bars response hash is invalid")
        rows_page, _ = _alpaca_bars_parse(bar_raw, symbol)
        all_rows.extend(rows_page)

    trading_dates = capture["trading_dates"]
    return _alpaca_bars_coverage(all_rows, trading_dates)
