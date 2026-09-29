"""M4.1 -- Strategy contract (proof of concept, DOC-002 SS3.1/SS3.5/SS6.1).
See module docstring in pipeline.py history / Etapa 4 docs for the full
rationale: never wired into M2.1/M2.2/M2.6 production functions.

Moved out of pipeline.py per DOC-014/DOC-015 (adaptado) SS4 (Etapa 4, M4.1
module decomposition). No behavior change.
"""

import math


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

