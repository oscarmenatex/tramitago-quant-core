"""Capture SVXY and the VIX term structure, then judge the variance risk premium.

RUN THIS YOURSELF, OSCAR -- it needs credentials, and credentials never reach me:

    $env:ALPACA_PAPER_API_KEY_ID = ...
    $env:ALPACA_PAPER_API_SECRET_KEY = ...
    python3.11 -B scripts/research/run_volatility_premium.py

The two VIX series are relayed over ssh from ~/VIXCLS.json and ~/VXVCLS.json on
the Oracle VM, because this host cannot reach FRED, and are SEALED here. Your
FRED key stays there and is never read by this process.

WHAT IS DIFFERENT ABOUT THIS ONE. The credit premium's monitor could only be
declared, never tested: its trigger state occurred on zero of 2184 days, so no
walk-forward could establish that the condition implies degradation, and §11.1
admitted it only through M2, the identity form. Here the trigger occurs on 173
of 2159 days across 7 of 8 years, so the EMPIRICAL form is reachable and this
runner tests it rather than asserting it.

WHAT THE M1 CLAIM RESTS ON HERE, STATED PLAINLY. §11.1 asks that the inferential
link reached VALIDATED. What this computes is the link's FOLD CONSISTENCY under
the same sign rule the level claim judge uses, in line, rather than driving it
through the full M2.x Hypothesis-Dataset-Experiment chain, which is built for
Hypotheses and the link is not one. That is weaker than a sealed
StatisticalValidation and is recorded as such in the evidence. If it fails to
clear, the runner falls back to M2 -- an inverted curve means the roll is
against the position by subtraction -- and says which form it used.
"""

import csv
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pipeline as p
from tramitago_quant_core.data.alpaca_equity_series import (
    capture_alpaca_equity_bars, ALPACA_FEED_SIP)
from tramitago_quant_core.data.fred_series import (
    capture_fred_series, verified_fred_series_capture, ENDPOINT_API)
from tramitago_quant_core.governance.evidence_host import require_evidence_host
from tramitago_quant_core.governance.admission import (
    adverse_bound, constitute_admission, SURVIVAL_P2, MONITOR_LINK_M1, MONITOR_LINK_M2,
    LINK_UNREACHABLE, LINK_REACHABLE_MET, LINK_REACHABLE_FAILED)
from tramitago_quant_core.research.historical_dataset import create_hypothesis_dataset
from tramitago_quant_core.research.level_claim import (
    level_claim, evaluate_fold, level_claim_outcome, level_claim_gate_report,
    constitute_level_claim_validation)
from tramitago_quant_core.research.pre_declaration import load_pre_declaration
from tramitago_quant_core.risk.statistic_bounds import (
    net_sharpe_lower_bound, drawdown_upper_bound, sharpe_ratio, max_drawdown,
    weight_within_drawdown_bound)
from tramitago_quant_core.strategy_contract.strategy import sma_crossover_strategy

ARTIFACTS = REPO / "artifacts" / "research"
HYPOTHESES = ARTIFACTS / "hypotheses.json"
PRE_DECLARATIONS = ARTIFACTS / "pre-declarations.json"
HYPOTHESIS_ID = "HYPOTHESIS|205003fe-7b8d-4fc0-b30c-936a404cd7c0"
SLUG = "volatility-premium-svxy"
DATASET = ARTIFACTS / "datasets" / SLUG.replace("-", "_")

INSTRUMENT, HORIZON = "SVXY", 1
VM_HOST = "core-oracle"
MONITOR_NEAR, MONITOR_FAR = "VIXCLS", "VXVCLS"
MINIMUM_TRADING_DAYS = 756

STRATEGY = sma_crossover_strategy(3)
WARMUP = STRATEGY["required_inputs"]["warmup_periods"]
BOOTSTRAP = {"confidence": "0.95", "resamples": 2000, "block_periods": 5, "seed": 0}
FOLDS = 7
ADVERSE_THRESHOLD, MINIMUM_EPISODES = "-0.03", 5
DRAWDOWN_LIMIT = "0.15"

# DECLARED in the Hypothesis: the curve inverting is the premium's compensation
# reversing. Zero is not a tuned level -- it is the sign change, the point at
# which a position that rolls down the curve begins rolling up it.
MONITOR_FLOOR = "0"
MONITOR_CONFIRMATIONS = 3
LINK_CONSISTENCY_THRESHOLD = "0.70"
# A RATIO OVER TOO FEW FOLDS IS NOT EVIDENCE, and §11.1 is explicit that
# INSUFFICIENT_EVIDENCE IS NOT A PASS. The dry run proved the point: with the
# trigger concentrated in a single fold the link came back 1 of 1 = 1.0000 and
# cleared 0.70 trivially, which is precisely the failure the clause was written
# to prevent. The probe measured 5 of 7 folds carrying 10 or more inverted days,
# so five is what the real data supports and what is required here.
LINK_MINIMUM_USABLE_FOLDS = 5

# DECLARED. SVXY is liquid and commission-free on this broker; the half spread
# on a volatility ETP is wider than on SPY and is declared, not measured.
COST_COMMISSION, COST_HALF_SPREAD, COST_SLIPPAGE = "0", "0.0003", "0.0001"
DECISION_BUILD_COST, DECISION_LIFE_YEARS = "0", "3"
FASE_1_TRANCHE = "1000"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _credential_injector():
    key_id = os.environ.get("ALPACA_PAPER_API_KEY_ID")
    secret = os.environ.get("ALPACA_PAPER_API_SECRET_KEY")
    if not key_id or not secret:
        raise SystemExit(
            "Set ALPACA_PAPER_API_KEY_ID and ALPACA_PAPER_API_SECRET_KEY as environment "
            "variables before running this script. Never put credentials in a file.")

    def injector(headers):
        return {**headers, "APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}
    return injector


def _declared():
    versions = [item for item in json.loads(HYPOTHESES.read_bytes())["hypotheses"]
                if item["hypothesis_id"] == HYPOTHESIS_ID]
    if not versions:
        raise SystemExit(f"{HYPOTHESIS_ID} is not in the registry; declare it first.")
    return max(versions, key=lambda item: item["version"])


def _relayed(series_id):
    """One FRED series, fetched on the VM and sealed here. The key stays there."""
    result = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=20", "-o", "BatchMode=yes", VM_HOST,
         f"base64 -w0 ~/{series_id}.json"], capture_output=True, text=True, timeout=180)
    if result.returncode != 0 or not result.stdout.strip():
        raise SystemExit(
            f"Could not relay ~/{series_id}.json from {VM_HOST}: "
            f"{result.stderr.strip()[:200]}\nFetch it there first.")
    import base64
    raw = base64.b64decode(result.stdout.strip())
    if b"api_key" in raw:
        raise SystemExit("The relayed response contains an api_key; refusing to seal it.")
    return raw


def _link_consistency(folds_of_slope, net, positions_in_fold):
    """Does an inverted curve imply a degraded return, fold by fold?

    §11.1's question, asked of the data rather than asserted. A fold counts when
    the mean net return on INVERTED days is below the mean on the rest -- the
    sign rule schema 3 uses, because significance inside a fifteen-month slice
    is a statement about the slice's length and not about the link.
    """
    met, usable = 0, 0
    for lo, hi in folds_of_slope:
        inverted = [net[i] for i in range(lo, hi) if positions_in_fold[i]]
        upright = [net[i] for i in range(lo, hi) if not positions_in_fold[i]]
        if len(inverted) < 10 or not upright:
            continue
        usable += 1
        if math.fsum(inverted) / len(inverted) < math.fsum(upright) / len(upright):
            met += 1
    return met, usable


def main():
    require_evidence_host(REPO)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, check=True).stdout.strip()
    now = _now()
    hypothesis = _declared()
    answers = load_pre_declaration(PRE_DECLARATIONS, HYPOTHESIS_ID)
    if answers is None:
        raise SystemExit("This Hypothesis has no pre-declaration; it must answer first.")
    period = hypothesis["constraints"]["period"]
    start, end = period["start_utc"], period["end_exclusive_utc"]
    print(f"{'=' * 78}\n{SLUG}   {start[:10]} -> {end[:10]}   (v{hypothesis['version']})")
    print(f"  declared plausible Sharpe "
          f"{answers['required_effect']['plausible_point_sharpe']} against a required "
          f"{answers['required_effect']['required_point_sharpe']}\n{'=' * 78}")

    if (DATASET / "manifest.json").exists():
        manifest = json.loads((DATASET / "manifest.json").read_bytes())
        print("[1/6] dataset already sealed; NOT re-fetching")
    else:
        print(f"[1/6] capturing {INSTRUMENT} on the SIP feed, dividend and split adjusted...")
        rows, capture, raw = capture_alpaca_equity_bars(
            symbol=INSTRUMENT, evaluable_start_utc=start, evaluable_end_exclusive_utc=end,
            warmup_periods=WARMUP, horizon=HORIZON, acquired_at=now,
            credential_injector=_credential_injector(), feed=ALPACA_FEED_SIP)
        print(f"      {len(rows)} bars ({rows[0]['timestamp'][:10]} -> "
              f"{rows[-1]['timestamp'][:10]})")
        if len(rows) < MINIMUM_TRADING_DAYS:
            raise SystemExit(f"REFUSED: {len(rows)} trading days against a declared minimum "
                             f"of {MINIMUM_TRADING_DAYS}. Nothing has been sealed.")
        print("[2/6] sealing dataset...")
        manifest = create_hypothesis_dataset(
            HYPOTHESES, HYPOTHESIS_ID, hypothesis["version"], DATASET,
            strategy=STRATEGY, horizon=HORIZON, acquired_at=now,
            primary_source={"rows": rows, "capture": capture, "raw": raw})
    print(f"      {manifest['dataset_id'][:46]}")

    sealed = [row for row in csv.DictReader((DATASET / "dataset.csv").open(encoding="utf-8"))
              if row.get("forward_return_1d")]
    gross = [float(row["forward_return_1d"]) for row in sealed]
    positions = [1] * len(gross)
    years = len(sealed) / 252

    print(f"[3/6] the position: {INSTRUMENT} held every day, bought once, sold once...")
    contract = p.cost_contract(
        regime=p.COST_REGIME_HOLDING, commission_rate=COST_COMMISSION,
        half_spread_rate=COST_HALF_SPREAD, slippage_rate=COST_SLIPPAGE,
        recurring_rate_per_period="0", legs=1,
        source="commission-free US ETP; half spread and slippage DECLARED, wider than "
               "SPY's because a volatility ETP's book is thinner, and not measured")
    net = p.net_returns(contract, positions, gross)
    transitions = 2
    gross_total = math.fsum(gross)
    cost_total = float(p.cost_per_side(contract)) * transitions
    print(f"      {len(sealed)} days ({years:.2f} yr), {transitions} transitions")
    print(f"      GROSS {gross_total:+.4f} ({gross_total / years:+.2%}/yr)"
          f"   COST {cost_total:.6f} ({cost_total / abs(gross_total):.3%} of gross)")
    print(f"      NET   {math.fsum(net):+.4f} ({math.fsum(net) / years:+.2%}/yr)")

    weight = weight_within_drawdown_bound(net, drawdown_limit=DRAWDOWN_LIMIT, **BOOTSTRAP)
    print(f"      sizing against the declared {DRAWDOWN_LIMIT} limit -> weight {weight:.1%}")
    if weight < 1.0:
        gross = [value * weight for value in gross]
        contract = p.cost_contract(
            regime=p.COST_REGIME_HOLDING,
            commission_rate=f"{float(Decimal(COST_COMMISSION)) * weight:.10f}",
            half_spread_rate=f"{float(Decimal(COST_HALF_SPREAD)) * weight:.10f}",
            slippage_rate=f"{float(Decimal(COST_SLIPPAGE)) * weight:.10f}",
            recurring_rate_per_period="0", legs=1,
            source=f"the declared rates scaled by the {weight:.4f} weight derived from "
                   f"the {DRAWDOWN_LIMIT} limit")
        net = p.net_returns(contract, positions, gross)
        gross_total = math.fsum(gross)
        cost_total = float(p.cost_per_side(contract)) * transitions
        print(f"      NET at that weight {math.fsum(net):+.4f} "
              f"({math.fsum(net) / years:+.2%}/yr)")
    mean_net = math.fsum(net) / len(net)

    print("[4/6] bounding the statistics...")
    sharpe_point = sharpe_ratio(net, periods_per_year=252)
    sharpe_bound = net_sharpe_lower_bound(net, periods_per_year=252, **BOOTSTRAP)
    drawdown_point, drawdown_bound = max_drawdown(net), drawdown_upper_bound(net, **BOOTSTRAP)
    print(f"      net Sharpe  point {sharpe_point:+.4f}   LOWER bound {sharpe_bound:+.4f}"
          f"   (gate: point >= +0.50 and bound > 0)")
    print(f"      drawdown    point {drawdown_point:.2%}      UPPER bound {drawdown_bound:.2%}"
          f"     (gate <= 15%)")
    print(f"      DECLARED plausible was "
          f"{answers['required_effect']['plausible_point_sharpe']}; measured "
          f"{sharpe_point:+.4f}")

    print(f"[5/6] the monitor: {MONITOR_FAR} minus {MONITOR_NEAR}, relayed and sealed here...")
    slope = {}
    for series_id in (MONITOR_NEAR, MONITOR_FAR):
        raw = _relayed(series_id)
        _, capture, stored = capture_fred_series(
            series_id, start[:10], end[:10], now,
            transport=lambda url, data=raw: data, endpoint=ENDPOINT_API)
        slope[series_id] = verified_fred_series_capture(stored, capture)
        print(f"      {series_id}: {len(slope[series_id])} observations, sealed")

    by_day = {}
    for day, near in slope[MONITOR_NEAR].items():
        far = slope[MONITOR_FAR].get(day)
        if near is not None and far is not None:
            by_day[day] = float(far) - float(near)
    inverted_flags = [1 if by_day.get(row["timestamp"][:10], 1.0) <= float(MONITOR_FLOOR) else 0
                      for row in sealed]
    inverted_days = sum(inverted_flags)
    print(f"      slope on {len(by_day)} days; INVERTED on {inverted_days} of {len(sealed)} "
          f"position days ({inverted_days / len(sealed):.1%})")

    size = len(sealed) // FOLDS
    bounds = [(i * size, len(sealed) if i == FOLDS - 1 else (i + 1) * size)
              for i in range(FOLDS)]
    met, usable = _link_consistency(bounds, net, inverted_flags)
    link_ratio = (met / usable) if usable else None
    enough = usable >= LINK_MINIMUM_USABLE_FOLDS
    clears = (enough and link_ratio is not None
              and Decimal(str(link_ratio)) >= Decimal(LINK_CONSISTENCY_THRESHOLD))
    print(f"      §11.1 inferential link: inverted days degrade the return in {met} of "
          f"{usable} usable folds"
          + (f" = {link_ratio:.4f}" if link_ratio is not None else "")
          + f"  (threshold {LINK_CONSISTENCY_THRESHOLD}, minimum "
            f"{LINK_MINIMUM_USABLE_FOLDS} usable)")
    if not enough:
        print(f"      only {usable} fold(s) carried enough trigger days: a ratio over that "
              f"many is not evidence, and §11.1 says INSUFFICIENT_EVIDENCE IS NOT A PASS")
    form = MONITOR_LINK_M1 if clears else MONITOR_LINK_M2
    print(f"      -> declaring {form}"
          + ("" if clears else
             ", the identity: an inverted curve rolls against the position by subtraction"))
    if not clears and enough:
        print("      and M2 CANNOT answer here: the empirical form was reachable "
              "and failed, so R3 FAILS.")
        print("      M2_AVAILABILITY|04a35abd was sealed after this very run "
              "certified a monitor the evidence had just refuted.")

    loss_if_dead = max(mean_net, 0.0)
    monitor_contract = p.monitoring_contract(
        variable=f"{MONITOR_FAR}-{MONITOR_NEAR}", observation_period_seconds=86400,
        degradation_threshold=MONITOR_FLOOR, degrades_when=p.DEGRADES_BELOW,
        confirmation_periods=MONITOR_CONFIRMATIONS, action=p.ACTION_SUSPEND,
        re_entry_threshold="1.0",
        source="CBOE VIX and VIX3M, published daily and distributed through FRED")
    latency_days = p.detection_latency_seconds(monitor_contract) / 86400
    print(f"      latency {latency_days:.2f} days, loss if dead {loss_if_dead:+.6f}/day")

    print("[6/6] judging the LEVEL CLAIM, then admission...")
    claim = level_claim(
        position_description=(f"{INSTRUMENT} held continuously {start[:10]} to {end[:10]} "
                              f"at a {weight:.4f} weight derived from the "
                              f"{DRAWDOWN_LIMIT} limit"),
        cost_contract=contract, minimum_folds_required=FOLDS,
        consistency_threshold="0.70", minimum_adverse_episodes=MINIMUM_EPISODES,
        adverse_period_threshold=f"{float(Decimal(ADVERSE_THRESHOLD)) * weight:.10f}",
        maximum_drawdown=DRAWDOWN_LIMIT,
        source=f"declared in {HYPOTHESIS_ID[:30]} with seven answers sealed beside it")

    folds = [evaluate_fold(claim, contract, fold_index=index,
                           period={"start_utc": sealed[lo]["timestamp"],
                                   "end_exclusive_utc": sealed[hi - 1]["timestamp"]},
                           positions=positions[lo:hi], gross_returns=gross[lo:hi])
             for index, (lo, hi) in enumerate(bounds)]
    outcome, reason, consistency, adverse = level_claim_outcome(claim, folds)
    validation = constitute_level_claim_validation(
        ARTIFACTS / f"level-claim-validations-{SLUG}.json", claim=claim, folds=folds,
        validated_at=_now(), validation_code_revision=revision)

    print(f"\n{'=' * 78}\nLEVEL CLAIM  ->  {outcome}\n{'=' * 78}")
    print(f"  {reason}\n  consistency {consistency}   adverse-period frequency {adverse}")
    print(f"  validation  {validation['validation_id'][:46]}")
    for gate in level_claim_gate_report(claim, folds):
        flag = ("DECIDED" if gate.get("decided_the_verdict")
                else "refuses" if gate.get("would_refuse") else "ok")
        print(f"    [{flag:>7}] {gate['gate']}")

    if outcome != "VALIDATED":
        print(f"\n  Admission not reached: it is open only to a VALIDATED claim, and this")
        print(f"  one is {outcome}. The figures above ARE the evidence; no admission")
        print(f"  record has been sealed, and none should be.")
        print("=" * 78)
        return 0

    volume = math.fsum(float(r["close"]) * float(r["volume"]) for r in sealed) / len(sealed)
    volatility = (math.fsum((v - mean_net) ** 2 for v in net) / (len(net) - 1)) ** 0.5
    REFERENCE, SEARCH_MULTIPLE = 200.0, 10_000
    try:
        ceiling = p.capacity_ceiling(
            p.capacity_contract(average_daily_volume=f"{volume:.2f}", impact_coefficient="0.1",
                                degradation_tolerance="0.20",
                                source="ADV MEASURED from the sealed dataset; impact "
                                       "coefficient 0.1 DECLARED, never calibrated"),
            gross_per_period=gross_total / len(gross),
            base_cost_per_period=cost_total / len(sealed), volatility_per_period=volatility,
            turnover_per_period=transitions / len(sealed), reference_capital=REFERENCE,
            periods_per_year=252, search_multiple=SEARCH_MULTIPLE)
        capacity = ({"ceiling": f"{ceiling['ceiling']:.2f}", "current_tranche": FASE_1_TRANCHE}
                    if ceiling["ceiling"] is not None else
                    {"ceiling": f"{REFERENCE * SEARCH_MULTIPLE:.2f}",
                     "current_tranche": FASE_1_TRANCHE,
                     "is_lower_bound_not_the_ceiling": True,
                     "source": f"NONE_FOUND: nothing binds below "
                               f"${REFERENCE * SEARCH_MULTIPLE:,.0f}, a FLOOR on the "
                               f"ceiling and never an estimate of it"})
    except ValueError as error:
        capacity = {"current_tranche": FASE_1_TRANCHE, "model_refusal": str(error)}

    monitor_evidence = {
        "variable": f"{MONITOR_FAR}-{MONITOR_NEAR}", "frequency_seconds": 86400,
        "degradation_threshold": MONITOR_FLOOR, "action": p.ACTION_SUSPEND,
        "detection_latency_days": f"{latency_days:.6f}",
        "expected_daily_loss_if_dead": f"{loss_if_dead:.8f}",
        "is_pnl_only": False,
        "observes_adverse_state_representatively": clears,
        "link_form": form,
        # What the empirical form RETURNED, which since M2_AVAILABILITY|04a35abd
        # decides whether the identity form may be used at all. M2 answers for
        # evidence that cannot be gathered, never for evidence gathered that
        # points the other way.
        "link_empirical_outcome": (
            LINK_UNREACHABLE if not enough
            else LINK_REACHABLE_MET if clears else LINK_REACHABLE_FAILED),
    }
    if form == MONITOR_LINK_M1:
        monitor_evidence["link_consistency"] = {
            "met": met, "usable_folds": usable, "ratio": f"{link_ratio:.6f}",
            "threshold": LINK_CONSISTENCY_THRESHOLD,
            "minimum_usable_folds": LINK_MINIMUM_USABLE_FOLDS,
            "what_this_is_not": (
                "a sealed StatisticalValidation. §11.1 asks that the link reached "
                "VALIDATED; this is its fold consistency computed in line under the same "
                "sign rule, because the M2.x chain is built for Hypotheses and a monitor's "
                "link is not one. Weaker than a sealed validation, and recorded as such."),
        }
    else:
        monitor_evidence.update({
            "identity": ("The position's return is the roll down the VIX futures curve. "
                         "When the curve inverts the roll is UP it, so the compensation "
                         "reverses by subtraction rather than by estimation."),
            "parameter": MONITOR_FLOOR,
            "parameter_source": "the sign change itself, not a level anyone chose",
            "holds_for_range": ("any curve shape, because the threshold is the sign of the "
                                "slope and the implication is the sign of the roll"),
        })

    evidence = {
        "net_sharpe": adverse_bound(
            f"{sharpe_bound:.6f}", is_adverse_bound=True,
            point_estimate=f"{sharpe_point:.6f}",
            source=f"moving-block bootstrap, {json.dumps(BOOTSTRAP, sort_keys=True)}, on "
                   f"{len(net)} net days of {SLUG} at a {weight:.4f} weight"),
        "worst_fold_drawdown": adverse_bound(
            f"{drawdown_bound:.6f}", is_adverse_bound=True,
            source="moving-block bootstrap, same parameters, on the WHOLE net series, "
                   "which dominates any fold and is the stricter reading of R1"),
        "survival": {
            "form": SURVIVAL_P2,
            "counterparty": ("buyers of VIX futures and index options: portfolio managers "
                             "and dealers purchasing protection against equity drawdowns"),
            "why_they_accept_losing": ("they are buying insurance against losses they are "
                                       "mandated or unwilling to bear, and insurance not "
                                       "priced above its expected cost would be written by "
                                       "nobody"),
            "what_would_end_it": ("equity drawdown risk ceasing to be disliked, or enough "
                                  "capital selling volatility to compress the curve below "
                                  "the losses it compensates -- which is what the declared "
                                  "monitor watches for"),
        },
        "monitor": monitor_evidence,
        "capacity": capacity,
        "decision_cost": {
            "build_cost": DECISION_BUILD_COST,
            "minimum_declared_life_years": DECISION_LIFE_YEARS,
            "recurring_cost_per_year": "0",
            "expected_annual_net_return": f"{mean_net * 252 * float(FASE_1_TRANCHE):.2f}",
            "surveillance_is_automatable": True,
        },
    }

    record = constitute_admission(
        ARTIFACTS / "admissions.json", hypothesis_id=HYPOTHESIS_ID,
        validation_id=validation["validation_id"], validation_outcome="VALIDATED",
        evidence=evidence, decided_at=_now(), code_revision=revision)

    print(f"\n{'=' * 78}\n{SLUG}  ->  {record['outcome']}\n{'=' * 78}")
    for gate in record["gates"]:
        mark = {"PASSED": "  ok  ", "FAILED": " FAIL ", "NOT_EVALUABLE": " none "}[gate["state"]]
        print(f" [{mark}] {gate['gate']}\n          {gate['detail']}")
    print(f"\n  admission  {record['admission_id'][:46]}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
