"""The carry, judged again over a window that CONTAINS the episodes.

The first level-claim verdict on the carry was INSUFFICIENT_EVIDENCE, and the
reason was never the premium: across 2024-2025 no single day lost more than
0.198%, so the window held no episode of the kind that ends a carry and the
measurement was blind to exactly the thing it needed to see.

This judges 2019-2021 instead, on BitMEX XBTUSD, because that window contains
the dates the earlier 1-minute basis work already identified as adverse episodes
(2019-04-02, 2019-10-25, 2020-03-19) and the March 2020 crash with them.

THE WINDOW IS ADVERSELY SELECTED ON PURPOSE, and the verdict has to be read that
way. It was chosen BECAUSE it contains crises. That makes it the right window to
ask "does this survive its tail" and the wrong one to ask "what does this earn" --
the expected return it reports is not an unbiased estimate of anything.

Everything is captured through the sealed contract and re-verified from stored
bytes before a number is computed. Funding at THREE payments a day: BitMEX
settles every eight hours, and the parameter is explicit because an implicit
per-venue convention is precisely how the Hyperliquid funding leg came to be
understated by a factor of 24.

    python3.11 -B scripts/research/run_carry_level_claim_2019_2021.py
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
from tramitago_quant_core.data.acquisition import normalize, _coinbase_public_request
from tramitago_quant_core.data.bitmex_perpetual_price import (
    capture_bitmex_perpetual_price, verified_bitmex_perpetual_price_capture,
)
from tramitago_quant_core.data.bitmex_funding_rate import (
    capture_bitmex_funding_rate, verified_bitmex_funding_rate_capture,
    BITMEX_FUNDING_PAYMENTS_PER_DAY,
)
from tramitago_quant_core.strategy_contract.outcome import carry_return_outcome
from tramitago_quant_core.research.level_claim import (
    level_claim, evaluate_fold, constitute_level_claim_validation,
)

SYMBOL = "XBTUSD"
SPOT = "BTC-USD"
START = "2019-01-01T00:00:00Z"
END = "2021-01-01T00:00:00Z"
FOLD_COUNT = 6

# Same declared terms as the 2024-2025 run. Not re-tuned for this window: a
# threshold chosen per window is a threshold chosen after seeing the answer.
MINIMUM_ADVERSE_PERIOD_FREQUENCY = "0.10"
ADVERSE_PERIOD_THRESHOLD = "-0.01"
MAXIMUM_DRAWDOWN = "0.15"
CONSISTENCY_THRESHOLD = "0.7"
MINIMUM_FOLDS = 5
COST_COMMISSION = "0.00045"
COST_HALF_SPREAD = "0.0001"
COST_SLIPPAGE = "0.0001"
COST_SOURCE = ("ASSUMED per-leg taker fee and execution cost for a spot/perpetual carry; "
               "NOT measured fills. Replace with observed fills before any capital decision.")

ARTIFACTS = REPO / "artifacts" / "research" / "carry-2019-2021"
MAX_CANDLES_PER_REQUEST = 300


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _code_revision():
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                          text=True, check=True).stdout.strip()


def _spot_closes():
    start, end = p.epoch(START), p.epoch(END)
    closes, digests = {}, []
    cursor = start
    while cursor < end:
        stop = min(cursor + MAX_CANDLES_PER_REQUEST * 86400, end)
        url = (f"https://api.exchange.coinbase.com/products/{SPOT}/candles"
               f"?granularity=86400&start={p.iso(cursor)}&end={p.iso(stop)}")
        with urlopen(_coinbase_public_request(url), timeout=30) as response:
            raw = response.read()
        digests.append(p.digest(raw))
        rows, report = normalize(raw, cursor, stop, SPOT)
        if report["errors"]:
            raise SystemExit(f"{SPOT} {p.iso(cursor)}: {report['errors'][:2]}")
        for row in rows:
            closes[row["timestamp"]] = row["close"]
        cursor = stop
    return closes, digests


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    perp, perp_capture, perp_raw = capture_bitmex_perpetual_price(
        SYMBOL, START, END, _now(), bin_size="1d")
    if verified_bitmex_perpetual_price_capture(perp_raw, perp_capture) != perp:
        raise SystemExit("Perpetual capture does not re-verify")
    print(f"perpetual    {len(perp)} days, capture re-verified from stored bytes")

    funding, funding_capture, funding_raw = capture_bitmex_funding_rate(
        SYMBOL, START, END, _now())
    if verified_bitmex_funding_rate_capture(funding_raw, funding_capture) != funding:
        raise SystemExit("Funding capture does not re-verify")
    print(f"funding      {len(funding)} days, capture re-verified from stored bytes")

    spot, spot_digests = _spot_closes()
    print(f"spot         {len(spot)} days")

    (ARTIFACTS / "perpetual-capture.json").write_bytes(p.encoded(perp_capture))
    (ARTIFACTS / "perpetual-raw.json").write_bytes(perp_raw)
    (ARTIFACTS / "funding-capture.json").write_bytes(p.encoded(funding_capture))
    (ARTIFACTS / "funding-raw.json").write_bytes(funding_raw)

    stamps = sorted(set(spot) & set(perp) & set(funding))
    rows = [{"timestamp": stamp, "close": spot[stamp],
             "perp_close": perp[stamp], "funding_rate": funding[stamp]} for stamp in stamps]
    print(f"aligned      {len(rows)} days all three legs printed")

    outcome = carry_return_outcome(payments_per_period=BITMEX_FUNDING_PAYMENTS_PER_DAY)
    series = [(rows[index]["timestamp"], outcome["compute"](rows[index], rows[index + 1]))
              for index in range(len(rows) - 1)]
    values = [value for _, value in series]
    print(f"gross carry  {sum(values) / len(values) * 365:+.2%} annualised "
          f"at {BITMEX_FUNDING_PAYMENTS_PER_DAY} payments a day")
    worst = sorted(series, key=lambda item: item[1])[:5]
    print("worst days   " + "  ".join(f"{day[:10]}:{value:+.4f}" for day, value in worst))
    print(f"days below {ADVERSE_PERIOD_THRESHOLD}: "
          f"{sum(1 for value in values if value < float(ADVERSE_PERIOD_THRESHOLD))} of {len(values)}")

    contract = p.cost_contract(
        regime=p.COST_REGIME_HOLDING, commission_rate=COST_COMMISSION,
        half_spread_rate=COST_HALF_SPREAD, slippage_rate=COST_SLIPPAGE,
        recurring_rate_per_period="0", legs=2, source=COST_SOURCE)
    claim = level_claim(
        position_description=("long BTC spot, short BitMEX XBTUSD perpetual, equal notional, "
                              "held continuously; funding at 3 payments a day"),
        cost_contract=contract, minimum_folds_required=MINIMUM_FOLDS,
        consistency_threshold=CONSISTENCY_THRESHOLD,
        minimum_adverse_period_frequency=MINIMUM_ADVERSE_PERIOD_FREQUENCY,
        adverse_period_threshold=ADVERSE_PERIOD_THRESHOLD,
        maximum_drawdown=MAXIMUM_DRAWDOWN, confidence_level="0.95",
        bootstrap_resamples=2000, bootstrap_block_periods=5, bootstrap_seed=0,
        source="DOC-011 §9.4 thresholds; same terms as the 2024-2025 run, not re-tuned")
    print(f"claim        {claim['claim_id']}")

    size = len(series) // FOLD_COUNT
    folds = []
    for index in range(FOLD_COUNT):
        chunk = series[index * size:(index + 1) * size if index < FOLD_COUNT - 1 else len(series)]
        folds.append(evaluate_fold(
            claim, contract, fold_index=index,
            period={"start_utc": chunk[0][0], "end_exclusive_utc": chunk[-1][0]},
            positions=[1] * len(chunk), gross_returns=[value for _, value in chunk]))

    print("\n  fold  period                     result    mean/day    adverse bound  bad  dd")
    for fold in folds:
        print(f"  {fold['fold_index']:>4}  {fold['period']['start_utc'][:10]}..."
              f"{fold['period']['end_exclusive_utc'][:10]}  {fold['result']:<9} "
              f"{float(fold['mean_net_return']):+.6f}  "
              f"{float(fold['adverse_mean_bound']):+.8f}   "
              f"{float(fold['adverse_period_frequency']):.1%}  "
              f"{float(fold['max_drawdown']):.2%}")

    record = constitute_level_claim_validation(
        REPO / "artifacts" / "research" / "level-claim-validations.json",
        claim=claim, folds=folds, validated_at=_now(),
        validation_code_revision=_code_revision())
    print(f"\nvalidation   {record['validation_id']}")
    print(f"OUTCOME      {record['outcome']}"
          + (f"  ({record['outcome_reason']})" if record["outcome_reason"] else ""))
    print(f"consistency  {record['consistency_ratio']}")
    print(f"adverse      {record['adverse_period_frequency']} of held periods lost more "
          f"than {ADVERSE_PERIOD_THRESHOLD}")
    print(f"spot sha256  {json.dumps(spot_digests)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
