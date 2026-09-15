"""Single-source historical data demonstration; Python standard library only."""

import argparse
import csv
from decimal import Decimal, InvalidOperation
import hashlib
from http.client import HTTPSConnection
import io
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
import platform
import sys
from urllib.parse import quote, urlencode, urlsplit
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


PAPER_SESSION_SCHEMA_VERSION = "1"


def _paper_session_identity(session_id, started_at):
    content = {"session_id": session_id, "mode": "PAPER",
               "instrument": "BTC-USD", "started_at": started_at,
               "schema_version": PAPER_SESSION_SCHEMA_VERSION}
    return f"PAPER_SESSION_STATE|{digest(encoded(content))}"


def _valid_paper_session_id(value):
    return isinstance(value, str) and value.startswith("PAPER_SESSION|") \
        and len(value) <= 160 and len(value) > len("PAPER_SESSION|") \
        and all(33 <= ord(char) <= 126 for char in value)


def paper_session_state_is_valid(state):
    """Validate the local PAPER session envelope without broker access."""
    if not isinstance(state, dict) or set(state) != {
            "session_id", "session_identity", "schema_version", "mode",
            "instrument", "started_at", "internal_position_state",
            "broker_position_observed", "reconciliation_status",
            "warmup_observations", "processed_observations", "decisions",
            "executions", "pending_actions", "broker_submissions"}:
        return False
    arrays = ("warmup_observations", "processed_observations", "decisions",
              "executions", "pending_actions", "broker_submissions")
    return (
        _valid_paper_session_id(state.get("session_id"))
        and state.get("session_identity") == _paper_session_identity(
            state["session_id"], state.get("started_at"))
        and state.get("schema_version") == PAPER_SESSION_SCHEMA_VERSION
        and state.get("mode") == "PAPER"
        and state.get("instrument") == "BTC-USD"
        and _explicit_utc(state.get("started_at"))
        and state.get("internal_position_state") in ("FLAT", "LONG")
        and state.get("broker_position_observed") in (0, 1, "UNKNOWN")
        and state.get("reconciliation_status") in (
            "PENDING_BROKER_OBSERVATION", "RECONCILED", "POSITION_MISMATCH")
        and all(isinstance(state.get(name), list) for name in arrays)
    )


def initialize_paper_session(state_path, output, session_id, mode, started_at):
    """Create or recover one explicit PAPER session; never overwrite another state."""
    state_path = Path(state_path)
    if mode != "PAPER":
        raise ValueError("Session mode must be explicit PAPER")
    if not _valid_paper_session_id(session_id):
        raise ValueError("A valid explicit PAPER session identity is required")
    if not _explicit_utc(started_at):
        raise ValueError("A canonical UTC session start is required")
    if state_path.exists():
        state = json.loads(state_path.read_bytes())
        if not paper_session_state_is_valid(state):
            raise ValueError("Existing state is not an operational PAPER session")
        if state["session_id"] != session_id or state["started_at"] != started_at:
            raise ValueError("Existing PAPER session cannot be reused for another identity")
        created = False
    else:
        state = {
            "session_id": session_id,
            "session_identity": _paper_session_identity(session_id, started_at),
            "schema_version": PAPER_SESSION_SCHEMA_VERSION,
            "mode": "PAPER", "instrument": "BTC-USD", "started_at": started_at,
            "internal_position_state": "FLAT",
            "broker_position_observed": "UNKNOWN",
            "reconciliation_status": "PENDING_BROKER_OBSERVATION",
            "warmup_observations": [], "processed_observations": [],
            "decisions": [], "executions": [], "pending_actions": [],
            "broker_submissions": [],
        }
        if not paper_session_state_is_valid(state):
            raise ValueError("Constructed PAPER session is invalid")
        _atomic_write(state_path, encoded(state))
        created = True
    state_bytes = state_path.read_bytes()
    result = {"status": "PASS", "created": created, "session": state,
              "network_calls": 0, "credentials_used": False,
              "paper_orders_sent": 0, "live_orders_sent": 0,
              "state_sha256": digest(state_bytes)}
    publish(output, {"paper-session.json": encoded(result)})
    return result


def load_paper_session(state_path):
    """Reload one persisted PAPER session and fail closed on incompatible state."""
    state_path = Path(state_path)
    state = json.loads(state_path.read_bytes())
    if not paper_session_state_is_valid(state):
        raise ValueError("Persisted PAPER session is invalid or incompatible")
    return state


def _valid_warmup_observation(observation, started_epoch):
    if not isinstance(observation, dict) or set(observation) != {
            "identity", "instrument", "timestamp", "open", "high", "low",
            "close", "volume"}:
        return False
    timestamp = observation.get("timestamp")
    if not _explicit_utc(timestamp) or observation.get("instrument") != "BTC-USD" \
            or observation.get("identity") != f"BTC-USD|86400|{timestamp}":
        return False
    numbers = [observation.get(name) for name in ("open", "high", "low", "close", "volume")]
    return all(type(value) in (int, float) and math.isfinite(value) for value in numbers) \
        and observation["volume"] >= 0 \
        and observation["low"] <= min(observation["open"], observation["close"]) \
        and observation["high"] >= max(observation["open"], observation["close"]) \
        and epoch(timestamp) % 86400 == 0 \
        and epoch(timestamp) + 86400 <= started_epoch


def load_paper_session_warmup(state_path, output, observations):
    """Persist closed SMA3 warm-up facts without routing them into decisions."""
    state_path = Path(state_path)
    state = load_paper_session(state_path)
    if not isinstance(observations, list) or len(observations) < 3:
        raise ValueError("At least three closed warm-up observations are required")
    started_epoch = epoch(state["started_at"])
    if not all(_valid_warmup_observation(item, started_epoch) for item in observations):
        raise ValueError("Warm-up contains an invalid, unclosed, or post-start observation")
    ordered = sorted(observations, key=lambda item: item["timestamp"])
    identities = [item["identity"] for item in ordered]
    timestamps = [epoch(item["timestamp"]) for item in ordered]
    if len(set(identities)) != len(identities) \
            or any(later - earlier != 86400
                   for earlier, later in zip(timestamps, timestamps[1:])):
        raise ValueError("Warm-up observations must be unique consecutive daily candles")
    existing = state["warmup_observations"]
    if existing:
        if existing != ordered:
            raise ValueError("Persisted warm-up cannot be silently replaced")
        loaded = False
    else:
        if state["processed_observations"] or state["decisions"] \
                or state["executions"] or state["pending_actions"] \
                or state["broker_submissions"]:
            raise ValueError("Warm-up must precede all operational processing")
        state["warmup_observations"] = ordered
        _atomic_write(state_path, encoded(state))
        loaded = True
    state_bytes = state_path.read_bytes()
    result = {"status": "PASS", "loaded": loaded,
              "session_id": state["session_id"],
              "warmup_observations": len(state["warmup_observations"]),
              "processed_operational_observations": len(state["processed_observations"]),
              "retroactive_decisions": len(state["decisions"]),
              "executions": len(state["executions"]),
              "pending_actions": len(state["pending_actions"]),
              "broker_submissions": len(state["broker_submissions"]),
              "network_calls": 0, "credentials_used": False,
              "state_sha256": digest(state_bytes)}
    publish(output, {"paper-warmup.json": encoded(result)})
    return result


def _valid_fixture_observation(observation, processing_epoch, *, accepted_required=False,
                               started_epoch=None):
    fields = {"identity", "instrument", "timestamp", "open", "high", "low",
              "close", "volume"}
    if accepted_required:
        fields.add("accepted_at_utc")
    if not isinstance(observation, dict) or set(observation) != fields:
        return False
    timestamp = observation.get("timestamp")
    if not _explicit_utc(timestamp) or observation.get("instrument") != "BTC-USD" \
            or observation.get("identity") != f"BTC-USD|86400|{timestamp}":
        return False
    numbers = [observation.get(name) for name in ("open", "high", "low", "close", "volume")]
    if not all(type(value) in (int, float) and math.isfinite(value) for value in numbers):
        return False
    observation_epoch = epoch(timestamp)
    valid = (
        observation["volume"] >= 0
        and observation["low"] <= min(observation["open"], observation["close"])
        and observation["high"] >= max(observation["open"], observation["close"])
        and observation_epoch % 86400 == 0
        and observation_epoch + 86400 <= processing_epoch
    )
    if accepted_required:
        accepted_at = observation.get("accepted_at_utc")
        valid = valid and _explicit_utc(accepted_at) and started_epoch is not None \
            and epoch(accepted_at) > started_epoch
    return valid


def _validate_paper_session_fixture(fixture, state, processing_instant_utc):
    if not isinstance(fixture, dict) or set(fixture) != {
            "schema_version", "session_id", "session_identity", "mode",
            "instrument", "frequency_seconds", "processing_instant_utc",
            "warmup_observations", "operational_observations"}:
        raise ValueError("Historical PAPER fixture envelope is invalid")
    if (fixture["schema_version"] != PAPER_SESSION_SCHEMA_VERSION
            or fixture["session_id"] != state["session_id"]
            or fixture["session_identity"] != state["session_identity"]
            or fixture["mode"] != "PAPER"
            or fixture["instrument"] != "BTC-USD"
            or fixture["frequency_seconds"] != 86400
            or fixture["processing_instant_utc"] != processing_instant_utc):
        raise ValueError("Historical PAPER fixture identity is incompatible")
    if not _explicit_utc(processing_instant_utc):
        raise ValueError("An explicit UTC processing instant is required")
    warmup = fixture["warmup_observations"]
    operational = fixture["operational_observations"]
    if not isinstance(warmup, list) or len(warmup) != 3 \
            or not isinstance(operational, list) or len(operational) != 1:
        raise ValueError("Historical PAPER fixture must contain 3 warm-up and 1 operational observations")
    observations = warmup + operational
    processing_epoch = epoch(processing_instant_utc)
    started_epoch = epoch(state["started_at"])
    if (not all(_valid_fixture_observation(item, processing_epoch) for item in warmup)
            or not _valid_fixture_observation(
                operational[0], processing_epoch, accepted_required=True,
                started_epoch=started_epoch)):
        raise ValueError("Historical PAPER fixture contains an invalid or open candle")
    timestamps = [epoch(item["timestamp"]) for item in observations]
    if len(set(timestamps)) != 4 or any(later - earlier != 86400
                                        for earlier, later in zip(timestamps, timestamps[1:])):
        raise ValueError("Historical PAPER fixture observations must be unique and chronological")
    if any(epoch(item["timestamp"]) + 86400 > epoch(state["started_at"])
           for item in warmup):
        raise ValueError("Warm-up observation must close before the PAPER session starts")
    return observations


def prepare_paper_session_fixture(state_path, fixture_path, output, observations,
                                  processing_instant_utc):
    """Persist three warm-up candles and one separate pending operational candle."""
    state_path = Path(state_path)
    fixture_path = Path(fixture_path)
    state = load_paper_session(state_path)
    if not _explicit_utc(processing_instant_utc):
        raise ValueError("An explicit UTC processing instant is required")
    if not isinstance(observations, list) or len(observations) != 4:
        raise ValueError("Exactly four historical observations are required")
    candidate = {
        "schema_version": PAPER_SESSION_SCHEMA_VERSION,
        "session_id": state["session_id"],
        "session_identity": state["session_identity"],
        "mode": "PAPER", "instrument": "BTC-USD", "frequency_seconds": 86400,
        "processing_instant_utc": processing_instant_utc,
        "warmup_observations": observations[:3],
        "operational_observations": observations[3:],
    }
    _validate_paper_session_fixture(candidate, state, processing_instant_utc)
    if not state["warmup_observations"]:
        load_paper_session_warmup(state_path, output.with_name(output.name + "-warmup"),
                                  observations[:3])
    state = load_paper_session(state_path)
    if fixture_path.exists():
        existing = json.loads(fixture_path.read_bytes())
        _validate_paper_session_fixture(existing, state, processing_instant_utc)
        if existing != candidate:
            raise ValueError("Persisted historical PAPER fixture cannot be silently replaced")
        created = False
    else:
        _atomic_write(fixture_path, encoded(candidate))
        created = True
    fixture_bytes = fixture_path.read_bytes()
    state = load_paper_session(state_path)
    result = {"status": "PASS", "created": created,
              "session_id": state["session_id"],
              "warmup_observations": len(candidate["warmup_observations"]),
              "operational_input_observations": len(candidate["operational_observations"]),
              "processed_operational_observations": len(state["processed_observations"]),
              "decisions": len(state["decisions"]), "executions": len(state["executions"]),
              "pending_actions": len(state["pending_actions"]),
              "processing_instant_utc": processing_instant_utc,
              "network_calls": 0, "credentials_used": False,
              "state_sha256": digest(state_path.read_bytes()),
              "fixture_sha256": digest(fixture_bytes)}
    publish(output, {"paper-session-fixture.json": encoded(result)})
    return result


def load_paper_session_fixture(state_path, fixture_path):
    """Reload and validate the isolated historical PAPER input without processing it."""
    state = load_paper_session(state_path)
    fixture = json.loads(Path(fixture_path).read_bytes())
    observations = _validate_paper_session_fixture(
        fixture, state, fixture.get("processing_instant_utc"))
    if state["warmup_observations"] != observations[:3]:
        raise ValueError("Persisted warm-up does not match the historical PAPER fixture")
    if state["processed_observations"] or state["decisions"] \
            or state["executions"] or state["pending_actions"]:
        raise ValueError("Historical PAPER fixture was already processed")
    return fixture


def validate_paper_session_operational_observation(state_path, fixture_path,
                                                   acceptance_path, output):
    """Accept one eligible operational candle without creating a decision."""
    state_path = Path(state_path)
    fixture_path = Path(fixture_path)
    acceptance_path = Path(acceptance_path)
    output = Path(output)
    state = load_paper_session(state_path)
    fixture = load_paper_session_fixture(state_path, fixture_path)
    processing_instant = fixture.get("processing_instant_utc")
    if not _explicit_utc(processing_instant):
        raise ValueError("An explicit UTC processing instant is required")
    operational = fixture["operational_observations"]
    if len(operational) != 1:
        raise ValueError("Exactly one operational observation is required")
    observation = operational[0]
    processing_epoch = epoch(processing_instant)
    started_epoch = epoch(state["started_at"])
    if not _valid_fixture_observation(observation, processing_epoch,
                                      accepted_required=True,
                                      started_epoch=started_epoch):
        raise ValueError("Operational observation is not temporally eligible")
    interval_start = observation["timestamp"]
    interval_end = iso(epoch(interval_start) + 86400)
    acceptance = {
        "schema_version": PAPER_SESSION_SCHEMA_VERSION,
        "session_id": state["session_id"],
        "session_identity": state["session_identity"],
        "observation_identity": observation["identity"],
        "processing_instant_utc": processing_instant,
        "interval_start_utc": interval_start,
        "interval_end_utc": interval_end,
        "accepted_at_utc": observation["accepted_at_utc"],
        "observation_accepted": True,
        "lookahead": "NOT_USED",
    }
    if acceptance_path.exists():
        existing = json.loads(acceptance_path.read_bytes())
        if existing != acceptance:
            raise ValueError("Persisted operational acceptance cannot be silently replaced")
        created = False
    else:
        _atomic_write(acceptance_path, encoded(acceptance))
        created = True
    state = load_paper_session(state_path)
    if state["decisions"] or state["executions"] or state["pending_actions"]:
        raise ValueError("Operational eligibility must not create effects")
    acceptance_bytes = acceptance_path.read_bytes()
    result = {"status": "PASS", "created": created,
              "session_id": state["session_id"],
              "observation_accepted": True, "lookahead": "NOT_USED",
              "processing_instant_utc": processing_instant,
              "interval_start_utc": interval_start,
              "interval_end_utc": interval_end,
              "accepted_at_utc": observation["accepted_at_utc"],
              "decisions": len(state["decisions"]),
              "executions": len(state["executions"]),
              "pending_actions": len(state["pending_actions"]),
              "proposals": 0,
              "network_calls": 0, "credentials_used": False,
              "acceptance_sha256": digest(acceptance_bytes)}
    publish(output, {"paper-operational-acceptance.json": encoded(result)})
    return result


def compose_paper_session_sma3(state_path, fixture_path, acceptance_path,
                               indicator_path, output):
    """Compose one causal SMA3 indicator without creating a decision."""
    state_path = Path(state_path)
    fixture_path = Path(fixture_path)
    acceptance_path = Path(acceptance_path)
    indicator_path = Path(indicator_path)
    output = Path(output)
    state = load_paper_session(state_path)
    fixture = load_paper_session_fixture(state_path, fixture_path)
    acceptance = json.loads(acceptance_path.read_bytes())
    if (acceptance.get("session_id") != state["session_id"]
            or acceptance.get("session_identity") != state["session_identity"]
            or acceptance.get("observation_accepted") is not True
            or acceptance.get("lookahead") != "NOT_USED"):
        raise ValueError("Operational observation was not accepted without lookahead")
    if acceptance.get("processing_instant_utc") != fixture.get("processing_instant_utc"):
        raise ValueError("Acceptance and fixture processing instants differ")
    operational = fixture["operational_observations"]
    if len(operational) != 1 or acceptance.get("observation_identity") != operational[0]["identity"]:
        raise ValueError("Accepted operational observation does not match fixture")
    processing_epoch = epoch(fixture["processing_instant_utc"])
    if not _valid_fixture_observation(
            operational[0], processing_epoch, accepted_required=True,
            started_epoch=epoch(state["started_at"])):
        raise ValueError("Operational observation is not eligible for SMA3")
    warmup = fixture["warmup_observations"]
    if state["warmup_observations"] != warmup:
        raise ValueError("Persisted warm-up does not match the fixture")
    combined = warmup + operational
    operational_index = len(warmup)
    if operational_index < 2:
        raise ValueError("SMA3 requires three available closed observations")
    inputs = combined[operational_index - 2:operational_index + 1]
    sma_close_3 = math.fsum(item["close"] / 3 for item in inputs)
    indicator = {
        "schema_version": PAPER_SESSION_SCHEMA_VERSION,
        "session_id": state["session_id"],
        "session_identity": state["session_identity"],
        "instrument": "BTC-USD",
        "frequency_seconds": 86400,
        "strategy": "SMA3_LONG_ONLY",
        "parameters": {"window": 3, "source": "close", "formula": "mean(close[t-2:t+1])"},
        "observation_identity": operational[0]["identity"],
        "input_observation_identities": [item["identity"] for item in inputs],
        "processing_instant_utc": fixture["processing_instant_utc"],
        "sma_close_3": sma_close_3,
        "lookahead": "NOT_USED",
    }
    if indicator_path.exists():
        existing = json.loads(indicator_path.read_bytes())
        if existing != indicator:
            raise ValueError("Persisted SMA3 indicator cannot be silently replaced")
        created = False
    else:
        _atomic_write(indicator_path, encoded(indicator))
        created = True
    state = load_paper_session(state_path)
    if state["decisions"] or state["executions"] or state["pending_actions"]:
        raise ValueError("SMA3 composition must not create operational effects")
    indicator_bytes = indicator_path.read_bytes()
    result = {"status": "PASS", "created": created,
              "session_id": state["session_id"],
              "warmup_observations": len(warmup),
              "operational_observations": len(operational),
              "observation_identity": operational[0]["identity"],
              "input_observation_identities": indicator["input_observation_identities"],
              "processing_instant_utc": indicator["processing_instant_utc"],
              "sma_close_3": sma_close_3, "lookahead": "NOT_USED",
              "decisions": len(state["decisions"]),
              "executions": len(state["executions"]),
              "pending_actions": len(state["pending_actions"]),
              "proposals": 0, "network_calls": 0, "credentials_used": False,
              "indicator_sha256": digest(indicator_bytes)}
    publish(output, {"paper-sma3-indicator.json": encoded(result)})
    return result


def _valid_operational_observation(observation, started_epoch, processed_epoch):
    if not isinstance(observation, dict) or set(observation) != {
            "identity", "instrument", "timestamp", "open", "high", "low",
            "close", "volume"}:
        return False
    timestamp = observation.get("timestamp")
    if not _explicit_utc(timestamp) or observation.get("instrument") != "BTC-USD" \
            or observation.get("identity") != f"BTC-USD|86400|{timestamp}":
        return False
    numbers = [observation.get(name) for name in ("open", "high", "low", "close", "volume")]
    if not all(type(value) in (int, float) and math.isfinite(value) for value in numbers):
        return False
    observation_epoch = epoch(timestamp)
    return (
        observation["volume"] >= 0
        and observation["low"] <= min(observation["open"], observation["close"])
        and observation["high"] >= max(observation["open"], observation["close"])
        and observation_epoch % 86400 == 0
        and observation_epoch > started_epoch
        and observation_epoch + 86400 <= processed_epoch
    )


def _paper_session_running_position(decisions):
    position = 0
    for decision in decisions:
        if decision.get("target_position") is not None:
            position = decision["target_position"]
    return position


def process_paper_session_observation(state_path, output, observation, processed_at):
    """Route one closed, post-started_at BTC-USD candle to a causal SMA3 PAPER decision."""
    state_path = Path(state_path)
    state = load_paper_session(state_path)
    if not _explicit_utc(processed_at):
        raise ValueError("An explicit UTC processing instant is required")
    started_epoch = epoch(state["started_at"])
    processed_epoch = epoch(processed_at)
    if processed_epoch < started_epoch:
        raise ValueError("Processing instant cannot precede session start")
    if not _valid_operational_observation(observation, started_epoch, processed_epoch):
        raise ValueError("Observation is not a valid closed post-start operational candle")

    identity = observation["identity"]
    if identity in {item["identity"] for item in state["warmup_observations"]}:
        raise ValueError("Observation identity collides with an existing warm-up candle")

    existing_processed = {item["identity"]: item for item in state["processed_observations"]}
    if identity in existing_processed:
        if existing_processed[identity] != observation:
            raise ValueError("Persisted operational observation cannot be silently altered")
        existing_decision = next(
            (item for item in state["decisions"] if item["observation_identity"] == identity),
            None)
        state_bytes = state_path.read_bytes()
        result = {"status": "PASS", "new_observation": False, "new_decision": False,
                  "decision": existing_decision,
                  "processed_observations": len(state["processed_observations"]),
                  "decision_count": len(state["decisions"]),
                  "state_sha256": digest(state_bytes)}
        publish(output, {"paper-decision.json": encoded(result)})
        return result

    combined = sorted(state["warmup_observations"] + state["processed_observations"]
                       + [observation], key=lambda row: row["timestamp"])
    index = next(i for i, row in enumerate(combined) if row["identity"] == identity)
    closes = [row["close"] for row in combined[max(0, index - 2):index + 1]]
    sma = math.fsum(closes) / 3.0 if len(closes) == 3 else None
    previous_position = _paper_session_running_position(state["decisions"])
    if sma is None:
        decision, target_position = "NO_DECISION", None
    else:
        target_position = 1 if observation["close"] > sma else 0
        decision = ("ENTER" if target_position == 1 and previous_position == 0 else
                    "EXIT" if target_position == 0 and previous_position == 1 else "HOLD")

    record = {"identity": f"SMA3|{identity}", "session_id": state["session_id"],
              "observation_identity": identity, "instrument": "BTC-USD",
              "frequency_seconds": 86400, "timestamp": observation["timestamp"],
              "strategy": "SMA3_LONG_ONLY",
              "parameters": {"window": 3, "entry": "close > sma_close_3",
                             "exit": "close <= sma_close_3"},
              "close": observation["close"], "sma_close_3": sma,
              "warm_up": sma is None, "decision": decision,
              "target_position": target_position,
              "previous_position": previous_position}

    state["processed_observations"] = state["processed_observations"] + [observation]
    state["decisions"] = state["decisions"] + [record]
    _atomic_write(state_path, encoded(state))
    state_bytes = state_path.read_bytes()
    result = {"status": "PASS", "new_observation": True, "new_decision": True,
              "decision": record,
              "processed_observations": len(state["processed_observations"]),
              "decision_count": len(state["decisions"]),
              "state_sha256": digest(state_bytes)}
    publish(output, {"paper-decision.json": encoded(result)})
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


def _proposal_decimal(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite decimal value")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{name} must be a finite decimal value") from None
    if not number.is_finite():
        raise ValueError(f"{name} must be a finite decimal value")
    return number


def _proposal_number(value):
    return format(value, "f")


PHASE4_RISK_CONTRACT_VERSION = "1"


def phase4_risk_contract_identity(config):
    """Derive the identity of the approved contract from its fixed terms."""
    fields = (
        "broker", "account_target", "instrument", "max_capital_usd",
        "max_exposure_usd", "risk_budget_usd", "order_type",
        "manual_approval_required", "risk_contract_version",
    )
    content = {key: config.get(key) for key in fields}
    return f"PHASE4_RISK_CONTRACT|{digest(encoded(content))}"


def _proposal_identity(proposal):
    mutable = {"proposal_identity", "status", "approval_record"}
    content = {key: value for key, value in proposal.items() if key not in mutable}
    return digest(encoded(content))


def _explicit_utc(value):
    if not isinstance(value, str) or not value or value != value.strip() \
            or not value.endswith("Z"):
        return False
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return instant.utcoffset() == timezone.utc.utcoffset(instant) \
        and instant.isoformat().replace("+00:00", "Z") == value


def proposal_is_manually_approved(proposal):
    """Fail closed unless one exact proposal has durable valid approval evidence."""
    if not isinstance(proposal, dict) or proposal.get("status") != "APPROVED" \
            or proposal.get("transmission_status") != "NOT_SENT":
        return False
    record = proposal.get("approval_record")
    if not isinstance(record, dict) or record.get("decision") != "APPROVED":
        return False
    try:
        identity = _proposal_identity(proposal)
        record_content = {key: value for key, value in record.items() if key != "identity"}
        record_identity = f"MANUAL_APPROVAL|{digest(encoded(record_content))}"
    except (TypeError, ValueError):
        return False
    return (
        proposal.get("proposal_identity") == identity
        and record.get("identity") == record_identity
        and record.get("proposal_id") == proposal.get("identity")
        and record.get("proposal_identity") == identity
        and record.get("decision_identity") == proposal.get("decision_identity")
        and record.get("risk") == proposal.get("risk")
        and isinstance(record.get("actor"), str)
        and bool(record["actor"])
        and record["actor"] == record["actor"].strip()
        and _explicit_utc(record.get("approved_at"))
        and record.get("rejected_at") is None
    )


def prepare_real_order_proposal(state_path, output, decision_identity, risk_config):
    """Persist one bounded Alpaca proposal without any broker transport."""
    state_path = Path(state_path)
    state = json.loads(state_path.read_bytes())
    decisions = [item for item in state.get("decisions", [])
                 if item.get("identity") == decision_identity]
    if len(decisions) != 1:
        raise ValueError("Exactly one persisted SMA3 decision is required")
    decision = decisions[0]
    existing = list(state.get("real_order_proposals", []))
    matching = [item for item in existing
                if item.get("decision_identity") == decision_identity]
    if matching:
        if len(matching) != 1:
            raise ValueError("Duplicate persisted proposals for one decision")
        result = {"proposal_created": False, "proposal": matching[0],
                  "proposal_count": len(existing), "broker_request_sent": False,
                  "real_market_effect": "NONE", "state_sha256": digest(state_path.read_bytes())}
        publish(output, {"order-proposal.json": encoded(result)})
        return result

    allowed = {
        "broker", "account_target", "instrument", "max_capital_usd",
        "max_exposure_usd", "risk_budget_usd", "proposed_notional_usd",
        "operational_risk_usd", "order_type", "limit_price",
        "manual_approval_required", "risk_contract_identity",
        "risk_contract_version",
    }
    reasons = []
    if not isinstance(risk_config, dict) or set(risk_config) != allowed:
        raise ValueError("Risk configuration fields are incomplete or unsupported")

    try:
        max_capital = _proposal_decimal(risk_config["max_capital_usd"], "max_capital_usd")
        max_exposure = _proposal_decimal(risk_config["max_exposure_usd"], "max_exposure_usd")
        risk_budget = _proposal_decimal(risk_config["risk_budget_usd"], "risk_budget_usd")
        notional = _proposal_decimal(risk_config["proposed_notional_usd"], "proposed_notional_usd")
        operational_risk = _proposal_decimal(risk_config["operational_risk_usd"], "operational_risk_usd")
        limit_price = _proposal_decimal(risk_config["limit_price"], "limit_price")
    except ValueError as error:
        reasons.append(str(error))
        max_capital = max_exposure = risk_budget = notional = operational_risk = limit_price = Decimal(0)

    if risk_config["broker"] != "Alpaca":
        reasons.append("broker must be Alpaca")
    if risk_config["account_target"] != "real/live":
        reasons.append("account target must be declared as real/live")
    if state.get("instrument") != "BTC-USD" or decision.get("instrument") != "BTC-USD" \
            or risk_config["instrument"] != "BTC-USD":
        reasons.append("instrument must be BTC-USD")
    if decision.get("strategy") != "SMA3_LONG_ONLY":
        reasons.append("decision must originate from SMA3_LONG_ONLY")
    action = decision.get("decision")
    position = state.get("virtual_position", 0)
    if action not in ("ENTER", "EXIT"):
        reasons.append("decision action must be ENTER or EXIT")
    elif (action, position) not in (("ENTER", 0), ("EXIT", 1)):
        reasons.append("decision is incompatible with the current virtual position")
    if max_capital != Decimal("200"):
        reasons.append("approved available capital must be exactly 200 USD")
    if max_exposure <= 0 or max_exposure > Decimal("50"):
        reasons.append("maximum exposure exceeds the approved 50 USD ceiling")
    if notional <= 0 or notional > max_exposure or notional > max_capital:
        reasons.append("proposed exposure exceeds the configured limit")
    if risk_budget != Decimal("5"):
        reasons.append("operational risk budget must be exactly 5 USD")
    if operational_risk < 0 or operational_risk > risk_budget:
        reasons.append("operational risk exceeds the 5 USD threshold")
    if risk_config["order_type"] != "limit" or limit_price <= 0:
        reasons.append("a positive limit price and limit order type are required")
    if risk_config["manual_approval_required"] is not True:
        reasons.append("manual approval must be explicitly required")
    if risk_config["risk_contract_version"] != PHASE4_RISK_CONTRACT_VERSION:
        reasons.append("risk contract version is not the approved version")
    if risk_config["risk_contract_identity"] != phase4_risk_contract_identity(risk_config):
        reasons.append("risk contract identity does not match its terms")

    side = {"ENTER": "BUY", "EXIT": "SELL"}.get(action)
    proposal = {
        "identity": f"REAL_ORDER_PROPOSAL|{decision_identity}",
        "decision_identity": decision_identity,
        "decision_timestamp": decision.get("timestamp"),
        "created_at": decision.get("timestamp"),
        "action": action,
        "instrument": "BTC-USD",
        "side": side,
        "order_type": "LIMIT",
        "notional_usd": _proposal_number(notional),
        "quantity": _proposal_number(notional / limit_price) if limit_price > 0 else None,
        "limit_price": _proposal_number(limit_price) if limit_price > 0 else None,
        "exposure_usd": _proposal_number(notional),
        "risk": {
            "broker": "Alpaca", "account_target": "real/live",
            "max_capital_usd": _proposal_number(max_capital),
            "max_exposure_usd": _proposal_number(max_exposure),
            "risk_budget_usd": _proposal_number(risk_budget),
            "operational_risk_usd": _proposal_number(operational_risk),
            "risk_budget_is_guaranteed_maximum_loss": False,
            "risk_contract_identity": risk_config["risk_contract_identity"],
            "risk_contract_version": risk_config["risk_contract_version"],
        },
        "status": "REJECTED" if reasons else "PENDING_MANUAL_APPROVAL",
        "transmission_status": "NOT_SENT",
        "rejection_reasons": reasons,
    }
    proposal["proposal_identity"] = _proposal_identity(proposal)
    existing.append(proposal)
    state["real_order_proposals"] = existing
    state_bytes = encoded(state)
    _atomic_write(state_path, state_bytes)
    result = {"proposal_created": True, "proposal": proposal,
              "proposal_count": len(existing), "broker_request_sent": False,
              "real_market_effect": "NONE", "state_sha256": digest(state_bytes)}
    publish(output, {"order-proposal.json": encoded(result)})
    return result


def _paper_position_value(internal_position_state):
    return {"FLAT": 0, "LONG": 1}.get(internal_position_state)


def prepare_paper_session_proposal(session_path, proposals_path, output,
                                   decision_identity, risk_config):
    """Persist one bounded proposal from a PAPER session decision, storage kept
    separate from the session's own strict state.json contract."""
    session_path = Path(session_path)
    proposals_path = Path(proposals_path)
    session = load_paper_session(session_path)
    decisions = [item for item in session["decisions"]
                 if item.get("identity") == decision_identity]
    if len(decisions) != 1:
        raise ValueError("Exactly one persisted SMA3 decision is required")
    decision = decisions[0]

    registry = {"proposals": []}
    if proposals_path.exists():
        registry = json.loads(proposals_path.read_bytes())
        if not isinstance(registry, dict) or not isinstance(registry.get("proposals"), list):
            raise ValueError("Persisted proposal registry is invalid")
    existing = list(registry["proposals"])
    matching = [item for item in existing
                if item.get("decision_identity") == decision_identity]
    if matching:
        if len(matching) != 1:
            raise ValueError("Duplicate persisted proposals for one decision")
        result = {"proposal_created": False, "proposal": matching[0],
                  "proposal_count": len(existing), "broker_request_sent": False,
                  "real_market_effect": "NONE",
                  "session_sha256": digest(session_path.read_bytes()),
                  "proposals_sha256": digest(proposals_path.read_bytes())}
        publish(output, {"paper-order-proposal.json": encoded(result)})
        return result

    allowed = {
        "broker", "account_target", "instrument", "max_capital_usd",
        "max_exposure_usd", "risk_budget_usd", "proposed_notional_usd",
        "operational_risk_usd", "order_type", "limit_price",
        "manual_approval_required", "risk_contract_identity",
        "risk_contract_version",
    }
    if not isinstance(risk_config, dict) or set(risk_config) != allowed:
        raise ValueError("Risk configuration fields are incomplete or unsupported")

    reasons = []
    try:
        max_capital = _proposal_decimal(risk_config["max_capital_usd"], "max_capital_usd")
        max_exposure = _proposal_decimal(risk_config["max_exposure_usd"], "max_exposure_usd")
        risk_budget = _proposal_decimal(risk_config["risk_budget_usd"], "risk_budget_usd")
        notional = _proposal_decimal(risk_config["proposed_notional_usd"], "proposed_notional_usd")
        operational_risk = _proposal_decimal(risk_config["operational_risk_usd"], "operational_risk_usd")
        limit_price = _proposal_decimal(risk_config["limit_price"], "limit_price")
    except ValueError as error:
        reasons.append(str(error))
        max_capital = max_exposure = risk_budget = notional = operational_risk = limit_price = Decimal(0)

    if risk_config["broker"] != "Alpaca":
        reasons.append("broker must be Alpaca")
    if risk_config["account_target"] != "real/live":
        reasons.append("account target must be declared as real/live")
    if session.get("instrument") != "BTC-USD" or decision.get("instrument") != "BTC-USD" \
            or risk_config["instrument"] != "BTC-USD":
        reasons.append("instrument must be BTC-USD")
    if decision.get("strategy") != "SMA3_LONG_ONLY":
        reasons.append("decision must originate from SMA3_LONG_ONLY")
    action = decision.get("decision")
    position = _paper_position_value(session.get("internal_position_state"))
    if action not in ("ENTER", "EXIT"):
        reasons.append("decision action must be ENTER or EXIT")
    elif position is None or (action, position) not in (("ENTER", 0), ("EXIT", 1)):
        reasons.append("decision is incompatible with the current PAPER session position")
    if max_capital != Decimal("200"):
        reasons.append("approved available capital must be exactly 200 USD")
    if max_exposure <= 0 or max_exposure > Decimal("50"):
        reasons.append("maximum exposure exceeds the approved 50 USD ceiling")
    if notional <= 0 or notional > max_exposure or notional > max_capital:
        reasons.append("proposed exposure exceeds the configured limit")
    if risk_budget != Decimal("5"):
        reasons.append("operational risk budget must be exactly 5 USD")
    if operational_risk < 0 or operational_risk > risk_budget:
        reasons.append("operational risk exceeds the 5 USD threshold")
    if risk_config["order_type"] != "limit" or limit_price <= 0:
        reasons.append("a positive limit price and limit order type are required")
    if risk_config["manual_approval_required"] is not True:
        reasons.append("manual approval must be explicitly required")
    if risk_config["risk_contract_version"] != PHASE4_RISK_CONTRACT_VERSION:
        reasons.append("risk contract version is not the approved version")
    if risk_config["risk_contract_identity"] != phase4_risk_contract_identity(risk_config):
        reasons.append("risk contract identity does not match its terms")

    side = {"ENTER": "BUY", "EXIT": "SELL"}.get(action)
    proposal = {
        "identity": f"PAPER_SESSION_ORDER_PROPOSAL|{decision_identity}",
        "session_id": session["session_id"],
        "decision_identity": decision_identity,
        "observation_identity": decision.get("observation_identity"),
        "decision_timestamp": decision.get("timestamp"),
        "created_at": decision.get("timestamp"),
        "action": action,
        "instrument": "BTC-USD",
        "side": side,
        "order_type": "LIMIT",
        "notional_usd": _proposal_number(notional),
        "quantity": _proposal_number(notional / limit_price) if limit_price > 0 else None,
        "limit_price": _proposal_number(limit_price) if limit_price > 0 else None,
        "exposure_usd": _proposal_number(notional),
        "risk": {
            "broker": "Alpaca", "account_target": "real/live",
            "max_capital_usd": _proposal_number(max_capital),
            "max_exposure_usd": _proposal_number(max_exposure),
            "risk_budget_usd": _proposal_number(risk_budget),
            "operational_risk_usd": _proposal_number(operational_risk),
            "risk_budget_is_guaranteed_maximum_loss": False,
            "risk_contract_identity": risk_config["risk_contract_identity"],
            "risk_contract_version": risk_config["risk_contract_version"],
        },
        "status": "REJECTED" if reasons else "PENDING_MANUAL_APPROVAL",
        "transmission_status": "NOT_SENT",
        "rejection_reasons": reasons,
    }
    proposal["proposal_identity"] = _proposal_identity(proposal)
    existing.append(proposal)
    registry["proposals"] = existing
    _atomic_write(proposals_path, encoded(registry))
    result = {"proposal_created": True, "proposal": proposal,
              "proposal_count": len(existing), "broker_request_sent": False,
              "real_market_effect": "NONE",
              "session_sha256": digest(session_path.read_bytes()),
              "proposals_sha256": digest(proposals_path.read_bytes())}
    publish(output, {"paper-order-proposal.json": encoded(result)})
    return result


def record_manual_approval(state_path, output, proposal_id, proposal_identity,
                           actor, approved_at, decision):
    """Persist one explicit manual decision; perform no subsequent action."""
    state_path = Path(state_path)
    state = json.loads(state_path.read_bytes())
    proposals = state.get("real_order_proposals", [])
    matching = [item for item in proposals if item.get("identity") == proposal_id]
    if len(matching) != 1:
        raise ValueError("Exactly one persisted order proposal is required")
    proposal = matching[0]
    if proposal.get("proposal_identity") != proposal_identity \
            or _proposal_identity(proposal) != proposal_identity:
        raise ValueError("Approval does not match the exact persisted proposal")
    if not isinstance(actor, str) or not actor or actor != actor.strip():
        raise ValueError("An explicit non-empty approval actor is required")
    if not _explicit_utc(approved_at):
        raise ValueError("An explicit canonical UTC approval timestamp is required")
    if decision not in ("APPROVED", "REJECTED"):
        raise ValueError("Approval decision must be APPROVED or REJECTED")
    if proposal.get("transmission_status") != "NOT_SENT":
        raise ValueError("Only a proposal that has not been sent may be reviewed")

    record_content = {
        "proposal_id": proposal_id,
        "proposal_identity": proposal_identity,
        "decision_identity": proposal.get("decision_identity"),
        "decision": decision,
        "actor": actor,
        "approved_at": approved_at if decision == "APPROVED" else None,
        "rejected_at": approved_at if decision == "REJECTED" else None,
        "risk": proposal.get("risk"),
    }
    record = {"identity": f"MANUAL_APPROVAL|{digest(encoded(record_content))}",
              **record_content}
    previous = proposal.get("approval_record")
    if previous is not None:
        if previous != record or proposal.get("status") != decision:
            raise ValueError("A reviewed proposal cannot be changed or reviewed again")
    else:
        if proposal.get("status") != "PENDING_MANUAL_APPROVAL":
            raise ValueError("Only a pending proposal may be approved or rejected")
        proposal["status"] = decision
        proposal["approval_record"] = record
        state_bytes = encoded(state)
        _atomic_write(state_path, state_bytes)

    state_bytes = state_path.read_bytes()
    result = {
        "proposal": proposal,
        "approval_record": record,
        "authorized": proposal_is_manually_approved(proposal),
        "broker_request_sent": False,
        "real_market_effect": "NONE",
        "state_sha256": digest(state_bytes),
    }
    publish(output, {"manual-approval.json": encoded(result)})
    return result


def _revalidation_decimal(value):
    try:
        return _proposal_decimal(value, "risk revalidation value")
    except ValueError:
        return None


def revalidate_approved_proposal(state_path, output, proposal_id,
                                 proposal_identity, risk_config, revalidated_at):
    """Persist a final local risk verdict without broker access or submission."""
    state_path = Path(state_path)
    state = json.loads(state_path.read_bytes())
    checks = {
        "proposal_present": False,
        "proposal_integrity": False,
        "approved_state": False,
        "approval_binding": False,
        "instrument": False,
        "action": False,
        "quantity": False,
        "limit_price": False,
        "exposure": False,
        "capital": False,
        "risk_budget_threshold": False,
        "risk_contract_identity": False,
        "risk_contract_version": False,
        "current_contract_match": False,
        "explicit_utc_timestamp": _explicit_utc(revalidated_at),
    }
    reasons = []
    proposals = state.get("real_order_proposals", [])
    matching = ([item for item in proposals if isinstance(item, dict)
                 and item.get("identity") == proposal_id]
                if isinstance(proposals, list) else [])
    proposal = matching[0] if len(matching) == 1 else None
    checks["proposal_present"] = proposal is not None
    if proposal is None:
        reasons.append("exactly one persisted proposal is required")

    if not isinstance(risk_config, dict):
        risk_config = {}
        reasons.append("current risk contract is absent or ambiguous")
    required_contract_fields = {
        "broker", "account_target", "instrument", "max_capital_usd",
        "max_exposure_usd", "risk_budget_usd", "proposed_notional_usd",
        "operational_risk_usd", "order_type", "limit_price",
        "manual_approval_required", "risk_contract_identity",
        "risk_contract_version",
    }
    if set(risk_config) != required_contract_fields:
        reasons.append("current risk contract is incomplete or ambiguous")

    current_identity = risk_config.get("risk_contract_identity")
    current_version = risk_config.get("risk_contract_version")
    checks["risk_contract_version"] = current_version == PHASE4_RISK_CONTRACT_VERSION
    try:
        checks["risk_contract_identity"] = (
            isinstance(current_identity, str)
            and current_identity == phase4_risk_contract_identity(risk_config)
        )
    except (TypeError, ValueError):
        checks["risk_contract_identity"] = False

    approval_identity = None
    if proposal is not None:
        try:
            checks["proposal_integrity"] = (
                proposal.get("proposal_identity") == proposal_identity
                and _proposal_identity(proposal) == proposal_identity
            )
        except (TypeError, ValueError):
            checks["proposal_integrity"] = False
        checks["approved_state"] = proposal.get("status") == "APPROVED"
        checks["approval_binding"] = proposal_is_manually_approved(proposal)
        approval = proposal.get("approval_record")
        if isinstance(approval, dict):
            approval_identity = approval.get("identity")
        checks["instrument"] = proposal.get("instrument") == "BTC-USD"
        checks["action"] = (
            (proposal.get("action"), proposal.get("side"))
            in (("ENTER", "BUY"), ("EXIT", "SELL"))
        )
        quantity = _revalidation_decimal(proposal.get("quantity"))
        price = _revalidation_decimal(proposal.get("limit_price"))
        exposure = _revalidation_decimal(proposal.get("exposure_usd"))
        notional = _revalidation_decimal(proposal.get("notional_usd"))
        checks["quantity"] = quantity is not None and quantity > 0
        checks["limit_price"] = (
            proposal.get("order_type") == "LIMIT" and price is not None and price > 0
        )
        checks["exposure"] = (
            exposure is not None and notional is not None
            and exposure > 0 and exposure <= Decimal("50")
            and exposure == notional
            and quantity is not None and price is not None
            and quantity * price == exposure
        )
        proposal_risk = proposal.get("risk")
        if isinstance(proposal_risk, dict):
            capital = _revalidation_decimal(proposal_risk.get("max_capital_usd"))
            max_exposure = _revalidation_decimal(proposal_risk.get("max_exposure_usd"))
            budget = _revalidation_decimal(proposal_risk.get("risk_budget_usd"))
            operational_risk = _revalidation_decimal(proposal_risk.get("operational_risk_usd"))
            current_capital = _revalidation_decimal(risk_config.get("max_capital_usd"))
            current_max_exposure = _revalidation_decimal(risk_config.get("max_exposure_usd"))
            current_budget = _revalidation_decimal(risk_config.get("risk_budget_usd"))
            current_operational_risk = _revalidation_decimal(
                risk_config.get("operational_risk_usd"))
            checks["capital"] = (
                capital == Decimal("200") and current_capital == Decimal("200")
            )
            checks["exposure"] = checks["exposure"] and (
                max_exposure is not None and current_max_exposure is not None
                and Decimal(0) < max_exposure <= Decimal("50")
                and Decimal(0) < current_max_exposure <= Decimal("50")
                and exposure <= max_exposure and exposure <= current_max_exposure
            )
            checks["risk_budget_threshold"] = (
                budget == Decimal("5")
                and operational_risk is not None
                and Decimal(0) <= operational_risk <= budget
                and current_budget == Decimal("5")
                and current_operational_risk is not None
                and Decimal(0) <= current_operational_risk <= current_budget
                and proposal_risk.get("risk_budget_is_guaranteed_maximum_loss") is False
            )
            checks["current_contract_match"] = (
                proposal_risk.get("risk_contract_identity") == current_identity
                and proposal_risk.get("risk_contract_version") == current_version
                and proposal_risk.get("broker") == risk_config.get("broker") == "Alpaca"
                and proposal_risk.get("account_target")
                == risk_config.get("account_target") == "real/live"
                and proposal.get("instrument") == risk_config.get("instrument")
                and proposal.get("limit_price") == str(risk_config.get("limit_price"))
                and proposal.get("notional_usd")
                == str(risk_config.get("proposed_notional_usd"))
                and proposal_risk.get("max_capital_usd")
                == str(risk_config.get("max_capital_usd"))
                and proposal_risk.get("max_exposure_usd")
                == str(risk_config.get("max_exposure_usd"))
                and proposal_risk.get("risk_budget_usd")
                == str(risk_config.get("risk_budget_usd"))
                and proposal_risk.get("operational_risk_usd")
                == str(risk_config.get("operational_risk_usd"))
                and risk_config.get("manual_approval_required") is True
                and risk_config.get("order_type") == "limit"
            )

    for name, passed in checks.items():
        if not passed:
            reasons.append(f"{name} check failed")
    result_status = "READY_FOR_SUBMISSION" if not reasons else "BLOCKED_RISK_REVALIDATION"
    record_content = {
        "proposal_id": proposal_id,
        "proposal_identity": proposal_identity,
        "approval_identity": approval_identity,
        "risk_contract_identity": current_identity,
        "risk_contract_version": current_version,
        "result": result_status,
        "checks": checks,
        "blocking_reasons": reasons,
        "revalidated_at": revalidated_at,
        "risk_budget_semantics": "OPERATIONAL_THRESHOLD_NOT_GUARANTEED_MAXIMUM_LOSS",
        "broker_request_sent": False,
    }
    record = {"identity": f"RISK_REVALIDATION|{digest(encoded(record_content))}",
              **record_content}
    persisted = state.get("risk_revalidations", [])
    if not isinstance(persisted, list):
        raise ValueError("Persisted risk revalidations must be a list")
    identical = [item for item in persisted if item.get("identity") == record["identity"]]
    if identical:
        if len(identical) != 1 or identical[0] != record:
            raise ValueError("Conflicting persisted risk revalidation")
    else:
        persisted.append(record)
        state["risk_revalidations"] = persisted
        _atomic_write(state_path, encoded(state))
    state_bytes = state_path.read_bytes()
    result = {"revalidation": record, "status": result_status,
              "broker_request_sent": False, "real_market_effect": "NONE",
              "state_sha256": digest(state_bytes)}
    publish(output, {"risk-revalidation.json": encoded(result)})
    return result


def _risk_revalidation_is_ready(record, proposal):
    if not isinstance(record, dict) or record.get("result") != "READY_FOR_SUBMISSION" \
            or record.get("broker_request_sent") is not False:
        return False
    content = {key: value for key, value in record.items() if key != "identity"}
    checks = record.get("checks")
    return (
        record.get("identity") == f"RISK_REVALIDATION|{digest(encoded(content))}"
        and isinstance(checks, dict) and bool(checks)
        and all(value is True for value in checks.values())
        and record.get("proposal_id") == proposal.get("identity")
        and record.get("proposal_identity") == proposal.get("proposal_identity")
        and record.get("approval_identity")
        == proposal.get("approval_record", {}).get("identity")
    )


def prepared_alpaca_request_is_valid(state, request):
    """Fail closed unless a NOT_SENT request still matches its complete provenance."""
    if not isinstance(state, dict) or not isinstance(request, dict) \
            or request.get("state") != "NOT_SENT" \
            or request.get("broker_request_sent") is not False \
            or request.get("credentials_present") is not False \
            or request.get("target_environment") not in ("PAPER", "LIVE"):
        return False
    proposals = state.get("real_order_proposals", [])
    revalidations = state.get("risk_revalidations", [])
    proposal = next((item for item in proposals if isinstance(item, dict)
                     and item.get("identity") == request.get("proposal_id")), None)
    revalidation = next((item for item in revalidations if isinstance(item, dict)
                         and item.get("identity") == request.get("revalidation_identity")), None)
    if proposal is None or revalidation is None \
            or not proposal_is_manually_approved(proposal) \
            or not _risk_revalidation_is_ready(revalidation, proposal):
        return False
    payload = request.get("payload")
    if not isinstance(payload, dict) or request.get("payload_sha256") != digest(encoded(payload)):
        return False
    identity_content = {
        "proposal_identity": request.get("proposal_identity"),
        "approval_identity": request.get("approval_identity"),
        "revalidation_identity": request.get("revalidation_identity"),
        "target_environment": request.get("target_environment"),
        "payload_sha256": request.get("payload_sha256"),
        "prepared_at": request.get("prepared_at"),
    }
    return (
        request.get("identity")
        == f"ALPACA_REQUEST|{digest(encoded(identity_content))}"
        and request.get("proposal_identity") == proposal.get("proposal_identity")
        and request.get("approval_identity") == proposal["approval_record"]["identity"]
        and request.get("risk_contract_identity")
        == revalidation.get("risk_contract_identity")
        and request.get("risk_contract_version")
        == revalidation.get("risk_contract_version")
        and request.get("decision_identity") == proposal.get("decision_identity")
        and request.get("instrument") == payload.get("instrument") == "BTC-USD"
        and request.get("action") == payload.get("action")
        and request.get("side") == payload.get("side")
        and request.get("quantity") == payload.get("quantity")
        and request.get("limit_price") == payload.get("limit_price")
        and request.get("order_type") == payload.get("order_type") == "LIMIT"
        and request.get("exposure_usd") == payload.get("exposure_usd")
    )


def prepare_alpaca_request(state_path, output, proposal_id, proposal_identity,
                           revalidation_identity, risk_config,
                           target_environment, prepared_at):
    """Persist one Alpaca-shaped NOT_SENT request without a transport capability."""
    state_path = Path(state_path)
    state = json.loads(state_path.read_bytes())
    reasons = []
    proposals = state.get("real_order_proposals", [])
    revalidations = state.get("risk_revalidations", [])
    proposal_matches = ([item for item in proposals if isinstance(item, dict)
                         and item.get("identity") == proposal_id]
                        if isinstance(proposals, list) else [])
    revalidation_matches = ([item for item in revalidations if isinstance(item, dict)
                             and item.get("identity") == revalidation_identity]
                            if isinstance(revalidations, list) else [])
    proposal = proposal_matches[0] if len(proposal_matches) == 1 else None
    revalidation = revalidation_matches[0] if len(revalidation_matches) == 1 else None
    if proposal is None:
        reasons.append("exactly one persisted proposal is required")
    if revalidation is None:
        reasons.append("exactly one persisted risk revalidation is required")
    if proposal is not None:
        try:
            integrity = (proposal.get("proposal_identity") == proposal_identity
                         and _proposal_identity(proposal) == proposal_identity)
        except (TypeError, ValueError):
            integrity = False
        if not integrity or not proposal_is_manually_approved(proposal):
            reasons.append("proposal integrity or approval is invalid")
    if proposal is not None and (revalidation is None
                                 or not _risk_revalidation_is_ready(revalidation, proposal)):
        reasons.append("risk revalidation is absent, blocked, or invalid")
    if not isinstance(risk_config, dict):
        risk_config = {}
        reasons.append("current risk contract is absent")
    expected_fields = {
        "broker", "account_target", "instrument", "max_capital_usd",
        "max_exposure_usd", "risk_budget_usd", "proposed_notional_usd",
        "operational_risk_usd", "order_type", "limit_price",
        "manual_approval_required", "risk_contract_identity",
        "risk_contract_version",
    }
    if set(risk_config) != expected_fields:
        reasons.append("current risk contract is incomplete or ambiguous")
    contract_identity = risk_config.get("risk_contract_identity")
    contract_version = risk_config.get("risk_contract_version")
    try:
        contract_valid = (
            contract_version == PHASE4_RISK_CONTRACT_VERSION
            and contract_identity == phase4_risk_contract_identity(risk_config)
        )
    except (TypeError, ValueError):
        contract_valid = False
    if not contract_valid:
        reasons.append("current risk contract identity or version is invalid")

    quantity = price = exposure = capital = maximum = budget = operational_risk = None
    if proposal is not None:
        quantity = _revalidation_decimal(proposal.get("quantity"))
        price = _revalidation_decimal(proposal.get("limit_price"))
        exposure = _revalidation_decimal(proposal.get("exposure_usd"))
        proposal_risk = proposal.get("risk", {})
        if isinstance(proposal_risk, dict):
            capital = _revalidation_decimal(proposal_risk.get("max_capital_usd"))
            maximum = _revalidation_decimal(proposal_risk.get("max_exposure_usd"))
            budget = _revalidation_decimal(proposal_risk.get("risk_budget_usd"))
            operational_risk = _revalidation_decimal(proposal_risk.get("operational_risk_usd"))
        valid_terms = (
            proposal.get("instrument") == risk_config.get("instrument") == "BTC-USD"
            and (proposal.get("action"), proposal.get("side"))
            in (("ENTER", "BUY"), ("EXIT", "SELL"))
            and quantity is not None and quantity > 0
            and price is not None and price > 0
            and proposal.get("order_type") == "LIMIT"
            and exposure is not None and Decimal(0) < exposure <= Decimal("50")
            and quantity * price == exposure
            and capital == Decimal("200")
            and maximum is not None and Decimal(0) < maximum <= Decimal("50")
            and exposure <= maximum
            and budget == Decimal("5")
            and operational_risk is not None and Decimal(0) <= operational_risk <= budget
            and proposal_risk.get("risk_budget_is_guaranteed_maximum_loss") is False
            and proposal_risk.get("risk_contract_identity") == contract_identity
            and proposal_risk.get("risk_contract_version") == contract_version
            and proposal_risk.get("broker") == risk_config.get("broker") == "Alpaca"
            and proposal_risk.get("account_target")
            == risk_config.get("account_target") == "real/live"
            and proposal.get("limit_price") == str(risk_config.get("limit_price"))
            and proposal.get("notional_usd")
            == str(risk_config.get("proposed_notional_usd"))
            and proposal_risk.get("max_capital_usd")
            == str(risk_config.get("max_capital_usd"))
            and proposal_risk.get("max_exposure_usd")
            == str(risk_config.get("max_exposure_usd"))
            and proposal_risk.get("risk_budget_usd")
            == str(risk_config.get("risk_budget_usd"))
            and proposal_risk.get("operational_risk_usd")
            == str(risk_config.get("operational_risk_usd"))
            and risk_config.get("manual_approval_required") is True
            and risk_config.get("order_type") == "limit"
            and revalidation is not None
            and revalidation.get("risk_contract_identity") == contract_identity
            and revalidation.get("risk_contract_version") == contract_version
        )
        if not valid_terms:
            reasons.append("proposal no longer satisfies the current risk contract")

    environment_valid = target_environment in ("PAPER", "LIVE")
    environment_reasons = [] if environment_valid else ["target environment must be explicit PAPER or LIVE"]
    previous_requests = state.get("alpaca_prepared_requests", [])
    if not isinstance(previous_requests, list):
        raise ValueError("Persisted Alpaca prepared requests must be a list")
    same_provenance = [item for item in previous_requests if isinstance(item, dict)
                       and item.get("proposal_id") == proposal_id
                       and item.get("revalidation_identity") == revalidation_identity]
    if same_provenance:
        if len(same_provenance) != 1:
            raise ValueError("Duplicate prepared requests for one revalidation")
        existing = same_provenance[0]
        if target_environment != existing.get("target_environment"):
            environment_reasons.append(
                "changing environment requires new approval and risk revalidation")
        elif not reasons and not environment_reasons \
                and prepared_alpaca_request_is_valid(state, existing):
            preparations = state.get("alpaca_request_preparations", [])
            preparation = next(
                (item for item in preparations if isinstance(item, dict)
                 and item.get("request_identity") == existing.get("identity")
                 and item.get("status") == "NOT_SENT"), None)
            if preparation is None:
                raise ValueError("Prepared request lacks its persistent preparation record")
            result = {"status": "NOT_SENT", "request_prepared": True,
                      "prepared_request": existing, "preparation": preparation,
                      "broker_request_sent": False,
                      "real_market_effect": "NONE", "state_sha256": digest(state_path.read_bytes())}
            publish(output, {"alpaca-request.json": encoded(result)})
            return result

    blocked_status = ("BLOCKED_RISK_REVALIDATION" if reasons
                      else "BLOCKED_ENVIRONMENT" if environment_reasons else None)
    request = None
    if blocked_status is None:
        payload = {
            "instrument": proposal["instrument"],
            "action": proposal["action"],
            "side": proposal["side"].lower(),
            "quantity": proposal["quantity"],
            "limit_price": proposal["limit_price"],
            "order_type": "LIMIT",
            "exposure_usd": proposal["exposure_usd"],
        }
        payload_hash = digest(encoded(payload))
        identity_content = {
            "proposal_identity": proposal_identity,
            "approval_identity": proposal["approval_record"]["identity"],
            "revalidation_identity": revalidation_identity,
            "target_environment": target_environment,
            "payload_sha256": payload_hash,
            "prepared_at": prepared_at,
        }
        request = {
            "identity": f"ALPACA_REQUEST|{digest(encoded(identity_content))}",
            "proposal_id": proposal_id,
            "proposal_identity": proposal_identity,
            "approval_identity": proposal["approval_record"]["identity"],
            "revalidation_identity": revalidation_identity,
            "decision_identity": proposal["decision_identity"],
            "target_environment": target_environment,
            "instrument": proposal["instrument"],
            "action": proposal["action"],
            "side": proposal["side"].lower(),
            "quantity": proposal["quantity"],
            "limit_price": proposal["limit_price"],
            "order_type": "LIMIT",
            "exposure_usd": proposal["exposure_usd"],
            "risk_contract_identity": contract_identity,
            "risk_contract_version": contract_version,
            "state": "NOT_SENT",
            "prepared_at": prepared_at,
            "payload": payload,
            "payload_sha256": payload_hash,
            "credentials_present": False,
            "broker_request_sent": False,
        }
        if not _explicit_utc(prepared_at) or not prepared_alpaca_request_is_valid(
                {**state, "alpaca_prepared_requests": previous_requests}, request):
            reasons.append("prepared request timestamp or provenance is invalid")
            blocked_status = "BLOCKED_RISK_REVALIDATION"
            request = None

    preparation_content = {
        "proposal_id": proposal_id,
        "proposal_identity": proposal_identity,
        "revalidation_identity": revalidation_identity,
        "target_environment": target_environment,
        "prepared_at": prepared_at,
        "status": blocked_status or "NOT_SENT",
        "blocking_reasons": reasons + environment_reasons,
        "request_identity": request.get("identity") if request else None,
        "broker_request_sent": False,
    }
    preparation = {
        "identity": f"ALPACA_REQUEST_PREPARATION|{digest(encoded(preparation_content))}",
        **preparation_content,
    }
    attempts = state.get("alpaca_request_preparations", [])
    if not isinstance(attempts, list):
        raise ValueError("Persisted Alpaca request preparations must be a list")
    if not any(item.get("identity") == preparation["identity"] for item in attempts):
        attempts.append(preparation)
        state["alpaca_request_preparations"] = attempts
    if request is not None:
        previous_requests.append(request)
        state["alpaca_prepared_requests"] = previous_requests
    _atomic_write(state_path, encoded(state))
    state_bytes = state_path.read_bytes()
    result = {"status": preparation["status"], "request_prepared": request is not None,
              "prepared_request": request, "preparation": preparation,
              "broker_request_sent": False, "real_market_effect": "NONE",
              "state_sha256": digest(state_bytes)}
    publish(output, {"alpaca-request.json": encoded(result)})
    return result


def execute_local_transport_attempt(state_path, output, request_id, attempted_at,
                                    mock_transport):
    """Invoke one injected local double only after durable attempt registration."""
    state_path = Path(state_path)
    state = json.loads(state_path.read_bytes())
    requests = state.get("alpaca_prepared_requests", [])
    matching = ([item for item in requests if isinstance(item, dict)
                 and item.get("identity") == request_id]
                if isinstance(requests, list) else [])
    request = matching[0] if len(matching) == 1 else None
    if request is None or not prepared_alpaca_request_is_valid(state, request) \
            or not _explicit_utc(attempted_at) or not callable(mock_transport):
        result = {"status": "BLOCKED_REQUEST", "attempt": None,
                  "broker_request_sent": False, "real_market_effect": "NONE",
                  "state_sha256": digest(state_path.read_bytes())}
        publish(output, {"local-transport-attempt.json": encoded(result)})
        return result

    request_hash = digest(encoded(request))
    attempt_content = {
        "request_id": request_id,
        "request_hash": request_hash,
        "payload_hash": request["payload_sha256"],
        "target_environment": request["target_environment"],
        "risk_contract_version": request["risk_contract_version"],
    }
    attempt_id = f"LOCAL_TRANSPORT_ATTEMPT|{digest(encoded(attempt_content))}"
    attempts = state.get("local_transport_attempts", [])
    if not isinstance(attempts, list):
        raise ValueError("Persisted local transport attempts must be a list")
    existing = [item for item in attempts if isinstance(item, dict)
                and item.get("identity") == attempt_id]
    if existing:
        if len(existing) != 1:
            raise ValueError("Duplicate local transport attempts for one request")
        attempt = existing[0]
        if attempt.get("request_hash") != request_hash:
            raise ValueError("Persisted attempt does not match the current request")
        if attempt.get("status") == "ATTEMPT_RECORDED":
            attempt["status"] = "UNKNOWN"
            attempt["result_persisted"] = True
            attempt["failure_class"] = "RECOVERED_AMBIGUOUS_ATTEMPT"
            _atomic_write(state_path, encoded(state))
        state_bytes = state_path.read_bytes()
        result = {"status": attempt["status"], "attempt": attempt,
                  "broker_request_sent": False, "real_market_effect": "NONE",
                  "state_sha256": digest(state_bytes)}
        publish(output, {"local-transport-attempt.json": encoded(result)})
        return result

    attempt = {
        "identity": attempt_id,
        "proposal_id": request["proposal_id"],
        "approval_id": request["approval_identity"],
        "risk_revalidation_id": request["revalidation_identity"],
        "request_id": request_id,
        "request_hash": request_hash,
        "payload_hash": request["payload_sha256"],
        "target_environment": request["target_environment"],
        "risk_contract_version": request["risk_contract_version"],
        "attempted_at": attempted_at,
        "status": "ATTEMPT_RECORDED",
        "mock_transport_invoked": False,
        "real_transport_used": False,
        "broker_request_sent": False,
        "mock_response": None,
        "failure_class": None,
        "result_persisted": False,
    }
    attempts.append(attempt)
    state["local_transport_attempts"] = attempts
    _atomic_write(state_path, encoded(state))

    try:
        response = mock_transport(request)
        attempt["mock_transport_invoked"] = True
        if response in ("MOCK_ACCEPTED", "MOCK_REJECTED"):
            attempt["status"] = response
            attempt["mock_response"] = response
        elif response in ("MOCK_ERROR", "MOCK_TIMEOUT"):
            attempt["status"] = "UNKNOWN"
            attempt["mock_response"] = response
            attempt["failure_class"] = response
        else:
            attempt["status"] = "UNKNOWN"
            attempt["failure_class"] = "INCOMPLETE_MOCK_RESPONSE"
    except BaseException as error:
        attempt["mock_transport_invoked"] = True
        attempt["status"] = "UNKNOWN"
        attempt["failure_class"] = type(error).__name__
    attempt["result_persisted"] = True
    _atomic_write(state_path, encoded(state))
    state_bytes = state_path.read_bytes()
    result = {"status": attempt["status"], "attempt": attempt,
              "broker_request_sent": False, "real_market_effect": "NONE",
              "state_sha256": digest(state_bytes)}
    publish(output, {"local-transport-attempt.json": encoded(result)})
    return result


def _paper_mock_text(value):
    if value is None:
        return None
    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) > 126 for char in value):
        return None
    return value[:256]


def _paper_unknown_response(attempt_id, timestamp, category):
    content = {
        "attempt_id": attempt_id,
        "environment": "PAPER",
        "timestamp": timestamp,
        "result": "UNKNOWN",
        "category": category,
        "external_id": None,
        "message": None,
        "determinable": False,
        "simulated": True,
    }
    return {"identity": f"PAPER_MOCK_RESPONSE|{digest(encoded(content))}", **content}


def execute_paper_adapter_attempt(state_path, output, request_id, attempted_at,
                                  paper_adapter):
    """Exercise a PAPER-only adapter double; no broker implementation exists here."""
    state_path = Path(state_path)
    state = json.loads(state_path.read_bytes())
    requests = state.get("alpaca_prepared_requests", [])
    matching = ([item for item in requests if isinstance(item, dict)
                 and item.get("identity") == request_id]
                if isinstance(requests, list) else [])
    request = matching[0] if len(matching) == 1 else None
    if request is None or not prepared_alpaca_request_is_valid(state, request) \
            or request.get("target_environment") != "PAPER" \
            or not _explicit_utc(attempted_at) or not callable(paper_adapter):
        result = {"status": "BLOCKED_REQUEST", "attempt": None,
                  "normalized_response": None, "paper_request_sent": False,
                  "live_request_sent": False, "real_market_effect": "NONE",
                  "state_sha256": digest(state_path.read_bytes())}
        publish(output, {"paper-adapter-attempt.json": encoded(result)})
        return result

    request_hash = digest(encoded(request))
    identity_content = {
        "request_id": request_id,
        "request_hash": request_hash,
        "payload_hash": request["payload_sha256"],
        "target_environment": request["target_environment"],
        "risk_contract_version": request["risk_contract_version"],
    }
    attempt_id = f"PAPER_ADAPTER_ATTEMPT|{digest(encoded(identity_content))}"
    attempts = state.get("paper_adapter_attempts", [])
    if not isinstance(attempts, list):
        raise ValueError("Persisted PAPER adapter attempts must be a list")
    existing = [item for item in attempts if isinstance(item, dict)
                and item.get("identity") == attempt_id]
    if existing:
        if len(existing) != 1:
            raise ValueError("Duplicate PAPER adapter attempts for one request")
        attempt = existing[0]
        if attempt.get("request_hash") != request_hash:
            raise ValueError("PAPER attempt does not match the current request")
        if attempt.get("status") in ("ATTEMPT_RECORDED", "SEND_ATTEMPTED"):
            response = _paper_unknown_response(
                attempt_id, attempted_at, "RECOVERED_AMBIGUOUS_ATTEMPT")
            attempt["status"] = "UNKNOWN"
            attempt["normalized_response"] = response
            attempt["result_persisted"] = True
            attempt["state_history"].append(
                {"status": "UNKNOWN", "timestamp": attempted_at})
            _atomic_write(state_path, encoded(state))
        state_bytes = state_path.read_bytes()
        result = {"status": attempt["status"], "attempt": attempt,
                  "normalized_response": attempt.get("normalized_response"),
                  "paper_request_sent": False, "live_request_sent": False,
                  "real_market_effect": "NONE", "state_sha256": digest(state_bytes)}
        publish(output, {"paper-adapter-attempt.json": encoded(result)})
        return result

    attempt = {
        "identity": attempt_id,
        "request_id": request_id,
        "request_hash": request_hash,
        "payload_hash": request["payload_sha256"],
        "proposal_id": request["proposal_id"],
        "approval_id": request["approval_identity"],
        "risk_revalidation_id": request["revalidation_identity"],
        "target_environment": "PAPER",
        "risk_contract_version": request["risk_contract_version"],
        "attempted_at": attempted_at,
        "status": "ATTEMPT_RECORDED",
        "state_history": [{"status": "ATTEMPT_RECORDED", "timestamp": attempted_at}],
        "paper_adapter_invoked": False,
        "real_transport_used": False,
        "paper_request_sent": False,
        "live_request_sent": False,
        "normalized_response": None,
        "result_persisted": False,
    }
    attempts.append(attempt)
    state["paper_adapter_attempts"] = attempts
    _atomic_write(state_path, encoded(state))

    attempt["status"] = "SEND_ATTEMPTED"
    attempt["state_history"].append(
        {"status": "SEND_ATTEMPTED", "timestamp": attempted_at})
    _atomic_write(state_path, encoded(state))
    try:
        candidate = paper_adapter(request)
        attempt["paper_adapter_invoked"] = True
        valid = (
            isinstance(candidate, dict)
            and set(candidate) == {
                "result", "category", "external_id", "message",
                "determinable", "simulated",
            }
            and candidate.get("result") in ("ACCEPTED", "REJECTED")
            and isinstance(candidate.get("category"), str)
            and bool(candidate["category"])
            and candidate["category"] == candidate["category"].strip()
            and (candidate.get("external_id") is None
                 or (isinstance(candidate["external_id"], str)
                     and bool(candidate["external_id"])
                     and candidate["external_id"] == candidate["external_id"].strip()))
            and (candidate.get("message") is None
                 or _paper_mock_text(candidate.get("message")) is not None)
            and candidate.get("determinable") is True
            and candidate.get("simulated") is True
        )
        if valid:
            content = {
                "attempt_id": attempt_id,
                "environment": "PAPER",
                "timestamp": attempted_at,
                "result": candidate["result"],
                "category": candidate["category"],
                "external_id": candidate["external_id"],
                "message": _paper_mock_text(candidate["message"]),
                "determinable": True,
                "simulated": True,
            }
            response = {
                "identity": f"PAPER_MOCK_RESPONSE|{digest(encoded(content))}",
                **content,
            }
        else:
            response = _paper_unknown_response(
                attempt_id, attempted_at, "AMBIGUOUS_MOCK_RESPONSE")
    except BaseException as error:
        attempt["paper_adapter_invoked"] = True
        response = _paper_unknown_response(
            attempt_id, attempted_at, type(error).__name__)
    attempt["status"] = response["result"]
    attempt["normalized_response"] = response
    attempt["result_persisted"] = True
    attempt["state_history"].append(
        {"status": response["result"], "timestamp": attempted_at})
    _atomic_write(state_path, encoded(state))
    state_bytes = state_path.read_bytes()
    result = {"status": attempt["status"], "attempt": attempt,
              "normalized_response": response, "paper_request_sent": False,
              "live_request_sent": False, "real_market_effect": "NONE",
              "state_sha256": digest(state_bytes)}
    publish(output, {"paper-adapter-attempt.json": encoded(result)})
    return result


ALPACA_PAPER_HOST = "paper-api.alpaca.markets"
ALPACA_PAPER_BASE_ENDPOINT = f"https://{ALPACA_PAPER_HOST}/v2"


def _paper_order_body(request):
    """Build the only broker order shape authorized by the Phase 4 contract."""
    if request.get("instrument") != "BTC-USD" or request.get("order_type") != "LIMIT":
        raise ValueError("Only a BTC-USD limit order is allowed")
    return {
        "symbol": "BTC/USD",
        "qty": request["quantity"],
        "side": request["side"],
        "type": "limit",
        "time_in_force": "gtc",
        "limit_price": request["limit_price"],
        "client_order_id": "tg-p4-" + digest(request["identity"].encode("utf-8"))[:40],
    }


def alpaca_paper_https_request(method, host, path, body, timeout_seconds,
                               credential_injector):
    """Perform one allowlisted PAPER order API request with in-memory credentials."""
    allowed = (
        method == "POST" and path == "/v2/orders" and isinstance(body, dict)
        or method == "GET" and path.startswith("/v2/orders/") and body is None
        or method == "GET" and path.startswith(
            "/v2/orders:by_client_order_id?client_order_id=tg-p4-") and body is None
        or method == "DELETE" and path.startswith("/v2/orders/") and body is None
        or method == "GET" and path == "/v2/positions/BTC%2FUSD" and body is None
    )
    if host != ALPACA_PAPER_HOST or not allowed or not callable(credential_injector):
        raise ValueError("PAPER order transport received a forbidden target")
    headers = credential_injector({
        "Accept": "application/json",
        "User-Agent": "TramitaGO-Quant-Core-Phase4-Paper/0.1",
    })
    payload = None
    if body is not None:
        payload = json.dumps(body, sort_keys=True, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")
        headers = {**headers, "Content-Type": "application/json"}
    connection = HTTPSConnection(host, timeout=timeout_seconds)
    try:
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        response_body = response.read(1_000_001)
        return {"status_code": response.status,
                "content_type": response.getheader("Content-Type"),
                "location": response.getheader("Location"),
                "body": response_body}
    finally:
        connection.close()


def _paper_json_response(response):
    if not isinstance(response, dict) or set(response) != {
            "status_code", "content_type", "location", "body"}:
        raise ValueError("ambiguous transport response")
    code = response["status_code"]
    if type(code) is not int or response["location"] is not None \
            or not isinstance(response["body"], bytes) \
            or len(response["body"]) > 1_000_000:
        raise ValueError("ambiguous broker response")
    content_type = response["content_type"]
    payload = None
    if response["body"]:
        if not isinstance(content_type, str) or \
                content_type.split(";", 1)[0].strip().lower() != "application/json":
            raise ValueError("broker response is not JSON")
        payload = json.loads(response["body"],
                             parse_constant=_reject_connectivity_json_constant,
                             object_pairs_hook=_unique_connectivity_json_object)
        if not isinstance(payload, dict):
            raise ValueError("broker response is not an object")
    return code, payload


def _safe_broker_text(value, maximum=128):
    if not isinstance(value, str) or not value or len(value) > maximum \
            or any(ord(char) < 32 or ord(char) > 126 for char in value):
        return None
    return value


def _paper_resource_path(value):
    value = _safe_broker_text(value)
    if value is None:
        raise ValueError("invalid broker resource identifier")
    return quote(value, safe="")


def _paper_order_snapshot(payload, expected_client_id=None):
    if not isinstance(payload, dict):
        raise ValueError("missing order response")
    order_id = _safe_broker_text(payload.get("id"))
    client_id = _safe_broker_text(payload.get("client_order_id"))
    status = _safe_broker_text(payload.get("status"), 64)
    if order_id is None or status is None \
            or expected_client_id is not None and client_id != expected_client_id:
        raise ValueError("incomplete order response")
    symbol = payload.get("symbol")
    if symbol not in (None, "BTC/USD", "BTCUSD"):
        raise ValueError("order response instrument mismatch")
    return {
        "broker_order_id": order_id,
        "client_order_id": client_id,
        "status": status,
        "filled_qty": _safe_broker_text(payload.get("filled_qty"), 64),
        "filled_avg_price": _safe_broker_text(payload.get("filled_avg_price"), 64),
        "submitted_at": _safe_broker_text(payload.get("submitted_at"), 64),
        "updated_at": _safe_broker_text(payload.get("updated_at"), 64),
    }


def _paper_attempt_result(state_path, output, attempt, status, *, sent):
    state_bytes = Path(state_path).read_bytes()
    result = {"status": status, "attempt": attempt,
              "paper_request_sent": sent, "live_request_sent": False,
              "real_capital_used": False, "state_sha256": digest(state_bytes)}
    publish(output, {"paper-order-attempt.json": encoded(result)})
    return result


def execute_alpaca_paper_order(state_path, output, request_id, attempted_at,
                               timeout_seconds, credential_provider, transport):
    """Submit one valid request to PAPER at most once, with durable pre-registration."""
    state_path = Path(state_path)
    state = json.loads(state_path.read_bytes())
    attempts = state.get("alpaca_paper_order_attempts", [])
    if not isinstance(attempts, list):
        raise ValueError("Persisted PAPER order attempts must be a list")
    existing = [item for item in attempts if isinstance(item, dict)
                and item.get("request_id") == request_id]
    if existing:
        if len(existing) != 1:
            raise ValueError("Duplicate PAPER order attempts for one request")
        attempt = existing[0]
        if attempt.get("status") in ("ATTEMPT_RECORDED", "SEND_ATTEMPTED"):
            attempt["status"] = "UNKNOWN"
            attempt["error_category"] = "RECOVERED_AMBIGUOUS_ATTEMPT"
            attempt["result_persisted"] = True
            _atomic_write(state_path, encoded(state))
        return _paper_attempt_result(state_path, output, attempt, attempt["status"], sent=False)

    requests = state.get("alpaca_prepared_requests", [])
    matching = ([item for item in requests if isinstance(item, dict)
                 and item.get("identity") == request_id]
                if isinstance(requests, list) else [])
    request = matching[0] if len(matching) == 1 else None
    valid_timeout = type(timeout_seconds) in (int, float) \
        and math.isfinite(timeout_seconds) and timeout_seconds > 0
    if request is None or not prepared_alpaca_request_is_valid(state, request) \
            or request.get("target_environment") != "PAPER" \
            or not _explicit_utc(attempted_at) or not valid_timeout \
            or not callable(credential_provider) or not callable(transport):
        result = {"status": "BLOCKED_REQUEST", "attempt": None,
                  "paper_request_sent": False, "live_request_sent": False,
                  "real_capital_used": False,
                  "state_sha256": digest(state_path.read_bytes())}
        publish(output, {"paper-order-attempt.json": encoded(result)})
        return result
    try:
        injector = credential_provider()
    except BaseException:
        injector = None
    if not callable(injector):
        result = {"status": "BLOCKED_CREDENTIALS", "attempt": None,
                  "paper_request_sent": False, "live_request_sent": False,
                  "real_capital_used": False,
                  "state_sha256": digest(state_path.read_bytes())}
        publish(output, {"paper-order-attempt.json": encoded(result)})
        return result

    order_body = _paper_order_body(request)
    attempt_content = {"request_id": request_id,
                       "request_hash": digest(encoded(request)),
                       "payload_hash": request["payload_sha256"],
                       "client_order_id": order_body["client_order_id"]}
    attempt = {
        "identity": "ALPACA_PAPER_ORDER_ATTEMPT|" + digest(encoded(attempt_content)),
        **attempt_content,
        "proposal_id": request["proposal_id"],
        "approval_id": request["approval_identity"],
        "risk_revalidation_id": request["revalidation_identity"],
        "environment": "PAPER", "endpoint_host": ALPACA_PAPER_HOST,
        "attempted_at": attempted_at, "status": "ATTEMPT_RECORDED",
        "http_status": None, "order": None, "error_category": None,
        "paper_request_sent": False, "live_request_sent": False,
        "credentials_persisted": False, "result_persisted": False,
    }
    attempts.append(attempt)
    state["alpaca_paper_order_attempts"] = attempts
    _atomic_write(state_path, encoded(state))
    attempt["status"] = "SEND_ATTEMPTED"
    _atomic_write(state_path, encoded(state))
    try:
        response = transport("POST", ALPACA_PAPER_HOST, "/v2/orders", order_body,
                             timeout_seconds, injector)
        attempt["paper_request_sent"] = True
        code, payload = _paper_json_response(response)
        attempt["http_status"] = code
        if code in (200, 201):
            attempt["order"] = _paper_order_snapshot(
                payload, order_body["client_order_id"])
            attempt["status"] = "ACCEPTED"
        elif 400 <= code < 500:
            attempt["status"] = "REJECTED"
            attempt["error_category"] = "BROKER_REJECTED"
        else:
            attempt["status"] = "UNKNOWN"
            attempt["error_category"] = "AMBIGUOUS_HTTP_STATUS"
    except BaseException as error:
        attempt["paper_request_sent"] = True
        attempt["status"] = "UNKNOWN"
        attempt["error_category"] = type(error).__name__
    attempt["result_persisted"] = True
    _atomic_write(state_path, encoded(state))
    return _paper_attempt_result(state_path, output, attempt, attempt["status"], sent=True)


def _paper_existing_attempt(state, attempt_id):
    attempts = state.get("alpaca_paper_order_attempts", [])
    matching = ([item for item in attempts if isinstance(item, dict)
                 and item.get("identity") == attempt_id]
                if isinstance(attempts, list) else [])
    attempt = matching[0] if len(matching) == 1 else None
    if attempt is None or attempt.get("environment") != "PAPER" \
            or attempt.get("status") not in ("ACCEPTED", "REJECTED", "UNKNOWN"):
        raise ValueError("A unique persisted PAPER order attempt is required")
    return attempt


def observe_alpaca_paper_order(state_path, output, attempt_id, observed_at,
                               timeout_seconds, credential_provider, transport):
    """Query a known PAPER order without creating or resending an order."""
    state_path = Path(state_path)
    state = json.loads(state_path.read_bytes())
    attempt = _paper_existing_attempt(state, attempt_id)
    order = attempt.get("order")
    if not _explicit_utc(observed_at):
        raise ValueError("An explicit UTC timestamp is required")
    observations = state.get("alpaca_paper_order_observations", [])
    if not isinstance(observations, list):
        raise ValueError("Persisted PAPER order observations must be a list")
    identity_content = {"attempt_id": attempt_id, "observed_at": observed_at}
    observation_id = "ALPACA_PAPER_ORDER_OBSERVATION|" + digest(encoded(identity_content))
    existing = [item for item in observations if item.get("identity") == observation_id]
    if existing:
        observation = existing[0]
    else:
        injector = credential_provider()
        if not callable(injector):
            raise ValueError("PAPER credentials are absent")
        if isinstance(order, dict):
            broker_order_id = order["broker_order_id"]
            path = "/v2/orders/" + _paper_resource_path(broker_order_id)
        else:
            broker_order_id = None
            path = ("/v2/orders:by_client_order_id?client_order_id="
                    + _paper_resource_path(attempt["client_order_id"]))
        response = transport("GET", ALPACA_PAPER_HOST, path, None,
                             timeout_seconds, injector)
        try:
            code, payload = _paper_json_response(response)
            snapshot = _paper_order_snapshot(payload) if code == 200 else None
            status = "OBSERVED" if snapshot else "UNAVAILABLE"
            error = None if snapshot else "ORDER_NOT_AVAILABLE"
        except BaseException as exc:
            code, snapshot, status, error = None, None, "UNKNOWN", type(exc).__name__
        observation = {"identity": observation_id, "attempt_id": attempt_id,
                       "broker_order_id": (snapshot.get("broker_order_id")
                                           if snapshot else broker_order_id),
                       "observed_at": observed_at, "http_status": code,
                       "status": status, "order": snapshot,
                       "error_category": error, "credentials_persisted": False}
        observations.append(observation)
        state["alpaca_paper_order_observations"] = observations
        _atomic_write(state_path, encoded(state))
    result = {"status": observation["status"], "observation": observation,
              "orders_sent": 0, "live_orders_sent": 0,
              "state_sha256": digest(state_path.read_bytes())}
    publish(output, {"paper-order-observation.json": encoded(result)})
    return result


def cancel_alpaca_paper_remainder(state_path, output, attempt_id, cancelled_at,
                                  timeout_seconds, credential_provider, transport):
    """Cancel only a known open/partial PAPER order, once."""
    state_path = Path(state_path)
    state = json.loads(state_path.read_bytes())
    attempt = _paper_existing_attempt(state, attempt_id)
    observations = state.get("alpaca_paper_order_observations", [])
    relevant = [item for item in observations if isinstance(item, dict)
                and item.get("attempt_id") == attempt_id and isinstance(item.get("order"), dict)]
    latest = relevant[-1] if relevant else None
    open_states = {"new", "accepted", "pending_new", "partially_filled"}
    if latest is None or latest["order"].get("status") not in open_states \
            or not _explicit_utc(cancelled_at):
        raise ValueError("A previously observed open or partial PAPER order is required")
    cancellations = state.get("alpaca_paper_order_cancellations", [])
    if not isinstance(cancellations, list):
        raise ValueError("Persisted PAPER cancellations must be a list")
    existing = [item for item in cancellations if item.get("attempt_id") == attempt_id]
    if existing:
        cancellation = existing[0]
    else:
        injector = credential_provider()
        if not callable(injector):
            raise ValueError("PAPER credentials are absent")
        order_id = attempt["order"]["broker_order_id"]
        response = transport("DELETE", ALPACA_PAPER_HOST,
                             "/v2/orders/" + _paper_resource_path(order_id),
                             None, timeout_seconds, injector)
        try:
            code, _ = _paper_json_response(response)
            status = "CANCELLED" if code == 204 else "UNKNOWN"
            error = None if code == 204 else "CANCEL_NOT_CONFIRMED"
        except BaseException as exc:
            code, status, error = None, "UNKNOWN", type(exc).__name__
        cancellation = {
            "identity": "ALPACA_PAPER_CANCELLATION|" + digest(
                encoded({"attempt_id": attempt_id, "cancelled_at": cancelled_at})),
            "attempt_id": attempt_id, "broker_order_id": order_id,
            "cancelled_at": cancelled_at, "http_status": code, "status": status,
            "error_category": error, "quantity_increased": False,
            "new_order_created": False, "credentials_persisted": False,
        }
        cancellations.append(cancellation)
        state["alpaca_paper_order_cancellations"] = cancellations
        _atomic_write(state_path, encoded(state))
    result = {"status": cancellation["status"], "cancellation": cancellation,
              "orders_sent": 0, "live_orders_sent": 0,
              "state_sha256": digest(state_path.read_bytes())}
    publish(output, {"paper-order-cancellation.json": encoded(result)})
    return result


def observe_alpaca_paper_position(state_path, output, attempt_id, observed_at,
                                  timeout_seconds, credential_provider, transport):
    """Query and persist a sanitized BTC/USD PAPER position snapshot."""
    state_path = Path(state_path)
    state = json.loads(state_path.read_bytes())
    _paper_existing_attempt(state, attempt_id)
    if not _explicit_utc(observed_at):
        raise ValueError("An explicit UTC timestamp is required")
    positions = state.get("alpaca_paper_position_observations", [])
    identity = "ALPACA_PAPER_POSITION|" + digest(encoded(
        {"attempt_id": attempt_id, "observed_at": observed_at}))
    existing = [item for item in positions if item.get("identity") == identity]
    if existing:
        observation = existing[0]
    else:
        injector = credential_provider()
        if not callable(injector):
            raise ValueError("PAPER credentials are absent")
        response = transport("GET", ALPACA_PAPER_HOST, "/v2/positions/BTC%2FUSD",
                             None, timeout_seconds, injector)
        try:
            code, payload = _paper_json_response(response)
            if code == 404:
                status, snapshot, error = "UNAVAILABLE", None, "NO_OPEN_POSITION"
            elif code == 200 and isinstance(payload, dict) \
                    and payload.get("symbol") in ("BTC/USD", "BTCUSD"):
                snapshot = {"symbol": "BTC/USD",
                            "qty": _safe_broker_text(payload.get("qty"), 64),
                            "side": _safe_broker_text(payload.get("side"), 16),
                            "market_value": _safe_broker_text(payload.get("market_value"), 64),
                            "avg_entry_price": _safe_broker_text(
                                payload.get("avg_entry_price"), 64)}
                status, error = "OBSERVED", None
            else:
                status, snapshot, error = "UNKNOWN", None, "POSITION_NOT_DETERMINABLE"
        except BaseException as exc:
            code, status, snapshot, error = None, "UNKNOWN", None, type(exc).__name__
        observation = {"identity": identity, "attempt_id": attempt_id,
                       "observed_at": observed_at, "http_status": code,
                       "status": status, "position": snapshot,
                       "error_category": error, "credentials_persisted": False}
        positions.append(observation)
        state["alpaca_paper_position_observations"] = positions
        _atomic_write(state_path, encoded(state))
    result = {"status": observation["status"], "observation": observation,
              "orders_sent": 0, "live_orders_sent": 0,
              "state_sha256": digest(state_path.read_bytes())}
    publish(output, {"paper-position-observation.json": encoded(result)})
    return result


ALPACA_LIVE_HOST = "api.alpaca.markets"
ALPACA_LIVE_BASE_ENDPOINT = f"https://{ALPACA_LIVE_HOST}/v2"
LIVE_REQUEST_MAX_AGE_SECONDS = 900


def _live_order_body(request):
    body = _paper_order_body(request)
    body["client_order_id"] = (
        "tg-p4-live-" + digest(request["identity"].encode("utf-8"))[:36])
    return body


def _live_request_is_fresh(request, attempted_at):
    if not _explicit_utc(attempted_at) or not _explicit_utc(request.get("prepared_at")):
        return False
    age = epoch(attempted_at) - epoch(request["prepared_at"])
    return 0 <= age <= LIVE_REQUEST_MAX_AGE_SECONDS


def alpaca_live_https_request(method, host, path, body, timeout_seconds,
                              credential_injector):
    """Perform one allowlisted LIVE request; PAPER targets are never accepted."""
    allowed = (
        method == "POST" and path == "/v2/orders" and isinstance(body, dict)
        or method == "GET" and path.startswith("/v2/orders/") and body is None
        or method == "GET" and path.startswith(
            "/v2/orders:by_client_order_id?client_order_id=tg-p4-live-") and body is None
        or method == "DELETE" and path.startswith("/v2/orders/") and body is None
        or method == "GET" and path == "/v2/positions/BTC%2FUSD" and body is None
    )
    if host != ALPACA_LIVE_HOST or not allowed or not callable(credential_injector):
        raise ValueError("LIVE transport received a forbidden target")
    headers = credential_injector({
        "Accept": "application/json",
        "User-Agent": "TramitaGO-Quant-Core-Phase4-Live/0.1",
    })
    payload = None
    if body is not None:
        payload = json.dumps(body, sort_keys=True, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")
        headers = {**headers, "Content-Type": "application/json"}
    connection = HTTPSConnection(host, timeout=timeout_seconds)
    try:
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        response_body = response.read(1_000_001)
        return {"status_code": response.status,
                "content_type": response.getheader("Content-Type"),
                "location": response.getheader("Location"),
                "body": response_body}
    finally:
        connection.close()


def _live_attempt_result(state_path, output, attempt, status, *, sent):
    state_bytes = Path(state_path).read_bytes()
    result = {"status": status, "attempt": attempt,
              "live_request_sent": sent, "paper_request_sent": False,
              "real_capital_used_during_implementation": False,
              "state_sha256": digest(state_bytes)}
    publish(output, {"live-order-attempt.json": encoded(result)})
    return result


def execute_alpaca_live_order(state_path, output, request_id, attempted_at,
                              timeout_seconds, credential_provider, transport):
    """Submit one current, approved LIVE request at most once."""
    state_path = Path(state_path)
    state = json.loads(state_path.read_bytes())
    attempts = state.get("alpaca_live_order_attempts", [])
    if not isinstance(attempts, list):
        raise ValueError("Persisted LIVE order attempts must be a list")
    existing = [item for item in attempts if isinstance(item, dict)
                and item.get("request_id") == request_id]
    if existing:
        if len(existing) != 1:
            raise ValueError("Duplicate LIVE order attempts for one request")
        attempt = existing[0]
        if attempt.get("state") in ("ATTEMPT_RECORDED", "SEND_ATTEMPTED"):
            attempt["state"] = "UNKNOWN"
            attempt["result"] = "UNKNOWN"
            attempt["error_category"] = "RECOVERED_AMBIGUOUS_ATTEMPT"
            attempt["reconciliation_status"] = "REQUIRED"
            attempt["result_persisted"] = True
            _atomic_write(state_path, encoded(state))
        return _live_attempt_result(
            state_path, output, attempt, attempt["result"], sent=False)

    requests = state.get("alpaca_prepared_requests", [])
    matching = ([item for item in requests if isinstance(item, dict)
                 and item.get("identity") == request_id]
                if isinstance(requests, list) else [])
    request = matching[0] if len(matching) == 1 else None
    valid_timeout = type(timeout_seconds) in (int, float) \
        and math.isfinite(timeout_seconds) and timeout_seconds > 0
    valid_request = (
        request is not None
        and prepared_alpaca_request_is_valid(state, request)
        and request.get("target_environment") == "LIVE"
        and _live_request_is_fresh(request, attempted_at)
        and valid_timeout and callable(credential_provider) and callable(transport)
    )
    if not valid_request:
        result = {"status": "BLOCKED_REQUEST", "attempt": None,
                  "live_request_sent": False, "paper_request_sent": False,
                  "real_capital_used_during_implementation": False,
                  "state_sha256": digest(state_path.read_bytes())}
        publish(output, {"live-order-attempt.json": encoded(result)})
        return result
    try:
        injector = credential_provider()
    except BaseException:
        injector = None
    if not callable(injector):
        result = {"status": "BLOCKED_CREDENTIALS", "attempt": None,
                  "live_request_sent": False, "paper_request_sent": False,
                  "real_capital_used_during_implementation": False,
                  "state_sha256": digest(state_path.read_bytes())}
        publish(output, {"live-order-attempt.json": encoded(result)})
        return result

    order_body = _live_order_body(request)
    request_hash = digest(encoded(request))
    attempt_content = {"request_id": request_id, "request_hash": request_hash,
                       "payload_hash": request["payload_sha256"],
                       "client_order_id": order_body["client_order_id"]}
    attempt = {
        "identity": "ALPACA_LIVE_ORDER_ATTEMPT|" + digest(encoded(attempt_content)),
        **attempt_content,
        "decision_id": request["decision_identity"],
        "proposal_id": request["proposal_id"],
        "approval_id": request["approval_identity"],
        "risk_revalidation_id": request["revalidation_identity"],
        "risk_contract_identity": request["risk_contract_identity"],
        "risk_contract_version": request["risk_contract_version"],
        "environment": "LIVE", "endpoint_host": ALPACA_LIVE_HOST,
        "created_at": attempted_at, "state": "ATTEMPT_RECORDED",
        "result": None, "http_status": None, "order": None,
        "error_category": None, "reconciliation_status": "NOT_STARTED",
        "live_request_sent": False, "paper_request_sent": False,
        "credentials_persisted": False, "result_persisted": False,
    }
    attempts.append(attempt)
    state["alpaca_live_order_attempts"] = attempts
    _atomic_write(state_path, encoded(state))
    attempt["state"] = "SEND_ATTEMPTED"
    _atomic_write(state_path, encoded(state))
    try:
        response = transport("POST", ALPACA_LIVE_HOST, "/v2/orders", order_body,
                             timeout_seconds, injector)
        attempt["live_request_sent"] = True
        code, payload = _paper_json_response(response)
        attempt["http_status"] = code
        if code in (200, 201):
            attempt["order"] = _paper_order_snapshot(
                payload, order_body["client_order_id"])
            attempt["state"] = attempt["result"] = "ACCEPTED"
            attempt["reconciliation_status"] = "ORDER_OBSERVATION_REQUIRED"
        elif 400 <= code < 500:
            attempt["state"] = attempt["result"] = "REJECTED"
            attempt["error_category"] = "BROKER_REJECTED"
            attempt["reconciliation_status"] = "BROKER_REJECTION_RECORDED"
        else:
            attempt["state"] = attempt["result"] = "UNKNOWN"
            attempt["error_category"] = "AMBIGUOUS_HTTP_STATUS"
            attempt["reconciliation_status"] = "REQUIRED"
    except BaseException as error:
        attempt["live_request_sent"] = True
        attempt["state"] = attempt["result"] = "UNKNOWN"
        attempt["error_category"] = type(error).__name__
        attempt["reconciliation_status"] = "REQUIRED"
    attempt["result_persisted"] = True
    _atomic_write(state_path, encoded(state))
    return _live_attempt_result(
        state_path, output, attempt, attempt["result"], sent=True)


def _live_existing_attempt(state, attempt_id):
    attempts = state.get("alpaca_live_order_attempts", [])
    matching = ([item for item in attempts if isinstance(item, dict)
                 and item.get("identity") == attempt_id]
                if isinstance(attempts, list) else [])
    attempt = matching[0] if len(matching) == 1 else None
    if attempt is None or attempt.get("environment") != "LIVE" \
            or attempt.get("result") not in ("ACCEPTED", "REJECTED", "UNKNOWN"):
        raise ValueError("A unique persisted LIVE order attempt is required")
    return attempt


def observe_alpaca_live_order(state_path, output, attempt_id, observed_at,
                              timeout_seconds, credential_provider, transport):
    """Query one known LIVE order without creating or resending an order."""
    state_path = Path(state_path)
    state = json.loads(state_path.read_bytes())
    attempt = _live_existing_attempt(state, attempt_id)
    if not _explicit_utc(observed_at):
        raise ValueError("An explicit UTC timestamp is required")
    observations = state.get("alpaca_live_order_observations", [])
    if not isinstance(observations, list):
        raise ValueError("Persisted LIVE order observations must be a list")
    identity = "ALPACA_LIVE_ORDER_OBSERVATION|" + digest(encoded(
        {"attempt_id": attempt_id, "observed_at": observed_at}))
    existing = [item for item in observations if item.get("identity") == identity]
    if existing:
        observation = existing[0]
    else:
        injector = credential_provider()
        if not callable(injector):
            raise ValueError("LIVE credentials are absent")
        order = attempt.get("order")
        if isinstance(order, dict):
            known_order_id = order["broker_order_id"]
            path = "/v2/orders/" + _paper_resource_path(known_order_id)
        else:
            known_order_id = None
            path = ("/v2/orders:by_client_order_id?client_order_id="
                    + _paper_resource_path(attempt["client_order_id"]))
        try:
            response = transport("GET", ALPACA_LIVE_HOST, path, None,
                                 timeout_seconds, injector)
            code, payload = _paper_json_response(response)
            snapshot = _paper_order_snapshot(payload) if code == 200 else None
            status = "OBSERVED" if snapshot else "UNAVAILABLE"
            error = None if snapshot else "ORDER_NOT_AVAILABLE"
        except BaseException as exc:
            code, snapshot, status, error = None, None, "UNKNOWN", type(exc).__name__
        broker_status = snapshot.get("status") if snapshot else None
        reconciliation = (
            "FILLED_AWAITING_POSITION" if broker_status == "filled" else
            "PARTIAL_FILL_REQUIRES_CANCELLATION" if broker_status == "partially_filled" else
            "OPEN_ORDER" if broker_status in {"new", "accepted", "pending_new"} else
            "REQUIRED" if status == "UNKNOWN" else "TERMINAL_ORDER_OBSERVED")
        observation = {
            "identity": identity, "attempt_id": attempt_id,
            "decision_id": attempt["decision_id"],
            "proposal_id": attempt["proposal_id"],
            "approval_id": attempt["approval_id"],
            "risk_revalidation_id": attempt["risk_revalidation_id"],
            "broker_order_id": (snapshot.get("broker_order_id")
                                if snapshot else known_order_id),
            "observed_at": observed_at, "http_status": code,
            "status": status, "order": snapshot, "error_category": error,
            "reconciliation_status": reconciliation,
            "credentials_persisted": False,
        }
        observations.append(observation)
        state["alpaca_live_order_observations"] = observations
        _atomic_write(state_path, encoded(state))
    result = {"status": observation["status"], "observation": observation,
              "orders_sent": 0, "paper_orders_sent": 0,
              "state_sha256": digest(state_path.read_bytes())}
    publish(output, {"live-order-observation.json": encoded(result)})
    return result


def cancel_alpaca_live_remainder(state_path, output, attempt_id, cancelled_at,
                                 timeout_seconds, credential_provider, transport):
    """Cancel only the remainder of a previously observed partial LIVE fill."""
    state_path = Path(state_path)
    state = json.loads(state_path.read_bytes())
    attempt = _live_existing_attempt(state, attempt_id)
    observations = state.get("alpaca_live_order_observations", [])
    relevant = [item for item in observations if isinstance(item, dict)
                and item.get("attempt_id") == attempt_id and isinstance(item.get("order"), dict)]
    latest = relevant[-1] if relevant else None
    if latest is None or latest["order"].get("status") != "partially_filled" \
            or not _explicit_utc(cancelled_at):
        raise ValueError("An observed partial LIVE fill is required")
    cancellations = state.get("alpaca_live_order_cancellations", [])
    if not isinstance(cancellations, list):
        raise ValueError("Persisted LIVE cancellations must be a list")
    existing = [item for item in cancellations if item.get("attempt_id") == attempt_id]
    if existing:
        cancellation = existing[0]
    else:
        injector = credential_provider()
        if not callable(injector):
            raise ValueError("LIVE credentials are absent")
        order_id = latest["order"]["broker_order_id"]
        try:
            response = transport("DELETE", ALPACA_LIVE_HOST,
                                 "/v2/orders/" + _paper_resource_path(order_id),
                                 None, timeout_seconds, injector)
            code, _ = _paper_json_response(response)
            status = "CANCELLED" if code == 204 else "UNKNOWN"
            error = None if code == 204 else "CANCEL_NOT_CONFIRMED"
        except BaseException as exc:
            code, status, error = None, "UNKNOWN", type(exc).__name__
        cancellation = {
            "identity": "ALPACA_LIVE_CANCELLATION|" + digest(encoded(
                {"attempt_id": attempt_id, "cancelled_at": cancelled_at})),
            "attempt_id": attempt_id, "broker_order_id": order_id,
            "cancelled_at": cancelled_at, "http_status": code, "status": status,
            "error_category": error, "quantity_increased": False,
            "new_order_created": False, "reconciliation_required": True,
            "credentials_persisted": False,
        }
        cancellations.append(cancellation)
        state["alpaca_live_order_cancellations"] = cancellations
        _atomic_write(state_path, encoded(state))
    result = {"status": cancellation["status"], "cancellation": cancellation,
              "orders_sent": 0, "paper_orders_sent": 0,
              "state_sha256": digest(state_path.read_bytes())}
    publish(output, {"live-order-cancellation.json": encoded(result)})
    return result


def observe_alpaca_live_position(state_path, output, attempt_id, observed_at,
                                 timeout_seconds, credential_provider, transport):
    """Observe a sanitized real BTC/USD position without changing internal positions."""
    state_path = Path(state_path)
    state = json.loads(state_path.read_bytes())
    attempt = _live_existing_attempt(state, attempt_id)
    if not _explicit_utc(observed_at):
        raise ValueError("An explicit UTC timestamp is required")
    positions = state.get("alpaca_live_position_observations", [])
    if not isinstance(positions, list):
        raise ValueError("Persisted LIVE position observations must be a list")
    identity = "ALPACA_LIVE_POSITION|" + digest(encoded(
        {"attempt_id": attempt_id, "observed_at": observed_at}))
    existing = [item for item in positions if item.get("identity") == identity]
    if existing:
        observation = existing[0]
    else:
        injector = credential_provider()
        if not callable(injector):
            raise ValueError("LIVE credentials are absent")
        try:
            response = transport("GET", ALPACA_LIVE_HOST,
                                 "/v2/positions/BTC%2FUSD", None,
                                 timeout_seconds, injector)
            code, payload = _paper_json_response(response)
            if code == 404:
                status, snapshot, error = "UNAVAILABLE", None, "NO_OPEN_POSITION"
            elif code == 200 and isinstance(payload, dict) \
                    and payload.get("symbol") in ("BTC/USD", "BTCUSD"):
                snapshot = {"symbol": "BTC/USD",
                            "qty": _safe_broker_text(payload.get("qty"), 64),
                            "side": _safe_broker_text(payload.get("side"), 16),
                            "market_value": _safe_broker_text(payload.get("market_value"), 64),
                            "avg_entry_price": _safe_broker_text(
                                payload.get("avg_entry_price"), 64)}
                status, error = "OBSERVED", None
            else:
                status, snapshot, error = "UNKNOWN", None, "POSITION_NOT_DETERMINABLE"
        except BaseException as exc:
            code, status, snapshot, error = None, "UNKNOWN", None, type(exc).__name__
        observation = {
            "identity": identity, "attempt_id": attempt_id,
            "decision_id": attempt["decision_id"],
            "proposal_id": attempt["proposal_id"],
            "broker_order_id": (attempt.get("order") or {}).get("broker_order_id"),
            "observed_at": observed_at, "http_status": code,
            "status": status, "broker_position": snapshot,
            "internal_target_position_changed": False,
            "virtual_position_changed": False,
            "reconciliation_status": ("POSITION_OBSERVED" if status == "OBSERVED"
                                      else "NO_OPEN_POSITION" if status == "UNAVAILABLE"
                                      else "REQUIRED"),
            "error_category": error, "credentials_persisted": False,
        }
        positions.append(observation)
        state["alpaca_live_position_observations"] = positions
        _atomic_write(state_path, encoded(state))
    result = {"status": observation["status"], "observation": observation,
              "orders_sent": 0, "paper_orders_sent": 0,
              "state_sha256": digest(state_path.read_bytes())}
    publish(output, {"live-position-observation.json": encoded(result)})
    return result


ALPACA_PAPER_ACCOUNT_ENDPOINT = "https://paper-api.alpaca.markets/v2/account"
ALPACA_LIVE_ACCOUNT_ENDPOINT = "https://api.alpaca.markets/v2/account"


def _reject_connectivity_json_constant(value):
    raise ValueError("Non-finite JSON constant")


def _unique_connectivity_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object field")
        result[key] = value
    return result


def alpaca_paper_credentials_from_environment():
    """Return an in-memory header injector or None; never expose credential values."""
    key_id = os.environ.get("ALPACA_PAPER_API_KEY_ID")
    secret_key = os.environ.get("ALPACA_PAPER_API_SECRET_KEY")
    if key_id is None and secret_key is None:
        return None
    valid = all(
        isinstance(value, str) and bool(value)
        and all(33 <= ord(char) <= 126 for char in value)
        for value in (key_id, secret_key)
    )
    if not valid:
        raise ValueError("PAPER credentials are incomplete or invalid")

    def inject(headers):
        return {**headers, "APCA-API-KEY-ID": key_id,
                "APCA-API-SECRET-KEY": secret_key}

    return inject


def alpaca_live_credentials_from_environment():
    """Return a LIVE-only in-memory header injector; never expose credential values."""
    key_id = os.environ.get("ALPACA_LIVE_API_KEY_ID")
    secret_key = os.environ.get("ALPACA_LIVE_API_SECRET_KEY")
    if key_id is None and secret_key is None:
        return None
    valid = all(
        isinstance(value, str) and bool(value)
        and all(33 <= ord(char) <= 126 for char in value)
        for value in (key_id, secret_key)
    )
    if not valid:
        raise ValueError("LIVE credentials are incomplete or invalid")

    def inject(headers):
        return {**headers, "APCA-API-KEY-ID": key_id,
                "APCA-API-SECRET-KEY": secret_key}

    return inject


def alpaca_paper_account_https_get(host, path, timeout_seconds, credential_injector):
    """Perform the one allowed certificate-verifying PAPER account GET."""
    if host != "paper-api.alpaca.markets" or path != "/v2/account" \
            or not callable(credential_injector):
        raise ValueError("PAPER account transport received a forbidden target")
    headers = credential_injector({
        "Accept": "application/json",
        "User-Agent": "TramitaGO-Quant-Core-Phase4-Connectivity/0.1",
    })
    connection = HTTPSConnection(host, timeout=timeout_seconds)
    try:
        connection.request("GET", path, body=None, headers=headers)
        response = connection.getresponse()
        body = response.read(1_000_001)
        return {
            "status_code": response.status,
            "content_type": response.getheader("Content-Type"),
            "location": response.getheader("Location"),
            "body": body,
        }
    finally:
        connection.close()


def validate_alpaca_paper_connectivity(output, environment, endpoint, checked_at,
                                       timeout_seconds, credential_provider,
                                       transport):
    """Persist sanitized evidence for one explicitly PAPER, read-only account GET."""
    host = "paper-api.alpaca.markets"
    method = "GET"
    path = "/v2/account"
    status = None
    response_code = None
    schema = []
    account_hash = None
    error_category = None
    redirect_status = "NOT_OBSERVED"
    credential_source = "NOT_USED"

    environment_valid = environment == "PAPER"
    endpoint_valid = endpoint == ALPACA_PAPER_ACCOUNT_ENDPOINT
    try:
        parsed = urlsplit(endpoint) if isinstance(endpoint, str) else None
        endpoint_shape_valid = (
            parsed is not None and parsed.scheme == "https" and parsed.hostname == host
            and parsed.port is None and parsed.path == path and not parsed.query
            and not parsed.fragment and parsed.username is None and parsed.password is None
        )
    except ValueError:
        endpoint_shape_valid = False
    if not environment_valid or not endpoint_valid or not endpoint_shape_valid:
        status = "BLOCKED_ENVIRONMENT"
        error_category = "PAPER_ENVIRONMENT_OR_ENDPOINT_REQUIRED"
    elif not _explicit_utc(checked_at):
        status = "PAPER_CONNECTION_UNKNOWN"
        error_category = "INVALID_EXPLICIT_UTC_TIMESTAMP"
    elif (type(timeout_seconds) not in (int, float)
          or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        status = "PAPER_CONNECTION_UNKNOWN"
        error_category = "INVALID_TIMEOUT"
    elif not callable(credential_provider) or not callable(transport):
        status = "BLOCKED_CREDENTIALS"
        error_category = "CREDENTIAL_PROVIDER_OR_TRANSPORT_ABSENT"
    else:
        try:
            credential_injector = credential_provider()
        except BaseException as error:
            credential_injector = None
            error_category = type(error).__name__
        if not callable(credential_injector):
            status = "BLOCKED_CREDENTIALS"
            error_category = error_category or "PAPER_CREDENTIALS_ABSENT"
        else:
            credential_source = "environment_or_secret_store"
            try:
                response = transport(host, path, timeout_seconds, credential_injector)
                if not isinstance(response, dict) or set(response) != {
                        "status_code", "content_type", "location", "body"}:
                    raise ValueError("ambiguous transport response")
                response_code = response["status_code"]
                location = response["location"]
                if isinstance(response_code, int) and 300 <= response_code < 400:
                    redirect_status = "BLOCKED"
                    status = "BLOCKED_ENVIRONMENT"
                    error_category = "REDIRECT_NOT_ALLOWED"
                elif response_code in (401, 403):
                    status = "PAPER_CONNECTION_REJECTED"
                    error_category = "AUTHENTICATION_REJECTED"
                elif response_code == 200 and location is None \
                        and isinstance(response["content_type"], str) \
                        and response["content_type"].split(";", 1)[0].strip().lower() \
                        == "application/json" \
                        and isinstance(response["body"], bytes) \
                        and len(response["body"]) <= 1_000_000:
                    payload = json.loads(
                        response["body"],
                        parse_constant=_reject_connectivity_json_constant,
                        object_pairs_hook=_unique_connectivity_json_object)
                    if not isinstance(payload, dict) \
                            or not isinstance(payload.get("id"), str) \
                            or not payload["id"] \
                            or not isinstance(payload.get("status"), str) \
                            or not payload["status"]:
                        raise ValueError("ambiguous PAPER account response")
                    allowed_schema = {
                        "id", "status", "currency", "account_blocked",
                        "trading_blocked", "transfers_blocked",
                    }
                    schema = sorted(set(payload) & allowed_schema)
                    account_hash = digest(payload["id"].encode("utf-8"))
                    status = "PAPER_CONNECTION_VERIFIED"
                else:
                    status = "PAPER_CONNECTION_UNKNOWN"
                    error_category = "UNEXPECTED_RESPONSE"
            except BaseException as error:
                status = "PAPER_CONNECTION_UNKNOWN"
                error_category = type(error).__name__

    evidence_content = {
        "environment": "PAPER" if environment_valid else "BLOCKED",
        "host": host,
        "method": method,
        "path": path,
        "checked_at": checked_at,
        "timeout_seconds": timeout_seconds,
        "response_status_code": response_code,
        "status": status,
        "response_schema": schema,
        "account_identifier_sha256": account_hash,
        "error_category": error_category,
        "redirect_to_unapproved_host": redirect_status,
        "credential_source": credential_source,
        "credentials_persisted": False,
        "order_payload_present": False,
        "orders_sent": False,
        "orders_cancelled": False,
        "real_market_effect": "NONE",
    }
    evidence = {
        "identity": f"PAPER_CONNECTIVITY|{digest(encoded(evidence_content))}",
        **evidence_content,
    }
    publish(output, {"paper-connectivity.json": encoded(evidence)})
    return evidence


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
    paper_session = commands.add_parser("initialize-paper-session")
    paper_session.add_argument("--state", required=True)
    paper_session.add_argument("--output", required=True)
    paper_session.add_argument("--session-id", required=True)
    paper_session.add_argument("--mode", required=True)
    paper_session.add_argument("--started-at", required=True)
    paper_warmup = commands.add_parser("load-paper-warmup")
    paper_warmup.add_argument("--state", required=True)
    paper_warmup.add_argument("--output", required=True)
    paper_warmup.add_argument("--input", required=True)
    paper_fixture = commands.add_parser("prepare-paper-fixture")
    paper_fixture.add_argument("--state", required=True)
    paper_fixture.add_argument("--fixture", required=True)
    paper_fixture.add_argument("--output", required=True)
    paper_fixture.add_argument("--input", required=True)
    paper_fixture.add_argument("--processing-instant", required=True)
    paper_operational = commands.add_parser("validate-paper-operational")
    paper_operational.add_argument("--state", required=True)
    paper_operational.add_argument("--fixture", required=True)
    paper_operational.add_argument("--acceptance", required=True)
    paper_operational.add_argument("--output", required=True)
    paper_sma3 = commands.add_parser("compose-paper-sma3")
    paper_sma3.add_argument("--state", required=True)
    paper_sma3.add_argument("--fixture", required=True)
    paper_sma3.add_argument("--acceptance", required=True)
    paper_sma3.add_argument("--indicator", required=True)
    paper_sma3.add_argument("--output", required=True)
    execution = commands.add_parser("execute-virtual")
    execution.add_argument("--state", required=True)
    execution.add_argument("--output", required=True)
    proposal = commands.add_parser("prepare-real-order")
    proposal.add_argument("--state", required=True)
    proposal.add_argument("--output", required=True)
    proposal.add_argument("--decision-identity", required=True)
    proposal.add_argument("--risk-config", required=True)
    approval = commands.add_parser("record-manual-approval")
    approval.add_argument("--state", required=True)
    approval.add_argument("--output", required=True)
    approval.add_argument("--proposal-id", required=True)
    approval.add_argument("--proposal-identity", required=True)
    approval.add_argument("--actor", required=True)
    approval.add_argument("--approved-at", required=True)
    approval.add_argument("--decision", choices=("APPROVED", "REJECTED"), required=True)
    revalidation = commands.add_parser("revalidate-approved-proposal")
    revalidation.add_argument("--state", required=True)
    revalidation.add_argument("--output", required=True)
    revalidation.add_argument("--proposal-id", required=True)
    revalidation.add_argument("--proposal-identity", required=True)
    revalidation.add_argument("--risk-config", required=True)
    revalidation.add_argument("--revalidated-at", required=True)
    alpaca_request = commands.add_parser("prepare-alpaca-request")
    alpaca_request.add_argument("--state", required=True)
    alpaca_request.add_argument("--output", required=True)
    alpaca_request.add_argument("--proposal-id", required=True)
    alpaca_request.add_argument("--proposal-identity", required=True)
    alpaca_request.add_argument("--revalidation-identity", required=True)
    alpaca_request.add_argument("--risk-config", required=True)
    alpaca_request.add_argument("--target-environment", required=True)
    alpaca_request.add_argument("--prepared-at", required=True)
    connectivity = commands.add_parser("validate-paper-connectivity")
    connectivity.add_argument("--output", required=True)
    connectivity.add_argument("--environment", required=True)
    connectivity.add_argument("--endpoint", required=True)
    connectivity.add_argument("--checked-at", required=True)
    connectivity.add_argument("--timeout-seconds", required=True, type=float)
    submit_paper = commands.add_parser("submit-paper-order")
    submit_paper.add_argument("--state", required=True)
    submit_paper.add_argument("--output", required=True)
    submit_paper.add_argument("--request-id", required=True)
    submit_paper.add_argument("--attempted-at", required=True)
    submit_paper.add_argument("--timeout-seconds", required=True, type=float)
    observe_paper_order = commands.add_parser("observe-paper-order")
    observe_paper_order.add_argument("--state", required=True)
    observe_paper_order.add_argument("--output", required=True)
    observe_paper_order.add_argument("--attempt-id", required=True)
    observe_paper_order.add_argument("--observed-at", required=True)
    observe_paper_order.add_argument("--timeout-seconds", required=True, type=float)
    cancel_paper = commands.add_parser("cancel-paper-remainder")
    cancel_paper.add_argument("--state", required=True)
    cancel_paper.add_argument("--output", required=True)
    cancel_paper.add_argument("--attempt-id", required=True)
    cancel_paper.add_argument("--cancelled-at", required=True)
    cancel_paper.add_argument("--timeout-seconds", required=True, type=float)
    observe_position = commands.add_parser("observe-paper-position")
    observe_position.add_argument("--state", required=True)
    observe_position.add_argument("--output", required=True)
    observe_position.add_argument("--attempt-id", required=True)
    observe_position.add_argument("--observed-at", required=True)
    observe_position.add_argument("--timeout-seconds", required=True, type=float)
    submit_live = commands.add_parser("submit-live-order")
    submit_live.add_argument("--state", required=True)
    submit_live.add_argument("--output", required=True)
    submit_live.add_argument("--request-id", required=True)
    submit_live.add_argument("--attempted-at", required=True)
    submit_live.add_argument("--timeout-seconds", required=True, type=float)
    observe_live_order = commands.add_parser("observe-live-order")
    observe_live_order.add_argument("--state", required=True)
    observe_live_order.add_argument("--output", required=True)
    observe_live_order.add_argument("--attempt-id", required=True)
    observe_live_order.add_argument("--observed-at", required=True)
    observe_live_order.add_argument("--timeout-seconds", required=True, type=float)
    cancel_live = commands.add_parser("cancel-live-remainder")
    cancel_live.add_argument("--state", required=True)
    cancel_live.add_argument("--output", required=True)
    cancel_live.add_argument("--attempt-id", required=True)
    cancel_live.add_argument("--cancelled-at", required=True)
    cancel_live.add_argument("--timeout-seconds", required=True, type=float)
    observe_live_position = commands.add_parser("observe-live-position")
    observe_live_position.add_argument("--state", required=True)
    observe_live_position.add_argument("--output", required=True)
    observe_live_position.add_argument("--attempt-id", required=True)
    observe_live_position.add_argument("--observed-at", required=True)
    observe_live_position.add_argument("--timeout-seconds", required=True, type=float)
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
        elif args.command == "initialize-paper-session":
            result = initialize_paper_session(
                args.state, args.output, args.session_id, args.mode, args.started_at)
        elif args.command == "load-paper-warmup":
            observations = json.loads(Path(args.input).read_bytes())
            result = load_paper_session_warmup(
                args.state, args.output, observations)
        elif args.command == "prepare-paper-fixture":
            observations = json.loads(Path(args.input).read_bytes())
            result = prepare_paper_session_fixture(
                Path(args.state), Path(args.fixture), Path(args.output),
                observations, args.processing_instant)
        elif args.command == "validate-paper-operational":
            result = validate_paper_session_operational_observation(
                Path(args.state), Path(args.fixture), Path(args.acceptance),
                Path(args.output))
        elif args.command == "compose-paper-sma3":
            result = compose_paper_session_sma3(
                Path(args.state), Path(args.fixture), Path(args.acceptance),
                Path(args.indicator), Path(args.output))
        elif args.command == "execute-virtual":
            result = execute_virtual(args.state, args.output)
        elif args.command == "prepare-real-order":
            config = json.loads(Path(args.risk_config).read_bytes())
            result = prepare_real_order_proposal(
                args.state, args.output, args.decision_identity, config)
        elif args.command == "record-manual-approval":
            result = record_manual_approval(
                args.state, args.output, args.proposal_id, args.proposal_identity,
                args.actor, args.approved_at, args.decision)
        elif args.command == "revalidate-approved-proposal":
            config = json.loads(Path(args.risk_config).read_bytes())
            result = revalidate_approved_proposal(
                args.state, args.output, args.proposal_id,
                args.proposal_identity, config, args.revalidated_at)
        elif args.command == "prepare-alpaca-request":
            config = json.loads(Path(args.risk_config).read_bytes())
            result = prepare_alpaca_request(
                args.state, args.output, args.proposal_id,
                args.proposal_identity, args.revalidation_identity, config,
                args.target_environment, args.prepared_at)
        elif args.command == "validate-paper-connectivity":
            result = validate_alpaca_paper_connectivity(
                args.output, args.environment, args.endpoint, args.checked_at,
                args.timeout_seconds, alpaca_paper_credentials_from_environment,
                alpaca_paper_account_https_get)
        elif args.command == "submit-paper-order":
            result = execute_alpaca_paper_order(
                args.state, args.output, args.request_id, args.attempted_at,
                args.timeout_seconds, alpaca_paper_credentials_from_environment,
                alpaca_paper_https_request)
        elif args.command == "observe-paper-order":
            result = observe_alpaca_paper_order(
                args.state, args.output, args.attempt_id, args.observed_at,
                args.timeout_seconds, alpaca_paper_credentials_from_environment,
                alpaca_paper_https_request)
        elif args.command == "cancel-paper-remainder":
            result = cancel_alpaca_paper_remainder(
                args.state, args.output, args.attempt_id, args.cancelled_at,
                args.timeout_seconds, alpaca_paper_credentials_from_environment,
                alpaca_paper_https_request)
        elif args.command == "observe-paper-position":
            result = observe_alpaca_paper_position(
                args.state, args.output, args.attempt_id, args.observed_at,
                args.timeout_seconds, alpaca_paper_credentials_from_environment,
                alpaca_paper_https_request)
        elif args.command == "submit-live-order":
            result = execute_alpaca_live_order(
                args.state, args.output, args.request_id, args.attempted_at,
                args.timeout_seconds, alpaca_live_credentials_from_environment,
                alpaca_live_https_request)
        elif args.command == "observe-live-order":
            result = observe_alpaca_live_order(
                args.state, args.output, args.attempt_id, args.observed_at,
                args.timeout_seconds, alpaca_live_credentials_from_environment,
                alpaca_live_https_request)
        elif args.command == "cancel-live-remainder":
            result = cancel_alpaca_live_remainder(
                args.state, args.output, args.attempt_id, args.cancelled_at,
                args.timeout_seconds, alpaca_live_credentials_from_environment,
                alpaca_live_https_request)
        elif args.command == "observe-live-position":
            result = observe_alpaca_live_position(
                args.state, args.output, args.attempt_id, args.observed_at,
                args.timeout_seconds, alpaca_live_credentials_from_environment,
                alpaca_live_https_request)
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
