"""Adverse confidence bounds on a STATISTIC of a return series, not just its mean.

WHY THIS EXISTS. §8.1 requires every admission threshold to be applied to the
adverse 95% bound and never to a point estimate -- and the first admission review
denied all three validated Hypotheses partly on that rule, including one whose
realised worst-fold drawdown was 1.24% against a 15% limit. It failed not because
it was large but because nobody had produced a BOUND on it. The project already
had a bound on the MEAN (`level_claim.adverse_mean_bound`) and nothing for the
two quantities the gates actually ask about: net Sharpe and drawdown.

THE BLOCK LENGTH IS NOT A DETAIL HERE. A Sharpe is a ratio of two moments and a
drawdown is PATH-DEPENDENT: resampling individual days would shuffle the order
and destroy the very runs a drawdown is made of, producing a bound far kinder
than reality. Moving blocks keep the runs intact, which is the only way the
statistic survives the resampling as itself.

WHICH TAIL IS ADVERSE DEPENDS ON THE GATE, so it is stated rather than inferred:
a lower bound for something that must exceed a minimum, an upper bound for
something that must stay under a maximum. Getting that backwards would report
the generous end of the interval as if it were the cautious one.

Deliberately NOT refactored out of `level_claim.adverse_mean_bound`, though the
resampling is the same. That function's seed and block length are sealed into
every level claim already issued, and sharing an implementation would make those
records depend on edits made here. Ten duplicated lines are cheaper than that
coupling.
"""

import math
import random
from decimal import Decimal

BOUND_LOWER = "LOWER"
BOUND_UPPER = "UPPER"
BOUND_SIDES = (BOUND_LOWER, BOUND_UPPER)

ANNUALISATION_DAILY = 365


def sharpe_ratio(returns, periods_per_year=ANNUALISATION_DAILY):
    """Annualised Sharpe of a return series, or None when it has no dispersion.

    None rather than infinity: a series with zero variance has no Sharpe, and
    reporting a huge number for one would be the single most misleading thing
    this module could do.
    """
    if len(returns) < 2:
        return None
    mean = math.fsum(returns) / len(returns)
    variance = math.fsum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    if variance <= 0:
        return None
    return mean / math.sqrt(variance) * math.sqrt(periods_per_year)


def max_drawdown(returns):
    """Maximum peak-to-trough decline of the implied equity curve, from 1.0."""
    equity, peak, worst = 1.0, 1.0, 0.0
    for value in returns:
        equity *= (1.0 + value)
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak)
    return worst


def _resample(returns, block_periods, rng):
    """One moving-block resample of the same length, wrapping at the end so every
    period keeps an equal chance of being drawn."""
    count = len(returns)
    sample = []
    for _ in range(math.ceil(count / block_periods)):
        start = rng.randrange(count)
        sample.extend(returns[(start + offset) % count] for offset in range(block_periods))
    del sample[count:]
    return sample


def bootstrap_bound(returns, statistic, *, side, confidence="0.95", resamples=2000,
                    block_periods=5, seed=0):
    """The adverse confidence bound on `statistic`, by moving-block bootstrap.

    Deterministic given the seed, which a caller seals alongside the figure. A
    resample whose statistic is undefined is DISCARDED rather than counted as
    zero -- counting it would pull the distribution toward a value the data never
    produced -- and a run where too few survive returns None instead of a bound
    computed from a handful.
    """
    if side not in BOUND_SIDES:
        raise ValueError("Side must be " + " or ".join(BOUND_SIDES))
    if not isinstance(returns, list) or len(returns) < block_periods:
        raise ValueError("A bound needs at least one full block of returns")
    for name, value in (("resamples", resamples), ("block periods", block_periods)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise ValueError("Seed must be a non-negative integer")
    level = float(Decimal(confidence))
    if not 0.5 < level < 1:
        raise ValueError("Confidence must be in (0.5, 1)")

    rng = random.Random(seed)
    values = []
    for _ in range(resamples):
        value = statistic(_resample(returns, block_periods, rng))
        if value is not None and math.isfinite(value):
            values.append(value)
    if len(values) < resamples // 2:
        return None
    values.sort()
    index = (int((1 - level) * len(values)) if side == BOUND_LOWER
             else int(level * len(values)))
    return values[min(index, len(values) - 1)]


def net_sharpe_lower_bound(net_returns, *, periods_per_year=ANNUALISATION_DAILY, **kwargs):
    """The maximando of §8.2: the LOWER bound, because the Sharpe must exceed a
    minimum. Takes NET returns -- there is no gross path, by the same reasoning
    the level claim judge applies."""
    return bootstrap_bound(
        net_returns, lambda sample: sharpe_ratio(sample, periods_per_year),
        side=BOUND_LOWER, **kwargs)


def drawdown_upper_bound(net_returns, **kwargs):
    """R1 of §8.3: the UPPER bound, because the drawdown must stay under a
    maximum. Blocks matter most here -- a drawdown is made of consecutive losses
    and shuffling days would dissolve them."""
    return bootstrap_bound(net_returns, max_drawdown, side=BOUND_UPPER, **kwargs)


def weight_within_drawdown_bound(net_returns, *, drawdown_limit, tolerance=0.001, **kwargs):
    """The largest weight at which the drawdown's 95% UPPER BOUND stays inside a
    limit -- which is what the gate actually reads.

    WHY THIS EXISTS, and it is the third instance of one defect found in a single
    day. CAP-006's `maximum_admissible_weight` resolves size against the REALISED
    drawdown, and §8.1 gates on the adverse 95% bound. Measured on SPY: realised
    33.79% and bound 45.79%, so CAP-006 returns 40.9% -- at which the realised
    drawdown is exactly 15.00% and the BOUND is 21.31%. The position is sized,
    reads as compliant, and the gate still refuses it. A tool computing a point
    quantity to satisfy a gate that wants a bound answers a question nobody asked.

    DOC-011 is explicit that the drawdown limit "es aquello contra lo que se
    resuelve el tamaño", so resolving against it is the declared mechanism rather
    than an evasion of it. What makes that safe from fitting is that every other
    gate is SCALE-INVARIANT -- the Sharpe bound is identical to four decimals at
    every weight -- so size can move this gate and no other.

    Bisection, not division: a drawdown compounds, so halving the weight does not
    halve it, and the bound is a bootstrap of a path statistic rather than an
    algebraic function of the series.
    """
    limit = float(Decimal(drawdown_limit))
    if not 0 < limit < 1:
        raise ValueError("Drawdown limit must be a fraction in (0, 1)")
    step = float(Decimal(tolerance))
    if not 0 < step < 1:
        raise ValueError("Tolerance must be a fraction in (0, 1)")

    def bound_at(weight):
        return drawdown_upper_bound([value * weight for value in net_returns], **kwargs)

    if bound_at(1.0) <= limit:
        return 1.0
    low, high = 0.0, 1.0
    while high - low > step:
        middle = (low + high) / 2
        if bound_at(middle) <= limit:
            low = middle
        else:
            high = middle
    return low
