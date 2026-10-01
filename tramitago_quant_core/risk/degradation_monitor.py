"""Degradation monitor (2026-09-30).

DOC-011 §9.4 turned monitorability into a gate, which makes this a prerequisite
rather than a future capability: without it NOTHING can be admitted, because an
opportunity that cannot be watched cannot satisfy "stop exploiting it when the
evidence stops supporting it" -- it is missing half of its lifecycle.

WHAT THIS IS NOT: a P&L watcher. Distinguishing a bad run from a dead edge by
P&L takes months of evidence, and by then the loss is already paid. The monitor
watches a DECLARED OBSERVABLE VARIABLE -- the condition that was supposed to make
the opportunity work -- so degradation is read off the cause instead of inferred
from the symptom. Carry is the clean example: funding is published every eight
hours, so its compression is visible the same day rather than after a quarter of
disappointing returns.

TWO DESIGN DECISIONS THAT MATTER:

Confirmation, not a hair trigger. A single breach is noise -- in the measured
crypto funding, 3-5% of days were negative while the carry was healthy. A rule
that exits on one bad observation would liquidate several times a year for
nothing. Degradation is confirmed only after `confirmation_periods` consecutive
breaches, which is also what makes detection latency computable.

Hysteresis on re-entry. The re-entry threshold must be STRICTLY BETTER than the
degradation threshold. With a single shared threshold a variable sitting on the
boundary flaps in and out, paying transaction costs each time. The gap is the
price of stability and must be declared, not discovered.

The admissibility check ties this module to the drawdown limit: detection must
be faster than the time a dead edge needs to consume the declared drawdown. If
it is not, the monitor is decorative -- you would find out after it had already
cost you the maximum you said you would tolerate.
"""

from decimal import Decimal, InvalidOperation

from tramitago_quant_core.shared.util import digest, encoded

MONITORING_CONTRACT_SCHEMA_VERSION = "1"

# Which way is bad. For carry, funding falling BELOW a floor is degradation; for
# a spread trade it could be the spread widening ABOVE one.
DEGRADES_BELOW = "BELOW"
DEGRADES_ABOVE = "ABOVE"
DEGRADATION_DIRECTIONS = (DEGRADES_BELOW, DEGRADES_ABOVE)

# What a confirmed degradation does. SUSPEND allows re-entry if the condition
# recovers; RETIRE is terminal and is for a mechanism believed structurally gone.
ACTION_SUSPEND = "SUSPEND"
ACTION_RETIRE = "RETIRE"
MONITOR_ACTIONS = (ACTION_SUSPEND, ACTION_RETIRE)

STATE_HEALTHY = "HEALTHY"
STATE_SUSPENDED = "SUSPENDED"
STATE_RETIRED = "RETIRED"
MONITOR_STATES = (STATE_HEALTHY, STATE_SUSPENDED, STATE_RETIRED)


def _decimal(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be declared as a string")
    try:
        amount = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{name} is not a decimal") from error
    if not amount.is_finite():
        raise ValueError(f"{name} must be finite")
    return amount


def monitoring_contract(*, variable, observation_period_seconds, degradation_threshold,
                        degrades_when, confirmation_periods, action,
                        re_entry_threshold=None, source):
    """Seal one declared monitoring rule.

    `variable` is the observable the opportunity rests on, not its P&L.
    `source` records where the observation comes from, so a later reader can
    check it is still published at the frequency claimed.
    """
    if not isinstance(variable, str) or not variable.strip():
        raise ValueError("A monitoring contract must declare the variable it watches")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("A monitoring contract must declare its observation source")
    if degrades_when not in DEGRADATION_DIRECTIONS:
        raise ValueError("Degradation direction must be " + " or ".join(DEGRADATION_DIRECTIONS))
    if action not in MONITOR_ACTIONS:
        raise ValueError("Action must be " + " or ".join(MONITOR_ACTIONS))
    for name, value in (("observation_period_seconds", observation_period_seconds),
                        ("confirmation_periods", confirmation_periods)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    threshold = _decimal(degradation_threshold, "degradation_threshold")

    if action == ACTION_SUSPEND:
        if re_entry_threshold is None:
            raise ValueError("A suspending contract must declare its re-entry threshold")
        re_entry = _decimal(re_entry_threshold, "re_entry_threshold")
        # Hysteresis: re-entry must be strictly harder to reach than the exit,
        # or a variable resting on the boundary flaps and pays costs each time.
        strict = re_entry > threshold if degrades_when == DEGRADES_BELOW else re_entry < threshold
        if not strict:
            raise ValueError(
                "Re-entry threshold must be strictly better than the degradation "
                "threshold, or the position will flap at the boundary")
    elif re_entry_threshold is not None:
        raise ValueError("A retiring contract cannot declare a re-entry threshold")

    content = {
        "schema_version": MONITORING_CONTRACT_SCHEMA_VERSION,
        "variable": variable.strip(),
        "source": source.strip(),
        "observation_period_seconds": observation_period_seconds,
        "degradation_threshold": degradation_threshold,
        "degrades_when": degrades_when,
        "confirmation_periods": confirmation_periods,
        "action": action,
        "re_entry_threshold": re_entry_threshold,
    }
    return {**content, "contract_id": "MONITORING_CONTRACT|" + digest(encoded(content))}


def verified_monitoring_contract(contract):
    """Re-derive the identity, failing closed on any altered threshold."""
    if not isinstance(contract, dict) or "contract_id" not in contract:
        raise ValueError("Monitoring contract is invalid")
    content = {key: value for key, value in contract.items() if key != "contract_id"}
    if contract["contract_id"] != "MONITORING_CONTRACT|" + digest(encoded(content)):
        raise ValueError("Monitoring contract identity does not match its terms")
    if (content.get("degrades_when") not in DEGRADATION_DIRECTIONS
            or content.get("action") not in MONITOR_ACTIONS):
        raise ValueError("Monitoring contract terms are invalid")
    return contract


def detection_latency_seconds(contract):
    """How long, at worst, before a dead edge is recognised as dead."""
    verified_monitoring_contract(contract)
    return contract["confirmation_periods"] * contract["observation_period_seconds"]


def monitoring_is_admissible(contract, *, drawdown_limit, expected_daily_loss_if_dead):
    """R3 against R1: detection must beat the drawdown it is supposed to protect.

        detection_latency  <  drawdown_limit / expected_daily_loss_if_dead

    Both sides are declared before operating. Returns (admissible, detail) rather
    than raising, so a refusal can state the numbers that produced it.
    """
    verified_monitoring_contract(contract)
    limit = _decimal(drawdown_limit, "drawdown_limit")
    daily_loss = _decimal(expected_daily_loss_if_dead, "expected_daily_loss_if_dead")
    if limit <= 0:
        return False, "drawdown limit must be positive"
    if daily_loss <= 0:
        # A dead edge that loses nothing cannot breach a drawdown, so latency is
        # unconstrained -- but claiming that is a strong assertion, not a default.
        return False, ("expected daily loss if dead must be positive; a claim that a "
                       "dead edge costs nothing has to be argued, not assumed")
    days_to_limit = limit / daily_loss
    latency_days = Decimal(detection_latency_seconds(contract)) / Decimal(86400)
    if latency_days >= days_to_limit:
        return False, (f"detection takes {latency_days} days but a dead edge would "
                       f"consume the {drawdown_limit} drawdown limit in {days_to_limit} days")
    return True, (f"detection in {latency_days} days against {days_to_limit} days "
                  f"to the drawdown limit")


def _breaches(contract, observation):
    threshold = Decimal(contract["degradation_threshold"])
    value = observation if isinstance(observation, Decimal) else _decimal(
        observation if isinstance(observation, str) else repr(observation), "observation")
    return (value < threshold if contract["degrades_when"] == DEGRADES_BELOW
            else value > threshold)


def _recovers(contract, observation):
    threshold = Decimal(contract["re_entry_threshold"])
    value = observation if isinstance(observation, Decimal) else _decimal(
        observation if isinstance(observation, str) else repr(observation), "observation")
    return (value >= threshold if contract["degrades_when"] == DEGRADES_BELOW
            else value <= threshold)


def evaluate_monitor(contract, observations, *, state=STATE_HEALTHY):
    """Advance the monitor over a sequence of observations.

    Observations are ordered oldest to newest. Returns the resulting state, the
    run of consecutive confirming observations at the end, and the index where a
    transition was confirmed -- so the caller can report WHEN, not just WHAT.

    RETIRED is terminal: nothing re-enters from it. That distinction is the
    reason SUSPEND exists, and it is also why RETIRED must never be the default.
    """
    verified_monitoring_contract(contract)
    if state not in MONITOR_STATES:
        raise ValueError("Unknown monitor state")
    if not isinstance(observations, list):
        raise ValueError("Observations must be a list")
    needed = contract["confirmation_periods"]
    run, transition_index = 0, None

    for index, observation in enumerate(observations):
        if state == STATE_RETIRED:
            break
        if state == STATE_HEALTHY:
            run = run + 1 if _breaches(contract, observation) else 0
            if run >= needed:
                state = (STATE_SUSPENDED if contract["action"] == ACTION_SUSPEND
                         else STATE_RETIRED)
                transition_index, run = index, 0
        elif state == STATE_SUSPENDED:
            run = run + 1 if _recovers(contract, observation) else 0
            if run >= needed:
                state, transition_index, run = STATE_HEALTHY, index, 0

    return {
        "contract_id": contract["contract_id"],
        "state": state,
        "consecutive_run": run,
        "confirmation_periods": needed,
        "transition_index": transition_index,
        "observations": len(observations),
    }
