"""Capture, seal and judge a held-position Hypothesis from a spec, with injected I/O.

This is the orchestration the twenty-three per-asset scripts each re-implemented:
find the Hypothesis and refuse one that has not answered its seven questions,
capture and seal the dataset (resumable, because once sealed the dataset IS the
evidence and a re-fetch could return revised bars), capture the auxiliary series the
spec asks for, hand all of it to the engine, and seal what comes back.

THE I/O IS INJECTED. `io` supplies two callables and nothing else:

  capture_bars(symbol, start, end, warmup, horizon, acquired_at, adjustment)
                    -> (rows, capture, raw)      bars from a venue
  relay_fred(series_id, start, end) -> bytes     a FRED response fetched elsewhere

so the real thing and a test can differ in exactly those two places. The scripts
could not be tested because credentials, an ssh hop and a network sat in the
middle of every one of them; here none of that is in the middle of anything.

NOTHING IS SEALED ON A VOID MEASUREMENT. If the spec's own data check fails -- an
adjustment that cannot be trusted -- the dataset and the auxiliary captures stay,
because they are what was observed, and no level claim and no admission are written.
A weaker measurement is still a measurement; one whose adjustment cannot be trusted
is not a measurement of this Hypothesis at all.
"""

import base64
import csv
import json
from pathlib import Path

from tramitago_quant_core.data.fred_series import (
    capture_fred_series, verified_fred_series_capture, ENDPOINT_API,
)
from tramitago_quant_core.governance.admission import constitute_admission
from tramitago_quant_core.research.historical_dataset import create_hypothesis_dataset
from tramitago_quant_core.research.level_claim import constitute_level_claim_validation
from tramitago_quant_core.research.monitors import monitor_series_names, needs_underlying
from tramitago_quant_core.research.premium_engine import judge, validate_spec
from tramitago_quant_core.research.pre_declaration import (
    load_pre_declaration, require_pre_declaration,
)
from tramitago_quant_core.shared.util import _atomic_write, encoded
from tramitago_quant_core.strategy_contract.strategy import sma_crossover_strategy

MINIMUM_TRADING_DAYS = 756          # three years: below it a lower bound means little
HORIZON = 1

# The dataset contract imposes the default Strategy on every Hypothesis and requires
# its indicator column among the declared variables, because every Hypothesis this
# platform held before the held-position ones was a signal. The position here has no
# signal and never reads it; the warmup it asks for is nonetheless real.
STRATEGY = sma_crossover_strategy(3)
WARMUP = STRATEGY["required_inputs"]["warmup_periods"]


def _latest(hypotheses_path, hypothesis_id):
    versions = [item for item in json.loads(Path(hypotheses_path).read_bytes())["hypotheses"]
                if item["hypothesis_id"] == hypothesis_id]
    if not versions:
        raise ValueError(f"{hypothesis_id} is not in the registry; declare it first")
    return max(versions, key=lambda item: item["version"])


def _closes(rows):
    return {row["timestamp"][:10]: float(row["close"]) for row in rows}


def _sealed_closes(directory, name, capture_bars, symbol, start, end, warmup, now, adjustment):
    """Bars captured once, sealed beside the dataset, and re-read on every later run.

    The parsed closes are stored with the capture so a resume never re-fetches: the
    first response is the evidence, and a second fetch may differ.
    """
    directory = Path(directory)
    closes_path = directory / f"{name}.closes.json"
    if closes_path.exists():
        return {day: float(value) for day, value in
                json.loads(closes_path.read_bytes())["closes"].items()}
    rows, capture, raw = capture_bars(
        symbol=symbol, start=start, end=end, warmup=warmup, horizon=HORIZON,
        acquired_at=now, adjustment=adjustment)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.capture.json").write_bytes(encoded(capture))
    (directory / f"{name}.raw.json").write_bytes(raw)
    closes = _closes(rows)
    _atomic_write(closes_path, encoded({"symbol": symbol, "adjustment": adjustment,
                                        "closes": closes}))
    return closes


def _sealed_fred(directory, series_id, relay_fred, start, end, now):
    directory = Path(directory)
    capture_path, raw_path = directory / f"{series_id}.capture.json", directory / f"{series_id}.raw.json"
    if capture_path.exists() and raw_path.exists():
        return verified_fred_series_capture(raw_path.read_bytes(),
                                            json.loads(capture_path.read_bytes()))
    raw = relay_fred(series_id, start, end)
    if not isinstance(raw, bytes) or b"api_key" in raw:
        # A credential must never reach a sealed artifact, and FRED takes its key as
        # a query parameter, so a response that echoes one is refused outright.
        raise ValueError(f"Refusing the {series_id} response: not bytes, or it contains a key")
    _, capture, stored = capture_fred_series(series_id, start, end, now,
                                             transport=lambda url, data=raw: data,
                                             endpoint=ENDPOINT_API)
    directory.mkdir(parents=True, exist_ok=True)
    _atomic_write(capture_path, encoded(capture))
    raw_path.write_bytes(stored if isinstance(stored, bytes) else stored.encode("utf-8"))
    return verified_fred_series_capture(raw_path.read_bytes(), capture)


def run_spec(spec, *, io, paths, now, code_revision, seal=True):
    """Capture, judge and seal. Returns the engine result plus what was sealed."""
    validate_spec(spec)
    hypothesis = _latest(paths["hypotheses"], spec["hypothesis_id"])
    # REFUSED BEFORE ANY NETWORK CALL, so a Hypothesis that has not answered its seven
    # questions costs nothing instead of a capture that the dataset contract would
    # reject afterwards.
    answers = require_pre_declaration(hypothesis, paths["pre_declarations"])
    if answers is None:
        answers = load_pre_declaration(paths["pre_declarations"], spec["hypothesis_id"])
    period = hypothesis["constraints"]["period"]
    start, end = period["start_utc"], period["end_exclusive_utc"]
    instrument = spec["instrument"]
    slug_dir = spec["slug"].replace("-", "_")
    dataset_dir = Path(paths["datasets"]) / slug_dir
    checks_dir = Path(paths["checks"]) / slug_dir

    if (dataset_dir / "manifest.json").exists():
        resumed = True
    else:
        resumed = False
        rows, capture, raw = io.capture_bars(
            symbol=instrument["symbol"], start=start, end=end, warmup=WARMUP,
            horizon=HORIZON, acquired_at=now, adjustment="all")
        if len(rows) < MINIMUM_TRADING_DAYS:
            raise ValueError(
                f"REFUSED: {len(rows)} trading days against a declared minimum of "
                f"{MINIMUM_TRADING_DAYS}. Judging a shorter window would answer a question "
                f"nobody declared. Nothing has been sealed.")
        create_hypothesis_dataset(
            paths["hypotheses"], spec["hypothesis_id"], hypothesis["version"], dataset_dir,
            strategy=STRATEGY, horizon=HORIZON, acquired_at=now,
            primary_source={"rows": rows, "capture": capture, "raw": raw})

    rows = [row for row in csv.DictReader((dataset_dir / "dataset.csv").open(encoding="utf-8"))
            if row.get("forward_return_1d")]

    # --- auxiliary captures the spec asks for ---------------------------------------
    raw_closes = underlying = None
    if any(check["kind"] == "distribution_adjustment" for check in spec.get("checks", [])):
        raw_closes = _sealed_closes(checks_dir, "raw_bars", io.capture_bars,
                                    instrument["symbol"], start, end, 0, now, "raw")
    monitor = spec.get("monitor", {"kind": "none"})
    if needs_underlying(monitor):
        underlying = _sealed_closes(checks_dir, "underlying_bars", io.capture_bars,
                                    monitor["underlying"], start, end, 0, now, "all")
    series = {sid: _sealed_fred(checks_dir, sid, io.relay_fred, start[:10], end[:10], now)
              for sid in monitor_series_names(monitor)}

    controls = {}
    for control in spec.get("controls", []):
        if control["kind"] != "underlying_forward_returns" or underlying is None:
            raise ValueError("A control needs the monitor's underlying to read returns from")
        days = sorted(underlying)
        controls[control["name"]] = {
            days[i]: underlying[days[i + 1]] / underlying[days[i]] - 1
            for i in range(len(days) - 1)}

    result = judge(spec, rows,
                   monitor_inputs={"series": series, "underlying": underlying},
                   raw_closes=raw_closes, controls=controls, code_revision=code_revision)
    result.update({"resumed": resumed, "sealed": {}, "pre_declaration": answers})
    if result["void"] or not seal:
        return result

    claim = result["level_claim"]
    validation = constitute_level_claim_validation(
        Path(paths["level_claims"]) / f"level-claim-validations-{spec['slug']}.json",
        claim=claim["claim"], folds=claim["folds"], validated_at=now,
        validation_code_revision=code_revision)
    result["sealed"]["validation_id"] = validation["validation_id"]
    if claim["outcome"] == "VALIDATED":
        record = constitute_admission(
            paths["admissions"], hypothesis_id=spec["hypothesis_id"],
            validation_id=validation["validation_id"], validation_outcome="VALIDATED",
            evidence=result["evidence"], decided_at=now, code_revision=code_revision)
        result["sealed"]["admission_id"] = record["admission_id"]
        result["sealed"]["admission_outcome"] = record["outcome"]
        result["sealed"]["admission_gates"] = record["gates"]
    return result
