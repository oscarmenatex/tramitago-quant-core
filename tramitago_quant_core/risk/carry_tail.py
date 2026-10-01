"""Tail model for a cash-and-carry position (2026-09-30).

The destination redefinition made this the deciding work: carry's magnitude,
mechanism and monitorability are all established, and what remains unknown is
the tail. "How far can the basis gap, at what margin does the short leg
liquidate, and does the worst case fit inside the drawdown limit?"

WHAT THIS MODULE DOES NOT DO, stated first because it is the important part:

    IT DOES NOT ESTIMATE THE BASIS DISTRIBUTION. It cannot. The sealed evidence
    holds Hyperliquid FUNDING RATES (timestamp and rate) and Coinbase SPOT
    closes. There are no PERPETUAL prices anywhere in it, so the spot-perp basis
    has never been observed by this project. Producing a distribution from what
    exists would be invention.

    The stress scenario is therefore a REQUIRED DECLARED INPUT with a mandatory
    source, exactly like the cost contract's rates. The arithmetic below is
    exact; the number you feed it is an assumption until perpetual mark prices
    are captured and the basis is measured.

THE STRUCTURAL FACT THAT DOMINATES, and which needs no data at all:

    A cash-and-carry is delta neutral -- long spot, short perp, same notional --
    so price moves cancel. Whether that protects you depends ENTIRELY on whether
    the two legs SHARE COLLATERAL.

      SHARED    one venue, cross-margined. The spot gain offsets the perp loss
                inside the same account, so only the BASIS divergence can
                liquidate you. Basis moves are small relative to price moves.
      SEPARATE  spot on one venue, perp on another. The short perp can be
                liquidated by a PRICE rally while your spot leg sits profitable
                and unreachable on the other venue. Delta neutrality is an
                accounting fact about the portfolio, not a margin fact about the
                account that gets liquidated.

    Under SEPARATE collateral a carry is not a low-risk position; it is a
    leveraged short that happens to own an unrelated hedge. That distinction
    decides more about survival than any funding rate.
"""

import math
from decimal import Decimal, InvalidOperation

from tramitago_quant_core.shared.util import digest, encoded

CARRY_TAIL_SCHEMA_VERSION = "1"

COLLATERAL_SHARED = "SHARED"
COLLATERAL_SEPARATE = "SEPARATE"
COLLATERAL_MODES = (COLLATERAL_SHARED, COLLATERAL_SEPARATE)


def _fraction(value, name, maximum=None):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be declared as a string")
    try:
        amount = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{name} is not a decimal") from error
    if not amount.is_finite() or amount <= 0:
        raise ValueError(f"{name} must be a finite positive decimal")
    if maximum is not None and amount >= maximum:
        raise ValueError(f"{name} must be below {maximum}")
    return amount


def stress_scenario(*, adverse_move, source):
    """Declare the stress the position must survive, and where it came from.

    `adverse_move` is a fraction: under SHARED collateral it is a BASIS
    divergence, under SEPARATE collateral it is an outright PRICE move. They are
    not the same magnitude and conflating them is the error this module exists
    to prevent.

    The mandatory `source` is structural, not bureaucratic: this project has
    never observed a basis, so any number here is an assumption and must say
    whose. A scenario sourced from "seems reasonable" is a tail model that
    certifies its own guess.
    """
    if not isinstance(source, str) or not source.strip():
        raise ValueError(
            "A stress scenario must declare its source; this project has never "
            "observed a spot-perp basis, so the number is an assumption")
    _fraction(adverse_move, "adverse_move", maximum=Decimal("1"))
    content = {"schema_version": CARRY_TAIL_SCHEMA_VERSION,
               "adverse_move": adverse_move, "source": source.strip()}
    return {**content, "scenario_id": "STRESS_SCENARIO|" + digest(encoded(content))}


def verified_stress_scenario(scenario):
    if not isinstance(scenario, dict) or "scenario_id" not in scenario:
        raise ValueError("Stress scenario is invalid")
    content = {k: v for k, v in scenario.items() if k != "scenario_id"}
    if scenario["scenario_id"] != "STRESS_SCENARIO|" + digest(encoded(content)):
        raise ValueError("Stress scenario identity does not match its terms")
    return scenario


def liquidation_move(*, leverage, maintenance_margin_rate):
    """The adverse move that liquidates the short leg. Exact, no data needed.

    A short of notional N on margin M = N/leverage is liquidated when the loss
    N*r has eaten the margin down to maintenance:

        N*r  >=  M - N*mmr     ->     r >= 1/leverage - mmr

    Whether `r` is a price move or a basis move is decided by the collateral
    mode, not by this formula.
    """
    if not isinstance(leverage, (int, float)) or leverage <= 0:
        raise ValueError("Leverage must be positive")
    mmr = float(_fraction(maintenance_margin_rate, "maintenance_margin_rate",
                          maximum=Decimal("1")))
    move = 1.0 / leverage - mmr
    if move <= 0:
        raise ValueError("Leverage is already at or beyond maintenance margin; "
                         "the position is liquidated before it opens")
    return move


def survives_stress(*, scenario, leverage, maintenance_margin_rate, collateral_mode):
    """Does the declared stress liquidate the short leg?

    Returns (survives, detail). The collateral mode is required and has no
    default: under SEPARATE collateral the stress must be read as an outright
    price move, and defaulting to the friendlier reading would hide the whole
    risk this module is about.
    """
    verified_stress_scenario(scenario)
    if collateral_mode not in COLLATERAL_MODES:
        raise ValueError("Collateral mode must be " + " or ".join(COLLATERAL_MODES))
    threshold = liquidation_move(leverage=leverage,
                                 maintenance_margin_rate=maintenance_margin_rate)
    move = float(Decimal(scenario["adverse_move"]))
    kind = "basis divergence" if collateral_mode == COLLATERAL_SHARED else "price move"
    if move >= threshold:
        return False, (f"a {move:.1%} {kind} liquidates the short leg at "
                       f"{leverage}x (threshold {threshold:.1%})")
    return True, (f"a {move:.1%} {kind} leaves {threshold - move:.1%} of headroom "
                  f"at {leverage}x (threshold {threshold:.1%})")


def loss_fraction_at_liquidation(*, leverage, collateral_mode):
    """Loss as a fraction of the capital committed to the carry, if liquidated.

    The perp margin is lost. Under SHARED collateral the spot leg's offsetting
    gain is inside the same account, so the net loss is bounded by the basis
    move that caused it. Under SEPARATE collateral the margin is simply gone and
    the surviving spot leg is left UNHEDGED -- which is a second loss this
    number does not attempt to price, because its size depends on what happens
    next rather than on the liquidation itself.
    """
    if collateral_mode not in COLLATERAL_MODES:
        raise ValueError("Collateral mode must be " + " or ".join(COLLATERAL_MODES))
    if not isinstance(leverage, (int, float)) or leverage <= 0:
        raise ValueError("Leverage must be positive")
    # Capital committed = spot notional (1 unit) + perp margin (1/leverage).
    committed = 1.0 + 1.0 / leverage
    margin_lost = 1.0 / leverage
    return margin_lost / committed


def loss_in_stress(*, scenario, leverage, maintenance_margin_rate, collateral_mode):
    """Loss as a fraction of committed capital IF THE DECLARED STRESS HAPPENS.

    This is the quantity the drawdown limit should be compared against, and
    getting it wrong is easy: `loss_fraction_at_liquidation` DECREASES with
    leverage, because a thinner margin is less to lose. Comparing that against a
    drawdown limit makes low leverage look dangerous, which is nonsense -- at
    low leverage the liquidation it describes never happens.

    What matters is the loss in the scenario actually declared:
      survived   -- the adverse move is borne on the notional and the position
                    is still open; the loss is real but not crystallised.
      liquidated -- the margin is gone. Under SEPARATE collateral the surviving
                    spot leg is also left unhedged, a further loss this does not
                    price because its size depends on what happens next.
    """
    verified_stress_scenario(scenario)
    survives, _ = survives_stress(
        scenario=scenario, leverage=leverage,
        maintenance_margin_rate=maintenance_margin_rate, collateral_mode=collateral_mode)
    committed = 1.0 + 1.0 / leverage
    if not survives:
        return 1.0 / leverage / committed, "LIQUIDATED"
    return float(Decimal(scenario["adverse_move"])) / committed, "SURVIVED"


def maximum_safe_leverage(*, scenario, maintenance_margin_rate, collateral_mode,
                          drawdown_limit, search_max=100.0):
    """The highest leverage that SURVIVES the declared stress and keeps the loss
    it causes inside the drawdown limit.

    Survival is required, not traded off: being liquidated is not an acceptable
    outcome that a favourable loss number can excuse. Among surviving leverages
    the loss grows with leverage -- a thinner margin means less committed
    capital to spread the same adverse move across -- so the maximum is well
    defined and the binding constraint is whichever fails first.
    """
    verified_stress_scenario(scenario)
    limit = float(_fraction(drawdown_limit, "drawdown_limit", maximum=Decimal("1")))
    best, binding, leverage = None, None, 1.0
    while leverage <= search_max:
        try:
            survives, _ = survives_stress(
                scenario=scenario, leverage=leverage,
                maintenance_margin_rate=maintenance_margin_rate,
                collateral_mode=collateral_mode)
        except ValueError:                       # past maintenance margin entirely
            binding = binding or "STRESS"
            break
        if not survives:
            binding = binding or "STRESS"
            break
        loss, _ = loss_in_stress(
            scenario=scenario, leverage=leverage,
            maintenance_margin_rate=maintenance_margin_rate,
            collateral_mode=collateral_mode)
        if loss > limit:
            binding = "DRAWDOWN"
            break
        best, leverage = leverage, round(leverage + 0.1, 1)
    if best is None:
        return {"maximum_leverage": None, "binding_constraint": binding or "STRESS",
                "scenario_id": scenario["scenario_id"], "collateral_mode": collateral_mode,
                "detail": "no leverage at or above 1x both survives this stress and "
                          "stays inside the drawdown limit; the position cannot be "
                          "sized safely under these terms"}
    loss, state = loss_in_stress(
        scenario=scenario, leverage=best,
        maintenance_margin_rate=maintenance_margin_rate, collateral_mode=collateral_mode)
    return {"maximum_leverage": best,
            "binding_constraint": binding or "SEARCH_LIMIT",
            "scenario_id": scenario["scenario_id"],
            "collateral_mode": collateral_mode,
            "loss_in_stress": loss, "state_in_stress": state}


def basis_is_unmeasured():
    """What has to be captured before any of this stops being an assumption.

    Returned as data rather than written only in a docstring, so a caller that
    reports a tail result can carry the caveat alongside the number instead of
    leaving it in a file nobody opens.
    """
    return {
        "measured": False,
        "missing": "perpetual mark price history alongside spot, same timestamps",
        "held_today": "Hyperliquid funding rates (timestamp, rate); Coinbase spot closes",
        "consequence": ("the spot-perp basis has never been observed by this project, "
                        "so every stress scenario here is a declared assumption and no "
                        "result from this module is evidence about the tail"),
    }
