"""Produce, for ONE validated Hypothesis, the evidence its admission gates lack.

The first admission review denied all three validated Hypotheses, and the real
finding was the second table: four of six gates could not be evaluated for ANY
Hypothesis in the project, because the quantities they ask for had never been
measured for anything. Three of those four already had machinery built on
2026-09-30 as §9.4 prerequisites -- cost model, degradation monitor, capacity
model -- and none of it had ever been pointed at a concrete Hypothesis.

This points them at the strongest candidate: CARRY_FUNDING_THRESHOLD on the
BitMEX holdout, the only Hypothesis this project has validated while carrying a
correction for its own search.

WHAT IS MEASURED HERE AND WHAT IS DECLARED. The distinction is the whole point of
the exercise, so each figure says which it is:

  MEASURED from the sealed dataset    the net Sharpe bound, the drawdown bound,
                                      the loss if the edge dies, the adverse
                                      state's frequency, turnover, volatility
  MEASURED from live books (2026-10-02) the execution cost of crossing both legs
  DECLARED by judgement                the commission, the monitor's confirmation
                                      periods, the capacity impact coefficient,
                                      and every input to R5

A DECLARED figure is not a worse measurement; it is not a measurement. Where one
decides a gate, the gate's verdict inherits that status and this says so.

THE POSITION BEING SIZED. A validated COMPARISON implies a position: hold the
favoured group. The exit rule's favoured group is "funding at or above zero", so
the admission gates are evaluated on the carry held exactly on those days and
flat on the rest -- the same construction the gated level claim used.

    python3.11 -B scripts/research/run_carry_admission_evidence.py
"""

import csv
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pipeline as p
from tramitago_quant_core.governance.evidence_host import require_evidence_host
from tramitago_quant_core.governance.admission import (
    adverse_bound, constitute_admission, SURVIVAL_P1, SURVIVAL_P2,
)
from tramitago_quant_core.risk.statistic_bounds import (
    net_sharpe_lower_bound, drawdown_upper_bound, sharpe_ratio, max_drawdown,
)

ARTIFACTS = REPO / "artifacts" / "research"
DATASET = ARTIFACTS / "datasets" / "carry_exit_bitmex_holdout"
SLUG = "carry-exit-bitmex-holdout"
BOOTSTRAP = {"confidence": "0.95", "resamples": 2000, "block_periods": 5, "seed": 0}

# DECLARED. Crossing both legs was measured at 0.00002315 of notional on live
# books on 2026-10-02 at $5,000; the commission is the component a book cannot
# read and remains an assumed per-leg taker rate.
COST_COMMISSION, COST_HALF_SPREAD, COST_SLIPPAGE = "0.00045", "0.0000059", "0.0000172"
FASE_1_TRANCHE = "1000"          # DOC-001 Fase 1: microcapital $200-$1000


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _revision():
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                          text=True, check=True).stdout.strip()


def _sealed_series():
    """The gated position's gross returns, and the losing group's, from the
    sealed dataset of the validated Hypothesis itself."""
    rows = [row for row in csv.DictReader((DATASET / "dataset.csv").open(encoding="utf-8"))
            if row["forward_carry_return_1d"] and row["funding_rate_lag_1"]]
    held, flat = [], []
    for row in rows:
        value = float(row["forward_carry_return_1d"])
        (held if float(row["funding_rate_lag_1"]) >= 0 else flat).append(value)
    positions = [1 if float(row["funding_rate_lag_1"]) >= 0 else 0 for row in rows]
    gross = [float(row["forward_carry_return_1d"]) for row in rows]
    return rows, gross, positions, held, flat


def main():
    require_evidence_host(REPO)
    revision = _revision()
    rows, gross, positions, held, flat = _sealed_series()
    contract = p.cost_contract(
        regime=p.COST_REGIME_HOLDING, commission_rate=COST_COMMISSION,
        half_spread_rate=COST_HALF_SPREAD, slippage_rate=COST_SLIPPAGE,
        recurring_rate_per_period="0", legs=2,
        source=("half-spread and slippage MEASURED on live Hyperliquid and Coinbase books "
                "2026-10-02 at $5,000; commission DECLARED, an assumed per-leg taker rate"))
    net = p.net_returns(contract, positions, gross)
    net_held = [value for value, position in zip(net, positions) if position == 1]

    print(f"dataset      {DATASET.name}, {len(rows)} rows")
    print(f"position     held {len(held)} days, flat {len(flat)}  "
          f"({len(held) / len(rows):.1%} of the window)")
    print(f"cost         {float(p.cost_per_side(contract)):.6f} per side, both legs\n")

    # --- maximando: net Sharpe, LOWER bound ------------------------------------
    sharpe_point = sharpe_ratio(net_held)
    sharpe_bound = net_sharpe_lower_bound(net_held, **BOOTSTRAP)
    print(f"net Sharpe   point {sharpe_point:+.4f}   lower 95% bound {sharpe_bound:+.4f}")

    # --- R1: drawdown, UPPER bound ---------------------------------------------
    drawdown_point = max_drawdown(net)
    drawdown_bound = drawdown_upper_bound(net, **BOOTSTRAP)
    print(f"drawdown     point {drawdown_point:.4%}   upper 95% bound {drawdown_bound:.4%}")

    # --- R3: what the edge costs per day if it dies ----------------------------
    # If the rule stops discriminating, the position is held through the days it
    # currently avoids. Their mean net return is the daily loss, MEASURED.
    net_flat = p.net_returns(contract, [1] * len(flat), flat)
    loss_if_dead = -(math.fsum(net_flat) / len(net_flat))
    adverse_frequency = len(flat) / len(rows)
    monitor = p.monitoring_contract(
        variable="funding_rate", observation_period_seconds=28800,
        degradation_threshold="0", degrades_when=p.DEGRADES_BELOW,
        confirmation_periods=3, action=p.ACTION_SUSPEND, re_entry_threshold="0.0001",
        source="BitMEX publishes the funding rate every eight hours")
    latency_days = p.detection_latency_seconds(monitor) / 86400
    admissible, reason = p.monitoring_is_admissible(
        monitor, drawdown_limit="0.15", expected_daily_loss_if_dead=f"{loss_if_dead:.8f}")
    print(f"monitor      latency {latency_days:.2f}d, loss if dead {loss_if_dead:+.6f}/day, "
          f"adverse state {adverse_frequency:.1%} of days -> {admissible}")

    # --- R4: capacity ceiling ---------------------------------------------------
    volume = math.fsum(float(row["close"]) * float(row["volume"]) for row in rows) / len(rows)
    volatility = (math.fsum((v - math.fsum(net_held) / len(net_held)) ** 2
                            for v in net_held) / (len(net_held) - 1)) ** 0.5
    transitions = sum(1 for index, position in enumerate(positions)
                      if position != (positions[index - 1] if index else 0))
    capacity = p.capacity_contract(
        average_daily_volume=f"{volume:.2f}", impact_coefficient="0.1",
        degradation_tolerance="0.20",
        source=("ADV MEASURED from the sealed dataset's own spot volume; impact coefficient "
                "0.1 DECLARED, a conventional square-root value nobody here has calibrated"))
    # The contract takes DECLARED strings, the per-period inputs take numbers:
    # the first are market facts someone asserts and seals, the second are
    # measurements. Passing a string here is a type error, not a convention.
    #
    # And the model REFUSES to size something with no positive net Sharpe, which
    # is the correct answer rather than an obstacle: a ceiling is the capital at
    # which performance degrades past a tolerance, and there is no performance
    # here to degrade. Recorded as NOT_EVALUABLE with that reason.
    capacity_evidence, ceiling_note = None, None
    try:
        ceiling = p.capacity_ceiling(
            capacity, gross_per_period=math.fsum(held) / len(held),
            base_cost_per_period=float(p.cost_per_side(contract)) * transitions / len(rows),
            volatility_per_period=volatility,
            turnover_per_period=transitions / len(rows),
            reference_capital=200.0, periods_per_year=365)
        capacity_evidence = {"ceiling": f"{ceiling['ceiling']:.2f}",
                             "current_tranche": FASE_1_TRANCHE}
        ceiling_note = f"ceiling ${ceiling['ceiling']:,.0f} ({ceiling['binding_constraint']})"
    except ValueError as error:
        # Recorded WITH the tranche and without a ceiling, rather than omitted
        # altogether: the difference between "the capacity model was asked and
        # could not answer" and "nobody asked it" is the whole distinction this
        # exercise exists to draw.
        capacity_evidence = {"current_tranche": FASE_1_TRANCHE,
                             "model_refusal": str(error)}
        ceiling_note = f"REFUSED: {error}"
    print(f"capacity     ADV ${volume:,.0f}  transitions {transitions}  {ceiling_note}")

    # --- R5: cost of the decision ----------------------------------------------
    annual_net = math.fsum(net) / len(net) * 365 * float(Decimal(FASE_1_TRANCHE))
    decision_cost = {
        "build_cost": "600", "minimum_declared_life_years": "3",
        "recurring_cost_per_year": "0", "expected_annual_net_return": f"{annual_net:.2f}",
        "surveillance_is_automatable": True,
    }
    print(f"decision     build $600 over 3y + $0/yr = $200/yr against an expected "
          f"${annual_net:+.2f}/yr at the ${FASE_1_TRANCHE} tranche")
    gross_total = math.fsum(held)
    cost_total = float(p.cost_per_side(contract)) * transitions
    print(f"\n  GROSS {gross_total:+.4f} ({gross_total / len(rows) * 365:+.2%}/yr)"
          f"   COST {cost_total:.4f} ({cost_total / gross_total:.0%} of gross,"
          f" {transitions} transitions)"
          f"\n  NET   {math.fsum(net):+.4f} ({math.fsum(net) / len(rows) * 365:+.2%}/yr)")

    evidence = {
        "net_sharpe": adverse_bound(
            f"{sharpe_bound:.6f}", is_adverse_bound=True,
            # BOTH numbers since the 2026-10-02 correction: 0.50 judges the
            # effect's SIZE on the point estimate, the bound judges whether it
            # is real at all, against zero.
            point_estimate=f"{sharpe_point:.6f}",
            source=f"moving-block bootstrap, {json.dumps(BOOTSTRAP, sort_keys=True)}, "
                   f"on {len(net_held)} net held days of {SLUG}"),
        "worst_fold_drawdown": adverse_bound(
            f"{drawdown_bound:.6f}", is_adverse_bound=True,
            source=("moving-block bootstrap, same parameters, on the WHOLE net series "
                    "rather than the worst fold. A drawdown measured over the full window "
                    "is at least as large as any contiguous fold's -- every peak-to-trough "
                    "pair inside a fold is also one in the whole -- so this DOMINATES the "
                    "quantity R1 asks for. It is reported in its place because it is the "
                    "stricter of the two, and it is said here rather than left to be "
                    "noticed later")),
        "survival": {
            "form": SURVIVAL_P2,
            "counterparty": ("levered long holders of the perpetual, who pay funding to "
                             "maintain leverage"),
            "why_they_accept_losing": ("they are buying directional exposure, not yield; the "
                                       "funding is the price of leverage and is small against "
                                       "the move they are positioned for"),
            "what_would_end_it": ("sustained negative funding (shorts paying longs), a venue "
                                  "fee or funding-formula change, or enough capital on the "
                                  "short side to flatten the basis"),
        },
        "monitor": {
            "variable": "funding_rate", "frequency_seconds": 28800,
            "degradation_threshold": "0", "action": p.ACTION_SUSPEND,
            "detection_latency_days": f"{latency_days:.6f}",
            "expected_daily_loss_if_dead": f"{loss_if_dead:.8f}",
            "is_pnl_only": False,
            "observes_adverse_state_representatively": adverse_frequency >= 0.20,
        },
        **({} if capacity_evidence is None else {"capacity": capacity_evidence}),
        "decision_cost": decision_cost,
    }

    record = constitute_admission(
        ARTIFACTS / "admissions.json", hypothesis_id=f"VALIDATION_SLUG|{SLUG}",
        validation_id=json.loads(
            (ARTIFACTS / f"statistical-validations-{SLUG}.json").read_bytes()
        )["validations"][0]["validation_id"],
        validation_outcome="VALIDATED", evidence=evidence,
        decided_at=_now(), code_revision=revision)

    print(f"\n{'=' * 78}\n{SLUG}  ->  {record['outcome']}\n{'=' * 78}")
    for gate in record["gates"]:
        mark = {"PASSED": "  ok  ", "FAILED": " FAIL ", "NOT_EVALUABLE": " none "}[gate["state"]]
        print(f" [{mark}] {gate['gate']}")
        print(f"          {gate['detail']}")
    print(f"\n  admission  {record['admission_id'][:46]}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
