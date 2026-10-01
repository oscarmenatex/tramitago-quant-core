"""Nivel 4 -- transaction costs (2026-09-30).

DOC-011 §9.4 turned "net after costs" into a gate, which made this a
prerequisite rather than an option: until net returns can be computed, nothing
is evaluable against the admission criteria at all.

Everything the project has sealed to date is GROSS. Thirty-three hypotheses were
accepted or rejected on a quantity no one could have traded.

TWO REGIMES, because one model does not serve both (proposal §5):

  ROTATION -- a position that changes with a signal. Cost is incurred on each
      TRANSITION, so it scales with turnover, which is a property of the signal
      and not of the market. A signal that flips daily pays many times what one
      holding for weeks pays on the same instrument.
  HOLDING -- a position carried for a long stretch, entered and exited once,
      with a recurring per-period charge (margin interest, borrow).

RATES ARE DECLARED, NEVER HARDCODED. Fee schedules change and a number baked
into source rots silently. A cost contract is declared, sealed and re-derivable
exactly like the risk contract, so every sealed result says which rates produced
it.

ON AMORTISATION, deliberately NOT done: a held position does amortise its entry
and exit economically, but SMOOTHING them across periods in the MEASUREMENT
would hand the Sharpe calculation a distribution the market never produced.
Costs are charged in the period they occur. The amortisation then emerges from
the arithmetic over a long hold instead of being assumed into it -- and over a
short one it correctly looks expensive.

This module knows nothing about Hypotheses, groups or directions. It takes
positions and gross returns. `positions_from_groups` is offered as the
translation, kept separate so the cost arithmetic stays independent of research
vocabulary.
"""

from decimal import Decimal, InvalidOperation

from tramitago_quant_core.shared.util import digest, encoded

COST_CONTRACT_SCHEMA_VERSION = "1"
COST_REGIME_ROTATION = "ROTATION"
COST_REGIME_HOLDING = "HOLDING"
COST_REGIMES = (COST_REGIME_ROTATION, COST_REGIME_HOLDING)

_RATE_FIELDS = ("commission_rate", "half_spread_rate", "slippage_rate",
                "recurring_rate_per_period")


def _rate(value, name):
    """A rate is a decimal fraction of notional, declared as a string so the
    sealed contract carries exactly what was declared and not a float's
    approximation of it."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Cost rate {name} must be declared as a string")
    try:
        amount = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"Cost rate {name} is not a decimal") from error
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"Cost rate {name} must be finite and non-negative")
    if amount >= 1:
        raise ValueError(f"Cost rate {name} must be a fraction below 1")
    return amount


def cost_contract(*, regime, commission_rate, half_spread_rate, slippage_rate,
                  recurring_rate_per_period="0", legs=1, source):
    """Seal one declared cost structure.

    `source` records WHERE the rates came from -- a broker fee schedule, a
    measured spread, a venue's published tier. A contract whose provenance is
    not stated cannot be audited later, when the rates will have changed.
    `legs` is 2 for a pair or a cash-and-carry: both sides pay.
    """
    if regime not in COST_REGIMES:
        raise ValueError("Cost regime must be " + " or ".join(COST_REGIMES))
    if not isinstance(legs, int) or isinstance(legs, bool) or legs < 1 or legs > 4:
        raise ValueError("Legs must be a small positive integer")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("A cost contract must declare the source of its rates")
    rates = {
        "commission_rate": commission_rate, "half_spread_rate": half_spread_rate,
        "slippage_rate": slippage_rate,
        "recurring_rate_per_period": recurring_rate_per_period,
    }
    for name, value in rates.items():
        _rate(value, name)
    if regime == COST_REGIME_ROTATION and Decimal(recurring_rate_per_period) != 0:
        raise ValueError("A rotation contract carries no recurring per-period rate")
    content = {
        "schema_version": COST_CONTRACT_SCHEMA_VERSION,
        "regime": regime,
        "legs": legs,
        "source": source.strip(),
        **rates,
    }
    return {**content, "contract_id": "COST_CONTRACT|" + digest(encoded(content))}


def verified_cost_contract(contract):
    """Re-derive the identity, failing closed on any alteration of the rates."""
    if not isinstance(contract, dict) or "contract_id" not in contract:
        raise ValueError("Cost contract is invalid")
    content = {key: value for key, value in contract.items() if key != "contract_id"}
    if contract["contract_id"] != "COST_CONTRACT|" + digest(encoded(content)):
        raise ValueError("Cost contract identity does not match its terms")
    if content.get("regime") not in COST_REGIMES:
        raise ValueError("Cost contract regime is invalid")
    for name in _RATE_FIELDS:
        _rate(content.get(name), name)
    return contract


def cost_per_side(contract):
    """What one side of one transition costs, as a fraction of notional.

    Commission, half the spread and slippage are all paid per side, and every
    leg pays. A round trip is twice this; a pair round trip is four times the
    single-leg side cost.
    """
    verified_cost_contract(contract)
    per_leg = (Decimal(contract["commission_rate"])
               + Decimal(contract["half_spread_rate"])
               + Decimal(contract["slippage_rate"]))
    return per_leg * Decimal(contract["legs"])


def positions_from_groups(groups, expected_direction, target_group="UPPER"):
    """Translate a classification sequence into a position sequence.

    A Hypothesis says returns DIFFER between groups; a position acts on that.
    Which group is held follows from the declared direction: INCREASE means the
    upper group is the one expected to earn more, DECREASE means the other one.
    Kept here rather than inside the cost arithmetic so that arithmetic stays
    ignorant of research vocabulary.
    """
    if expected_direction not in ("INCREASE", "DECREASE"):
        raise ValueError("Expected direction must be INCREASE or DECREASE")
    held = target_group if expected_direction == "INCREASE" else "LOWER_OR_EQUAL"
    return [1 if group == held else 0 for group in groups]


def net_returns(contract, positions, gross_returns):
    """Per-period net returns, charging costs in the period they occur.

    Returns a list the same length as the inputs. A period out of position
    returns 0.0 and costs nothing. Entry is charged in the period it happens;
    the final exit is charged in the last period if the position is still open,
    because a position that is never closed has not realised anything.
    """
    verified_cost_contract(contract)
    if not isinstance(positions, list) or not isinstance(gross_returns, list):
        raise ValueError("Positions and gross returns must be lists")
    if len(positions) != len(gross_returns) or not positions:
        raise ValueError("Positions and gross returns must align and be non-empty")
    if any(position not in (0, 1) for position in positions):
        raise ValueError("A position is 1 (held) or 0 (flat)")

    side = float(cost_per_side(contract))
    recurring = float(Decimal(contract["recurring_rate_per_period"]))
    holding = contract["regime"] == COST_REGIME_HOLDING

    net, previous = [], 0
    for index, (position, gross) in enumerate(zip(positions, gross_returns)):
        value = float(gross) * position
        if position != previous:
            value -= side                       # entering, or exiting into flat
        if position == 1 and holding:
            value -= recurring
        if position == 1 and index == len(positions) - 1:
            value -= side                       # the close that never happened
        net.append(value)
        previous = position
    return net


def cost_summary(contract, positions, gross_returns):
    """What the costs did, which is the number worth reporting alongside a net
    Sharpe: a strategy can stay profitable while costs consume most of its edge,
    and that is a materially different object from one that is cheap to run."""
    gross_total = sum(float(g) * p for g, p in zip(gross_returns, positions))
    net = net_returns(contract, positions, gross_returns)
    transitions = sum(1 for index, position in enumerate(positions)
                      if position != (positions[index - 1] if index else 0))
    periods_held = sum(positions)
    return {
        "contract_id": contract["contract_id"],
        "regime": contract["regime"],
        "periods": len(positions),
        "periods_held": periods_held,
        "transitions": transitions,
        "turnover_per_period": transitions / len(positions),
        "gross_total": gross_total,
        "net_total": sum(net),
        "cost_total": gross_total - sum(net),
        "cost_per_period_held": ((gross_total - sum(net)) / periods_held
                                 if periods_held else None),
    }
