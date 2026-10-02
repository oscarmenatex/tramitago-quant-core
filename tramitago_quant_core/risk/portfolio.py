"""CAP-006 Gestión de Portafolio -- how much of a thing fits.

WHY IT EXISTS NOW. Vía 7.D was closed on a carry whose worst fold drew down
15.77% against a declared 15% limit -- an excess of 0.77 percentage points, with
the position at ONE HUNDRED PERCENT of capital, over a window chosen because it
contained the worst crash in the asset's history. The limit is a PORTFOLIO limit
and it was applied to a POSITION, because the platform could not express the
difference. This is that difference.

THE ONE THING THIS MODULE REFUSES TO LET ANYONE DO: add drawdowns. A portfolio's
drawdown is not the weighted sum of its positions' drawdowns and is not bounded
by any simple function of them -- losses at different times partly cancel, losses
at the same time compound, and only the combined equity curve knows which
happened. There is therefore no function here that takes drawdowns as input. The
curve is recomputed from combined returns, every time.

NO OPTIMISATION, AND THAT IS THE POINT. Weights are DECLARED and sealed, never
fitted. Choosing weights to maximise something on the same returns that produced
them is the data snooping this entire project is built against, and it is far
more seductive here than in a signal: a weight looks like engineering rather than
a prediction. `maximum_admissible_weight` exists and deliberately returns a
CAPACITY -- how much would fit under a limit -- never an allocation to then go
and evaluate at that weight.

NO CORRELATION IS ASSUMED ANYWHERE. Diversification is not modelled, hoped for or
estimated; it either shows up in the combined curve or it does not. With a single
position there is none by definition, and the module says so rather than
flattering the arithmetic.

CASH IS A POSITION. Weights need not sum to one, and the remainder is cash
earning a DECLARED rate -- zero unless stated. Weights summing above one is
leverage, and leverage is refused here rather than inferred: the destination
document names it the dominant control on this axis, and a control that arrives
by accident is not a control.
"""

import json
import math
from decimal import Decimal, InvalidOperation
from pathlib import Path

from tramitago_quant_core.shared.util import digest, encoded, _hypothesis_text_is_valid

PORTFOLIO_ALLOCATION_SCHEMA_VERSION = "1"

_WEIGHT_PRECISION = 9


def _fixed(value):
    return None if value is None else f"{value:.{_WEIGHT_PRECISION}f}"


def _rate(value, name, *, allow_negative=False):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be declared as a string")
    try:
        amount = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{name} is not a decimal") from error
    if not amount.is_finite():
        raise ValueError(f"{name} must be finite")
    if not allow_negative and amount < 0:
        raise ValueError(f"{name} must not be negative")
    return amount


def portfolio_allocation(*, weights, cash_return_per_period="0", source):
    """Seal one DECLARED allocation.

    `source` records where the weights came from, and it is required for the same
    reason a cost contract's is: a number whose provenance is not stated cannot be
    audited later, and the thing most worth auditing about a weight is whether it
    was chosen before or after seeing what it would have earned.
    """
    if not isinstance(weights, dict) or not weights:
        raise ValueError("An allocation needs at least one declared position")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("An allocation must declare where its weights came from")
    for label, weight in weights.items():
        if not _hypothesis_text_is_valid(label):
            raise ValueError("Every position needs a label")
        if _rate(weight, f"Weight for {label}") > 1:
            raise ValueError(f"Weight for {label} exceeds the whole portfolio")
    total = sum(Decimal(value) for value in weights.values())
    if total > 1:
        raise ValueError(
            f"Declared weights sum to {total}, which is leverage. Leverage is the dominant "
            "control on this axis and must be declared deliberately, never arrive as the "
            "remainder of an arithmetic nobody checked.")
    _rate(cash_return_per_period, "Cash return per period", allow_negative=True)

    content = {
        "schema_version": PORTFOLIO_ALLOCATION_SCHEMA_VERSION,
        "weights": dict(sorted(weights.items())),
        "cash_weight": _fixed(float(1 - total)),
        "cash_return_per_period": cash_return_per_period,
        "positions": len(weights),
        "source": source.strip(),
    }
    return {**content, "allocation_id": "PORTFOLIO_ALLOCATION|" + digest(encoded(content))}


def verified_portfolio_allocation(allocation):
    if not isinstance(allocation, dict) or "allocation_id" not in allocation:
        raise ValueError("Portfolio allocation is invalid")
    content = {key: value for key, value in allocation.items() if key != "allocation_id"}
    if allocation["allocation_id"] != "PORTFOLIO_ALLOCATION|" + digest(encoded(content)):
        raise ValueError("Portfolio allocation identity does not match its weights")
    return allocation


def portfolio_returns(allocation, series_by_label):
    """Per-period portfolio returns from per-period position returns.

    Every declared position must have a series and every series a declared
    position: an allocation naming something it was not given, or given something
    it does not name, is a mismatch rather than a portfolio, and guessing which
    was meant is how a weight silently applies to the wrong thing.
    """
    verified_portfolio_allocation(allocation)
    declared, supplied = set(allocation["weights"]), set(series_by_label)
    if declared != supplied:
        raise ValueError(
            f"Allocation and series do not correspond: declared {sorted(declared)}, "
            f"supplied {sorted(supplied)}")
    lengths = {len(values) for values in series_by_label.values()}
    if len(lengths) != 1 or not lengths or lengths == {0}:
        raise ValueError("Every position's series must be non-empty and the same length")

    cash_weight = float(allocation["cash_weight"])
    cash_return = float(Decimal(allocation["cash_return_per_period"]))
    periods = lengths.pop()
    combined = []
    for index in range(periods):
        total = cash_weight * cash_return
        for label, weight in allocation["weights"].items():
            value = series_by_label[label][index]
            if not isinstance(value, (int, float)) or isinstance(value, bool) \
                    or not math.isfinite(value):
                raise ValueError(f"Non-finite return in {label} at period {index}")
            total += float(Decimal(weight)) * value
        combined.append(total)
    return combined


def portfolio_drawdown(returns):
    """Maximum peak-to-trough decline of the COMBINED equity curve.

    Recomputed from the curve every time, and this module offers no way to do it
    any other way. A portfolio's drawdown is not the weighted sum of its
    positions' drawdowns: losses at different times partly cancel and losses at
    the same time compound, and only the curve knows which happened.
    """
    if not returns:
        raise ValueError("A drawdown needs returns")
    equity, peak, worst = 1.0, 1.0, 0.0
    for value in returns:
        equity *= (1.0 + value)
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak)
    return worst


def maximum_admissible_weight(series, *, drawdown_limit, cash_return_per_period="0",
                              tolerance="0.0001"):
    """The largest weight at which a SINGLE position keeps the portfolio within
    the limit, with the remainder in cash.

    THIS IS A CAPACITY, NOT AN ALLOCATION. It answers "how much would have fitted"
    over the periods supplied, and that is a different question from "how much
    should be held", which no amount of looking at these returns can answer.
    Evaluating a claim AT this weight would be fitting the weight to the data that
    produced it -- the same snooping in a costume that looks like engineering.

    Found by bisection rather than division: with a non-zero cash return the
    portfolio's drawdown is not proportional to the weight, and dividing the limit
    by the position's own drawdown would be right only in the special case where
    cash earns exactly nothing.
    """
    limit = float(_rate(drawdown_limit, "Drawdown limit"))
    if not 0 < limit < 1:
        raise ValueError("Drawdown limit must be a fraction in (0, 1)")
    step = float(_rate(tolerance, "Tolerance"))
    if not 0 < step < 1:
        raise ValueError("Tolerance must be a fraction in (0, 1)")
    if not series:
        raise ValueError("A capacity needs returns")

    def drawdown_at(weight):
        allocation = portfolio_allocation(
            weights={"position": f"{weight:.9f}"},
            cash_return_per_period=cash_return_per_period,
            source="internal bisection, never sealed as an allocation")
        return portfolio_drawdown(portfolio_returns(allocation, {"position": list(series)}))

    if drawdown_at(1.0) <= limit:
        return 1.0
    low, high = 0.0, 1.0
    while high - low > step:
        middle = (low + high) / 2
        if drawdown_at(middle) <= limit:
            low = middle
        else:
            high = middle
    return low


def allocation_summary(allocation, series_by_label):
    """What the declared allocation actually did, with the one caveat that
    matters stated in the record itself."""
    returns = portfolio_returns(allocation, series_by_label)
    drawdown = portfolio_drawdown(returns)
    worst_alone = {label: portfolio_drawdown(values)
                   for label, values in series_by_label.items()}
    return {
        "allocation_id": allocation["allocation_id"],
        "periods": len(returns),
        "positions": allocation["positions"],
        "cash_weight": allocation["cash_weight"],
        "portfolio_total_return": _fixed(math.fsum(returns)),
        "portfolio_drawdown": _fixed(drawdown),
        "position_drawdown_alone": {label: _fixed(value)
                                    for label, value in sorted(worst_alone.items())},
        "sum_of_position_drawdowns": _fixed(math.fsum(worst_alone.values())),
        "diversification_was_not_assumed": True,
        "note": ("The portfolio drawdown is recomputed from the combined curve. It is NOT "
                 "the sum of the position drawdowns reported beside it, and comparing the "
                 "two is the only use that sum has."),
    }
