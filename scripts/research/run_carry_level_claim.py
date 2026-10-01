"""The carry, judged as a LEVEL claim: does holding it pay, net of costs?

The question the comparative apparatus structurally could not ask. It computes
`upper_mean - lower_mean` and goes INCONCLUSIVE when a group is empty, so a
premium -- one position, no second group -- has never faced a verdict here, while
thirty-eight comparative Hypotheses were judged and none validated.

THE FUNDING IS READ AT 24 PAYMENTS A DAY, AND THAT IS A CORRECTION. Hyperliquid's
daily series is the MEAN OF THAT DAY'S HOURLY RATES, so the sealed
`forward_carry_return_1d` column subtracts a full day's basis move from ONE
HOUR's funding. At face value the sealed funding leg is +1.01% annualised for BTC
2024; read at 24 payments a day it is +24.14%, which is exactly what the
hypothesis catalogue recorded for that instrument and year. Hypothesis #37 was
evaluated on the understated quantity. Its record stands as what it was; this
measures the other one.

Nothing is fetched. Both inputs are already-sealed datasets, and the runner first
reproduces their own stored column from their own stored prices before computing
anything new, so a corrupted file cannot pass as a measurement.

    python3.11 -B scripts/research/run_carry_level_claim.py
"""

import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pipeline as p
from tramitago_quant_core.strategy_contract.outcome import carry_return_outcome
from tramitago_quant_core.research.level_claim import (
    level_claim, evaluate_fold, constitute_level_claim_validation,
)

DATASETS = [REPO / "artifacts" / "research" / "datasets" / f"carry_exit_btc_{year}"
            for year in (2024, 2025)]
ARTIFACTS = REPO / "artifacts" / "research"

# Hyperliquid pays funding every hour.
PAYMENTS_PER_DAY = 24
FOLD_COUNT = 6

# DECLARED, AND THE WEAKEST INPUT HERE. These are assumed per-leg execution costs,
# not measured fills, and the script prints the breakeven so the verdict can be
# read against a cost that was never observed. Both legs pay: long spot, short
# perpetual. Entry and exit are charged when they occur, so over a fold of ~four
# months they amortise -- which is the honest economics of a held position and the
# reason a carry is not killed by a round trip the way a daily-rotating signal is.
COST_COMMISSION = "0.00045"
COST_HALF_SPREAD = "0.0001"
COST_SLIPPAGE = "0.0001"
COST_SOURCE = ("ASSUMED per-leg taker fee and execution cost for a spot/perpetual carry; "
               "NOT measured fills. Replace with observed fills before any capital decision.")

MINIMUM_ADVERSE_PERIOD_FREQUENCY = "0.10"
# WHAT COUNTS AS A BAD DAY, declared rather than left at "any loss".
#
# At "any loss" this window registers 27.8% adverse periods out of ordinary daily
# noise and the gate reports the tail as observed -- while the worst single day
# of two years lost 0.198%, against the +2.74% adverse basis excursion measured
# on BitMEX during a squeeze. A premium's representativeness has to be about
# magnitude or it is about nothing.
#
# 1% is a day that costs roughly a MONTH of what the position earns (~17%/yr is
# ~0.047%/day, so thirty days is ~1.4%). Stated plainly: the distribution had
# already been inspected when this was chosen, so it is a declared floor rather
# than a blind one -- though any threshold above 0.2% gives the same answer here,
# since no day in the window reaches it.
ADVERSE_PERIOD_THRESHOLD = "-0.01"
MAXIMUM_DRAWDOWN = "0.15"
CONSISTENCY_THRESHOLD = "0.7"
MINIMUM_FOLDS = 5


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _code_revision():
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                          text=True, check=True).stdout.strip()


def _sealed_rows(directory):
    """Load a sealed carry dataset and PROVE its stored carry column is the one
    its own stored prices and funding produce. A file that fails this is not a
    measurement, whatever it says in its manifest."""
    rows = list(csv.DictReader((directory / "dataset.csv").open(encoding="utf-8")))
    typed = [{"timestamp": row["timestamp"], "close": float(row["close"]),
              "perp_close": float(row["perp_close"]),
              "funding_rate": float(row["funding_rate"]),
              "sealed_carry": (float(row["forward_carry_return_1d"])
                               if row["forward_carry_return_1d"] else None)}
             for row in rows]

    as_sealed = carry_return_outcome()
    checked = 0
    for index, row in enumerate(typed[:-1]):
        if row["sealed_carry"] is None:
            continue
        reproduced = as_sealed["compute"](row, typed[index + 1])
        if abs(reproduced - row["sealed_carry"]) > 1e-12:
            raise SystemExit(f"{directory.name} row {row['timestamp']} does not reproduce: "
                             f"stored {row['sealed_carry']} vs {reproduced}")
        checked += 1
    return typed, checked


def _corrected_series(rows):
    """The same Outcome, told how often the venue actually pays."""
    corrected = carry_return_outcome(payments_per_period=PAYMENTS_PER_DAY)
    series = []
    for index, row in enumerate(rows[:-1]):
        if row["sealed_carry"] is None:
            continue
        series.append((row["timestamp"], corrected["compute"](row, rows[index + 1])))
    return series


def _folds(series, count):
    size = len(series) // count
    return [series[index * size:(index + 1) * size if index < count - 1 else len(series)]
            for index in range(count)]


def _breakeven_cost_per_side(series, periods_per_fold):
    """The per-side cost at which a fold's total net return reaches zero.

    Reported rather than gated: the declared rates are assumed, and a verdict
    whose weakest input is an assumption should say how much room that
    assumption has. Entry and exit are two sides per leg, two legs.
    """
    total = sum(value for _, value in series[:periods_per_fold])
    return total / 4 if total > 0 else 0.0


def main():
    contract = p.cost_contract(
        regime=p.COST_REGIME_HOLDING, commission_rate=COST_COMMISSION,
        half_spread_rate=COST_HALF_SPREAD, slippage_rate=COST_SLIPPAGE,
        recurring_rate_per_period="0", legs=2, source=COST_SOURCE)

    rows, series = [], []
    for directory in DATASETS:
        sealed, checked = _sealed_rows(directory)
        print(f"verified     {directory.name}: {checked} rows reproduce their sealed carry column")
        rows.append(sealed)
        series.extend(_corrected_series(sealed))

    annual = sum(value for _, value in series) / len(series) * 365
    print(f"periods      {len(series)}  {series[0][0]} .. {series[-1][0]}")
    print(f"gross carry  {annual:+.2%} annualised at {PAYMENTS_PER_DAY} funding payments a day")

    claim = level_claim(
        position_description=("long BTC spot, short BTC perpetual, equal notional, held "
                              "continuously; funding at 24 payments a day"),
        cost_contract=contract, minimum_folds_required=MINIMUM_FOLDS,
        consistency_threshold=CONSISTENCY_THRESHOLD,
        minimum_adverse_period_frequency=MINIMUM_ADVERSE_PERIOD_FREQUENCY,
        adverse_period_threshold=ADVERSE_PERIOD_THRESHOLD,
        maximum_drawdown=MAXIMUM_DRAWDOWN, confidence_level="0.95",
        bootstrap_resamples=2000, bootstrap_block_periods=5, bootstrap_seed=0,
        source="DOC-011 §9.4 thresholds; drawdown limit 15% confirmed 2026-09-30")
    print(f"claim        {claim['claim_id']}")

    folds = []
    for index, chunk in enumerate(_folds(series, FOLD_COUNT)):
        folds.append(evaluate_fold(
            claim, contract, fold_index=index,
            period={"start_utc": chunk[0][0], "end_exclusive_utc": chunk[-1][0]},
            positions=[1] * len(chunk), gross_returns=[value for _, value in chunk]))

    print("\n  fold  period                     result    mean/day    adverse bound  loses  dd")
    for fold in folds:
        print(f"  {fold['fold_index']:>4}  {fold['period']['start_utc'][:10]}..."
              f"{fold['period']['end_exclusive_utc'][:10]}  {fold['result']:<9} "
              f"{float(fold['mean_net_return']):+.6f}  "
              f"{float(fold['adverse_mean_bound']):+.8f}   "
              f"{float(fold['adverse_period_frequency']):.0%}  "
              f"{float(fold['max_drawdown']):.2%}")

    record = constitute_level_claim_validation(
        ARTIFACTS / "level-claim-validations.json", claim=claim, folds=folds,
        validated_at=_now(), validation_code_revision=_code_revision())

    print(f"\nvalidation   {record['validation_id']}")
    print(f"OUTCOME      {record['outcome']}"
          + (f"  ({record['outcome_reason']})" if record["outcome_reason"] else ""))
    print(f"consistency  {record['consistency_ratio']}")
    print(f"adverse      {record['adverse_period_frequency']} of held periods lost more "
          f"than {ADVERSE_PERIOD_THRESHOLD}")
    print(f"costs        {json.dumps(folds[0]['costs'], sort_keys=True)}")
    print(f"breakeven    {_breakeven_cost_per_side(series, len(series) // FOLD_COUNT):.6f} "
          f"per side per leg, against {float(COST_COMMISSION) + float(COST_HALF_SPREAD) + float(COST_SLIPPAGE):.6f} declared")
    return 0


if __name__ == "__main__":
    sys.exit(main())
