"""CAP-002 Research -- Hypothesis Dataset (M2.2-T1): captures and publishes a
sealed historical dataset for exactly one Hypothesis version. Never
evaluates the Hypothesis; only proves that its declared capture window can
be independently reproduced.

Moved out of pipeline.py per DOC-014/DOC-015 (adaptado) SS4 (Etapa 4, M4.1
module decomposition). No behavior change. Depends on both
research/hypothesis.py (load_hypothesis) and data/acquisition.py (COLUMNS,
SCHEMA, normalize, validate) -- this is why it lives under research/, not
data/ (see DOC-014 adaptado SS4, correction 2).
"""

import base64
import csv
import io
import json
import math
import platform
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tramitago_quant_core.shared.util import (
    digest, encoded, epoch, iso, publish, _explicit_utc, _pipeline_source_bytes,
)
from tramitago_quant_core.data.acquisition import (
    COLUMNS, SCHEMA, normalize, validate, indicators, _coinbase_public_request_headers,
)
from tramitago_quant_core.research.hypothesis import load_hypothesis

HYPOTHESIS_DATASET_SCHEMA_VERSION = "1"
HYPOTHESIS_DATASET_CAPTURE_SCHEMA_VERSION = "1"
HYPOTHESIS_DATASET_COLUMNS = COLUMNS + ["row_role", "forward_return_1d"]
HYPOTHESIS_DATASET_SCHEMA = {
    **SCHEMA,
    "row_role": "SUPPORT_SMA3_WARMUP | EVALUATION | SUPPORT_FORWARD_RETURN",
    "forward_return_1d": "nullable finite float64; defined only for EVALUATION rows",
}
HYPOTHESIS_DATASET_MAX_CANDLES_PER_REQUEST = 300
HYPOTHESIS_DATASET_ENDPOINT = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
HYPOTHESIS_DATASET_WARMUP_ROLE = "SUPPORT_SMA3_WARMUP"
HYPOTHESIS_DATASET_EVALUATION_ROLE = "EVALUATION"
HYPOTHESIS_DATASET_FORWARD_ROLE = "SUPPORT_FORWARD_RETURN"


def _hypothesis_dataset_config(hypothesis):
    """Derive the only capture window permitted by one registered Hypothesis."""
    constraints = hypothesis["constraints"]
    period = constraints["period"]
    required_variables = {"close", "sma_close_3", "forward_return_1d"}
    if (constraints["universe"] != ["BTC-USD"]
            or not required_variables.issubset(constraints["variables"])
            or not _explicit_utc(period["start_utc"])
            or not _explicit_utc(period["end_exclusive_utc"])
            or epoch(period["start_utc"]) % 86400
            or epoch(period["end_exclusive_utc"]) % 86400):
        raise ValueError("Hypothesis is incompatible with the historical BTC-USD daily dataset")
    start = epoch(period["start_utc"])
    end = epoch(period["end_exclusive_utc"])
    if end <= start:
        raise ValueError("Hypothesis evaluation period is invalid")
    return {
        "schema_version": HYPOTHESIS_DATASET_SCHEMA_VERSION,
        "source": "Coinbase Exchange public candles",
        "instrument": "BTC-USD",
        "frequency_seconds": 86400,
        "timezone": "UTC",
        "evaluable_period": {
            "start_utc": iso(start),
            "end_exclusive_utc": iso(end),
        },
        "capture_period": {
            "start_utc": iso(start - 2 * 86400),
            "end_exclusive_utc": iso(end + 86400),
        },
        "support_policy": "exactly two SMA3 warm-up rows before and one forward-return row after the evaluable period",
        "missing_policy": "reject entire dataset; no imputation",
        "duplicate_policy": "reject entire dataset",
        "temporal_policy": "reject rows outside the declared capture period",
    }


def _hypothesis_dataset_windows(config):
    start = epoch(config["capture_period"]["start_utc"])
    end = epoch(config["capture_period"]["end_exclusive_utc"])
    width = HYPOTHESIS_DATASET_MAX_CANDLES_PER_REQUEST * config["frequency_seconds"]
    return [(iso(left), iso(min(left + width, end))) for left in range(start, end, width)]


def _hypothesis_dataset_url(start_utc, end_exclusive_utc):
    # Coinbase's ``end`` candle bound is inclusive.  The dataset contract remains
    # half-open, so request the final included candle rather than its successor.
    endpoint_end = iso(epoch(end_exclusive_utc) - 86400)
    return HYPOTHESIS_DATASET_ENDPOINT + "?" + urlencode({
        "granularity": 86400,
        "start": start_utc,
        "end": endpoint_end,
    })


def _hypothesis_dataset_live_get(url, headers, timeout_seconds):
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Response exceeds bounded capture size")
        return raw, {key: response.headers.get(key) for key in ("Date", "Content-Type")}


def _hypothesis_dataset_response(transport, url):
    response = (transport or _hypothesis_dataset_live_get)(
        url, _coinbase_public_request_headers(), 30)
    if isinstance(response, tuple) and len(response) == 2:
        raw, headers = response
    else:
        raw, headers = response, {}
    if not isinstance(raw, bytes) or not isinstance(headers, dict):
        raise ValueError("Historical capture transport returned an invalid response")
    return raw, headers


def _hypothesis_dataset_capture_content(config, acquired_at, responses):
    content = {
        "schema_version": HYPOTHESIS_DATASET_CAPTURE_SCHEMA_VERSION,
        "kind": "historical-hypothesis-dataset",
        "source": config["source"],
        "config": config,
        "acquired_at": acquired_at,
        "responses": responses,
    }
    return {**content, "capture_id": "COINBASE_HISTORICAL_CAPTURE|" + digest(encoded(content))}


def _hypothesis_dataset_raw_content(captures):
    return {
        "schema_version": HYPOTHESIS_DATASET_CAPTURE_SCHEMA_VERSION,
        "captures": captures,
    }


def _hypothesis_dataset_capture_rows(raw_bytes, capture, config):
    """Verify preserved source bytes and return the combined candle payload."""
    if not isinstance(capture, dict):
        raise ValueError("Historical capture is invalid")
    responses = capture.get("responses")
    content = {key: capture[key] for key in (
        "schema_version", "kind", "source", "config", "acquired_at", "responses")
        if key in capture}
    if (set(capture) != set(content) | {"capture_id"}
            or capture.get("schema_version") != HYPOTHESIS_DATASET_CAPTURE_SCHEMA_VERSION
            or capture.get("kind") != "historical-hypothesis-dataset"
            or capture.get("source") != config["source"]
            or capture.get("config") != config
            or not _explicit_utc(capture.get("acquired_at"))
            or capture.get("capture_id") != "COINBASE_HISTORICAL_CAPTURE|" + digest(encoded(content))
            or not isinstance(responses, list)):
        raise ValueError("Historical capture metadata is invalid")
    try:
        raw = json.loads(raw_bytes)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Historical raw capture is invalid") from error
    if (not isinstance(raw, dict) or set(raw) != {"schema_version", "captures"}
            or raw.get("schema_version") != HYPOTHESIS_DATASET_CAPTURE_SCHEMA_VERSION
            or not isinstance(raw.get("captures"), list)
            or len(raw["captures"]) != len(responses)):
        raise ValueError("Historical raw capture is incomplete")
    expected_windows = _hypothesis_dataset_windows(config)
    if len(responses) != len(expected_windows):
        raise ValueError("Historical capture has an incompatible request count")
    payload = []
    for number, ((start_utc, end_exclusive_utc), metadata, stored) in enumerate(
            zip(expected_windows, responses, raw["captures"]), 1):
        fields = {"sequence", "url", "start_utc", "end_exclusive_utc", "response_sha256", "response_headers"}
        raw_fields = {"sequence", "response_sha256", "response_base64"}
        if (not isinstance(metadata, dict) or set(metadata) != fields
                or not isinstance(stored, dict) or set(stored) != raw_fields
                or metadata["sequence"] != number or stored["sequence"] != number
                or metadata["start_utc"] != start_utc
                or metadata["end_exclusive_utc"] != end_exclusive_utc
                or metadata["url"] != _hypothesis_dataset_url(start_utc, end_exclusive_utc)
                or metadata["response_sha256"] != stored["response_sha256"]
                or not isinstance(metadata["response_headers"], dict)):
            raise ValueError("Historical capture request identity is invalid")
        try:
            response = base64.b64decode(stored["response_base64"].encode("ascii"), validate=True)
            part = json.loads(response)
        except (ValueError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("Historical capture response is invalid") from error
        if digest(response) != metadata["response_sha256"] or not isinstance(part, list):
            raise ValueError("Historical capture response integrity is invalid")
        payload.extend(part)
    return payload


def _hypothesis_dataset_rows(rows, config):
    derived = indicators(rows)
    start = epoch(config["evaluable_period"]["start_utc"])
    end = epoch(config["evaluable_period"]["end_exclusive_utc"])
    result = []
    for index, row in enumerate(derived):
        timestamp = epoch(row["timestamp"])
        if timestamp < start:
            role = HYPOTHESIS_DATASET_WARMUP_ROLE
        elif timestamp >= end:
            role = HYPOTHESIS_DATASET_FORWARD_ROLE
        else:
            role = HYPOTHESIS_DATASET_EVALUATION_ROLE
        forward_return = None
        if role == HYPOTHESIS_DATASET_EVALUATION_ROLE:
            if index + 1 >= len(derived) or epoch(derived[index + 1]["timestamp"]) != timestamp + 86400:
                raise ValueError("Evaluation row lacks its next daily close")
            forward_return = derived[index + 1]["close"] / row["close"] - 1
            if not math.isfinite(forward_return):
                raise ValueError("Forward return is non-finite")
        result.append({**row, "row_role": role, "forward_return_1d": forward_return})
    return result


def _hypothesis_dataset_bytes(rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=HYPOTHESIS_DATASET_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _hypothesis_dataset_selection(hypothesis, config, rows):
    support_rows = [{"timestamp": row["timestamp"], "row_role": row["row_role"]}
                    for row in rows if row["row_role"] != HYPOTHESIS_DATASET_EVALUATION_ROLE]
    evaluation_rows = [row for row in rows if row["row_role"] == HYPOTHESIS_DATASET_EVALUATION_ROLE]
    if (len(support_rows) != 3 or len(evaluation_rows) != 365
            or [item["row_role"] for item in support_rows] != [
                HYPOTHESIS_DATASET_WARMUP_ROLE, HYPOTHESIS_DATASET_WARMUP_ROLE,
                HYPOTHESIS_DATASET_FORWARD_ROLE]
            or not all(row["sma_close_3"] is not None and row["forward_return_1d"] is not None
                       for row in evaluation_rows)):
        raise ValueError("Historical selection does not contain exactly the required support and evaluable rows")
    return {
        "schema_version": HYPOTHESIS_DATASET_SCHEMA_VERSION,
        "hypothesis_id": hypothesis["hypothesis_id"],
        "hypothesis_version": hypothesis["version"],
        "evaluable_period": config["evaluable_period"],
        "evaluable_row_count": len(evaluation_rows),
        "support_rows": support_rows,
        "row_roles": {
            "support_sma3_warmup": HYPOTHESIS_DATASET_WARMUP_ROLE,
            "evaluable": HYPOTHESIS_DATASET_EVALUATION_ROLE,
            "support_forward_return": HYPOTHESIS_DATASET_FORWARD_ROLE,
        },
    }


def _hypothesis_dataset_identity(hypothesis, config, hashes):
    content = {
        "schema_version": HYPOTHESIS_DATASET_SCHEMA_VERSION,
        "hypothesis_id": hypothesis["hypothesis_id"],
        "hypothesis_version": hypothesis["version"],
        "system_version": hypothesis["system_version"],
        "code_revision": hypothesis["code_revision"],
        "config": config,
        "hashes": hashes,
    }
    return "HISTORICAL_HYPOTHESIS_DATASET|" + digest(encoded(content)), content


def _hypothesis_dataset_audit(dataset_id, hypothesis, config, selection):
    return {
        "milestone": "M2.2-T1",
        "status": "VERIFIED",
        "dataset_id": dataset_id,
        "hypothesis_id": hypothesis["hypothesis_id"],
        "hypothesis_version": hypothesis["version"],
        "evaluable_period": config["evaluable_period"],
        "support_rows": selection["support_rows"],
        "metric_evaluated": False,
        "backtest_executed": False,
        "validation": "PASS",
        "integrity": "VERIFIED",
    }


def _hypothesis_dataset_validation(payload, config):
    start = epoch(config["capture_period"]["start_utc"])
    end = epoch(config["capture_period"]["end_exclusive_utc"])
    rows, report = normalize(json.dumps(payload, separators=(",", ":")).encode("utf-8"),
                             start=start, end=end)
    if report["excluded_outside_range"]:
        report["errors"].append({
            "row": None,
            "reason": "Temporally incompatible response contains rows outside the capture period",
        })
    expected = {iso(value) for value in range(start, end, config["frequency_seconds"])}
    return rows, validate(rows, report, expected_timestamps=expected)


def create_hypothesis_dataset(registry_path, hypothesis_id, version, output, *,
                              transport=None, acquired_at=None):
    """Capture and publish a sealed historical dataset; never evaluate the Hypothesis."""
    hypothesis = load_hypothesis(registry_path, hypothesis_id, version)
    config = _hypothesis_dataset_config(hypothesis)
    acquired_at = acquired_at or datetime.now(timezone.utc).isoformat(
        timespec="microseconds").replace("+00:00", "Z")
    if not _explicit_utc(acquired_at):
        raise ValueError("Capture time must be canonical UTC")
    responses, stored = [], []
    for sequence, (start_utc, end_exclusive_utc) in enumerate(
            _hypothesis_dataset_windows(config), 1):
        url = _hypothesis_dataset_url(start_utc, end_exclusive_utc)
        response, headers = _hypothesis_dataset_response(transport, url)
        response_sha256 = digest(response)
        responses.append({
            "sequence": sequence,
            "url": url,
            "start_utc": start_utc,
            "end_exclusive_utc": end_exclusive_utc,
            "response_sha256": response_sha256,
            "response_headers": headers,
        })
        stored.append({
            "sequence": sequence,
            "response_sha256": response_sha256,
            "response_base64": base64.b64encode(response).decode("ascii"),
        })
    raw = encoded(_hypothesis_dataset_raw_content(stored))
    capture = _hypothesis_dataset_capture_content(config, acquired_at, responses)
    capture_bytes = encoded(capture)
    source = _pipeline_source_bytes()
    payload = _hypothesis_dataset_capture_rows(raw, capture, config)
    rows, validation = _hypothesis_dataset_validation(payload, config)
    base_files = {
        "raw.json": raw,
        "capture.json": capture_bytes,
        "validation.json": encoded(validation),
        "pipeline_snapshot.py": source,
    }
    if validation["status"] != "PASS":
        publish(output, base_files | {"failure.json": encoded({
            "status": "FAIL",
            "hypothesis_id": hypothesis_id,
            "hypothesis_version": version,
            "raw_sha256": digest(raw),
            "capture_sha256": digest(capture_bytes),
            "code_sha256": digest(source),
        })})
        raise ValueError("Historical dataset rejected; see validation.json")
    selected_rows = _hypothesis_dataset_rows(rows, config)
    dataset = _hypothesis_dataset_bytes(selected_rows)
    selection = _hypothesis_dataset_selection(hypothesis, config, selected_rows)
    selection_bytes = encoded(selection)
    hashes = {
        "dataset_sha256": digest(dataset),
        "selection_sha256": digest(selection_bytes),
        "validation_sha256": digest(base_files["validation.json"]),
        "raw_sha256": digest(raw),
        "capture_sha256": digest(capture_bytes),
        "code_sha256": digest(source),
    }
    dataset_id, identity = _hypothesis_dataset_identity(hypothesis, config, hashes)
    audit = _hypothesis_dataset_audit(dataset_id, hypothesis, config, selection)
    audit_bytes = encoded(audit)
    manifest = {
        "status": "PASS",
        "dataset_id": dataset_id,
        "identity": identity,
        "config": config,
        "schema": HYPOTHESIS_DATASET_SCHEMA,
        "columns": HYPOTHESIS_DATASET_COLUMNS,
        "rows": len(selected_rows),
        "range": [selected_rows[0]["timestamp"], selected_rows[-1]["timestamp"]],
        "audit_sha256": digest(audit_bytes),
        **hashes,
        "runtime": platform.python_version(),
        "input_kind": capture["kind"],
        "replay": "create_hypothesis_dataset(<registry>, <hypothesis_id>, <version>, <output>)",
    }
    publish(output, base_files | {
        "dataset.csv": dataset,
        "selection.json": selection_bytes,
        "audit.json": audit_bytes,
        "manifest.json": encoded(manifest),
    })
    return manifest


def verified_hypothesis_dataset(directory, registry_path=None):
    """Fail closed unless all dataset artifacts, selection, and linked Hypothesis agree."""
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_bytes())
    required = {
        "status", "dataset_id", "identity", "config", "schema", "columns", "rows", "range",
        "audit_sha256", "dataset_sha256", "selection_sha256", "validation_sha256", "raw_sha256",
        "capture_sha256", "code_sha256", "runtime", "input_kind", "replay",
    }
    if not isinstance(manifest, dict) or set(manifest) != required or manifest["status"] != "PASS":
        raise ValueError("Historical dataset manifest is invalid")
    files = {
        "dataset.csv": "dataset_sha256",
        "selection.json": "selection_sha256",
        "validation.json": "validation_sha256",
        "raw.json": "raw_sha256",
        "capture.json": "capture_sha256",
        "pipeline_snapshot.py": "code_sha256",
        "audit.json": "audit_sha256",
    }
    for name, field in files.items():
        if digest((directory / name).read_bytes()) != manifest[field]:
            raise ValueError("Historical dataset integrity failure: " + name)
    config = manifest["config"]
    capture = json.loads((directory / "capture.json").read_bytes())
    payload = _hypothesis_dataset_capture_rows((directory / "raw.json").read_bytes(), capture, config)
    rows, validation = _hypothesis_dataset_validation(payload, config)
    if validation["status"] != "PASS" or validation != json.loads((directory / "validation.json").read_bytes()):
        raise ValueError("Historical dataset validation is invalid")
    identity = manifest["identity"]
    if (not isinstance(identity, dict) or identity.get("config") != config
            or manifest["schema"] != HYPOTHESIS_DATASET_SCHEMA
            or manifest["columns"] != HYPOTHESIS_DATASET_COLUMNS
            or manifest["rows"] != len(rows)):
        raise ValueError("Historical dataset identity is invalid")
    expected_id = "HISTORICAL_HYPOTHESIS_DATASET|" + digest(encoded(identity))
    if manifest["dataset_id"] != expected_id:
        raise ValueError("Historical dataset identifier is invalid")
    selected_rows = _hypothesis_dataset_rows(rows, config)
    if _hypothesis_dataset_bytes(selected_rows) != (directory / "dataset.csv").read_bytes():
        raise ValueError("Historical dataset rows are invalid")
    hypothesis = None
    if registry_path is not None:
        hypothesis = load_hypothesis(registry_path, identity["hypothesis_id"],
                                     identity["hypothesis_version"])
        expected_id, expected_identity = _hypothesis_dataset_identity(
            hypothesis, _hypothesis_dataset_config(hypothesis), identity["hashes"])
        if expected_identity != identity or expected_id != manifest["dataset_id"]:
            raise ValueError("Historical dataset is not linked to its Hypothesis version")
    selection = json.loads((directory / "selection.json").read_bytes())
    expected_selection = _hypothesis_dataset_selection(
        hypothesis or {"hypothesis_id": identity["hypothesis_id"],
                       "version": identity["hypothesis_version"]}, config, selected_rows)
    if selection != expected_selection:
        raise ValueError("Historical dataset selection is invalid")
    audit = json.loads((directory / "audit.json").read_bytes())
    if audit != _hypothesis_dataset_audit(manifest["dataset_id"], hypothesis or {
            "hypothesis_id": identity["hypothesis_id"], "version": identity["hypothesis_version"]},
            config, selection):
        raise ValueError("Historical dataset audit record is invalid")
    return manifest
