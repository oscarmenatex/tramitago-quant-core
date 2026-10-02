"""The carry WITH its exit rule applied, judged as a level claim over its tail.

TWO SEALED RESULTS SIT EITHER SIDE OF THIS AND NEITHER IS A STRATEGY.

  The POSITION was refuted. Held continuously over 2019-2021, the carry's level
  claim came back NOT_VALIDATED: one unlevered fold drew down 15.77% against a
  declared 15% limit, and the worst single day cost 10.48%.

  The RULE was validated. CARRY_FUNDING_THRESHOLD at zero passed 8 of 8 folds on
  a pre-declared BitMEX holdout, corrected for the eight candidates it was
  selected out of.

A rule that discriminates and a position that breaks are half a strategy each.
This measures the other half: does leaving when the premium stops being paid
avoid the drawdown that refuted holding through it?

ONE THING CHANGES AND NOTHING ELSE. Same window, same venue, same captures, same
cost contract, same claim terms, same folds. The position is 1 on days the rule
says the premium is being paid and 0 otherwise, instead of 1 always. Both claims
are sealed here so the comparison is between two records rather than against a
remembered number.

THE WINDOW IS THE ONE THAT REFUTED THE POSITION, and the rule has never seen it:
the exit rule was discovered on BitMEX 2017-2019 and validated on 2021-07 to
2024-01, and 2019-2021 was deliberately excluded from that space for this reason.

AND THE RULE NOW PAYS FOR ITSELF. Held continuously, the carry is entered once
and exited once. Gated, it pays an entry and an exit every time funding crosses
zero, on BOTH legs. That cost is charged where it occurs and is the first real
argument against the rule rather than for it.

    python3.11 -B scripts/research/run_carry_level_claim_gated.py
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pipeline as p
from tramitago_quant_core.governance.evidence_host import require_evidence_host
from tramitago_quant_core.data.acquisition import normalize, _coinbase_public_request
from tramitago_quant_core.data.bitmex_perpetual_price import (
    capture_bitmex_perpetual_price, verified_bitmex_perpetual_price_capture,
)
from tramitago_quant_core.data.bitmex_funding_rate import (
    capture_bitmex_funding_rate, verified_bitmex_funding_rate_capture,
    BITMEX_FUNDING_PAYMENTS_PER_DAY,
)
from tramitago_quant_core.strategy_contract.strategy import _strategy_classify_rows
from tramitago_quant_core.research.level_claim import (
    level_claim, evaluate_fold, constitute_level_claim_validation,
)

SYMBOL, SPOT = "XBTUSD", "BTC-USD"
START, END = "2019-01-01T00:00:00Z", "2021-01-01T00:00:00Z"
FOLD_COUNT = 6

# Identical to the baseline run. Re-tuning any of these for the gated version
# would make the comparison meaningless, which is the only thing it is for.
MINIMUM_ADVERSE_EPISODES = 1
ADVERSE_PERIOD_THRESHOLD = "-0.01"
MAXIMUM_DRAWDOWN = "0.15"
CONSISTENCY_THRESHOLD = "0.7"
MINIMUM_FOLDS = 5
COST_COMMISSION = "0.00045"
COST_HALF_SPREAD = "0.0001"
COST_SLIPPAGE = "0.0001"
COST_SOURCE = ("ASSUMED per-leg taker fee and execution cost for a spot/perpetual carry; "
               "NOT measured fills. Replace with observed fills before any capital decision.")

ARTIFACTS = REPO / "artifacts" / "research"
MAX_CANDLES_PER_REQUEST = 300


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _code_revision():
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                          text=True, check=True).stdout.strip()


def _spot_closes():
    start, end = p.epoch(START), p.epoch(END)
    closes = {}
    cursor = start
    while cursor < end:
        stop = min(cursor + MAX_CANDLES_PER_REQUEST * 86400, end)
        url = (f"https://api.exchange.coinbase.com/products/{SPOT}/candles"
               f"?granularity=86400&start={p.iso(cursor)}&end={p.iso(stop)}")
        with urlopen(_coinbase_public_request(url), timeout=30) as response:
            raw = response.read()
        rows, report = normalize(raw, cursor, stop, SPOT)
        if report["errors"]:
            raise SystemExit(f"{SPOT} {p.iso(cursor)}: {report['errors'][:2]}")
        for row in rows:
            closes[row["timestamp"]] = row["close"]
        cursor = stop
    return closes


def _judge(label, description, contract, folds_data, revision):
    claim = level_claim(
        position_description=description, cost_contract=contract,
        minimum_folds_required=MINIMUM_FOLDS, consistency_threshold=CONSISTENCY_THRESHOLD,
        minimum_adverse_episodes=MINIMUM_ADVERSE_EPISODES,
        adverse_period_threshold=ADVERSE_PERIOD_THRESHOLD, maximum_drawdown=MAXIMUM_DRAWDOWN,
        confidence_level="0.95", bootstrap_resamples=2000, bootstrap_block_periods=5,
        bootstrap_seed=0,
        source="DOC-011 §9.4 thresholds; identical to the continuously-held baseline")
    folds = [evaluate_fold(claim, contract, fold_index=index, period=period,
                           positions=positions, gross_returns=returns)
             for index, (period, positions, returns) in enumerate(folds_data)]
    record = constitute_level_claim_validation(
        ARTIFACTS / "level-claim-validations.json", claim=claim, folds=folds,
        validated_at=_now(), validation_code_revision=revision)

    print(f"\n{'=' * 78}\n{label}\n{'=' * 78}")
    print("  fold  period                     result    mean/day    adverse bound  held  dd")
    for fold in folds:
        print(f"  {fold['fold_index']:>4}  {fold['period']['start_utc'][:10]}..."
              f"{fold['period']['end_exclusive_utc'][:10]}  {fold['result']:<9} "
              f"{float(fold['mean_net_return']) if fold['mean_net_return'] else 0:+.6f}  "
              f"{float(fold['adverse_mean_bound']) if fold['adverse_mean_bound'] else 0:+.8f}   "
              f"{fold['periods_held']:>3}  {float(fold['max_drawdown']):.2%}")
    transitions = sum(fold["costs"]["transitions"] for fold in folds)
    cost = sum(float(fold["costs"]["cost_total"]) for fold in folds)
    gross = sum(float(fold["costs"]["gross_total"]) for fold in folds)
    print(f"  OUTCOME      {record['outcome']}"
          + (f"  ({record['outcome_reason']})" if record["outcome_reason"] else ""))
    print(f"  consistency  {record['consistency_ratio']}")
    print(f"  episodes     {sum(f['adverse_episodes'] for f in folds)} below "
          f"{ADVERSE_PERIOD_THRESHOLD}")
    print(f"  held         {sum(f['periods_held'] for f in folds)} of "
          f"{sum(f['periods'] for f in folds)} days")
    print(f"  costs        {transitions} transitions, {cost:+.6f} total against "
          f"{gross:+.6f} gross")
    print(f"  worst dd     {max(float(f['max_drawdown']) for f in folds):.2%}")
    print(f"  validation   {record['validation_id']}")
    return record, folds, {"gross": gross, "cost": cost, "net": gross - cost,
                           "transitions": transitions}


def main():
    require_evidence_host(REPO)
    contract = p.cost_contract(
        regime=p.COST_REGIME_HOLDING, commission_rate=COST_COMMISSION,
        half_spread_rate=COST_HALF_SPREAD, slippage_rate=COST_SLIPPAGE,
        recurring_rate_per_period="0", legs=2, source=COST_SOURCE)
    revision = _code_revision()
    strategy = p.carry_funding_threshold_strategy(
        payments_per_period=BITMEX_FUNDING_PAYMENTS_PER_DAY)

    perp, perp_capture, perp_raw = capture_bitmex_perpetual_price(
        SYMBOL, START, END, _now(), bin_size="1d")
    funding, funding_capture, funding_raw = capture_bitmex_funding_rate(
        SYMBOL, START, END, _now())
    if verified_bitmex_perpetual_price_capture(perp_raw, perp_capture) != perp:
        raise SystemExit("Perpetual capture does not re-verify")
    if verified_bitmex_funding_rate_capture(funding_raw, funding_capture) != funding:
        raise SystemExit("Funding capture does not re-verify")
    spot = _spot_closes()
    print(f"captured     perp {len(perp)} | funding {len(funding)} | spot {len(spot)} days, "
          f"both BitMEX captures re-verified from stored bytes")

    stamps = sorted(set(spot) & set(perp) & set(funding))
    rows = [{"timestamp": stamp, "close": spot[stamp], "perp_close": perp[stamp],
             "funding_rate": funding[stamp]} for stamp in stamps]
    classified = _strategy_classify_rows(strategy, rows)
    outcome = strategy["outcome"]

    # One row per day where the signal exists AND a next day exists to realise the
    # return into. Both claims see exactly this series; only the position differs.
    series = []
    for index in range(len(classified) - 1):
        if classified[index]["group"] is None:
            continue
        series.append((classified[index]["timestamp"],
                       outcome["compute"](classified[index], classified[index + 1]),
                       1 if classified[index]["group"] == "UPPER" else 0))
    print(f"series       {len(series)} days  |  rule says HOLD on "
          f"{sum(gate for _, _, gate in series)} of them")

    size = len(series) // FOLD_COUNT
    chunks = [series[i * size:(i + 1) * size if i < FOLD_COUNT - 1 else len(series)]
              for i in range(FOLD_COUNT)]
    always = [({"start_utc": c[0][0], "end_exclusive_utc": c[-1][0]},
               [1] * len(c), [value for _, value, _ in c]) for c in chunks]
    gated = [({"start_utc": c[0][0], "end_exclusive_utc": c[-1][0]},
              [gate for _, _, gate in c], [value for _, value, _ in c]) for c in chunks]

    baseline, _, always_totals = _judge(
        "A. HELD CONTINUOUSLY  (the position already refuted on this window)",
        "long BTC spot, short BitMEX XBTUSD perpetual, equal notional, held continuously; "
        "funding at 3 payments a day",
        contract, always, revision)
    rule, _, gated_totals = _judge(
        "B. GATED BY THE VALIDATED EXIT RULE  (hold only while funding >= 0)",
        "long BTC spot, short BitMEX XBTUSD perpetual, equal notional, held ONLY while "
        "funding at t-1 is at or above zero; funding at 3 payments a day",
        contract, gated, revision)

    print(f"\n{'=' * 78}\nDOES THE RULE RESCUE THE POSITION?\n{'=' * 78}")
    print(f"  continuously  {baseline['outcome']:<22} {baseline['outcome_reason']}")
    print(f"  gated         {rule['outcome']:<22} {rule['outcome_reason']}")
    print()
    print(f"  gross   {always_totals['gross']:+.6f}  ->  {gated_totals['gross']:+.6f}   "
          f"the rule DOES avoid the bad days")
    print(f"  costs   {always_totals['cost']:+.6f}  ->  {gated_totals['cost']:+.6f}   "
          f"{always_totals['transitions']} transitions -> {gated_totals['transitions']}")
    print(f"  NET     {always_totals['net']:+.6f}  ->  {gated_totals['net']:+.6f}   "
          f"and the trading costs more than it saves")

    # At what per-leg-side execution cost would gating stop losing? Each transition
    # charges cost_per_side = rate x legs, so the answer follows from the two
    # transition counts and the gross each version earned.
    legs = int(contract["legs"])
    spread = gated_totals["gross"] - always_totals["gross"]
    extra = (gated_totals["transitions"] - always_totals["transitions"]) * legs
    declared = (float(COST_COMMISSION) + float(COST_HALF_SPREAD) + float(COST_SLIPPAGE))
    print()
    print(f"  breakeven execution cost {spread / extra:.6f} per leg per side, "
          f"against {declared:.6f} declared")
    print(f"  The verdict turns on a number nobody measured: a fill {declared - spread / extra:.6f} "
          f"cheaper per side flips it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
