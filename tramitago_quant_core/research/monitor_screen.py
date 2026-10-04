"""The monitor screen scanner: a sealed space in, every candidate observed once, a holdout spent once.

WHAT IT DOES, in the order the discipline requires:

  1. SEAL. The space is validated and sealed BEFORE any series or return is read. Everything
     afterwards refuses a space that does not match its sealed record, so the candidates, the
     windows and the pass rule cannot move once a result has been seen.

  2. SCAN. Every candidate is observed on the DISCOVERY window and nowhere else. The scan is
     handed only discovery rows and RAISES on a row outside the window: the protection is a
     refusal and not a convention. Each candidate is judged by the engine own M1 rule, so the
     screen is exactly as hard as the gate a premium later faces. All of them are sealed, not
     just the winners, with the multiplicity beside them.

  3. CONFIRM, ONCE. Only candidates that met the rule are confirmed, on the holdout, by the single
     claim the space declared: the upper bound of (mean on ON days minus mean on OFF days) is below
     zero, at confidence 1 minus 0.05 over the number that passed. A marker is sealed BEFORE the
     holdout returns are read, so a crash after reading cannot be retried: an opened holdout is
     spent, and a second attempt is refused whatever happened to the first.

THE STATE IS READ WITHOUT LOOKING FORWARD. A state on day t uses only observations dated no later
than t, or no later than the previous trading day for a series published with a lag. The median
excludes the day it is compared with. A day with no observation is UNKNOWN, never ON: a gappy series
must not read as one that fires often.

THIS MODULE READS NOTHING BY ITSELF. Series and returns are handed in, so every property above is
testable with a dictionary. The CLI is the only place that touches the network or the datasets.
"""

import copy
import json
import statistics
from decimal import Decimal
from pathlib import Path

from tramitago_quant_core.research.monitor_screen_space import (
    validate_space, expected_false_passes, STATUS_SEALED)
from tramitago_quant_core.research.monitors import fold_bounds, link_consistency
from tramitago_quant_core.research.premium_engine import (
    LINK_CONSISTENCY_THRESHOLD, LINK_MINIMUM_USABLE_FOLDS)
from tramitago_quant_core.research.monitors import MINIMUM_TRIGGER_DAYS_PER_FOLD
from tramitago_quant_core.risk.statistic_bounds import bootstrap_bound, BOUND_UPPER
from tramitago_quant_core.shared.util import digest, encoded, _atomic_write

LINK_MET, LINK_FAILED, LINK_UNREACHABLE = (
    "REACHABLE_AND_MET", "REACHABLE_AND_FAILED", "UNREACHABLE")
FAMILY_ALPHA = Decimal("0.05")
SCHEMA_VERSION = "1"


# --- sealing ----------------------------------------------------------------------------------

def canonical_space(space):
    """The space as it is sealed: the same content with the status that says so."""
    validate_space(space)
    sealed = copy.deepcopy(space)
    sealed["status"] = STATUS_SEALED
    return sealed


def space_identity(space):
    return "MONITOR_SCREEN_SPACE|" + digest(encoded(canonical_space(space)))


def _registry(path, key):
    path = Path(path)
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, key: []}
    return json.loads(path.read_bytes())


def seal_space(path, space, *, sealed_at, code_revision, authorised_by, link_rule_id):
    """Seal the space. Idempotent on content: the same space seals once."""
    space_id = space_identity(space)
    registry = _registry(path, "spaces")
    for record in registry["spaces"]:
        if record["space_id"] == space_id:
            return record, False
    record = {"space_id": space_id, "space": canonical_space(space), "sealed_at": sealed_at,
              "code_revision": code_revision, "authorised_by": authorised_by,
              "link_rule_id": link_rule_id, "schema_version": SCHEMA_VERSION}
    registry["spaces"].append(record)
    _atomic_write(Path(path), encoded(registry))
    return record, True


def load_sealed_space(path, space):
    """The sealed record that matches this space exactly, re-verified, or a refusal."""
    wanted = space_identity(space)
    for record in _registry(path, "spaces")["spaces"]:
        if record["space_id"] == wanted:
            if "MONITOR_SCREEN_SPACE|" + digest(encoded(record["space"])) != wanted:
                raise ValueError("REFUSED: the sealed space does not reproduce its identity")
            return record
    raise ValueError(
        "REFUSED: this space is not sealed. It must be sealed before any series or return is "
        "read, so that no candidate, window or rule can change after a result is seen. A space "
        "that differs from the sealed one in any way is a new space and needs its own seal.")


# --- states ------------------------------------------------------------------------------------

def state_flags(form, dates, series, *, lag_days, change_horizon, median_window):
    """ON/OFF for each exposure date, and how many days were unknown. Looks only backward.

    `series` is {date: value or None} and may begin long before `dates`: the change and the median
    need history. A day whose observation is absent, or whose history is too short, is UNKNOWN and
    flagged 0, and the count is returned so the observation can state it.
    """
    observed = sorted(day for day, value in series.items() if value is not None)
    values = [float(series[day]) for day in observed]
    position = {day: index for index, day in enumerate(observed)}
    flags, unknown = [], 0
    for at, day in enumerate(dates):
        if lag_days:
            effective = dates[at - 1] if at > 0 else None
        else:
            effective = day
        i = position.get(effective)
        if i is None:
            flags.append(0)
            unknown += 1
            continue
        if form == "SIGN_AT_OR_BELOW_ZERO":
            flags.append(1 if values[i] <= 0 else 0)
        elif form == "CHANGE_21D_ABOVE_ZERO":
            if i < change_horizon:
                flags.append(0)
                unknown += 1
            else:
                flags.append(1 if values[i] > values[i - change_horizon] else 0)
        elif form == "ABOVE_TRAILING_MEDIAN_252":
            if i < median_window:
                flags.append(0)
                unknown += 1
            else:
                flags.append(1 if values[i] > statistics.median(values[i - median_window:i]) else 0)
        else:
            raise ValueError(f"unknown state form {form!r}")
    return flags, unknown


# --- the scan ----------------------------------------------------------------------------------

def _window_dates(window):
    return window["start_utc"][:10], window["end_exclusive_utc"][:10]


def _inside(window, day):
    start, end = _window_dates(window)
    return start <= day < end


def _outcome(met, usable):
    if usable < LINK_MINIMUM_USABLE_FOLDS:
        return LINK_UNREACHABLE
    ratio = Decimal(met) / Decimal(usable)
    return LINK_MET if ratio >= LINK_CONSISTENCY_THRESHOLD else LINK_FAILED


def _variable(space, variable_id):
    return next(v for v in space["variables"] if v["id"] == variable_id)


def observe_candidate(space, candidate, *, series, dates, returns):
    """One candidate on one window: the fold rule, with the diagnostics a reader needs."""
    variable = _variable(space, candidate["variable"])
    fixed = space["fixed_parameters"]
    flags, unknown = state_flags(
        candidate["form"], dates, series[variable["series"]], lag_days=variable["lag_days"],
        change_horizon=fixed["change_horizon_days"], median_window=fixed["median_window_days"])
    aligned = [returns[day] for day in dates]
    bounds = fold_bounds(len(dates), space["folds"])
    met, usable = link_consistency(bounds, aligned, flags, MINIMUM_TRIGGER_DAYS_PER_FOLD)
    folds = []
    for lo, hi in bounds:
        on = [aligned[i] for i in range(lo, hi) if flags[i]]
        off = [aligned[i] for i in range(lo, hi) if not flags[i]]
        folds.append({
            "days": hi - lo, "on_days": len(on),
            "mean_on": (sum(on) / len(on)) if on else None,
            "mean_off": (sum(off) / len(off)) if off else None,
            "usable": len(on) >= MINIMUM_TRIGGER_DAYS_PER_FOLD and bool(off),
            "met": (bool(on) and bool(off)
                    and len(on) >= MINIMUM_TRIGGER_DAYS_PER_FOLD
                    and sum(on) / len(on) < sum(off) / len(off))})
    return {
        "candidate": candidate["id"], "variable": candidate["variable"],
        "form": candidate["form"], "exposure": candidate["exposure"],
        "days": len(dates), "on_days": sum(flags), "unknown_days": unknown,
        "met": met, "usable": usable, "ratio": (met / usable) if usable else None,
        "outcome": _outcome(met, usable), "folds": folds}


def _check_window(window, name, returns_by_exposure):
    for exposure, rows in returns_by_exposure.items():
        for day in rows:
            if not _inside(window, day):
                raise ValueError(
                    f"REFUSED: {exposure} holds a row dated {day}, outside the {name} window "
                    f"{_window_dates(window)}. This step is handed that window and nothing "
                    f"else, and it raises rather than reading beyond it.")


def scan(space, *, series, returns_by_exposure):
    """Observe every candidate on the DISCOVERY window. Returns the observations and a summary."""
    _check_window(space["windows"]["discovery"], "discovery", returns_by_exposure)
    observations = []
    for candidate in space["candidates"]:
        rows = returns_by_exposure[candidate["exposure"]]
        dates = sorted(rows)
        observations.append(observe_candidate(space, candidate, series=series, dates=dates,
                                              returns=rows))
    passes = [o["candidate"] for o in observations if o["outcome"] == LINK_MET]
    summary = {
        "examined": len(observations), "passes": passes,
        "by_outcome": {name: sum(1 for o in observations if o["outcome"] == name)
                       for name in (LINK_MET, LINK_FAILED, LINK_UNREACHABLE)},
        "expected_false_passes_by_chance": round(expected_false_passes(space), 3),
        "total_tested_on_this_question": space["multiplicity"]["total_tested_on_this_question"],
        "reading": ("A pass here is NOT evidence: about one in four candidates with no relation to "
                    "the returns passes this rule. Only the holdout claim confirms anything."),
    }
    return observations, summary


def _inputs_digest(series, returns_by_exposure):
    return digest(encoded({
        "series": {sid: sorted((d, v) for d, v in values.items()) for sid, values in series.items()},
        "returns": {eid: sorted(rows.items()) for eid, rows in returns_by_exposure.items()}}))


def seal_scan(path, space_record, observations, summary, *, series, returns_by_exposure,
              scanned_at, code_revision):
    """Seal the scan. Idempotent on content, never on the clock."""
    content = {"schema_version": SCHEMA_VERSION, "kind": "monitor-screen-scan",
               "space_id": space_record["space_id"], "observations": observations,
               "summary": summary, "inputs_digest": _inputs_digest(series, returns_by_exposure)}
    scan_id = "MONITOR_SCREEN_SCAN|" + digest(encoded(content))
    registry = _registry(path, "scans")
    for record in registry["scans"]:
        if record["scan_id"] == scan_id:
            return record, False
    record = {**content, "scan_id": scan_id, "scanned_at": scanned_at,
              "code_revision": code_revision}
    registry["scans"].append(record)
    _atomic_write(Path(path), encoded(registry))
    return record, True


# --- the holdout, spent once --------------------------------------------------------------------

def holdout_confidence(passes):
    """1 minus 0.05 over the number that passed, declared in the sealed rule before the scan."""
    if passes < 1:
        raise ValueError("there is no holdout confidence for zero candidates")
    return f"{Decimal(1) - FAMILY_ALPHA / passes:.8f}"


def _difference_of_means(pairs):
    on = [value for flag, value in pairs if flag]
    off = [value for flag, value in pairs if not flag]
    if not on or not off:
        return None
    return sum(on) / len(on) - sum(off) / len(off)


def confirm_on_holdout(space_record, scan_record, *, series, load_holdout_returns, path,
                       opened_at, code_revision, resamples=2000, block_periods=5, seed=0):
    """Confirm the candidates that met the rule, on a holdout that can be opened ONCE.

    `load_holdout_returns()` is called only after the marker is sealed, and only if something met
    the rule: a scan with nothing to confirm leaves the holdout unopened.
    """
    space = space_record["space"]
    passed = [o for o in scan_record["observations"] if o["outcome"] == LINK_MET]
    if not passed:
        return {"status": "NOTHING_TO_CONFIRM", "holdout_opened": False}

    registry = _registry(path, "records")
    if any(r["space_id"] == space_record["space_id"] for r in registry["records"]):
        raise ValueError(
            "REFUSED: the holdout of this space was already opened. It is spent: whether the "
            "result was recorded or not, it is not read a second time.")
    marker = {"kind": "holdout-opened", "space_id": space_record["space_id"],
              "scan_id": scan_record["scan_id"], "opened_at": opened_at,
              "code_revision": code_revision, "candidates": [o["candidate"] for o in passed]}
    registry["records"].append(marker)
    _atomic_write(Path(path), encoded(registry))

    returns_by_exposure = load_holdout_returns()
    _check_window(space["windows"]["holdout"], "holdout", returns_by_exposure)
    confidence = holdout_confidence(len(passed))
    results = []
    for observation in passed:
        candidate = next(c for c in space["candidates"] if c["id"] == observation["candidate"])
        rows = returns_by_exposure[candidate["exposure"]]
        dates = sorted(rows)
        variable = _variable(space, candidate["variable"])
        fixed = space["fixed_parameters"]
        flags, unknown = state_flags(
            candidate["form"], dates, series[variable["series"]], lag_days=variable["lag_days"],
            change_horizon=fixed["change_horizon_days"], median_window=fixed["median_window_days"])
        pairs = [(flags[i], rows[day]) for i, day in enumerate(dates)]
        on_days = sum(flags)
        off_days = len(pairs) - on_days
        result = {"candidate": candidate["id"], "days": len(pairs), "on_days": on_days,
                  "off_days": off_days, "unknown_days": unknown, "confidence": confidence}
        if on_days < MINIMUM_TRIGGER_DAYS_PER_FOLD or off_days < MINIMUM_TRIGGER_DAYS_PER_FOLD:
            result.update({"status": "UNTESTABLE", "confirmed": False,
                           "why": "a group has fewer than the minimum days: a claim on it would "
                                  "rest on too few observations, and is not read as a pass"})
        else:
            bound = bootstrap_bound(pairs, _difference_of_means, side=BOUND_UPPER,
                                    confidence=confidence, resamples=resamples,
                                    block_periods=block_periods, seed=seed)
            point = _difference_of_means(pairs)
            result.update({"status": "TESTED", "difference_point": point,
                           "difference_upper_bound": bound,
                           "confirmed": bound is not None and bound < 0})
        results.append(result)

    outcome = {"kind": "holdout-result", "space_id": space_record["space_id"],
               "scan_id": scan_record["scan_id"], "opened_at": opened_at,
               "code_revision": code_revision, "confidence": confidence,
               "passed_discovery": len(passed), "results": results,
               "confirmed": [r["candidate"] for r in results if r["confirmed"]],
               "claim": space["holdout_rule"]["claim"],
               "bootstrap": {"resamples": resamples, "block_periods": block_periods, "seed": seed}}
    outcome["record_id"] = "MONITOR_SCREEN_HOLDOUT|" + digest(encoded(outcome))
    registry["records"].append(outcome)
    _atomic_write(Path(path), encoded(registry))
    return {"status": "CONFIRMED_OR_NOT", "holdout_opened": True, "result": outcome}


def holdout_was_opened(path, space_id):
    return any(r["space_id"] == space_id for r in _registry(path, "records")["records"])
