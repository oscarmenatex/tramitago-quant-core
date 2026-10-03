"""Monitors as DATA, so judging a Hypothesis does not need a script written for it.

WHY THIS EXISTS. Twenty-three research runners sit in scripts/research. All
twenty-three stamp a code_revision into SEALED records, none has a test, and the
three most recent share 52 to 61 percent of their executable code after string
literals are masked. A defect fixed in one -- the adverse threshold that must scale
with the weight, the capacity model returning no ceiling, the identity form
certifying a monitor the evidence had refuted -- had to be fixed again in each of
the others by hand, and was missed at least once. The system was tied to the
instrument because the JUDGEMENT lived inside a script written for it.

A monitor is the part of that judgement that actually varies from one premium to
the next. What a variable IS, and when it counts as having died, is a fact about the
premium. How a trigger is turned into daily flags, and how the empirical link is
tested fold by fold, is not -- and that is what lives here, once.

THREE KINDS cover every monitor this project has declared:

  level_below             one series falls to or below a floor
                          (BAA10Y below the credit loss it must compensate)
  difference_below        the DIFFERENCE of two series falls to or below a floor
                          (the VIX term structure: VIX3M minus VIX)
  implied_minus_realised  an implied volatility minus the realised volatility of an
                          underlying, at or below a floor
                          (the variance risk premium measured against itself)

A MISSING OBSERVATION IS NEVER A TRIGGER. A day with no value is not a day the
monitor fired, and counting it as one would let a gappy series read as a monitor
that fires often. It is flagged zero and counted separately.
"""

import math
from decimal import Decimal

KIND_NONE = "none"
KIND_LEVEL_BELOW = "level_below"
KIND_DIFFERENCE_BELOW = "difference_below"
KIND_IMPLIED_MINUS_REALISED = "implied_minus_realised"
MONITOR_KINDS = (KIND_NONE, KIND_LEVEL_BELOW, KIND_DIFFERENCE_BELOW,
                 KIND_IMPLIED_MINUS_REALISED)

TRADING_DAYS = 252
MINIMUM_TRIGGER_DAYS_PER_FOLD = 10

# What each kind needs from the outside, so a caller can fetch exactly that and
# nothing else, and a spec that names something missing is refused up front rather
# than failing halfway through a capture.
REQUIRED_SERIES = {
    KIND_NONE: (),
    KIND_LEVEL_BELOW: ("series",),
    KIND_DIFFERENCE_BELOW: ("far", "near"),
    KIND_IMPLIED_MINUS_REALISED: ("implied",),
}


def monitor_series_names(spec):
    """The FRED series a monitor spec reads, in a stable order."""
    kind = spec.get("kind", KIND_NONE)
    if kind not in MONITOR_KINDS:
        raise ValueError(f"Unknown monitor kind {kind!r}; expected one of "
                         + ", ".join(MONITOR_KINDS))
    return [spec[key] for key in REQUIRED_SERIES[kind]]


def needs_underlying(spec):
    return spec.get("kind") == KIND_IMPLIED_MINUS_REALISED


def _number(value):
    return None if value is None else float(value)


def _realised_volatility(underlying, window):
    """Annualised realised volatility in percent, on each date of the underlying
    that has a full window of returns before and including it."""
    dates = sorted(underlying)
    closes = [float(underlying[day]) for day in dates]
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    out = {}
    for i in range(window - 1, len(returns)):
        sample = returns[i - window + 1:i + 1]
        mean = math.fsum(sample) / window
        variance = math.fsum((x - mean) ** 2 for x in sample) / (window - 1)
        out[dates[i + 1]] = math.sqrt(variance) * math.sqrt(TRADING_DAYS) * 100
    return out


def evaluate_monitor(spec, dates, series, underlying=None):
    """Turn a monitor spec into daily trigger flags aligned to `dates`.

    `series` maps a series id to {date: value-or-None} as the verified FRED
    captures return it; `underlying` maps date to close for the one kind that
    needs prices. Returns None for kind none.
    """
    kind = spec.get("kind", KIND_NONE)
    if kind == KIND_NONE:
        return None
    if kind not in MONITOR_KINDS:
        raise ValueError(f"Unknown monitor kind {kind!r}")
    floor = float(Decimal(spec["floor"]))

    if kind == KIND_LEVEL_BELOW:
        level = series[spec["series"]]
        value_on = lambda day: _number(level.get(day))
    elif kind == KIND_DIFFERENCE_BELOW:
        far, near = series[spec["far"]], series[spec["near"]]

        def value_on(day):
            a, b = _number(far.get(day)), _number(near.get(day))
            return None if a is None or b is None else a - b
    else:
        if not underlying:
            raise ValueError("implied_minus_realised needs the underlying's closes")
        window = int(spec.get("window", 21))
        realised = _realised_volatility(underlying, window)
        implied = series[spec["implied"]]

        def value_on(day):
            level, rv = _number(implied.get(day)), realised.get(day)
            return None if level is None or rv is None else level - rv

    flags, observed = [], 0
    for day in dates:
        value = value_on(day)
        if value is None:
            flags.append(0)
            continue
        observed += 1
        flags.append(1 if value <= floor else 0)
    return {
        "kind": kind, "floor": spec["floor"], "flags": flags,
        "days": len(dates), "observed_days": observed,
        "missing_days": len(dates) - observed,
        "trigger_days": sum(flags),
        "trigger_frequency": (sum(flags) / observed) if observed else None,
    }


def fold_bounds(count, folds):
    """The same split the level claim uses, so the link is tested on the SAME folds
    the claim is judged on and never on a partition chosen to flatter it."""
    size = count // folds
    return [(index * size, count if index == folds - 1 else (index + 1) * size)
            for index in range(folds)]


def link_consistency(bounds, returns, flags, minimum_days=MINIMUM_TRIGGER_DAYS_PER_FOLD):
    """Does a triggered day degrade the return, fold by fold?

    A fold counts when the mean return on TRIGGERED days is below the mean on the
    rest -- the sign rule schema 3 uses, because significance inside a fifteen
    month slice is a statement about the slice's length and not about the link.
    A fold with fewer than `minimum_days` triggered days, or none untriggered, is
    UNUSABLE rather than failed: it could not be measured, and counting it as
    evidence against the link would let a sparse trigger refute itself.

    `returns` may contain None where a control series has no value on that day;
    those days are skipped, never treated as zero.
    """
    met = usable = 0
    for lo, hi in bounds:
        triggered = [returns[i] for i in range(lo, hi)
                     if flags[i] and returns[i] is not None]
        quiet = [returns[i] for i in range(lo, hi)
                 if not flags[i] and returns[i] is not None]
        if len(triggered) < minimum_days or not quiet:
            continue
        usable += 1
        if math.fsum(triggered) / len(triggered) < math.fsum(quiet) / len(quiet):
            met += 1
    return met, usable
