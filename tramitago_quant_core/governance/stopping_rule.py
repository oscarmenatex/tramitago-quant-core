"""The search stopping rule, pre-declared 2026-09-30 and never once executed.

It turns "do we keep looking" from a question of mood into a measured statement,
and it has sat in a document for two days while the search carried on by feel.

THE RULE, as sealed:

  SCOPE     applies per EVIDENCE POPULATION -- the triple (data source x
            frequency x claim type) -- never globally. Exhausting one says
            nothing about the others.
  MINIMUM   the test CANNOT fire below 5 distinct real hypotheses. With fewer,
            the bound is set by the sample size rather than by the evidence: a
            single failed hypothesis already yields 0.393, which would close an
            axis by arithmetic instead of by knowledge.
  TEST      once the minimum is reached, if the 95% UPPER bound of that
            population's aggregate per-fold rate is BELOW 0.70, the population is
            declared EXHAUSTED BY MEASUREMENT.

THE BOUND IS THE UPPER ONE AND THAT IS THE WHOLE POINT. Declaring an axis dead
is a claim that no more looking will help, so the statistic has to be generous to
the axis: it asks whether the rate could PLAUSIBLY still reach the bar, not
whether it currently does. An axis is closed only when even the optimistic end of
the interval falls short.

CLOPPER-PEARSON, NOT A NORMAL APPROXIMATION. The rate is a proportion from a
bounded count and the normal interval misbehaves exactly where this is used --
near zero, at small n. Clopper-Pearson is exact and conservative, which errs
toward keeping an axis open, which is the right direction for a rule whose output
is "stop".

Computed by bisection on the exact binomial CDF rather than a Beta quantile, so
this needs nothing outside the standard library and can be checked by hand.
"""

import math
from decimal import Decimal

STOPPING_RULE_SCHEMA_VERSION = "1"
STOPPING_RULE_MINIMUM_HYPOTHESES = 5
STOPPING_RULE_THRESHOLD = "0.70"
STOPPING_RULE_CONFIDENCE = "0.95"

REASON_BELOW_MINIMUM = "FEWER_HYPOTHESES_THAN_THE_MINIMUM"
REASON_BOUND_REACHES_THRESHOLD = "UPPER_BOUND_STILL_REACHES_THE_THRESHOLD"
REASON_EXHAUSTED = "EXHAUSTED_BY_MEASUREMENT"


def _binomial_cdf(successes, trials, probability):
    """P(X <= successes) for X ~ Binomial(trials, probability), exactly."""
    return math.fsum(math.comb(trials, k) * probability ** k
                     * (1 - probability) ** (trials - k) for k in range(successes + 1))


def clopper_pearson_upper(successes, trials, confidence="0.95", tolerance=1e-12):
    """The exact upper confidence limit for a proportion.

    It is the p at which observing this few successes becomes implausible: the
    solution of P(X <= successes | trials, p) = 1 - confidence. At successes ==
    trials the bound is 1 by definition, since nothing observed rules out
    certainty.
    """
    if not isinstance(trials, int) or isinstance(trials, bool) or trials < 1:
        raise ValueError("Trials must be a positive integer")
    if not isinstance(successes, int) or isinstance(successes, bool) \
            or not 0 <= successes <= trials:
        raise ValueError("Successes must be between zero and the number of trials")
    alpha = 1 - float(Decimal(confidence))
    if not 0 < alpha < 1:
        raise ValueError("Confidence must be in (0, 1)")
    if successes == trials:
        return 1.0
    low, high = successes / trials, 1.0
    while high - low > tolerance:
        middle = (low + high) / 2
        # The CDF falls as p rises, so the root is above middle when the CDF is
        # still larger than alpha.
        if _binomial_cdf(successes, trials, middle) > alpha:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def population_status(*, population, hypotheses, passing_folds, usable_folds,
                      minimum_hypotheses=STOPPING_RULE_MINIMUM_HYPOTHESES,
                      threshold=STOPPING_RULE_THRESHOLD,
                      confidence=STOPPING_RULE_CONFIDENCE):
    """Whether one evidence population is exhausted by measurement.

    Reports the bound even when the minimum is not met, and marks it NOT
    APPLICABLE rather than hiding it -- the number below the minimum is exactly
    the artefact the minimum exists to stop anyone reading as a conclusion, and
    the only way to make that visible is to show it while saying so.
    """
    if usable_folds < 1:
        raise ValueError("A population needs at least one usable fold")
    rate = passing_folds / usable_folds
    bound = clopper_pearson_upper(passing_folds, usable_folds, confidence)
    bar = float(Decimal(threshold))
    enough = hypotheses >= minimum_hypotheses

    if not enough:
        reason, exhausted = REASON_BELOW_MINIMUM, False
    elif bound >= bar:
        reason, exhausted = REASON_BOUND_REACHES_THRESHOLD, False
    else:
        reason, exhausted = REASON_EXHAUSTED, True

    return {
        "population": population,
        "hypotheses": hypotheses,
        "minimum_hypotheses": minimum_hypotheses,
        "passing_folds": passing_folds,
        "usable_folds": usable_folds,
        "rate": f"{rate:.6f}",
        "upper_bound_95": f"{bound:.6f}",
        "threshold": threshold,
        "bound_is_applicable": enough,
        "exhausted": exhausted,
        "reason": reason,
    }
