"""The carry, re-asked as a portfolio question instead of a position one.

WHAT THIS IS TESTING, AND IT IS PARTLY MY OWN CLAIM. Vía 7.D was closed on a
carry whose worst fold drew down 15.77% against a declared 15% limit -- an excess
of 0.77 percentage points, with the position at ONE HUNDRED PERCENT of capital,
over a window chosen because it contained the worst crash in the asset's history.
I said the route had been closed by applying a PORTFOLIO limit to a POSITION, and
that the platform could not ask the right question. CAP-006 now exists, so the
claim can be measured rather than asserted.

AND THE MEASUREMENT MAY REFUTE IT. The gate report added hours earlier says the
drawdown limit never DECIDED anything: consistency did, and the drawdown gate was
shadowed. Scaling a position by a weight multiplies every period's return by that
weight, which moves a drawdown and preserves every SIGN -- so a weight can rescue
a drawdown gate and can never rescue a consistency one. If that holds, the
closure of 7.D stands and my framing of it was wrong.

WHAT IS REPORTED AND WHAT IS NOT. The maximum admissible weight is a CAPACITY --
how much would have fitted over these periods -- and evaluating a claim at that
weight would be fitting the weight to the data that produced it. So a weight is
DECLARED in advance here, at a round number with a stated reason, and judged; the
capacity is reported beside it as the separate quantity it is.

    python3.11 -B scripts/research/run_carry_portfolio_capacity.py
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.request import urlopen

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pipeline as p
from tramitago_quant_core.governance.evidence_host import require_evidence_host
from tramitago_quant_core.data.acquisition import normalize, _coinbase_public_request
from tramitago_quant_core.data.bitmex_perpetual_price import capture_bitmex_perpetual_price
from tramitago_quant_core.data.bitmex_funding_rate import (
    capture_bitmex_funding_rate, BITMEX_FUNDING_PAYMENTS_PER_DAY,
)
from tramitago_quant_core.strategy_contract.strategy import _strategy_classify_rows
from tramitago_quant_core.risk.portfolio import (
    portfolio_allocation, portfolio_returns, portfolio_drawdown,
    maximum_admissible_weight, allocation_summary,
)
from tramitago_quant_core.research.level_claim import (
    level_claim, evaluate_fold, constitute_level_claim_validation, level_claim_gate_report,
)

START, END = "2019-01-01T00:00:00Z", "2021-01-01T00:00:00Z"
SYMBOL, SPOT, FOLD_COUNT = "XBTUSD", "BTC-USD", 6
DRAWDOWN_LIMIT = "0.15"

# DECLARED BEFORE THE CAPACITY IS COMPUTED, and the reason is stated rather than
# fitted: a quarter is the largest round fraction that leaves the position a
# minority of capital, which is the only property anyone can defend without
# looking at what it would have earned.
DECLARED_WEIGHT = "0.25"

MINIMUM_ADVERSE_EPISODES = 1
ADVERSE_PERIOD_THRESHOLD = "-0.01"
CONSISTENCY_THRESHOLD = "0.7"
MINIMUM_FOLDS = 5
COST_COMMISSION, COST_HALF_SPREAD, COST_SLIPPAGE = "0.00045", "0.0001", "0.0001"
ARTIFACTS = REPO / "artifacts" / "research"
MAX_CANDLES_PER_REQUEST = 300


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _code_revision():
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                          text=True, check=True).stdout.strip()


def _spot_closes():
    start, end = p.epoch(START), p.epoch(END)
    closes, cursor = {}, p.epoch(START)
    while cursor < end:
        stop = min(cursor + MAX_CANDLES_PER_REQUEST * 86400, end)
        url = (f"https://api.exchange.coinbase.com/products/{SPOT}/candles"
               f"?granularity=86400&start={p.iso(cursor)}&end={p.iso(stop)}")
        with urlopen(_coinbase_public_request(url), timeout=30) as response:
            raw = response.read()
        rows, report = normalize(raw, cursor, stop, SPOT)
        if report["errors"]:
            raise SystemExit(f"{SPOT}: {report['errors'][:2]}")
        for row in rows:
            closes[row["timestamp"]] = row["close"]
        cursor = stop
    return closes


def main():
    require_evidence_host(REPO)
    revision = _code_revision()
    # THE RATES SCALE WITH THE WEIGHT, and getting this wrong is how the first run
    # of this script produced a verdict for the wrong reason. A cost rate is a
    # fraction of the POSITION's notional; the returns here are fractions of the
    # PORTFOLIO. A carry at a quarter of capital trades a quarter of the notional
    # and pays a quarter of the cost, so charging the full rate against scaled
    # returns taxes it four times over -- which moved consistency from 0.667 to
    # 0.500 and would have been read as the weight making things worse.
    weight = float(Decimal(DECLARED_WEIGHT))
    contract = p.cost_contract(
        regime=p.COST_REGIME_HOLDING,
        commission_rate=f"{float(COST_COMMISSION) * weight:.12f}",
        half_spread_rate=f"{float(COST_HALF_SPREAD) * weight:.12f}",
        slippage_rate=f"{float(COST_SLIPPAGE) * weight:.12f}",
        recurring_rate_per_period="0", legs=2,
        source=(f"ASSUMED per-leg execution cost, rates scaled by the declared weight "
                f"{DECLARED_WEIGHT} because a position at that fraction of capital trades "
                f"that fraction of the notional. At weight 1 these are the position-level "
                f"rates unchanged."))

    perp, _, _ = capture_bitmex_perpetual_price(SYMBOL, START, END, _now(), bin_size="1d")
    funding, _, _ = capture_bitmex_funding_rate(SYMBOL, START, END, _now())
    spot = _spot_closes()
    strategy = p.carry_funding_threshold_strategy(
        payments_per_period=BITMEX_FUNDING_PAYMENTS_PER_DAY)
    stamps = sorted(set(spot) & set(perp) & set(funding))
    rows = [{"timestamp": s, "close": spot[s], "perp_close": perp[s], "funding_rate": funding[s]}
            for s in stamps]
    classified = _strategy_classify_rows(strategy, rows)
    outcome = strategy["outcome"]
    series = [(classified[i]["timestamp"], outcome["compute"](classified[i], classified[i + 1]))
              for i in range(len(classified) - 1) if classified[i]["group"] is not None]
    values = [value for _, value in series]
    print(f"series       {len(values)} days, {START[:10]} .. {END[:10]} (contains March 2020)")

    alone = portfolio_drawdown(values)
    capacity = maximum_admissible_weight(values, drawdown_limit=DRAWDOWN_LIMIT)
    print(f"\n{'=' * 74}\nCAPACITY -- how much would have fitted\n{'=' * 74}")
    print(f"  the carry alone, whole period     drawdown {alone:.2%}")
    print(f"  declared limit                             {float(DRAWDOWN_LIMIT):.2%}")
    print(f"  maximum admissible weight                  {capacity:.1%} of capital")
    print("  This is a CAPACITY, not an allocation: it says how much would have fitted")
    print("  over these periods, never how much should be held.")

    allocation = portfolio_allocation(
        weights={"carry": DECLARED_WEIGHT}, cash_return_per_period="0",
        source=("DECLARED before the capacity was computed: a quarter is the largest round "
                "fraction leaving the position a minority of capital, which is the only "
                "property defensible without looking at what it would have earned."))
    summary = allocation_summary(allocation, {"carry": values})
    print(f"\n{'=' * 74}\nTHE DECLARED ALLOCATION: carry at {DECLARED_WEIGHT}, rest in cash\n"
          f"{'=' * 74}")
    print(f"  portfolio drawdown   {float(summary['portfolio_drawdown']):.2%}   "
          f"(position alone {alone:.2%})")
    print(f"  allocation           {allocation['allocation_id'][:52]}")

    claim = level_claim(
        position_description=(f"PORTFOLIO: long BTC spot / short BitMEX XBTUSD carry at "
                              f"{DECLARED_WEIGHT} of capital, remainder in cash at zero"),
        cost_contract=contract, minimum_folds_required=MINIMUM_FOLDS,
        consistency_threshold=CONSISTENCY_THRESHOLD,
        minimum_adverse_episodes=MINIMUM_ADVERSE_EPISODES,
        adverse_period_threshold=ADVERSE_PERIOD_THRESHOLD, maximum_drawdown=DRAWDOWN_LIMIT,
        confidence_level="0.95", bootstrap_resamples=2000, bootstrap_block_periods=5,
        bootstrap_seed=0,
        source="identical terms to the position-level runs; only the weight differs")

    size = len(series) // FOLD_COUNT
    chunks = [series[i * size:(i + 1) * size if i < FOLD_COUNT - 1 else len(series)]
              for i in range(FOLD_COUNT)]
    folds = []
    for index, chunk in enumerate(chunks):
        scaled = portfolio_returns(allocation, {"carry": [value for _, value in chunk]})
        folds.append(evaluate_fold(
            claim, contract, fold_index=index,
            period={"start_utc": chunk[0][0], "end_exclusive_utc": chunk[-1][0]},
            positions=[1] * len(scaled), gross_returns=scaled))

    record = constitute_level_claim_validation(
        ARTIFACTS / "level-claim-validations.json", claim=claim, folds=folds,
        validated_at=_now(), validation_code_revision=revision)
    print(f"\n{'=' * 74}\nVERDICT ON THE PORTFOLIO\n{'=' * 74}")
    print(f"  OUTCOME      {record['outcome']}"
          + (f"  ({record['outcome_reason']})" if record["outcome_reason"] else ""))
    for gate in record["gate_report"]:
        mark = "  <- DECIDED" if gate["decided_the_verdict"] else ""
        print(f"  {gate['order']}. {gate['gate']:<24} reached={str(gate['reached']):<5} "
              f"would_refuse={str(gate['would_refuse']):<5} {gate['detail']:<36}{mark}")
    print(f"  validation   {record['validation_id'][:52]}")

    drawdown_gate = next(g for g in record["gate_report"] if g["gate"] == "maximum_drawdown")
    print(f"\n{'=' * 74}")
    if not drawdown_gate["would_refuse"]:
        print("  The weight DID fix the drawdown gate -- it no longer refuses.")
    if record["outcome"] != "VALIDATED":
        print(f"  And the verdict did NOT move: {record['outcome_reason']}.")
        print("  Scaling multiplies every period by the weight, which moves a drawdown and")
        print("  preserves every SIGN. A weight can rescue a drawdown gate and can never")
        print("  rescue a consistency one. The closure of via 7.D stands, and the claim that")
        print("  it was closed by the wrong question was wrong.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
