"""Single-source historical data demonstration; Python standard library only."""

import argparse
import csv
import hashlib
import io
import json
import math
import os
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


def normalize(raw, start=None, end=None):
    """Parse source order [time, low, high, open, close, volume], sort in UTC."""
    if start is None and end is None:
        start = epoch(CONFIG["start"])
        end = epoch(CONFIG["end_exclusive"])
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
            rows.append({"instrument": CONFIG["instrument"], "timestamp": timestamp,
                         "open": opening, "high": high, "low": low, "close": close,
                         "volume": volume, "_source_row": number})
        except (ValueError, OverflowError, OSError) as exc:
            report["rejected"] += 1
            report["errors"].append({"row": number, "reason": str(exc)})
    rows.sort(key=lambda r: r["timestamp"])
    return rows, report


def validate(rows, report, require_coverage=True):
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
    expected = ({iso(t) for t in range(epoch(CONFIG["start"]), epoch(CONFIG["end_exclusive"]), 86400)}
                if require_coverage else set())
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


def _atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def observe(input_dir, state_path, output, *, raw=None, now=None):
    """Route recent Coinbase candles to closed observations or open-only events."""
    input_dir = Path(input_dir)
    state_path = Path(state_path)
    output = Path(output)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("Observation time must be timezone-aware")
    if raw is None:
        end = int(current.timestamp())
        start = end - 4 * 86400
        url = "https://api.exchange.coinbase.com/products/BTC-USD/candles?" + urlencode(
            {"granularity": 86400, "start": iso(start), "end": iso(end)})
        request = Request(url, headers={"User-Agent": "TramitaGO-Quant-Core-Phase3/0.1",
                                        "Accept": "application/json"})
        with urlopen(request, timeout=30) as response:
            raw = response.read(2_000_001)
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise ValueError("Expected a candle array")
    closed_payload, openings = [], []
    for item in payload:
        if not isinstance(item, list) or not item:
            raise ValueError("Recent observation contains invalid rows")
        stamp = item[0]
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) \
                or not math.isfinite(stamp) or stamp % 86400 != 0:
            raise ValueError("Recent observation contains invalid rows")
        timestamp = iso(stamp)
        if stamp + 86400 <= current.timestamp():
            closed_payload.append(item)
        elif stamp <= current.timestamp():
            openings.append({"identity": f"BTC-USD|86400|{timestamp}",
                             "instrument": "BTC-USD", "frequency_seconds": 86400,
                             "timestamp": timestamp, "closed": False,
                             "open": item[3] if len(item) > 3 else None})
    rows, report = normalize(json.dumps(closed_payload).encode(), start=0)
    if report["rejected"]:
        raise ValueError("Recent observation contains invalid rows")
    closed = [row for row in rows if epoch(row["timestamp"]) + 86400 <= current.timestamp()]
    if closed:
        report = validate(closed, report, require_coverage=False)
    if report["errors"]:
        raise ValueError("Recent observation failed validation")
    state = {"instrument": "BTC-USD", "frequency_seconds": 86400,
             "processed_observation_ids": [], "observations": [],
             "last_observation_timestamp": None}
    if state_path.exists():
        state = json.loads(state_path.read_bytes())
    processed = set(state["processed_observation_ids"])
    persisted = {row["identity"]: row for row in state.get("observations", [])}
    accepted = []
    for row in closed:
        identity = f"BTC-USD|86400|{row['timestamp']}"
        if identity not in processed:
            accepted.append({"identity": identity, **{key: row[key] for key in COLUMNS[:-1]}})
            persisted[identity] = accepted[-1]
            processed.add(identity)
    accepted.sort(key=lambda row: row["timestamp"])
    timestamps = [row["timestamp"] for row in accepted]
    if timestamps:
        state["last_observation_timestamp"] = timestamps[-1]
    state["processed_observation_ids"] = sorted(processed)
    state["observations"] = [persisted[key] for key in sorted(persisted)]
    state_bytes = encoded(state)
    _atomic_write(state_path, state_bytes)
    captured, rejected = [], []
    for event in sorted(openings, key=lambda event: event["identity"]):
        try:
            captured.append(capture_open_event(state_path, event)["open_event"])
        except ValueError as exc:
            rejected.append({"identity": event["identity"], "reason": str(exc)})
    state_bytes = state_path.read_bytes()
    result = {"source": "Coinbase Exchange public candles", "instrument": "BTC-USD",
              "frequency_seconds": 86400, "timezone": "UTC", "new_observations": accepted,
              "already_processed": len(closed) - len(accepted),
              "unclosed_observations": len(payload) - len(closed_payload),
              "captured_open_events": captured, "rejected_open_events": rejected,
              "last_observation_timestamp": state["last_observation_timestamp"],
              "state_sha256": digest(state_bytes)}
    publish(output, {"observation.json": encoded(result)})
    return result


def decide(state_path, output):
    """Apply causal SMA3 decisions to accepted observations not yet decided."""
    state_path = Path(state_path)
    state = json.loads(state_path.read_bytes())
    if state.get("instrument") != "BTC-USD" or state.get("frequency_seconds") != 86400:
        raise ValueError("Decision state is incompatible with SMA3")
    observations = sorted(state.get("observations", []), key=lambda row: row["timestamp"])
    decisions = list(state.get("decisions", []))
    decided = {decision["observation_identity"] for decision in decisions}
    logical_position = state.get("logical_target_position", 0)
    new_decisions = []
    for index, observation in enumerate(observations):
        identity = observation["identity"]
        if identity in decided:
            logical_position = decisions[[item["observation_identity"] for item in decisions].index(identity)]["target_position"]
            if logical_position is None:
                logical_position = 0
            continue
        closes = [item["close"] for item in observations[max(0, index - 2):index + 1]]
        sma = math.fsum(closes) / 3.0 if len(closes) == 3 else None
        if sma is None:
            decision = "NO_DECISION"
            target_position = None
        else:
            target_position = 1 if observation["close"] > sma else 0
            decision = ("ENTER" if target_position == 1 and logical_position == 0 else
                        "EXIT" if target_position == 0 and logical_position == 1 else "HOLD")
        record = {"identity": f"SMA3|{identity}", "observation_identity": identity,
                  "instrument": "BTC-USD", "frequency_seconds": 86400,
                  "timestamp": observation["timestamp"], "strategy": "SMA3_LONG_ONLY",
                  "parameters": {"window": 3, "entry": "close > sma_close_3",
                                 "exit": "close <= sma_close_3"},
                  "close": observation["close"], "sma_close_3": sma,
                  "warm_up": sma is None, "decision": decision,
                  "target_position": target_position,
                  "logical_position_before": logical_position}
        new_decisions.append(record)
        decisions.append(record)
        decided.add(identity)
        if target_position is not None:
            logical_position = target_position
    state["decisions"] = decisions
    state["logical_target_position"] = logical_position
    state_bytes = encoded(state)
    _atomic_write(state_path, state_bytes)
    result = {"strategy": "SMA3_LONG_ONLY", "new_decisions": new_decisions,
              "already_decided": len(observations) - len(new_decisions),
              "decision_count": len(decisions), "state_sha256": digest(state_bytes)}
    publish(output, {"decision.json": encoded(result)})
    return result


def execute_virtual(state_path, output):
    """Execute persisted ENTER/EXIT decisions only at the next candle open."""
    state_path = Path(state_path)
    state = json.loads(state_path.read_bytes())
    if state.get("instrument") != "BTC-USD" or state.get("frequency_seconds") != 86400:
        raise ValueError("Execution state is incompatible with SMA3")
    observations = sorted(state.get("observations", []), key=lambda row: row["timestamp"])
    by_timestamp = {row["timestamp"]: row for row in observations}
    open_events = {event["timestamp"]: event for event in state.get("open_events", [])}
    pending = list(state.get("pending_actions", []))
    executions = list(state.get("executions", []))
    executed_ids = {item["identity"] for item in executions}
    pending_ids = {item["decision_identity"] for item in pending}
    virtual_position = state.get("virtual_position", 0)
    if virtual_position not in (0, 1):
        raise ValueError("Virtual position must be 0 or 1")
    for decision in state.get("decisions", []):
        if decision.get("decision") not in ("ENTER", "EXIT"):
            continue
        decision_identity = decision["identity"]
        if decision_identity in pending_ids or any(
            item["decision_identity"] == decision_identity for item in executions
        ):
            continue
        timestamp = datetime.fromisoformat(decision["timestamp"].replace("Z", "+00:00"))
        pending.append({
            "identity": f"PENDING|{decision_identity}",
            "decision_identity": decision_identity,
            "observation_identity": decision["observation_identity"],
            "expected_execution_timestamp": iso(int(timestamp.timestamp()) + 86400),
            "instrument": "BTC-USD", "frequency_seconds": 86400,
            "target_position": decision["target_position"],
            "virtual_position_before": virtual_position,
        })
        pending_ids.add(decision_identity)
    new_executions = []
    remaining = []
    for action in pending:
        execution_observation = open_events.get(action["expected_execution_timestamp"])
        if execution_observation is None:
            execution_observation = by_timestamp.get(action["expected_execution_timestamp"])
        if execution_observation is None:
            remaining.append(action)
            continue
        execution_identity = f"EXECUTION|{action['identity']}|{execution_observation['identity']}"
        if execution_identity in executed_ids:
            continue
        target = action["target_position"]
        if target == virtual_position:
            remaining.append(action)
            continue
        record = {"identity": execution_identity, "decision_identity": action["decision_identity"],
                  "source_observation_identity": action["observation_identity"],
                  "execution_observation_identity": execution_observation["identity"],
                  "timestamp": execution_observation["timestamp"], "price": execution_observation["open"],
                  "action": "ENTER" if target == 1 else "EXIT",
                  "virtual_position_before": virtual_position, "virtual_position_after": target,
                  "costs": 0, "slippage": 0}
        new_executions.append(record)
        executions.append(record)
        executed_ids.add(execution_identity)
        virtual_position = target
    state["pending_actions"] = remaining
    state["executions"] = executions
    state["virtual_position"] = virtual_position
    state["last_execution_timestamp"] = executions[-1]["timestamp"] if executions else None
    state_bytes = encoded(state)
    _atomic_write(state_path, state_bytes)
    result = {"new_executions": new_executions, "pending_actions": remaining,
              "virtual_position": virtual_position, "execution_count": len(executions),
              "state_sha256": digest(state_bytes)}
    publish(output, {"execution.json": encoded(result)})
    return result


def realize_results(state_path):
    """Append closed-position results from the persisted execution ledger only."""
    state_path = Path(state_path)
    state = json.loads(state_path.read_bytes())
    if not isinstance(state, dict) or state.get("instrument") != "BTC-USD" \
            or state.get("frequency_seconds") != 86400:
        raise ValueError("Realized result state is incompatible with BTC-USD daily data")
    if type(state.get("virtual_position")) is not int or state["virtual_position"] not in (0, 1):
        raise ValueError("Realized result state requires a binary virtual position")
    executions = state.get("executions")
    decisions = state.get("decisions")
    if not isinstance(executions, list) or not isinstance(decisions, list):
        raise ValueError("Realized results require persisted executions and decisions")
    by_decision = {}
    for decision in decisions:
        if not isinstance(decision, dict) or not isinstance(decision.get("identity"), str) \
                or not decision["identity"] or decision["identity"] in by_decision:
            raise ValueError("Invalid or duplicate persisted decision identity")
        by_decision[decision["identity"]] = decision
    required = ("identity", "decision_identity", "source_observation_identity",
                "execution_observation_identity", "timestamp", "price", "action",
                "virtual_position_before", "virtual_position_after", "costs", "slippage")
    seen, used_decisions = set(), set()
    entry, previous_timestamp = None, None
    results = []
    for number, execution in enumerate(executions, 1):
        error = f"Invalid persisted execution {number}: "
        if not isinstance(execution, dict) or any(key not in execution for key in required):
            raise ValueError(error + "incomplete record")
        if any(not isinstance(execution[key], str) or not execution[key] for key in required[:5]):
            raise ValueError(error + "invalid identity or timestamp")
        try:
            stamp = epoch(execution["timestamp"])
            if stamp % 86400 or iso(stamp) != execution["timestamp"]:
                raise ValueError("not UTC midnight")
        except (ValueError, OverflowError, OSError):
            raise ValueError(error + "timestamp must be a canonical UTC midnight") from None
        if previous_timestamp is not None and stamp <= previous_timestamp:
            raise ValueError(error + "executions must be strictly chronological")
        previous_timestamp = stamp
        if execution["identity"] in seen or execution["decision_identity"] in used_decisions:
            raise ValueError(error + "duplicate execution or decision")
        seen.add(execution["identity"])
        used_decisions.add(execution["decision_identity"])
        for key in ("price", "costs", "slippage"):
            value = execution[key]
            try:
                valid = type(value) in (int, float) and math.isfinite(value) \
                    and (value > 0 if key == "price" else value == 0)
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError(error + "price must be positive and finite; costs and slippage must be zero")
        action = execution["action"]
        before, after = (0, 1) if action == "ENTER" else (1, 0)
        if action not in ("ENTER", "EXIT") or any(
            type(execution[key]) is not int or execution[key] != value
            for key, value in (("virtual_position_before", before), ("virtual_position_after", after))
        ) or before != int(entry is not None):
            raise ValueError(error + "invalid long-only entry/exit sequence")
        source_identity = f"BTC-USD|86400|{iso(stamp - 86400)}"
        opening_identity = f"BTC-USD|86400|{execution['timestamp']}"
        decision_identity = f"SMA3|{source_identity}"
        decision = by_decision.get(decision_identity, {})
        if execution["source_observation_identity"] != source_identity \
                or execution["execution_observation_identity"] != opening_identity \
                or execution["decision_identity"] != decision_identity \
                or execution["identity"] != f"EXECUTION|PENDING|{decision_identity}|{opening_identity}" \
                or decision.get("observation_identity") != source_identity \
                or decision.get("timestamp") != iso(stamp - 86400) \
                or decision.get("decision") != action or decision.get("target_position") != after:
            raise ValueError(error + "inconsistent causal linkage")
        if action == "ENTER":
            entry = execution
            continue
        gross_return = execution["price"] / entry["price"] - 1
        if not math.isfinite(gross_return):
            raise ValueError(error + "non-finite realized return")
        result = {"identity": f"REALIZED|{entry['identity']}|{execution['identity']}",
                  "instrument": "BTC-USD", "frequency_seconds": 86400,
                  "gross_return": gross_return, "costs": 0, "slippage": 0}
        for side, record in (("entry", entry), ("exit", execution)):
            result[side + "_execution_identity"] = record["identity"]
            for key in ("decision_identity", "source_observation_identity",
                        "execution_observation_identity", "timestamp", "price"):
                result[side + "_" + key] = record[key]
        results.append(result)
        entry = None
    if state["virtual_position"] != int(entry is not None):
        raise ValueError("Persisted virtual position disagrees with execution ledger")
    existing = state.get("realized_results", [])
    if not isinstance(existing, list) or len(existing) > len(results) \
            or encoded(existing) != encoded(results[:len(existing)]):
        raise ValueError("Persisted realized results disagree with execution ledger")
    new_results = results[len(existing):]
    state["realized_results"] = results
    state_bytes = encoded(state)
    if state_path.read_bytes() != state_bytes:
        _atomic_write(state_path, state_bytes)
    return {"new_realized_results": new_results, "realized_result_count": len(results),
            "virtual_position": state["virtual_position"], "state_sha256": digest(state_bytes)}


def _valuation_time(value):
    """Parse an explicit UTC instant without consulting the clock."""
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.utcoffset() is None or instant.utcoffset().total_seconds() != 0:
            raise ValueError("not UTC")
        return instant
    except (AttributeError, TypeError, ValueError):
        raise ValueError("Valuation timestamps must be explicit UTC instants") from None


def mark_unrealized(state_path, valuation_instant):
    """Value the open ENTER using the latest eligible persisted daily close."""
    instant = _valuation_time(valuation_instant)
    valuation_instant = instant.isoformat().replace("+00:00", "Z")
    state_path = Path(state_path)
    state = json.loads(state_path.read_bytes())
    if not isinstance(state, dict) or state.get("instrument") != "BTC-USD" \
            or state.get("frequency_seconds") != 86400 \
            or type(state.get("virtual_position")) is not int \
            or state["virtual_position"] not in (0, 1):
        raise ValueError("Valuation requires BTC-USD daily state with a binary position")
    current = {"status": "NO OPEN POSITION", "valuation_instant": valuation_instant,
               "position_identity": None, "mark_identity": None}
    marks = state.get("unrealized_marks", [])
    if not isinstance(marks, list) or any(
        not isinstance(mark, dict) or not isinstance(mark.get("identity"), str) for mark in marks
    ) or len({mark["identity"] for mark in marks}) != len(marks):
        raise ValueError("Invalid or duplicate persisted mark identities")
    if state["virtual_position"] == 1:
        executions = state.get("executions")
        if not isinstance(executions, list) or not executions or not isinstance(executions[-1], dict):
            raise ValueError("Open position requires a persisted ENTER execution")
        entry = executions[-1]
        try:
            entry_time = _valuation_time(entry["timestamp"])
            entry_stamp = entry_time.timestamp()
            source = f"BTC-USD|86400|{iso(entry_stamp - 86400)}"
            opening = f"BTC-USD|86400|{iso(entry_stamp)}"
            if entry_stamp % 86400 or entry["timestamp"] != iso(entry_stamp) \
                    or entry["action"] != "ENTER" \
                    or type(entry["virtual_position_before"]) is not int or entry["virtual_position_before"] != 0 \
                    or type(entry["virtual_position_after"]) is not int or entry["virtual_position_after"] != 1 \
                    or entry["identity"] != f"EXECUTION|PENDING|SMA3|{source}|{opening}" \
                    or type(entry["price"]) not in (int, float) \
                    or not math.isfinite(entry["price"]) or entry["price"] <= 0 \
                    or any(type(entry[key]) not in (int, float) or entry[key] != 0 for key in ("costs", "slippage")):
                raise ValueError("inconsistent ENTER")
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            raise ValueError("Invalid persisted ENTER for open-position valuation") from None
        position_identity = f"POSITION|{entry['identity']}"
        current.update(status="NOT AVAILABLE", position_identity=position_identity)
        observations = state.get("observations")
        if not isinstance(observations, list):
            raise ValueError("Valuation requires persisted observations")
        eligible = {}
        for observation in observations:
            try:
                if not isinstance(observation, dict) or observation.get("instrument") != "BTC-USD" \
                        or observation.get("frequency_seconds", state["frequency_seconds"]) != 86400 \
                        or observation.get("closed", True) is not True:
                    continue
                stamp = _valuation_time(observation["timestamp"]).timestamp()
                if stamp < entry_stamp or stamp + 86400 > instant.timestamp():
                    continue
                if observation["timestamp"] != iso(stamp) \
                        or observation["identity"] != f"BTC-USD|86400|{observation['timestamp']}":
                    continue
                if any(_valuation_time(observation[key]) > instant
                       for key in ("accepted_at", "available_at") if key in observation):
                    continue
                raw = json.dumps([[stamp] + [observation[key] for key in
                                            ("low", "high", "open", "close", "volume")]]).encode()
                rows, report = normalize(raw, start=0)
                if validate(rows, report, require_coverage=False)["status"] != "PASS":
                    continue
            except (KeyError, TypeError, ValueError, OverflowError, OSError):
                continue
            if stamp in eligible:
                raise ValueError("Duplicate eligible observation timestamp")
            eligible[stamp] = rows[0]
        if eligible:
            row = eligible[max(eligible)]
            mark_observation_identity = f"BTC-USD|86400|{row['timestamp']}"
            unrealized_return = row["close"] / entry["price"] - 1
            if not math.isfinite(unrealized_return):
                raise ValueError("Non-finite unrealized return")
            mark = {"identity": f"MARK|{position_identity}|{mark_observation_identity}",
                    "position_identity": position_identity, "entry_execution_identity": entry["identity"],
                    "mark_observation_identity": mark_observation_identity,
                    "valuation_instant": valuation_instant, "mark_timestamp": row["timestamp"],
                    "mark_price": row["close"], "unrealized_return": unrealized_return,
                    "costs": 0, "slippage": 0}
            existing = next((item for item in marks if item["identity"] == mark["identity"]), None)
            if existing is not None:
                # Keep the first valuation instant; a later replay is not a new mark.
                if {key: value for key, value in existing.items() if key != "valuation_instant"} != \
                        {key: value for key, value in mark.items() if key != "valuation_instant"}:
                    raise ValueError("Conflicting persisted valuation for the same position and observation")
            else:
                marks = marks + [mark]
            current.update(status="AVAILABLE", mark_identity=mark["identity"],
                           unrealized_return=unrealized_return)
    state["unrealized_marks"] = sorted(marks, key=lambda mark: mark["identity"])
    state["unrealized_valuation"] = current
    state_bytes = encoded(state)
    if state_path.read_bytes() != state_bytes:
        _atomic_write(state_path, state_bytes)
    return {**current, "state_sha256": digest(state_bytes)}


def capture_open_event(state_path, event, output=None):
    """Persist an open-only event without adding it to closed observations."""
    state_path = Path(state_path)
    state = json.loads(state_path.read_bytes())
    if state.get("instrument") != "BTC-USD" or state.get("frequency_seconds") != 86400:
        raise ValueError("Open event state is incompatible with BTC-USD daily data")
    required = ("identity", "instrument", "frequency_seconds", "timestamp", "open", "closed")
    if any(key not in event for key in required) or event["instrument"] != "BTC-USD" \
            or event["frequency_seconds"] != 86400 or event["closed"] is not False:
        raise ValueError("Open event is incomplete or incompatible")
    expected_identity = f"BTC-USD|86400|{event['timestamp']}"
    if event["identity"] != expected_identity:
        raise ValueError("Open event identity is invalid")
    if isinstance(event["open"], bool) or not isinstance(event["open"], (int, float)) \
            or not math.isfinite(event["open"]) or event["open"] <= 0:
        raise ValueError("Open event price is invalid")
    events = {item["identity"]: item for item in state.get("open_events", [])}
    if event["identity"] in events and events[event["identity"]] != {key: event[key] for key in required}:
        raise ValueError("Conflicting open event for the same identity")
    events[event["identity"]] = {key: event[key] for key in required}
    state["open_events"] = [events[key] for key in sorted(events)]
    state_bytes = encoded(state)
    _atomic_write(state_path, state_bytes)
    result = {"open_event": events[event["identity"]], "state_sha256": digest(state_bytes)}
    if output is not None:
        publish(output, {"open-event.json": encoded(result)})
    return result


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
    observation = commands.add_parser("observe")
    observation.add_argument("--input", required=True)
    observation.add_argument("--state", required=True)
    observation.add_argument("--output", required=True)
    decision = commands.add_parser("decide")
    decision.add_argument("--state", required=True)
    decision.add_argument("--output", required=True)
    execution = commands.add_parser("execute-virtual")
    execution.add_argument("--state", required=True)
    execution.add_argument("--output", required=True)
    realized = commands.add_parser("realize-results")
    realized.add_argument("--state", required=True)
    marking = commands.add_parser("mark-unrealized")
    marking.add_argument("--state", required=True)
    marking.add_argument("--valuation-instant", required=True)
    open_event = commands.add_parser("open-event")
    open_event.add_argument("--state", required=True)
    open_event.add_argument("--input", required=True)
    open_event.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.command == "acquire":
            result = acquire(args.output)
        elif args.command == "run":
            result = run(args.input, args.output)
        elif args.command == "evaluate":
            result = evaluate(args.input, args.output)
        elif args.command == "observe":
            result = observe(args.input, args.state, args.output)
        elif args.command == "decide":
            result = decide(args.state, args.output)
        elif args.command == "execute-virtual":
            result = execute_virtual(args.state, args.output)
        elif args.command == "realize-results":
            result = realize_results(args.state)
        elif args.command == "mark-unrealized":
            result = mark_unrealized(args.state, args.valuation_instant)
        elif args.command == "open-event":
            result = capture_open_event(args.state, json.loads(Path(args.input).read_bytes()), args.output)
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
