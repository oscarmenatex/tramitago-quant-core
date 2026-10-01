"""Supervision of a running loop -- the first thing the platform watches by itself.

Until now the platform computed verdicts nobody consumed: `evaluate_monitor` and
`tranche_is_admissible` existed only as imports. A machinery-verification loop is
now ticking every five minutes and NOTHING READS ITS OUTPUT. If it starts failing
silently, no one learns.

This supervises THE LOOP, not a strategy. There is nothing admitted to operate,
so anything that sized or traded would be anticipatory. What is not anticipatory
is watching the one thing that is actually running -- and it is the same pattern
the destination requires ("stop when the evidence stops supporting it") applied
where it can be exercised honestly today.

THE STATE VARIABLE IS OPERATIONAL, NEVER FINANCIAL. It is the rate of ticks that
complete, not a return. A P&L from a rejected signal means nothing and must never
reach a supervision decision; keeping the variable operational makes that
structural rather than a reminder.

TWO FAILURES, AND ONLY ONE IS VISIBLE IN THE LOG:

  DEGRADATION  ticks arrive and increasingly fail. Visible in the record, and
               handled by the sealed monitoring contract through evaluate_monitor
               -- finally exercising machinery that was dead code.
  ABSENCE      ticks stop arriving. NOT visible in the record, because a stopped
               writer leaves no entry saying so. A supervisor that only reads the
               log cannot detect its own silence, so staleness is checked against
               the clock and the expected cadence, independently of the contents.

That second one is the reason this module exists at all rather than a threshold
on a number: the most likely way an unattended loop dies is by stopping, and
stopping produces no evidence.
"""

import json
import math
from pathlib import Path

from tramitago_quant_core.shared.util import epoch
from tramitago_quant_core.risk.degradation_monitor import (
    evaluate_monitor, verified_monitoring_contract, STATE_HEALTHY,
)

SUPERVISION_SCHEMA_VERSION = "1"

SUPERVISION_OK = "RUNNING"
SUPERVISION_STALE = "STALE"
SUPERVISION_DEGRADED = "DEGRADED"
SUPERVISION_STATES = (SUPERVISION_OK, SUPERVISION_STALE, SUPERVISION_DEGRADED)

# A tick that completed. Anything else -- a recorded error, a blocked activation
# -- counts against the rate, because the loop is being asked to prove it runs.
TICK_SUCCESS_OUTCOME = "PASS"


def read_observations(path):
    """Load the append-only operational record. A malformed line is a failure of
    the record itself and is raised, never skipped: silently dropping lines would
    make a corrupted log look healthy."""
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    for number, line in enumerate(path.read_text("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise ValueError(f"Operational record is corrupt at line {number}") from error
    return rows


def tick_success_rate(observations, window):
    """Fraction of the last `window` ticks that completed.

    A rate rather than a boolean so the monitoring contract's hysteresis has a
    continuous variable to work with: with a binary signal there is no gap
    between the exit and re-entry thresholds and the state would flap on every
    single failure.
    """
    if not isinstance(window, int) or isinstance(window, bool) or window < 1:
        raise ValueError("Window must be a positive integer")
    if not observations:
        return None
    recent = observations[-window:]
    passing = sum(1 for row in recent if row.get("outcome") == TICK_SUCCESS_OUTCOME)
    return passing / len(recent)


def staleness_seconds(observations, now_utc):
    """Seconds since the last recorded tick, or None if nothing was ever recorded.

    Checked against the clock rather than the record, because ABSENCE leaves no
    entry. This is the half of supervision the monitoring contract cannot see.
    """
    if not observations:
        return None
    last = observations[-1].get("observed_at")
    if not isinstance(last, str):
        raise ValueError("Operational record has an observation without a timestamp")
    return epoch(now_utc) - epoch(last)


def supervise(*, contract, observations, now_utc, expected_cadence_seconds,
              missed_ticks_tolerated=3, rate_window=12, state=STATE_HEALTHY):
    """Decide whether the loop is running, stale, or degrading.

    Staleness is evaluated FIRST and short-circuits: a loop that stopped is not
    "healthy with an old rate", and computing a success rate over ticks that are
    no longer arriving would report health from history.
    """
    verified_monitoring_contract(contract)
    if expected_cadence_seconds <= 0:
        raise ValueError("Expected cadence must be positive")
    if not isinstance(missed_ticks_tolerated, int) or missed_ticks_tolerated < 1:
        raise ValueError("Missed ticks tolerated must be a positive integer")

    if not observations:
        return {"state": SUPERVISION_STALE, "reason": "no tick has ever been recorded",
                "observations": 0, "success_rate": None, "staleness_seconds": None,
                "monitor_state": state, "is_operational_not_financial": True}

    stale_after = expected_cadence_seconds * missed_ticks_tolerated
    stale = staleness_seconds(observations, now_utc)
    if stale > stale_after:
        return {"state": SUPERVISION_STALE,
                "reason": (f"no tick for {stale}s, beyond {missed_ticks_tolerated} "
                           f"missed at a {expected_cadence_seconds}s cadence"),
                "observations": len(observations), "success_rate": None,
                "staleness_seconds": stale, "monitor_state": state,
                "is_operational_not_financial": True}

    # A SERIES of rates, not one. evaluate_monitor counts consecutive breaches
    # within the sequence it is given and does not carry a partial count between
    # calls, so feeding it a single value each time would reset the confirmation
    # counter forever and nothing could ever be confirmed. One rolling rate per
    # recent tick, enough of them to satisfy the contract's confirmation.
    confirmation = contract["confirmation_periods"]
    rates = []
    for offset in range(min(confirmation, len(observations)) - 1, -1, -1):
        upto = observations[:len(observations) - offset]
        rates.append(f"{tick_success_rate(upto, rate_window):.6f}")
    rate = float(rates[-1])
    monitor = evaluate_monitor(contract, rates, state=state)
    degraded = monitor["state"] != STATE_HEALTHY
    return {
        "state": SUPERVISION_DEGRADED if degraded else SUPERVISION_OK,
        "reason": (f"tick success rate {rate:.3f} over the last "
                   f"{min(rate_window, len(observations))} ticks"),
        "observations": len(observations), "success_rate": rate,
        "staleness_seconds": stale, "monitor_state": monitor["state"],
        "consecutive_run": monitor["consecutive_run"],
        "is_operational_not_financial": True,
    }
