"""CAP-002 Research extension -- Etapa 2.7, M2.7-T1: a bounded, pre-justified
search space for autonomous hypothesis generation.

See "Etapa 2.7 -- Generacion Autonoma de Hipotesis.txt" (tramitago-quant-core-docs)
for the full risk analysis (data snooping / multiple comparisons, DOC-004 SS14)
this Etapa exists to neutralize by design. R-2.7-001 requires the search
space to be declared and justified in writing BEFORE any hypothesis in the
batch is generated -- never expanded ad hoc after seeing results.

This module only declares the search space (M2.7-T1). It does not apply
statistical correction (M2.7-T2, walk_forward.py's
_statistical_validation_outcome), register a batch (M2.7-T3), or run the
batch (M2.7-T4) -- those are separate microciclos.
"""

from tramitago_quant_core.strategy_contract.strategy import (
    sma_crossover_strategy, momentum_crossover_strategy,
)

# R-2.7-001 -- bounded, pre-justified search space. Written justification,
# fixed at declaration time (2026-09-28):
#
#   - SMA_CROSSOVER windows {3, 5, 7, 10}: the same strategy family already
#     investigated manually for the rejected SMA3 Hypothesis (BTC-USD,
#     ETH-USD, both NOT_VALIDATED against the Fase 0->1 bar). Widening the
#     window is the narrowest possible variation of an already-understood
#     strategy, not an unrelated new idea reached for after a rejection.
#   - MOMENTUM_CROSSOVER lookbacks {3, 5, 7, 10}: the only other Strategy
#     family proven through the Strategy contract (M4.1) at the time this
#     search space was declared. Deliberately the same four period lengths
#     as the SMA family, so the batch cannot be read as favoring one
#     family's parameter range over the other's.
#   - Single instrument (BTC-USD): the instrument every prior Hypothesis in
#     this project has used. Testing a second instrument in the same batch
#     as N=8 new strategy variants would confound which dimension --
#     strategy or instrument -- explains any result.
#
# N = 8. This is the ENTIRE space for this batch. No other window,
# lookback, instrument, or indicator family may be added to THIS declared
# batch after it is generated; a future batch is a new, separately-declared
# search space, never a silent extension of this one.
HYPOTHESIS_GENERATION_SEARCH_SPACE_ID = "SEARCH_SPACE|2026-09-28|SMA_MOMENTUM_BTC_USD"
HYPOTHESIS_GENERATION_INSTRUMENT = "BTC-USD"
HYPOTHESIS_GENERATION_SMA_WINDOWS = (3, 5, 7, 10)
HYPOTHESIS_GENERATION_MOMENTUM_LOOKBACKS = (3, 5, 7, 10)


def hypothesis_generation_search_space():
    """Return this batch's Strategy objects, in a fixed, declared order.

    Deterministic and side-effect free: calling this twice must produce
    structurally identical Strategies (verified by the corresponding test),
    since the batch registry (M2.7-T3) will hash this declaration.
    """
    strategies = [sma_crossover_strategy(window)
                 for window in HYPOTHESIS_GENERATION_SMA_WINDOWS]
    strategies += [momentum_crossover_strategy(lookback)
                  for lookback in HYPOTHESIS_GENERATION_MOMENTUM_LOOKBACKS]
    return strategies
