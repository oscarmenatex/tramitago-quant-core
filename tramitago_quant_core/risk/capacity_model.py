"""Capacity model -- the ceiling C of DOC-011 §9.4 (2026-09-30).

The Director declared capital INCREMENTAL and open-ended, which is why §8.6 of
the admission criteria asks the inverted question. Not "does it work at capital
X?" -- there is no X -- but "AT WHAT CAPITAL DOES IT STOP WORKING?". The answer
is a CEILING DECLARED PER OPPORTUNITY, not a bar to clear.

Admission requires only that C cover the CURRENT tranche. Never a future one:
that capital does not exist yet and its figure is not knowable.

TWO CEILINGS, and the binding one is whichever comes first:

  IMPACT      your own size moves the price against you. Modelled with the
              square-root law, impact ~ k*sqrt(participation), which is the
              form the execution literature supports. The FORM is declared, not
              hardcoded, because it is a modelling choice and a future
              opportunity may need another.
  STRUCTURAL  a hard limit that no amount of patience gets around: exchange
              position caps, borrow availability, issue size. Impact is smooth
              and structural limits are not, so a model that only knows about
              impact will happily report a ceiling above a wall.

HONESTY ABOUT WHAT THIS IS: every other number in this project is measured from
sealed evidence. This one comes from a MODEL, before any capital has traded. It
is a PRIOR, and §8.6 requires it to be re-estimated at each tranche from real
execution data -- which is better evidence about impact and liquidity than any
model computed in advance. `ceiling_from_realised_impact` exists for exactly
that, and its result should always replace the modelled one.
"""

import math
from decimal import Decimal, InvalidOperation

from tramitago_quant_core.shared.util import digest, encoded

CAPACITY_CONTRACT_SCHEMA_VERSION = "1"

IMPACT_SQUARE_ROOT = "SQUARE_ROOT"      # impact = k * sqrt(participation)
IMPACT_LINEAR = "LINEAR"                # impact = k * participation
IMPACT_FORMS = (IMPACT_SQUARE_ROOT, IMPACT_LINEAR)


def _positive(value, name, allow_zero=False):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be declared as a string")
    try:
        amount = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{name} is not a decimal") from error
    if not amount.is_finite() or (amount < 0 if allow_zero else amount <= 0):
        raise ValueError(f"{name} must be a finite positive decimal")
    return amount


def capacity_contract(*, average_daily_volume, impact_coefficient,
                      impact_form=IMPACT_SQUARE_ROOT, degradation_tolerance="0.20",
                      structural_cap=None, source):
    """Seal the declared market facts a capacity estimate rests on.

    `average_daily_volume` and `structural_cap` are in the same currency as the
    capital being sized. `degradation_tolerance` is the 20% of §8.6: above that
    loss of net Sharpe, what was validated small no longer describes what runs.
    `source` records where these came from -- they are market facts that change.
    """
    if impact_form not in IMPACT_FORMS:
        raise ValueError("Impact form must be " + " or ".join(IMPACT_FORMS))
    if not isinstance(source, str) or not source.strip():
        raise ValueError("A capacity contract must declare the source of its market facts")
    _positive(average_daily_volume, "average_daily_volume")
    _positive(impact_coefficient, "impact_coefficient")
    tolerance = _positive(degradation_tolerance, "degradation_tolerance")
    if tolerance >= 1:
        raise ValueError("Degradation tolerance must be a fraction below 1")
    if structural_cap is not None:
        _positive(structural_cap, "structural_cap")
    content = {
        "schema_version": CAPACITY_CONTRACT_SCHEMA_VERSION,
        "average_daily_volume": average_daily_volume,
        "impact_coefficient": impact_coefficient,
        "impact_form": impact_form,
        "degradation_tolerance": degradation_tolerance,
        "structural_cap": structural_cap,
        "source": source.strip(),
    }
    return {**content, "contract_id": "CAPACITY_CONTRACT|" + digest(encoded(content))}


def verified_capacity_contract(contract):
    if not isinstance(contract, dict) or "contract_id" not in contract:
        raise ValueError("Capacity contract is invalid")
    content = {key: value for key, value in contract.items() if key != "contract_id"}
    if contract["contract_id"] != "CAPACITY_CONTRACT|" + digest(encoded(content)):
        raise ValueError("Capacity contract identity does not match its terms")
    if content.get("impact_form") not in IMPACT_FORMS:
        raise ValueError("Capacity contract impact form is invalid")
    return contract


def impact_rate(contract, capital):
    """Cost per side, as a fraction of notional, caused by your own size.

    Participation is the traded capital as a fraction of average daily volume.
    Under the square-root law a position ten times larger costs about three
    times more per unit, not ten -- which is why capacity degrades gradually
    rather than falling off a cliff, and why the ceiling has to be computed
    rather than guessed.
    """
    verified_capacity_contract(contract)
    if capital < 0:
        raise ValueError("Capital must be non-negative")
    adv = float(Decimal(contract["average_daily_volume"]))
    coefficient = float(Decimal(contract["impact_coefficient"]))
    participation = float(capital) / adv
    if contract["impact_form"] == IMPACT_LINEAR:
        return coefficient * participation
    return coefficient * math.sqrt(participation)


def net_sharpe_at(contract, capital, *, gross_per_period, base_cost_per_period,
                  volatility_per_period, turnover_per_period, periods_per_year=252):
    """Annualised net Sharpe at a given capital.

    Impact is charged per side on every transition, so it enters scaled by
    turnover: the same size costs a daily-flipping signal far more than a
    position held for months. Volatility is taken as unchanged by size, which
    is the conservative assumption here -- impact erodes the numerator and
    leaving the denominator alone does not flatter the result.
    """
    verified_capacity_contract(contract)
    if volatility_per_period <= 0:
        raise ValueError("Volatility must be positive")
    cost = base_cost_per_period + impact_rate(contract, capital) * turnover_per_period
    return (gross_per_period - cost) / volatility_per_period * math.sqrt(periods_per_year)


def capacity_ceiling(contract, *, gross_per_period, base_cost_per_period,
                     volatility_per_period, turnover_per_period,
                     reference_capital, periods_per_year=252, search_multiple=10_000):
    """The capital at which net Sharpe has degraded past the declared tolerance.

    Returns the ceiling and which constraint produced it. The structural cap
    binds whenever it is lower: impact is smooth, a position limit is a wall,
    and reporting the smooth number past a wall would be a fiction.
    """
    verified_capacity_contract(contract)
    tolerance = float(Decimal(contract["degradation_tolerance"]))
    reference = net_sharpe_at(
        contract, reference_capital, gross_per_period=gross_per_period,
        base_cost_per_period=base_cost_per_period,
        volatility_per_period=volatility_per_period,
        turnover_per_period=turnover_per_period, periods_per_year=periods_per_year)
    if reference <= 0:
        raise ValueError("Net Sharpe is not positive at the reference capital; "
                         "there is no capacity to size")
    target = reference * (1 - tolerance)

    def degraded(capital):
        return net_sharpe_at(
            contract, capital, gross_per_period=gross_per_period,
            base_cost_per_period=base_cost_per_period,
            volatility_per_period=volatility_per_period,
            turnover_per_period=turnover_per_period,
            periods_per_year=periods_per_year) <= target

    low, high = float(reference_capital), float(reference_capital) * search_multiple
    unbounded = not degraded(high)
    if not unbounded:
        for _ in range(200):                     # bisection; the curve is monotone
            middle = (low + high) / 2
            if degraded(middle):
                high = middle
            else:
                low = middle
    impact_ceiling = None if unbounded else low

    structural = contract.get("structural_cap")
    structural_ceiling = float(Decimal(structural)) if structural is not None else None

    candidates = [(value, name) for value, name in
                  ((impact_ceiling, "IMPACT"), (structural_ceiling, "STRUCTURAL"))
                  if value is not None]
    if not candidates:
        # Neither binds within the searched range. Say so rather than inventing
        # a number: "no ceiling found" is a finding, not a licence to scale.
        return {"contract_id": contract["contract_id"], "ceiling": None,
                "binding_constraint": "NONE_FOUND",
                "searched_to": high, "reference_net_sharpe": reference,
                "detail": "no ceiling within the searched range; re-estimate from "
                          "real execution before treating capacity as unlimited"}
    ceiling, binding = min(candidates)
    return {"contract_id": contract["contract_id"], "ceiling": ceiling,
            "binding_constraint": binding, "searched_to": high,
            "reference_net_sharpe": reference,
            "net_sharpe_at_ceiling": net_sharpe_at(
                contract, ceiling, gross_per_period=gross_per_period,
                base_cost_per_period=base_cost_per_period,
                volatility_per_period=volatility_per_period,
                turnover_per_period=turnover_per_period,
                periods_per_year=periods_per_year)}


def tranche_is_admissible(ceiling_result, tranche_capital):
    """§8.6: admission needs C to cover the CURRENT tranche, nothing further.

    Returns (admissible, detail). A ceiling that was never found does NOT pass:
    an unknown ceiling is not an absent one.
    """
    if tranche_capital <= 0:
        return False, "tranche capital must be positive"
    ceiling = ceiling_result.get("ceiling")
    if ceiling is None:
        return False, ("no capacity ceiling was established; an unknown ceiling is "
                       "not an absent one")
    if tranche_capital > ceiling:
        return False, (f"tranche {tranche_capital} exceeds the {ceiling_result['binding_constraint']} "
                       f"ceiling {ceiling:.2f}; cap the allocation at the ceiling rather "
                       f"than scaling past what was validated")
    return True, (f"tranche {tranche_capital} within the "
                  f"{ceiling_result['binding_constraint']} ceiling {ceiling:.2f}")


def ceiling_from_realised_impact(*, realised_impact_rate, capital_traded, contract,
                                 gross_per_period, base_cost_per_period,
                                 volatility_per_period, turnover_per_period,
                                 periods_per_year=252):
    """Re-estimate the ceiling from what execution ACTUALLY cost (§8.6 (c)).

    Required at every tranche. Real fills are better evidence about impact and
    liquidity than any model computed before trading, so this result REPLACES
    the modelled ceiling rather than being compared against it. The contract's
    coefficient is re-derived from the observed rate, keeping the declared form.
    """
    verified_capacity_contract(contract)
    if capital_traded <= 0 or realised_impact_rate < 0:
        raise ValueError("Realised impact needs positive traded capital and a "
                         "non-negative observed rate")
    adv = float(Decimal(contract["average_daily_volume"]))
    participation = float(capital_traded) / adv
    basis = math.sqrt(participation) if contract["impact_form"] == IMPACT_SQUARE_ROOT \
        else participation
    if basis <= 0:
        raise ValueError("Traded capital is too small to infer an impact coefficient")
    observed = capacity_contract(
        average_daily_volume=contract["average_daily_volume"],
        impact_coefficient=str(realised_impact_rate / basis),
        impact_form=contract["impact_form"],
        degradation_tolerance=contract["degradation_tolerance"],
        structural_cap=contract["structural_cap"],
        source=f"RE-ESTIMATED from realised fills at capital {capital_traded}")
    result = capacity_ceiling(
        observed, gross_per_period=gross_per_period,
        base_cost_per_period=base_cost_per_period,
        volatility_per_period=volatility_per_period,
        turnover_per_period=turnover_per_period,
        reference_capital=capital_traded, periods_per_year=periods_per_year)
    return {**result, "supersedes_contract_id": contract["contract_id"]}
