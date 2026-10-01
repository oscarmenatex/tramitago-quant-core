"""M4.1 -- Strategy contract (proof of concept, DOC-002 SS3.1/SS3.5/SS6.1).
See module docstring in pipeline.py history / Etapa 4 docs for the full
rationale: never wired into M2.1/M2.2/M2.6 production functions.

Moved out of pipeline.py per DOC-014/DOC-015 (adaptado) SS4 (Etapa 4, M4.1
module decomposition). No behavior change.
"""

import math

from tramitago_quant_core.strategy_contract.outcome import (
    spread_return_outcome, carry_return_outcome,
)


# M4.1 -- Strategy contract (proof of concept, DOC-002 SS3.1/SS3.5/SS6.1).
#
# Any Strategy must expose the same shape: what raw inputs it needs
# (required_inputs) and a pure compute() that turns a window of raw rows into
# a signal. The rest of the system (dataset construction, research,
# decision) only ever reads required_inputs and the returned "group" -- it
# never needs to know HOW a strategy computes its signal. This is
# demonstrated below with two structurally different strategies (a moving
# average crossover and a lag-based momentum crossover) run through the
# same generic classifier, _strategy_classify_rows, which never inspects
# strategy_id or parameters.
#
# This is deliberately NOT wired into M2.1/M2.2/M2.6's production functions
# yet (those still compute SMA3 exactly as before) -- see the M4.1 note in
# "Etapa 4 -- Revision y optimizacion.txt" for why that migration is a
# separate, larger microciclo.

STRATEGY_SCHEMA_VERSION = "1"


def sma_crossover_strategy(window):
    """A Strategy: classifies each observation by whether its close crosses
    above its own trailing simple moving average."""
    if not isinstance(window, int) or isinstance(window, bool) or window < 2:
        raise ValueError("SMA crossover window must be an integer >= 2")
    indicator_name = f"SMA{window}"
    column_name = f"sma_close_{window}"

    def compute(window_rows):
        if len(window_rows) != window:
            raise ValueError("Strategy compute window has the wrong length")
        indicator_value = math.fsum(row["close"] / window for row in window_rows)
        if not math.isfinite(indicator_value):
            raise ValueError("Non-finite indicator")
        close_t = window_rows[-1]["close"]
        group = "UPPER" if close_t > indicator_value else "LOWER_OR_EQUAL"
        return {"indicator_value": indicator_value, "group": group}

    return {
        "schema_version": STRATEGY_SCHEMA_VERSION,
        "strategy_id": "SMA_CROSSOVER",
        "parameters": {"window": window},
        "indicator_name": indicator_name,
        "column_name": column_name,
        "required_inputs": {"variables": ["close"], "warmup_periods": window - 1},
        "upper_group_description": f"close_t > {indicator_name}_t",
        "lower_or_equal_group_description": f"close_t <= {indicator_name}_t",
        "compute": compute,
    }


def momentum_crossover_strategy(lookback):
    """A structurally different Strategy: classifies each observation by
    whether its close exceeds the close from `lookback` periods ago -- no
    averaging, no window-wide aggregation. Proves the contract is agnostic
    to HOW a strategy computes its signal, not just to its parameters."""
    if not isinstance(lookback, int) or isinstance(lookback, bool) or lookback < 1:
        raise ValueError("Momentum lookback must be a positive integer")
    indicator_name = f"MOM{lookback}"
    column_name = f"close_lag_{lookback}"

    def compute(window_rows):
        if len(window_rows) != lookback + 1:
            raise ValueError("Strategy compute window has the wrong length")
        indicator_value = window_rows[0]["close"]
        if not math.isfinite(indicator_value):
            raise ValueError("Non-finite indicator")
        close_t = window_rows[-1]["close"]
        group = "UPPER" if close_t > indicator_value else "LOWER_OR_EQUAL"
        return {"indicator_value": indicator_value, "group": group}

    return {
        "schema_version": STRATEGY_SCHEMA_VERSION,
        "strategy_id": "MOMENTUM_CROSSOVER",
        "parameters": {"lookback": lookback},
        "indicator_name": indicator_name,
        "column_name": column_name,
        "required_inputs": {"variables": ["close"], "warmup_periods": lookback},
        "upper_group_description": f"close_t > {indicator_name}_t",
        "lower_or_equal_group_description": f"close_t <= {indicator_name}_t",
        "compute": compute,
    }


def volume_surge_strategy(window):
    """A third Strategy family, structurally unrelated to SMA_CROSSOVER and
    MOMENTUM_CROSSOVER: classifies each observation by whether its trading
    volume exceeds its own trailing average volume over the prior `window`
    days -- an information-flow hypothesis (unusual volume, not price
    trend), never derived from `close`. Proposed 2026-09-28 after 9 real
    SMA/Momentum variants all showed a full-sample effect an order of
    magnitude smaller than the instrument's own daily volatility -- a
    genuinely different signal source, not another parameter of the same
    idea."""
    if not isinstance(window, int) or isinstance(window, bool) or window < 2:
        raise ValueError("Volume surge window must be an integer >= 2")
    indicator_name = f"VOLSURGE{window}"
    column_name = f"volume_avg_{window}"

    def compute(window_rows):
        if len(window_rows) != window + 1:
            raise ValueError("Strategy compute window has the wrong length")
        prior = window_rows[:-1]
        indicator_value = math.fsum(row["volume"] for row in prior) / window
        if not math.isfinite(indicator_value):
            raise ValueError("Non-finite indicator")
        current_volume = window_rows[-1]["volume"]
        group = "UPPER" if current_volume > indicator_value else "LOWER_OR_EQUAL"
        return {"indicator_value": indicator_value, "group": group}

    return {
        "schema_version": STRATEGY_SCHEMA_VERSION,
        "strategy_id": "VOLUME_SURGE",
        "parameters": {"window": window},
        "indicator_name": indicator_name,
        "column_name": column_name,
        "required_inputs": {"variables": ["volume"], "warmup_periods": window},
        "upper_group_description": f"volume_t > {indicator_name}_t",
        "lower_or_equal_group_description": f"volume_t <= {indicator_name}_t",
        "compute": compute,
    }


def funding_rate_sign_strategy():
    """A fourth Strategy family, and the first requiring a variable outside
    the base OHLCV set: classifies each day by the SIGN of the PRIOR day's
    daily-mean perpetual-futures funding rate (positive: longs paying
    shorts, a leverage-crowded-long market; negative: the reverse) --
    never today's still-accruing funding rate, to avoid same-day lookahead.
    Proposed 2026-09-28 (Etapa 2.8) after diagnosing that price/volume-only
    signals on BTC-USD/ETH-USD all showed effects inside the noise floor --
    funding rate comes from an entirely different market (derivatives
    positioning, not spot price/volume), a genuinely different information
    source, not another transform of the same series. Sign, not a
    magnitude/percentile threshold, was chosen deliberately to avoid
    introducing a free parameter that could be tuned to the data."""
    indicator_name = "FUNDINGSIGN"
    column_name = "funding_rate_lag_1"

    def compute(window_rows):
        if len(window_rows) != 2:
            raise ValueError("Strategy compute window has the wrong length")
        indicator_value = window_rows[0]["funding_rate"]
        if not math.isfinite(indicator_value):
            raise ValueError("Non-finite indicator")
        group = "UPPER" if indicator_value > 0 else "LOWER_OR_EQUAL"
        return {"indicator_value": indicator_value, "group": group}

    return {
        "schema_version": STRATEGY_SCHEMA_VERSION,
        "strategy_id": "FUNDING_RATE_SIGN",
        "parameters": {},
        "indicator_name": indicator_name,
        "column_name": column_name,
        "required_inputs": {"variables": ["funding_rate"], "warmup_periods": 1},
        "upper_group_description": f"{indicator_name}_t > 0",
        "lower_or_equal_group_description": f"{indicator_name}_t <= 0",
        "compute": compute,
    }


def pair_ratio_reversion_strategy(window, pair_variable="pair_close"):
    """Vía 7.C -- the first RELATIVE-VALUE Strategy: it reads two real price
    series and classifies by where their ratio sits against its own trailing
    average, and it declares a SPREAD Outcome, so the quantity predicted is
    the return of a market-neutral position rather than one asset's
    direction.

    Why this is structurally different from everything tested before: all 27
    prior Hypotheses predicted the direction of ONE instrument, fighting
    against that instrument's own 3-4% daily volatility. A spread between
    two correlated assets has far lower volatility, so an equally small edge
    is relatively larger. The aggregate signal measurement that closed the
    single-signal axis does not cover this -- it measured single-instrument
    directional prediction only.

    Both legs are real captured prices: the primary instrument travels the
    normal dataset path and the second arrives as an auxiliary variable,
    sealed and independently re-verified. No synthetic ratio instrument is
    built, because the high/low of A/B are not derivable from the daily OHLC
    of A and B.

    Signal and P&L are deliberately distinct here: the signal is read off
    the ratio, but the P&L is the difference of two real returns."""
    if not isinstance(window, int) or isinstance(window, bool) or window < 2:
        raise ValueError("Pair ratio window must be an integer >= 2")
    if not isinstance(pair_variable, str) or not pair_variable:
        raise ValueError("Pair variable name is required")
    indicator_name = f"PAIRRATIO{window}"
    column_name = f"pair_ratio_avg_{window}"

    def ratio(row):
        return row["close"] / row[pair_variable]

    def compute(window_rows):
        if len(window_rows) != window + 1:
            raise ValueError("Strategy compute window has the wrong length")
        prior = window_rows[:-1]
        indicator_value = math.fsum(ratio(row) for row in prior) / window
        if not math.isfinite(indicator_value):
            raise ValueError("Non-finite indicator")
        current = ratio(window_rows[-1])
        if not math.isfinite(current):
            raise ValueError("Non-finite indicator")
        group = "UPPER" if current > indicator_value else "LOWER_OR_EQUAL"
        return {"indicator_value": indicator_value, "group": group}

    return {
        "schema_version": STRATEGY_SCHEMA_VERSION,
        "strategy_id": "PAIR_RATIO_REVERSION",
        "parameters": {"window": window, "pair_variable": pair_variable},
        "indicator_name": indicator_name,
        "column_name": column_name,
        "required_inputs": {"variables": ["close", pair_variable],
                            "warmup_periods": window},
        "upper_group_description": (
            f"close_t / {pair_variable}_t > {indicator_name}_t"),
        "lower_or_equal_group_description": (
            f"close_t / {pair_variable}_t <= {indicator_name}_t"),
        "outcome": spread_return_outcome(pair_variable),
        "compute": compute,
    }


def signal_portfolio_strategy(sma_window, volume_window, range_window, funding_window):
    """A weak-signal PORTFOLIO: a majority vote across four structurally
    different signal families, rather than one signal evaluated alone.

    Proposed 2026-09-29 after the aggregate measurement showed the
    project's acceptance criterion (>= 70% fold consistency) is a
    whole-strategy bar being applied to individual components -- a bar no
    genuinely weak signal ever clears alone, while real systems combine
    many such signals. Measurement backs the mechanism here: the eight
    price variants already tested are redundant (mean pairwise correlation
    0.753, effective breadth 1.28 of 8), but these four families are
    genuinely diverse (0.131, effective breadth 2.87 of 4).

    Votes, each keeping the exact direction its own original Hypothesis
    was declared with, before any result was known:
        close_t              > SMA_t          (momentum)
        volume_t             > VOLAVG_t       (information flow)
        (high_t-low_t)/close > RANGEAVG_t     (volatility expansion)
        funding_rate_{t-1}   > FUNDAVG_t      (positioning; keeps lag-1)
    UPPER when at least 3 of 4 agree; ties (2-2) fall to LOWER_OR_EQUAL,
    matching the ">" convention used throughout.

    A vote, not a weighted average: there is no scale to standardize and
    no weight to fit. Fitting weights would introduce four free parameters
    and collapse the anti-data-snooping apparatus; equal votes cannot be
    overfitted because nothing is fitted.

    Known, deliberately accepted limitation: the volume and range votes
    correlate 0.772 with each other, so "market agitation" carries two of
    the four votes. Dropping one of them by comparing their in-sample IC
    would itself be a snooped choice, so both are kept and the imbalance
    is documented instead.
    """
    for name, window in (("SMA", sma_window), ("Volume", volume_window),
                         ("Range", range_window), ("Funding", funding_window)):
        if not isinstance(window, int) or isinstance(window, bool) or window < 2:
            raise ValueError(name + " window must be an integer >= 2")
    indicator_name = f"PORTFOLIO{sma_window}_{volume_window}_{range_window}_{funding_window}"
    column_name = "portfolio_votes"
    warmup = max(sma_window - 1, volume_window, range_window, funding_window + 1)

    def compute(window_rows):
        if len(window_rows) != warmup + 1:
            raise ValueError("Strategy compute window has the wrong length")
        today = window_rows[-1]

        sma = math.fsum(row["close"] / sma_window for row in window_rows[-sma_window:])
        volume_avg = math.fsum(
            row["volume"] for row in window_rows[-(volume_window + 1):-1]) / volume_window
        normalized_range = lambda row: (row["high"] - row["low"]) / row["close"]
        range_avg = math.fsum(
            normalized_range(row) for row in window_rows[-(range_window + 1):-1]) / range_window
        funding_avg = math.fsum(
            row["funding_rate"]
            for row in window_rows[-(funding_window + 2):-2]) / funding_window
        yesterday_funding = window_rows[-2]["funding_rate"]

        for value in (sma, volume_avg, range_avg, funding_avg, yesterday_funding):
            if not math.isfinite(value):
                raise ValueError("Non-finite indicator")

        votes = sum((today["close"] > sma,
                     today["volume"] > volume_avg,
                     normalized_range(today) > range_avg,
                     yesterday_funding > funding_avg))
        return {"indicator_value": float(votes),
                "group": "UPPER" if votes >= 3 else "LOWER_OR_EQUAL"}

    upper = (f"at least 3 of 4 votes: close>SMA{sma_window}, volume>VOLAVG{volume_window}, "
             f"range>RANGEAVG{range_window}, funding_t-1>FUNDAVG{funding_window}")
    return {
        "schema_version": STRATEGY_SCHEMA_VERSION,
        "strategy_id": "SIGNAL_PORTFOLIO",
        "parameters": {"sma_window": sma_window, "volume_window": volume_window,
                       "range_window": range_window, "funding_window": funding_window},
        "indicator_name": indicator_name,
        "column_name": column_name,
        "required_inputs": {"variables": ["close", "volume", "high", "low", "funding_rate"],
                            "warmup_periods": warmup},
        "upper_group_description": upper,
        "lower_or_equal_group_description": f"NOT ({upper})",
        "compute": compute,
    }


def funding_rate_surge_strategy(window):
    """A seventh Strategy family: classifies each day by whether the PRIOR
    day's mean perpetual-futures funding rate exceeds the trailing average
    of the same quantity over the `window` days before it -- a RELATIVE
    threshold, not the absolute sign FUNDING_RATE_SIGN used.

    Proposed 2026-09-29 to fix the design flaw FUNDING_RATE_SIGN's own
    Knowledge Record documented: classifying by sign cannot produce a
    balanced partition when the underlying series carries a near-constant
    bias, and BTC funding is positive on the large majority of days (93 of
    96 in the first real funding test, leaving one walk-forward fold with
    an empty group and the whole validation INSUFFICIENT_EVIDENCE). More
    data alone would not have fixed that -- the split stays degenerate at
    any length. Comparing the series against its OWN recent history is
    exactly how volume_surge_strategy handles the same problem for volume,
    which is likewise always positive, and it produces a balanced split by
    construction.

    Keeps FUNDING_RATE_SIGN's lag-1 convention: the signal at day t reads
    day t-1's funding, never day t's still-accruing value."""
    if not isinstance(window, int) or isinstance(window, bool) or window < 2:
        raise ValueError("Funding rate surge window must be an integer >= 2")
    indicator_name = f"FUNDSURGE{window}"
    column_name = f"funding_rate_avg_{window}"

    def compute(window_rows):
        if len(window_rows) != window + 2:
            raise ValueError("Strategy compute window has the wrong length")
        prior = window_rows[:-2]          # the `window` days before yesterday
        indicator_value = math.fsum(row["funding_rate"] for row in prior) / window
        if not math.isfinite(indicator_value):
            raise ValueError("Non-finite indicator")
        yesterday = window_rows[-2]["funding_rate"]
        if not math.isfinite(yesterday):
            raise ValueError("Non-finite indicator")
        group = "UPPER" if yesterday > indicator_value else "LOWER_OR_EQUAL"
        return {"indicator_value": indicator_value, "group": group}

    return {
        "schema_version": STRATEGY_SCHEMA_VERSION,
        "strategy_id": "FUNDING_RATE_SURGE",
        "parameters": {"window": window},
        "indicator_name": indicator_name,
        "column_name": column_name,
        "required_inputs": {"variables": ["funding_rate"], "warmup_periods": window + 1},
        "upper_group_description": f"funding_rate_t-1 > {indicator_name}_t",
        "lower_or_equal_group_description": f"funding_rate_t-1 <= {indicator_name}_t",
        "compute": compute,
    }


def intraday_range_strategy(window):
    """A sixth Strategy family, and the first to read `high`/`low` at all:
    classifies each day by whether its own intraday range, normalized by
    that day's close ((high - low) / close), exceeds the trailing average
    of the same quantity over the prior `window` days -- a
    volatility-expansion hypothesis (how UNCERTAIN the day was), never
    direction (SMA/Momentum) and never activity level (Volume Surge).

    Proposed 2026-09-29 after the catalog's Nivel 1 and the first combined
    Strategy all failed: every Strategy tested so far read only `close`,
    `volume` or `funding_rate`, leaving the high/low half of the OHLCV
    record entirely untouched. Normalizing by close (rather than the raw
    high-low spread) keeps the indicator comparable across price levels,
    so the trailing average is not dominated by the instrument's own drift
    in nominal price."""
    if not isinstance(window, int) or isinstance(window, bool) or window < 2:
        raise ValueError("Intraday range window must be an integer >= 2")
    indicator_name = f"RANGE{window}"
    column_name = f"range_avg_{window}"

    def _normalized_range(row):
        return (row["high"] - row["low"]) / row["close"]

    def compute(window_rows):
        if len(window_rows) != window + 1:
            raise ValueError("Strategy compute window has the wrong length")
        prior = window_rows[:-1]
        indicator_value = math.fsum(_normalized_range(row) for row in prior) / window
        if not math.isfinite(indicator_value):
            raise ValueError("Non-finite indicator")
        current_range = _normalized_range(window_rows[-1])
        if not math.isfinite(current_range):
            raise ValueError("Non-finite indicator")
        group = "UPPER" if current_range > indicator_value else "LOWER_OR_EQUAL"
        return {"indicator_value": indicator_value, "group": group}

    return {
        "schema_version": STRATEGY_SCHEMA_VERSION,
        "strategy_id": "INTRADAY_RANGE",
        "parameters": {"window": window},
        "indicator_name": indicator_name,
        "column_name": column_name,
        "required_inputs": {"variables": ["high", "low", "close"], "warmup_periods": window},
        "upper_group_description": f"(high_t - low_t) / close_t > {indicator_name}_t",
        "lower_or_equal_group_description": f"(high_t - low_t) / close_t <= {indicator_name}_t",
        "compute": compute,
    }


def sma_volume_confirmation_strategy(sma_window, volume_window):
    """A fifth Strategy family, and the first COMBINED signal: classifies
    UPPER only when BOTH the SMA_CROSSOVER condition (close above its own
    trailing average) AND the VOLUME_SURGE condition (volume above its own
    trailing average) hold at once -- the "confirmation" heuristic technical
    analysts commonly invoke (a price move is more meaningful when
    accompanied by above-average volume). Proposed 2026-09-28/29 after the
    hypothesis catalog's Nivel 1 (period, liquidity, direction) all failed
    to help: tests whether a CONJUNCTION of two signals that are each,
    individually, inside the noise floor carries information neither one
    does alone -- genuinely different from adding a fourth solo indicator.

    Reuses SMA_CROSSOVER's and VOLUME_SURGE's own averaging formulas
    unchanged, so a direct comparison against SMA3/VOLSURGE20 in isolation
    is exact, not approximate."""
    if not isinstance(sma_window, int) or isinstance(sma_window, bool) or sma_window < 2:
        raise ValueError("SMA window must be an integer >= 2")
    if not isinstance(volume_window, int) or isinstance(volume_window, bool) or volume_window < 2:
        raise ValueError("Volume window must be an integer >= 2")
    indicator_name = f"SMA{sma_window}CONFIRM{volume_window}"
    column_name = f"sma_close_{sma_window}_confirm_vol_{volume_window}"
    warmup = max(sma_window - 1, volume_window)

    def compute(window_rows):
        if len(window_rows) != warmup + 1:
            raise ValueError("Strategy compute window has the wrong length")
        sma_rows = window_rows[-sma_window:]
        sma_value = math.fsum(row["close"] / sma_window for row in sma_rows)
        volume_rows = window_rows[-(volume_window + 1):-1]
        volume_avg = math.fsum(row["volume"] for row in volume_rows) / volume_window
        if not math.isfinite(sma_value) or not math.isfinite(volume_avg):
            raise ValueError("Non-finite indicator")
        close_t = window_rows[-1]["close"]
        volume_t = window_rows[-1]["volume"]
        group = "UPPER" if (close_t > sma_value and volume_t > volume_avg) else "LOWER_OR_EQUAL"
        return {"indicator_value": sma_value, "group": group}

    upper_description = (
        f"close_t > SMA{sma_window}_t AND volume_t > VOLAVG{volume_window}_t")
    lower_description = f"NOT ({upper_description})"
    return {
        "schema_version": STRATEGY_SCHEMA_VERSION,
        "strategy_id": "SMA_VOLUME_CONFIRMATION",
        "parameters": {"sma_window": sma_window, "volume_window": volume_window},
        "indicator_name": indicator_name,
        "column_name": column_name,
        "required_inputs": {"variables": ["close", "volume"], "warmup_periods": warmup},
        "upper_group_description": upper_description,
        "lower_or_equal_group_description": lower_description,
        "compute": compute,
    }


def _strategy_classify_rows(strategy, rows):
    """Generic classification runner: given ANY Strategy (via its contract)
    and raw rows (each with at least 'close'), produces one classified row
    per input row once enough warmup is available.

    Deliberately never reads strategy["strategy_id"] or
    strategy["parameters"] -- only required_inputs.warmup_periods and
    compute() -- proving the rest of the system can stay agnostic to which
    strategy is in use.
    """
    warmup = strategy["required_inputs"]["warmup_periods"]
    column_name = strategy["column_name"]
    result = []
    for index, row in enumerate(rows):
        if index < warmup:
            result.append({**row, column_name: None, "group": None})
            continue
        window_rows = rows[index - warmup:index + 1]
        signal = strategy["compute"](window_rows)
        result.append({**row, column_name: signal["indicator_value"], "group": signal["group"]})
    return result



def carry_funding_threshold_strategy(funding_variable="funding_rate",
                                     perpetual_variable="perp_close"):
    """The EXIT RULE of a carry, expressed as something the Core can judge.

    The destination is now harvesting risk premia, and its defining clause is
    "stop exploiting it when the evidence stops supporting it". That exit rule
    is currently a DECLARED threshold inside a monitoring contract -- a
    conjecture nobody has tested. This Strategy turns it into a Hypothesis.

    Why this rather than "does carry pay": whether the premium exists is already
    measured (10.63%-24.14% annualised gross on sealed funding), and it is a
    LEVEL claim about one group, which the M2.x apparatus structurally cannot
    express. Forcing it in would certify what is not in doubt. Whether the
    threshold DISCRIMINATES is a two-group comparison, which is exactly the
    shape the apparatus judges natively.

    THE THRESHOLD IS ZERO, and that choice is the integrity of the test. Zero is
    the only parameter-free boundary available: it is the point where the
    economics invert, from being paid to hold the position to paying to hold it.
    Any other number would have to come from looking at the data first, which is
    the failure every sealed Hypothesis in this project was built to avoid --
    the same reasoning that made funding_rate_sign_strategy use a sign rather
    than a tuned percentile.

    NO LOOKAHEAD, and the mechanics are worth stating exactly because an earlier
    draft of this docstring got them wrong. A row at time t is classified by the
    funding of t-1 -- the column is named funding_rate_lag_1 for that reason --
    never by t's own, which is still accruing when the decision is made. The
    Outcome then takes its funding from t+1, which is what is actually paid
    while the position is held. Signal and outcome therefore read funding two
    periods apart and cannot overlap. Same discipline as
    funding_rate_sign_strategy, which uses the prior day for the same reason.
    """
    indicator_name = "CARRYFUNDING"
    column_name = "funding_rate_lag_1"

    def compute(window_rows):
        if len(window_rows) != 2:
            raise ValueError("Strategy compute window has the wrong length")
        indicator_value = window_rows[0][funding_variable]
        if not math.isfinite(indicator_value):
            raise ValueError("Non-finite indicator")
        group = "UPPER" if indicator_value >= 0 else "LOWER_OR_EQUAL"
        return {"indicator_value": indicator_value, "group": group}

    return {
        "schema_version": STRATEGY_SCHEMA_VERSION,
        "strategy_id": "CARRY_FUNDING_THRESHOLD",
        "parameters": {"funding_variable": funding_variable,
                       "perpetual_variable": perpetual_variable,
                       "threshold": "0"},
        "indicator_name": indicator_name,
        "column_name": column_name,
        "required_inputs": {"variables": [funding_variable, perpetual_variable],
                            "warmup_periods": 1},
        "upper_group_description": f"{indicator_name}_t >= 0 (the premium is being paid)",
        "lower_or_equal_group_description": f"{indicator_name}_t < 0 (the premium has inverted)",
        "outcome": carry_return_outcome(funding_variable, perpetual_variable),
        "compute": compute,
    }
