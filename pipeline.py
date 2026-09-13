"""Single-source historical data demonstration; Python standard library only."""

import argparse
import csv
import hashlib
import io
import json
import math
from datetime import datetime, timezone
from pathlib import Path
import platform
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen

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


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def epoch(value):
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def publish(directory, files):
    """Same bytes are a no-op; never overwrite a different or incomplete run."""
    directory = Path(directory)
    if directory.exists():
        if set(p.name for p in directory.iterdir()) != set(files):
            raise ValueError("Output exists with different/incomplete contents")
        if any((directory / name).read_bytes() != data for name, data in files.items()):
            raise ValueError("Output exists with different contents")
        return
    directory.mkdir(parents=True)
    for name, data in files.items():
        (directory / name).write_bytes(data)


def save_capture(raw, output, *, kind, acquired_at, response_headers=None):
    if kind not in ("live", "synthetic-test"):
        raise ValueError("Unknown capture kind")
    metadata = {"config": CONFIG, "url": URL, "kind": kind,
                "acquired_at": acquired_at, "raw_sha256": digest(raw),
                "response_headers": response_headers or {}}
    publish(output, {"raw.json": raw, "capture.json": encoded(metadata)})


def acquire(output):
    """One public GET; this function is never invoked by deterministic tests."""
    if Path(output).exists():
        raise ValueError("Capture output already exists; use its conserved input for replay")
    acquired_at = datetime.now(timezone.utc).isoformat()
    try:
        request = Request(URL, headers={"User-Agent": "TramitaGO-Quant-Core-Phase1/0.1",
                                       "Accept": "application/json"})
        with urlopen(request, timeout=30) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError("Response exceeds bounded capture size")
            headers = {key: response.headers.get(key) for key in ("Date", "Content-Type")}
        save_capture(raw, output, kind="live", acquired_at=acquired_at, response_headers=headers)
    except Exception as exc:
        if not Path(output).exists():
            publish(output, {"acquisition_failure.json": encoded(
                {"url": URL, "acquired_at": acquired_at, "error": str(exc), "status": "FAIL"})})
        raise
    return {"status": "CAPTURED_NOT_YET_VALIDATED", "input": str(output), "raw_sha256": digest(raw)}


def normalize(raw):
    """Parse source order [time, low, high, open, close, volume], sort in UTC."""
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
            if not epoch(CONFIG["start"]) <= stamp < epoch(CONFIG["end_exclusive"]):
                report["excluded_outside_range"] += 1
                continue
            rows.append({"instrument": CONFIG["instrument"], "timestamp": timestamp,
                         "open": opening, "high": high, "low": low, "close": close,
                         "volume": volume, "_source_row": number})
        except (ValueError, OverflowError, OSError) as exc:
            report["rejected"] += 1
            report["errors"].append({"row": number, "reason": str(exc)})
    rows.sort(key=lambda r: r["timestamp"])
    return rows, report


def validate(rows, report):
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
    expected = {iso(t) for t in range(epoch(CONFIG["start"]), epoch(CONFIG["end_exclusive"]), 86400)}
    if not rows:
        report["errors"].append({"row": None, "reason": "Empty dataset"})
    if seen != expected:
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


def run(input_dir, output):
    input_dir = Path(input_dir)
    raw = (input_dir / "raw.json").read_bytes()
    capture_bytes = (input_dir / "capture.json").read_bytes()
    capture = json.loads(capture_bytes)
    if capture.get("config") != CONFIG or capture.get("url") != URL or capture.get("raw_sha256") != digest(raw):
        raise ValueError("Capture hash or configuration mismatch")
    if capture.get("kind") not in ("live", "synthetic-test"):
        raise ValueError("Unknown capture provenance")
    source = Path(__file__).read_bytes()
    rows, report = normalize(raw)
    report = validate(rows, report)
    files = {"validation.json": encoded(report)}
    if report["status"] != "PASS":
        files["failure.json"] = encoded({"input_sha256": digest(raw), "code_sha256": digest(source),
                                          "config": CONFIG, "status": "FAIL"})
        publish(output, files)
        raise ValueError("Dataset rejected; see validation.json")
    derived = indicators(rows)
    dataset = dataset_bytes(derived)
    manifest = {"status": "PASS", "config": CONFIG, "schema": SCHEMA, "columns": COLUMNS,
                "rows": len(rows), "range": [rows[0]["timestamp"], rows[-1]["timestamp"]],
                "dataset_sha256": digest(dataset), "validation_sha256": digest(files["validation.json"]),
                "raw_sha256": digest(raw), "capture_sha256": digest(capture_bytes),
                "code_sha256": digest(source), "baseline": "uncommitted source identified by SHA256",
                "runtime": platform.python_version(), "input_kind": capture["kind"],
                "indicator_non_null": sum(r["sma_close_3"] is not None for r in derived),
                "indicator_sample": [{k: r[k] for k in ("timestamp", "sma_close_3")} for r in derived],
                "replay": "python -B pipeline.py run --input <this-run> --output <new-run>"}
    files.update({"dataset.csv": dataset, "manifest.json": encoded(manifest),
                  "raw.json": raw, "capture.json": capture_bytes, "pipeline_snapshot.py": source})
    publish(output, files)
    return manifest


def verified_manifest(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_bytes())
    for name, field in [("dataset.csv", "dataset_sha256"), ("validation.json", "validation_sha256"),
                        ("raw.json", "raw_sha256"), ("capture.json", "capture_sha256"),
                        ("pipeline_snapshot.py", "code_sha256")]:
        if digest((directory / name).read_bytes()) != manifest[field]:
            raise ValueError("Artifact integrity failure: " + name)
    if manifest["status"] != "PASS":
        raise ValueError("Only successful runs can be compared")
    return manifest


def compare(first, second):
    a, b = verified_manifest(first), verified_manifest(second)
    checks = {key: a[key] == b[key] for key in (
        "config", "schema", "columns", "rows", "range", "dataset_sha256", "validation_sha256",
        "raw_sha256", "capture_sha256", "code_sha256", "runtime", "input_kind",
        "indicator_non_null", "indicator_sample")}
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks}


def _metrics(trades):
    returns = [trade["gross_return"] for trade in trades]
    winning_trades = sum(value > 0 for value in returns)
    losing_trades = sum(value < 0 for value in returns)
    return {
        "cumulative_compounded_return": math.prod(1.0 + value for value in returns) - 1.0 if returns else 0.0,
        "trade_count": len(returns),
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate": winning_trades / len(returns) if returns else None,
    }


def _evaluation_rows(input_dir):
    input_dir = Path(input_dir)
    manifest = json.loads((input_dir / "manifest.json").read_bytes())
    if manifest.get("status") != "PASS":
        raise ValueError("Evaluation requires a PASS manifest")
    if manifest.get("columns") != COLUMNS or manifest.get("schema") != SCHEMA:
        raise ValueError("Dataset schema does not match the validated Phase 1 schema")
    dataset = (input_dir / "dataset.csv").read_bytes()
    if manifest.get("dataset_sha256") != digest(dataset):
        raise ValueError("Dataset integrity check failed")
    rows = []
    with io.StringIO(dataset.decode("utf-8"), newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != COLUMNS:
            raise ValueError("Dataset columns do not match the validated schema")
        for row in reader:
            try:
                values = {name: row[name] for name in EVALUATION_COLUMNS}
                values["open"] = float(values["open"])
                values["close"] = float(values["close"])
                values["sma_close_3"] = (
                    None if values["sma_close_3"] == "" else float(values["sma_close_3"])
                )
                datetime.fromisoformat(values["timestamp"].replace("Z", "+00:00"))
            except (KeyError, ValueError, TypeError) as exc:
                raise ValueError("Dataset contains an invalid evaluation row") from exc
            rows.append(values)
    if not rows or any(rows[index]["timestamp"] >= rows[index + 1]["timestamp"]
                       for index in range(len(rows) - 1)):
        raise ValueError("Dataset rows must be non-empty and strictly ordered")
    if any(row["instrument"] != "BTC-USD" for row in rows):
        raise ValueError("Evaluation requires the BTC-USD dataset")
    return manifest, rows


def evaluate(input_dir, output):
    manifest, rows = _evaluation_rows(input_dir)
    signals = []
    executions = []
    positions = []
    trades = []
    position = 0
    pending = None
    entry = None
    for index, row in enumerate(rows):
        if pending is not None:
            action = pending
            price = row["open"]
            executions.append({"timestamp": row["timestamp"], "action": action, "price": price})
            if action == "BUY":
                position = 1
                entry = {"timestamp": row["timestamp"], "price": price}
            else:
                position = 0
                trades.append({
                    "entry_timestamp": entry["timestamp"],
                    "entry_price": entry["price"],
                    "exit_timestamp": row["timestamp"],
                    "exit_price": price,
                    "gross_return": price / entry["price"] - 1.0,
                })
                entry = None
            pending = None
        indicator = row["sma_close_3"]
        action = None
        if indicator is not None:
            if position == 0 and row["close"] > indicator and index + 1 < len(rows):
                action = "BUY"
                pending = action
            elif position == 1 and row["close"] <= indicator and index + 1 < len(rows):
                action = "SELL"
                pending = action
        signals.append({"timestamp": row["timestamp"], "signal": action})
        positions.append({"timestamp": row["timestamp"], "position": position})
    if position == 1:
        last = rows[-1]
        executions.append({"timestamp": last["timestamp"], "action": "LIQUIDATE", "price": last["close"]})
        trades.append({
            "entry_timestamp": entry["timestamp"],
            "entry_price": entry["price"],
            "exit_timestamp": last["timestamp"],
            "exit_price": last["close"],
            "gross_return": last["close"] / entry["price"] - 1.0,
        })
        positions[-1]["position"] = 0
    payload = {
        "strategy": {"id": "SMA3_LONG_ONLY", "instrument": "BTC-USD", "frequency_seconds": 86400,
                     "indicator": "sma_close_3", "entry": "close[t] > sma_close_3[t]",
                     "exit": "close[t] <= sma_close_3[t]", "execution": "open[t+1]",
                     "costs": 0, "slippage": 0},
        "input": {"dataset_sha256": manifest["dataset_sha256"], "manifest_status": manifest["status"],
                  "rows": len(rows)},
        "signals": signals,
        "executions": executions,
        "positions": positions,
        "trades": trades,
        "metrics": _metrics(trades),
    }
    payload["output_sha256"] = digest(encoded(payload))
    publish(output, {"evaluation.json": encoded(payload)})
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    live = commands.add_parser("acquire")
    live.add_argument("--output", required=True)
    replay = commands.add_parser("run")
    replay.add_argument("--input", required=True)
    replay.add_argument("--output", required=True)
    comparison = commands.add_parser("compare")
    comparison.add_argument("first")
    comparison.add_argument("second")
    comparison.add_argument("--output", required=True)
    evaluation = commands.add_parser("evaluate")
    evaluation.add_argument("--input", required=True)
    evaluation.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.command == "acquire":
            result = acquire(args.output)
        elif args.command == "run":
            result = run(args.input, args.output)
        elif args.command == "evaluate":
            result = evaluate(args.input, args.output)
        else:
            result = compare(args.first, args.second)
            publish(args.output, {"comparison.json": encoded(result)})
        print(json.dumps(result, indent=2))
        return 0 if result.get("status", "PASS") != "FAIL" else 1
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
