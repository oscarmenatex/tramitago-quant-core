"""Outcome contract (vía 7.C, 2026-09-29): what a Hypothesis measures the
return OF.

Every Hypothesis so far measured the same thing -- the forward return of
one instrument's close -- so that definition was hardcoded in five places
across historical_dataset.py and experiment.py. A relative-value
(pairs) Hypothesis measures something different: the return of a
market-neutral position, long one leg and short the other. That is not a
different Strategy (the signal) nor a different horizon (the distance);
it is a different OUTCOME (the quantity being predicted).

An Outcome is declared BY a Strategy, not threaded separately: a pairs
Strategy inherently knows its P&L is a spread, and `strategy` already
reaches every layer that needs it. A Strategy with no "outcome" key gets
close_return_outcome() -- which is exactly what every already-sealed real
artifact was built with, so they all reproduce byte-for-byte unchanged.

Deliberately NOT done here: synthesizing a ratio instrument with a
fabricated high/low. The high of A/B is not derivable from the daily OHLC
of A and B (max(A/B) is not max(A)/min(B)), so building one would mean
inventing data the market never printed. Both legs keep their real prices
instead, and the second leg arrives as an auxiliary variable through the
Etapa 2.8 fusion point.
"""

OUTCOME_SCHEMA_VERSION = "1"


def _forward_column(horizon):
    """The LITERAL column name every already-sealed real dataset was built
    with at horizon=1 ("forward_return_1d"); any other horizon derives its
    own so it can coexist without colliding."""
    return "forward_return_1d" if horizon == 1 else f"forward_return_{horizon}d"


def close_return_outcome():
    """The default, and the only Outcome any already-sealed real Hypothesis
    ever used: the forward return of a single instrument's close."""
    return {
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "outcome_id": "CLOSE_RETURN",
        "parameters": {},
        "required_inputs": {"variables": ["close"]},
        "column": _forward_column,
        "name": lambda horizon: f"return_t+{horizon}",
        "formula": lambda horizon: f"(close_t+{horizon} / close_t) - 1",
        "compute": lambda row, future_row: future_row["close"] / row["close"] - 1,
    }


def spread_return_outcome(pair_variable):
    """The return of a market-neutral pair: long the primary instrument,
    short the second leg, equal notional, held `horizon` periods.

        (close_t+h / close_t) - (pair_t+h / pair_t)

    Both legs are real captured prices -- the second one arrives as an
    auxiliary variable, sealed and independently re-verified exactly like
    any other auxiliary source. Nothing is synthesized.

    This is the quantity a relative-value Hypothesis actually predicts, and
    it is why such a Hypothesis cannot be expressed by changing the
    Strategy alone: the signal may be read off the ratio, but the P&L is a
    difference of two real returns."""
    if not isinstance(pair_variable, str) or not pair_variable:
        raise ValueError("Pair variable name is required")

    def column(horizon):
        return f"forward_spread_return_{horizon}d"

    def compute(row, future_row):
        return (future_row["close"] / row["close"]
                - future_row[pair_variable] / row[pair_variable])

    return {
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "outcome_id": "SPREAD_RETURN",
        "parameters": {"pair_variable": pair_variable},
        "required_inputs": {"variables": ["close", pair_variable]},
        "column": column,
        "name": lambda horizon: f"spread_return_t+{horizon}",
        "formula": lambda horizon: (
            f"(close_t+{horizon} / close_t) - "
            f"({pair_variable}_t+{horizon} / {pair_variable}_t)"),
        "compute": compute,
    }


def strategy_outcome(strategy):
    """Resolve the Outcome a Strategy declares, defaulting to the close
    return. Field presence IS the discriminator -- never a version flag --
    the same additive pattern used for the legacy "sma" field and for
    auxiliary variables."""
    return strategy.get("outcome") or close_return_outcome()
