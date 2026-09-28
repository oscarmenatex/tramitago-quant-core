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

