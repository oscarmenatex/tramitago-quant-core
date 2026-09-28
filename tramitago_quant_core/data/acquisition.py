"""CAP-001 Data -- acquisition contract, normalization and validation for the
original M1.1 historical BTC-USD demo dataset (Coinbase public candles).

Moved out of pipeline.py per DOC-014/DOC-015 (adaptado) SS4 (Etapa 4, M4.1
module decomposition). No behavior change: byte-for-byte identical logic.
`acquire`, `run`, `verified_manifest`, and `compare` (CLI orchestration, not
pure data logic) stay in pipeline.py.
"""

import csv
import io
import json
import math
from urllib.parse import urlencode
from urllib.request import Request

from tramitago_quant_core.shared.util import epoch, iso

CONFIG = {
    "source": "Coinbase Exchange public candles",
    "instrument": "BTC-USD",
    "frequency_seconds": 86400,
    "start": "2024-01-01T00:00:00Z",
    "end_exclusive": "2024-01-11T00:00:00Z",
    "schema_version": "1",
    "timezone": "UTC",
    "missing_policy": "reject entire dataset; no imputation",
    "duplicate_policy": "reject entire dataset",
    "coverage_policy": "one closed candle per UTC day; reject missing days",
    "indicator": {"name": "sma_close_3", "window": 3, "initial": "null for first 2 rows"},
}
COLUMNS = ["instrument", "timestamp", "open", "high", "low", "close", "volume", "sma_close_3"]
EVALUATION_COLUMNS = ["instrument", "timestamp", "open", "close", "sma_close_3"]
SCHEMA = {"instrument": "string", "timestamp": "UTC ISO8601 bucket start",
          **{name: "finite float64" for name in COLUMNS[2:-1]},
          "sma_close_3": "nullable finite float64"}
URL = "https://api.exchange.coinbase.com/products/BTC-USD/candles?" + urlencode(
    {"granularity": 86400, "start": CONFIG["start"], "end": CONFIG["end_exclusive"]})
COINBASE_PUBLIC_REQUEST_HEADERS = {
    "User-Agent": "TramitaGO-Quant-Core-Phase1/0.1",
    "Accept": "application/json",
}


def _coinbase_public_request_headers():
    """Return the shared, unauthenticated public Coinbase request contract."""
    return dict(COINBASE_PUBLIC_REQUEST_HEADERS)


def _coinbase_public_request(url):
    return Request(url, headers=_coinbase_public_request_headers())


def normalize(raw, start=None, end=None, instrument=None):
    """Parse source order [time, low, high, open, close, volume], sort in UTC.

    Instrument extension (2026-09-28): `instrument` defaults to CONFIG's
    fixed "BTC-USD" (preserving the original M1.1 CLI demo unchanged) but
    can be overridden -- generalized after finding it hardcoded while
    investigating a second instrument (ETH-USD) for the M2.2-T1 Hypothesis
    Dataset, which calls this function for any instrument its Hypothesis
    declares.
    """
    if start is None and end is None:
        start = epoch(CONFIG["start"])
        end = epoch(CONFIG["end_exclusive"])
    instrument = instrument if instrument is not None else CONFIG["instrument"]
    report = {"status": "FAIL", "received": 0, "accepted": 0, "rejected": 0,
              "excluded_outside_range": 0, "errors": [], "warnings": []}
    rows = []
    try:
        payload = json.loads(raw)
        if not isinstance(payload, list):
            raise ValueError("Expected a candle array")
    except (ValueError, UnicodeError) as exc:
        report["errors"].append({"row": None, "reason": "Invalid JSON response: " + str(exc)})
        return rows, report
    report["received"] = len(payload)
    for number, item in enumerate(payload, 1):
        try:
            if not isinstance(item, list) or len(item) != 6:
                raise ValueError("Expected exactly six candle fields")
            if any(isinstance(v, bool) or v is None or not isinstance(v, (int, float)) for v in item):
                raise ValueError("Missing or nonnumeric field")
            values = [float(v) for v in item]
            if not all(math.isfinite(v) for v in values):
                raise ValueError("Non-finite field")
            stamp, low, high, opening, close, volume = values
            if stamp != int(stamp) or stamp % 86400 != 0:
                raise ValueError("Timestamp must be an integer UTC midnight")
            timestamp = iso(stamp)
            if start is not None and stamp < start or end is not None and stamp >= end:
                report["excluded_outside_range"] += 1
                continue
            rows.append({"instrument": instrument, "timestamp": timestamp,
                         "open": opening, "high": high, "low": low, "close": close,
                         "volume": volume, "_source_row": number})
        except (ValueError, OverflowError, OSError) as exc:
            report["rejected"] += 1
            report["errors"].append({"row": number, "reason": str(exc)})
    rows.sort(key=lambda r: r["timestamp"])
    return rows, report


def validate(rows, report, require_coverage=True, expected_timestamps=None):
    """Reject whole batch on any bad row, duplicate, or incomplete daily coverage."""
    seen = set()
    valid_count = 0
    for row in rows:
        reason = None
        prices = [row[k] for k in ("open", "high", "low", "close")]
        if not all(math.isfinite(v) and v > 0 for v in prices) or not math.isfinite(row["volume"]) or row["volume"] < 0:
            reason = "Prices must be finite and positive; volume finite and nonnegative"
        elif not row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"]:
            reason = "Invalid OHLC relations"
        elif row["timestamp"] in seen:
            reason = "Duplicate timestamp"
        seen.add(row["timestamp"])
        if reason:
            report["rejected"] += 1
            report["errors"].append({"row": row["_source_row"], "reason": reason})
        else:
            valid_count += 1
    expected = (set(expected_timestamps) if expected_timestamps is not None else {
        iso(t) for t in range(epoch(CONFIG["start"]), epoch(CONFIG["end_exclusive"]), 86400)
    }) if require_coverage else set()
    if not rows:
        report["errors"].append({"row": None, "reason": "Empty dataset"})
    if require_coverage and seen != expected:
        report["errors"].append({"row": None, "reason": "Incomplete coverage", "missing": sorted(expected - seen)})
    report["valid_rows_before_batch_gate"] = valid_count
    report["status"] = "FAIL" if report["errors"] else "PASS"
    report["accepted"] = len(rows) if report["status"] == "PASS" else 0
    report["withheld_by_batch_gate"] = valid_count if report["status"] == "FAIL" else 0
    return report


def indicators(rows):
    result = []
    for i, row in enumerate(rows):
        # Divide before summing to avoid overflow on otherwise finite prices.
        value = math.fsum(r["close"] / 3 for r in rows[i - 2:i + 1]) if i >= 2 else None
        if value is not None and not math.isfinite(value):
            raise ValueError("Non-finite indicator")
        result.append({k: row[k] for k in COLUMNS[:-1]} | {"sma_close_3": value})
    return result


def dataset_bytes(rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")
