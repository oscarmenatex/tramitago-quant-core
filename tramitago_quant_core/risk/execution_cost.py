"""What a fill actually cost, measured instead of assumed.

THE QUESTION THIS ANSWERS, and it arrived from a verdict rather than a plan. The
carry gated by its validated exit rule needs 64 round trips a year on two legs,
and it came back NOT_VALIDATED on costs -- but the breakeven is 0.000494 per leg
per side against 0.000650 DECLARED, and the declared number was an assumed taker
rate nobody had measured. A fill 0.000156 cheaper flips the verdict. The result
therefore did not say the rule is uneconomic; it said the answer is decided by
execution quality and nobody here had any.

Every cost contract this project has sealed carries `source` text beginning
"ASSUMED". This is the machinery that lets one say "MEASURED" instead.

THREE COMPONENTS, SEPARATED BECAUSE THEY BEHAVE DIFFERENTLY:

  COMMISSION      what the venue charges. Known before the order, scales with
                  notional, and is the only one a fee schedule can tell you.
  HALF-SPREAD     half the quoted bid-ask at decision time. The cost of crossing,
                  knowable from the book BEFORE the order exists, which is what
                  makes it separable from what follows.
  SLIPPAGE        where the fill landed relative to the quote that was standing
                  when the decision was made. This is the only component that
                  cannot be known in advance, and the only one a simulator gets
                  wrong.

WHAT A PAPER FILL CAN AND CANNOT TELL YOU, stated here because the distinction is
the whole value of the measurement:

  the commission is REAL -- it comes from the venue's own schedule;
  the half-spread is REAL -- it is the live book's own bid and ask;
  the slippage is NOT -- a paper engine has no queue and no market impact, so it
  fills at or near the quote and reports a number that is a FLOOR, never an
  estimate. A measured contract built from paper fills is therefore an optimistic
  bound on cost, and `fill_venue` records which kind of fill produced it so no
  later reader can mistake one for the other.

The arithmetic is deliberately one-sided: every component is measured as a COST,
so a fill better than the quote reports zero slippage rather than a negative
cost. A measurement that can come out negative would let a lucky fill subsidise
an unlucky one and quietly produce a contract cheaper than any real execution.
"""

import json
import math
import re
from decimal import Decimal
from pathlib import Path

from tramitago_quant_core.shared.util import (
    digest, encoded, _atomic_write, _explicit_utc, _hypothesis_text_is_valid,
)
from tramitago_quant_core.risk.cost_model import cost_contract, COST_REGIME_HOLDING

EXECUTION_MEASUREMENT_SCHEMA_VERSION = "1"
EXECUTION_MEASUREMENT_REGISTRY_SCHEMA_VERSION = "1"

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"
SIDES = (SIDE_BUY, SIDE_SELL)

# Which engine produced the fill. PAPER slippage is a floor, not an estimate, and
# this is how a sealed measurement says so about itself.
FILL_PAPER = "PAPER"
FILL_LIVE = "LIVE"
FILL_VENUES = (FILL_PAPER, FILL_LIVE)

_PRECISION = 9


def _fixed(value):
    return f"{value:.{_PRECISION}f}"


def _positive(value, name):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return float(value)


def measure_execution(*, side, bid, ask, fill_price, filled_quantity, commission,
                      fill_venue, symbol, observed_at):
    """One fill, decomposed into the three rates a cost contract declares.

    The quote must be the one standing WHEN THE DECISION WAS MADE, not one
    fetched afterwards: measured against a later quote, slippage silently becomes
    a measure of how much the market moved while the order travelled, which is a
    different quantity that happens to have the same units.
    """
    if side not in SIDES:
        raise ValueError("Side must be " + " or ".join(SIDES))
    if fill_venue not in FILL_VENUES:
        raise ValueError("Fill venue must be " + " or ".join(FILL_VENUES))
    if not _hypothesis_text_is_valid(symbol):
        raise ValueError("A symbol is required")
    if not _explicit_utc(observed_at):
        raise ValueError("Observation time must be canonical UTC")
    bid, ask = _positive(bid, "Bid"), _positive(ask, "Ask")
    if bid >= ask:
        raise ValueError("Bid must be below ask; a crossed quote is not a measurement")
    fill_price = _positive(fill_price, "Fill price")
    filled_quantity = _positive(filled_quantity, "Filled quantity")
    if not isinstance(commission, (int, float)) or isinstance(commission, bool) \
            or not math.isfinite(commission) or commission < 0:
        raise ValueError("Commission must be finite and non-negative")

    mid = (bid + ask) / 2
    notional = fill_price * filled_quantity
    half_spread_rate = (ask - bid) / 2 / mid
    # Signed so that paying MORE than mid on a buy, or receiving LESS on a sell,
    # is positive slippage. Floored at zero: see the module docstring.
    raw = (fill_price - mid) / mid if side == SIDE_BUY else (mid - fill_price) / mid
    slippage_rate = max(raw - half_spread_rate, 0.0)
    commission_rate = commission / notional

    content = {
        "schema_version": EXECUTION_MEASUREMENT_SCHEMA_VERSION,
        "symbol": symbol,
        "side": side,
        "fill_venue": fill_venue,
        "observed_at": observed_at,
        "bid": repr(bid), "ask": repr(ask),
        "mid": _fixed(mid),
        "fill_price": repr(fill_price),
        "filled_quantity": repr(filled_quantity),
        "notional": _fixed(notional),
        "commission": repr(float(commission)),
        "commission_rate": _fixed(commission_rate),
        "half_spread_rate": _fixed(half_spread_rate),
        "slippage_rate": _fixed(slippage_rate),
        # What one side of this fill cost in total, which is the number the carry
        # verdict's breakeven is expressed in.
        "cost_per_side": _fixed(commission_rate + half_spread_rate + slippage_rate),
    }
    return {**content, "measurement_id": "EXECUTION_MEASUREMENT|" + digest(encoded(content))}


def verified_execution_measurement(measurement):
    if not isinstance(measurement, dict) or "measurement_id" not in measurement:
        raise ValueError("Execution measurement is invalid")
    content = {key: value for key, value in measurement.items() if key != "measurement_id"}
    if measurement["measurement_id"] != "EXECUTION_MEASUREMENT|" + digest(encoded(content)):
        raise ValueError("Execution measurement identity does not match its contents")
    return measurement


def append_execution_measurement(registry_path, measurement):
    """Append-only. A measurement is an observation of something that happened
    once and cannot be revised; the registry therefore never updates, and a
    duplicate identity is returned rather than written twice."""
    verified_execution_measurement(measurement)
    path = Path(registry_path)
    registry = (json.loads(path.read_bytes()) if path.exists() else {
        "schema_version": EXECUTION_MEASUREMENT_REGISTRY_SCHEMA_VERSION, "measurements": []})
    if (not isinstance(registry, dict) or set(registry) != {"schema_version", "measurements"}
            or registry["schema_version"] != EXECUTION_MEASUREMENT_REGISTRY_SCHEMA_VERSION):
        raise ValueError("Persisted execution measurement registry is invalid")
    for stored in registry["measurements"]:
        verified_execution_measurement(stored)
        if stored["measurement_id"] == measurement["measurement_id"]:
            return stored
    registry["measurements"].append(measurement)
    _atomic_write(path, encoded(registry))
    return measurement


def load_execution_measurements(registry_path, *, symbol=None, fill_venue=None):
    path = Path(registry_path)
    if not path.exists():
        return []
    registry = json.loads(path.read_bytes())
    rows = [verified_execution_measurement(item) for item in registry["measurements"]]
    return [item for item in rows
            if (symbol is None or item["symbol"] == symbol)
            and (fill_venue is None or item["fill_venue"] == fill_venue)]


def measured_cost_contract(measurements, *, legs, source, quantile="0.5"):
    """Turn observed fills into a cost contract whose rates are MEASURED.

    THE QUANTILE IS DECLARED, not defaulted to a mean, because execution cost is
    right-skewed: most fills are ordinary and a few are terrible, so an average
    is dragged by the tail while a median describes the fill you will usually
    get. Which one a decision should use is the decision's business -- a capacity
    question wants the tail, a breakeven question wants the typical fill -- so it
    is a parameter and it is sealed into the contract's source text.

    Refuses an empty set rather than returning zero rates: no measurement is not
    the same as free, and that confusion is exactly what "ASSUMED" was hiding.
    """
    rows = [verified_execution_measurement(item) for item in measurements]
    if not rows:
        raise ValueError(
            "No execution measurements: absence of fills is not evidence of zero cost")
    fraction = float(Decimal(quantile))
    if not 0 < fraction <= 1:
        raise ValueError("Quantile must be in (0, 1]")

    def at(name):
        values = sorted(float(item[name]) for item in rows)
        index = min(int(fraction * len(values)), len(values) - 1)
        return values[index]

    venues = sorted({item["fill_venue"] for item in rows})
    return cost_contract(
        regime=COST_REGIME_HOLDING,
        commission_rate=_fixed(at("commission_rate")),
        half_spread_rate=_fixed(at("half_spread_rate")),
        slippage_rate=_fixed(at("slippage_rate")),
        recurring_rate_per_period="0", legs=legs,
        source=(f"MEASURED from {len(rows)} fills on {'+'.join(venues)} at the "
                f"{quantile} quantile. {source}"
                + (" PAPER fills report a slippage FLOOR, not an estimate: a paper "
                   "engine has no queue and no market impact."
                   if FILL_PAPER in venues else "")))


def round_trip_cost(contract, round_trips):
    """What `round_trips` cost in total, as a fraction of notional.

    A round trip is two sides, and every leg pays both. This is the number the
    gated carry verdict turns on: 64 round trips a year on two legs.
    """
    if not isinstance(round_trips, int) or isinstance(round_trips, bool) or round_trips < 0:
        raise ValueError("Round trips must be a non-negative integer")
    per_side = (Decimal(contract["commission_rate"]) + Decimal(contract["half_spread_rate"])
                + Decimal(contract["slippage_rate"])) * Decimal(contract["legs"])
    return float(per_side * 2 * round_trips)


def execution_summary(measurements):
    """Descriptive spread, reported beside any contract built from these fills.

    A single number for execution cost hides the only thing that matters about
    it, which is how much it varies: a median and a worst case that differ by an
    order of magnitude describe a very different trading problem from two that
    agree.
    """
    rows = [verified_execution_measurement(item) for item in measurements]
    if not rows:
        return {"fills": 0}
    per_side = sorted(float(item["cost_per_side"]) for item in rows)
    return {
        "fills": len(rows),
        "venues": sorted({item["fill_venue"] for item in rows}),
        "symbols": sorted({item["symbol"] for item in rows}),
        "cost_per_side_min": _fixed(per_side[0]),
        "cost_per_side_median": _fixed(per_side[len(per_side) // 2]),
        "cost_per_side_max": _fixed(per_side[-1]),
        "commission_rate_median": _fixed(
            sorted(float(item["commission_rate"]) for item in rows)[len(rows) // 2]),
        "half_spread_rate_median": _fixed(
            sorted(float(item["half_spread_rate"]) for item in rows)[len(rows) // 2]),
        "slippage_rate_median": _fixed(
            sorted(float(item["slippage_rate"]) for item in rows)[len(rows) // 2]),
    }
