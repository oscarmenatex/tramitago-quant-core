"""Measure what option B would touch, from the sealed evidence. READ ONLY: it seals nothing.

    python3.11 -B scripts/research/measure_beta_alternatives.py

Two questions, both about hypotheses that are ALREADY measured and sealed:

  1. HOW MANY IDEAS ARE SPY, XYLD AND QYLD? Their returns are regressed on their own underlying.
     If a covered-call fund is the index with its upside sold, it adds diluted beta and nothing
     else, and three admissions would be one.

  2. WHAT DOES A DIFFERENT CONTROL DO TO THEM? The same sealed datasets are re-judged by the engine
     with a tighter drawdown limit. The Sharpe is scale invariant, so only the weight, and what a
     tranche earns, move.

Nothing here changes a gate or a record. It exists so that the cost of option B is a measurement
before it is an argument, which is what section 11.0 asks of any change to how a gate reads.
"""

import csv
import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tramitago_quant_core.research.mechanism_family import member_spec
from tramitago_quant_core.research.premium_runner import run_spec

ARTIFACTS = REPO / "artifacts" / "research"
SPECS = REPO / "scripts" / "research" / "specs"
FAMILY = REPO / "scripts" / "research" / "families" / "covered-call-equity.json"
TRADING_DAYS = 252
PATHS = {"hypotheses": ARTIFACTS / "hypotheses.json",
         "pre_declarations": ARTIFACTS / "pre-declarations.json",
         "datasets": ARTIFACTS / "datasets", "checks": ARTIFACTS / "checks",
         "level_claims": ARTIFACTS, "admissions": ARTIFACTS / "admissions.json"}
NOW, CODE = "2026-10-04T12:00:00.000000Z", "a" * 40


class NoNetwork:
    """Everything needed is sealed on disk; reaching out is a failure."""

    def capture_bars(self, **kwargs):
        raise AssertionError(f"a capture was requested for {kwargs.get('symbol')}")

    def relay_fred(self, series_id, start, end):
        raise AssertionError(f"a FRED fetch was requested for {series_id}")


def _dataset(name):
    out = {}
    with (ARTIFACTS / "datasets" / name / "dataset.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("forward_return_1d"):
                out[row["timestamp"][:10]] = float(row["forward_return_1d"])
    return out


def _underlying(name):
    closes = json.loads((ARTIFACTS / "checks" / name / "underlying_bars.closes.json").read_bytes())["closes"]
    days = sorted(closes)
    return {days[i]: closes[days[i + 1]] / closes[days[i]] - 1 for i in range(len(days) - 1)}


def _stats(values):
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return mean, math.sqrt(var)


def _sharpe(values):
    mean, sd = _stats(values)
    return mean / sd * math.sqrt(TRADING_DAYS)


def decomposition(label, fund, underlying, underlying_label):
    """Regress the fund on its underlying over the dates they share."""
    days = sorted(set(fund) & set(underlying))
    y, x = [fund[d] for d in days], [underlying[d] for d in days]
    my, sy = _stats(y)
    mx, sx = _stats(x)
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y)) / (len(x) - 1)
    beta = cov / sx ** 2
    corr = cov / (sx * sy)
    alpha = my - beta * mx
    residual = [b - alpha - beta * a for a, b in zip(x, y)]
    _, resid_sd = _stats(residual)
    # An exact relation leaves no residual and the statistic is undefined, not infinite.
    t_alpha = alpha / (resid_sd / math.sqrt(len(days))) if resid_sd > 0 else 0.0
    print(f"  {label:6s} on {underlying_label:4s}  {len(days)} days   correlation {corr:5.3f}   beta {beta:5.3f}   "
          f"R2 {corr ** 2:5.3f}   alpha {alpha * TRADING_DAYS:+6.2%}/yr (t {t_alpha:+5.2f})   "
          f"Sharpe fund {_sharpe(y):5.3f} vs {underlying_label} {_sharpe(x):5.3f}")
    return days


def part_one():
    spy, xyld, qyld = _dataset("equity_risk_premium_spy"), _dataset("variance_premium_xyld"), _dataset("variance_premium_qyld")
    spy_u, qqq_u = _underlying("variance_premium_xyld"), _underlying("variance_premium_qyld")
    print("=" * 100)
    print("1. HOW MANY IDEAS? each fund regressed on its own underlying (adjusted, gross, unsized, iid t)")
    print("=" * 100)
    decomposition("XYLD", xyld, spy_u, "SPY")
    decomposition("QYLD", qyld, qqq_u, "QQQ")
    days = sorted(set(xyld) & set(qyld))
    a, b = [xyld[d] for d in days], [qyld[d] for d in days]
    ma, sa = _stats(a)
    mb, sb = _stats(b)
    corr = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (len(a) - 1) / (sa * sb)
    print(f"  XYLD with QYLD: correlation {corr:.3f} over {len(days)} days")
    common = sorted(set(spy) & set(xyld) & set(qyld))
    s, x, q = [spy[d] for d in common], [xyld[d] for d in common], [qyld[d] for d in common]
    print(f"  Sharpe on the {len(common)} days all three share ({common[0]} to {common[-1]}): "
          f"SPY {_sharpe(s):.3f}   XYLD {_sharpe(x):.3f}   QYLD {_sharpe(q):.3f}")
    print("\n  worst calendar year, gross and unsized:")
    for label, series in (("SPY", spy), ("XYLD", xyld), ("QYLD", qyld)):
        years = {}
        for day, value in series.items():
            years.setdefault(day[:4], []).append(value)
        worst = min(years, key=lambda y: math.prod(1 + v for v in years[y]))
        total = math.prod(1 + v for v in years[worst]) - 1
        print(f"    {label:5s} {worst}: {total:+.1%}")


def _spec(label):
    if label == "SPY":
        return json.loads((SPECS / "spy.json").read_text(encoding="utf-8"))
    family = json.loads(FAMILY.read_text(encoding="utf-8"))
    member = next(m for m in family["members"] if m["symbol"] == label)
    return member_spec(family, member)


def part_two():
    print("\n" + "=" * 100)
    print("2. THE SAME SEALED DATASETS RE-JUDGED UNDER A TIGHTER DRAWDOWN LIMIT (Sharpe does not move)")
    print("=" * 100)
    print(f"  {'':5s} {'limit':>6s} {'weight':>7s} {'Sharpe':>7s} {'bound':>7s} {'DD bound':>9s} "
          f"{'$/yr on 1000':>13s} {'$/yr on 200':>12s}")
    for label in ("SPY", "XYLD", "QYLD"):
        for limit in ("0.15", "0.10", "0.075"):
            spec = _spec(label)
            spec["sizing"]["drawdown_limit"] = limit
            result = run_spec(spec, io=NoNetwork(), paths=PATHS, now=NOW, code_revision=CODE, seal=False)
            annual = result["mean_net"] * TRADING_DAYS
            print(f"  {label:5s} {limit:>6s} {result['weight']:7.3f} {result['sharpe_point']:7.3f} "
                  f"{result['sharpe_bound']:7.3f} {result['drawdown_bound']:9.4f} "
                  f"{annual * 1000:13.2f} {annual * 200:12.2f}")


def main():
    part_one()
    part_two()


if __name__ == "__main__":
    main()
