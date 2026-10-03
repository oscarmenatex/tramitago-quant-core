"""Lifecycle triggers as pure functions: facts in, notifications out.

NOTHING HERE ACTS. A trigger reads facts it is handed and returns notifications. It
never reads the clock, a file or the network, never writes, and never touches capital:
the decision to suspend, retire or promote stays with the Director, and a notification
only says what changed and what the options are.

THE FACTS COME FROM THE CALLER. `lifecycle_status()` already derives the state of every
Hypothesis; the rest (monitor states, last bars, declared lives) are handed in. That
keeps each trigger testable with a dictionary and keeps the clock out: `as_of` is a date
the caller passes.

A NOTIFICATION IS IDEMPOTENT. Its identity is a digest of the trigger, the subject and
what changed, and deliberately NOT of the date or of how many days have passed, so a
condition that persists produces the same notification every run and a notifier can
send it once. How long it has persisted is carried separately in `facts`.

THE SEVERITIES, and why each trigger has the one it has:
  CRITICAL  something that may be costing money NOW or that invalidates what an
            operating position relies on: a suspended monitor, an instrument that
            stopped quoting, a family that stopped generalizing.
  WARNING   evidence is stale or was withdrawn: an overdue re-measurement, an expired
            life, a voided measurement, a cost drifted past its tolerance.
  INFO      something is due or improved and nobody is harmed by waiting.

A bad P&L is NOT a trigger. Telling a bad run from a dead mechanism by P&L takes months,
and the loss is paid by then.
"""

from datetime import date

from tramitago_quant_core.research.lifecycle import (
    AUTORIZADO, MEDIDO, DENEGADO, add_months)
from tramitago_quant_core.risk.degradation_monitor import STATE_SUSPENDED, STATE_RETIRED
from tramitago_quant_core.shared.util import digest, encoded

CRITICAL, WARNING, INFO = "CRITICAL", "WARNING", "INFO"
_RANK = {CRITICAL: 0, WARNING: 1, INFO: 2}
_STATE_ORDER = {DENEGADO: 0, MEDIDO: 1, AUTORIZADO: 2}


def _date(value):
    return value if isinstance(value, date) else date.fromisoformat(value)


def _note(trigger, severity, subject, what, options, facts=None):
    identity = digest(encoded({"trigger": trigger, "subject": subject["hypothesis_id"],
                               "what": what}))
    return {"notification_id": "NOTIFICATION|" + identity, "trigger": trigger,
            "severity": severity, "subject": subject, "what_changed": what,
            "options": list(options), "facts": facts or {}}


def _subject(item):
    return {"hypothesis_id": item["hypothesis_id"], "label": item.get("label", "")}


# --- calendar ---------------------------------------------------------------------------

def remeasure_due(report, *, warn_days):
    """A re-measurement that is overdue, or due within `warn_days`."""
    as_of = _date(report["as_of"])
    notes = []
    for member in report["members"]:
        due = _date(member["remeasure_due"])
        if due < as_of:
            notes.append(_note(
                "REMEASURE_OVERDUE", WARNING, _subject(member),
                "the re-measurement is overdue; the sealed verdict is older than the cadence",
                ["re-measure with the data since the last decision",
                 "extend the cadence in config/lifecycle.json",
                 "retire the Hypothesis"],
                {"due": due.isoformat(), "days_overdue": (as_of - due).days}))
        elif (due - as_of).days <= warn_days:
            notes.append(_note(
                "REMEASURE_DUE_SOON", INFO, _subject(member),
                "a re-measurement falls due inside the warning window",
                ["schedule the re-measurement"],
                {"due": due.isoformat(), "days_left": (due - as_of).days}))
    return notes


def life_expired(lives, *, as_of, warn_days):
    """A declared useful life that has run out, or will within `warn_days`.

    Each item: hypothesis_id, label, declared_on, life_years. The life is the one the
    Hypothesis declared for itself, so an expiry is its own deadline arriving.
    """
    as_of = _date(as_of)
    notes = []
    for item in lives:
        ends = add_months(_date(item["declared_on"]), 12 * int(item["life_years"]))
        if ends <= as_of:
            notes.append(_note(
                "LIFE_EXPIRED", WARNING, _subject(item),
                "the declared useful life has ended; no evidence covers it beyond this date",
                ["renew the evidence with a new pre-declared measurement", "retire it"],
                {"ended": ends.isoformat(), "days_since": (as_of - ends).days}))
        elif (ends - as_of).days <= warn_days:
            notes.append(_note(
                "LIFE_ENDING", INFO, _subject(item),
                "the declared useful life ends inside the warning window",
                ["start renewing the evidence"],
                {"ends": ends.isoformat(), "days_left": (ends - as_of).days}))
    return notes


def forward_test_due(tests, *, as_of):
    """A forward-dated Hypothesis whose first permitted look has arrived.

    Before `evaluable_from` nothing may look at it, and this trigger says nothing then:
    a notification that named its results early would be the look itself.
    """
    as_of = _date(as_of)
    return [_note("FORWARD_TEST_DUE", INFO, _subject(item),
                  "the forward test reaches the date from which it may be evaluated",
                  ["evaluate it under the procedure sealed with it"],
                  {"evaluable_from": _date(item["evaluable_from"]).isoformat()})
            for item in tests if as_of >= _date(item["evaluable_from"])]


# --- events -----------------------------------------------------------------------------

def member_state_changes(before, after):
    """A state that moved between two lifecycle reports.

    Losing AUTORIZADO is CRITICAL: an operation that rested on it no longer has the
    evidence it was authorised on. Any other fall is a WARNING and a rise is INFO.
    """
    previous = {m["hypothesis_id"]: m for m in before["members"]}
    notes = []
    for member in after["members"]:
        old = previous.get(member["hypothesis_id"])
        if old is None or old["state"] == member["state"]:
            continue
        fell = _STATE_ORDER[member["state"]] < _STATE_ORDER[old["state"]]
        severity = (CRITICAL if fell and old["state"] == AUTORIZADO
                    else WARNING if fell else INFO)
        notes.append(_note(
            "STATE_CHANGED", severity, _subject(member),
            f"the derived state moved from {old['state']} to {member['state']}",
            ["review the evidence that moved it"] + (
                ["suspend any operation that relied on the earlier state"]
                if severity == CRITICAL else []),
            {"gates_failed": member["gates_failed"]}))
    return notes


def family_verdict_changes(before, after):
    """A family whose latest verdict changed between two snapshots of the registry.

    Leaving GENERALIZES is CRITICAL, because every member's clearance rested on it.
    """
    def latest(verdicts):
        out = {}
        for record in verdicts:
            current = out.get(record["family_id"])
            if current is None or record["decided_at"] > current["decided_at"]:
                out[record["family_id"]] = record
        return out

    old, new = latest(before), latest(after)
    notes = []
    for family_id, record in new.items():
        was = old.get(family_id)
        if was is None or was["verdict"] == record["verdict"]:
            continue
        severity = CRITICAL if was["verdict"] == "GENERALIZES" else INFO
        notes.append(_note(
            "FAMILY_VERDICT_CHANGED", severity,
            {"hypothesis_id": family_id, "label": "mechanism family"},
            f"the family verdict moved from {was['verdict']} to {record['verdict']}",
            ["re-derive the clearance of every member"],
            {"reason": record["reason"]}))
    return notes


def monitor_states(monitors):
    """A monitor that has suspended or retired a position it was watching.

    Each item: hypothesis_id, label, state, since, variable. HEALTHY says nothing.
    """
    notes = []
    for item in monitors:
        if item["state"] == STATE_SUSPENDED:
            notes.append(_note(
                "MONITOR_SUSPENDED", CRITICAL, _subject(item),
                "the monitor confirmed degradation of the variable it was declared on",
                ["confirm the suspension", "override it, with the reason written down"],
                {"since": str(item.get("since")), "variable": item.get("variable")}))
        elif item["state"] == STATE_RETIRED:
            notes.append(_note(
                "MONITOR_RETIRED", CRITICAL, _subject(item),
                "the monitor retired the position",
                ["confirm the retirement"], {"since": str(item.get("since"))}))
    return notes


def measurements_voided(results):
    """A measurement that was voided, with the reason it gave. Each item:
    hypothesis_id, label, void."""
    return [_note("MEASUREMENT_VOID", WARNING, _subject(item),
                  "a measurement was voided, so nothing was judged: " + str(item["void"]),
                  ["fix the cause and re-run", "accept that it cannot be measured"])
            for item in results if item.get("void")]


def instruments_gone_quiet(last_bars, *, as_of, max_gap_days):
    """An instrument that has stopped quoting. {symbol: {last_bar, hypothesis_id, label}}.

    A fund can close (PUTW did): the exit then happens at a time and price the platform
    did not choose, and no return series shows the risk in advance.
    """
    as_of = _date(as_of)
    notes = []
    for symbol, item in sorted(last_bars.items()):
        gap = (as_of - _date(item["last_bar"])).days
        if gap > max_gap_days:
            notes.append(_note(
                "INSTRUMENT_STOPPED_QUOTING", CRITICAL, _subject(item),
                f"{symbol} has no bar for longer than the allowed gap",
                ["check whether the instrument closed or was halted",
                 "suspend any operation in it"],
                {"symbol": symbol, "last_bar": _date(item["last_bar"]).isoformat(),
                 "days_without_a_bar": gap}))
    return notes


def drifts(observations, *, tolerance):
    """A declared figure that reality has moved past, beyond a relative tolerance.

    Each item: hypothesis_id, label, metric, declared, observed, worse_when ("higher" for
    a cost, "lower" for a capacity).
    """
    tolerance = float(tolerance)
    notes = []
    for item in observations:
        declared, observed = float(item["declared"]), float(item["observed"])
        if declared == 0:
            continue
        change = (observed - declared) / abs(declared)
        worse = change > tolerance if item["worse_when"] == "higher" else change < -tolerance
        if worse:
            notes.append(_note(
                "DRIFT", WARNING, _subject(item),
                f"{item['metric']} drifted past its tolerance against what was declared",
                ["re-measure with the observed figure", "revise the declared figure, "
                 "as a new version"],
                {"metric": item["metric"], "declared": item["declared"],
                 "observed": item["observed"], "relative_change": round(change, 4)}))
    return notes


# --- all of them ------------------------------------------------------------------------

def evaluate_triggers(*, report, settings, as_of, before_report=None, verdicts_before=None,
                      verdicts_after=None, monitors=(), voided=(), last_bars=None,
                      forward_tests=(), lives=(), observed_drifts=()):
    """Run every trigger, drop duplicates, and order by severity then identity.

    A trigger whose input was not supplied does not run: silence is "not asked", never
    "all clear", and the caller decides which questions to put.
    """
    notes = remeasure_due(report, warn_days=settings["remeasure_warn_days"])
    if before_report is not None:
        notes += member_state_changes(before_report, report)
    if verdicts_before is not None and verdicts_after is not None:
        notes += family_verdict_changes(verdicts_before, verdicts_after)
    notes += monitor_states(monitors)
    notes += measurements_voided(voided)
    if last_bars is not None:
        notes += instruments_gone_quiet(last_bars, as_of=as_of,
                                        max_gap_days=settings["instrument_max_gap_days"])
    notes += forward_test_due(forward_tests, as_of=as_of)
    notes += life_expired(lives, as_of=as_of, warn_days=settings["life_warn_days"])
    notes += drifts(observed_drifts, tolerance=settings["drift_tolerance"])
    unique = {note["notification_id"]: note for note in notes}
    return sorted(unique.values(),
                  key=lambda n: (_RANK[n["severity"]], n["notification_id"]))
