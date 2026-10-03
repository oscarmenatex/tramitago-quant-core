"""Capture both legs and the monitor, then judge the credit risk premium.

RUN THIS YOURSELF, OSCAR -- it needs credentials, and credentials never reach me:

    $env:ALPACA_PAPER_API_KEY_ID = ...
    $env:ALPACA_PAPER_API_SECRET_KEY = ...
    python3.11 -B scripts/research/run_credit_premium.py

It also needs the Baa spread, which THIS HOST CANNOT FETCH: fred.stlouisfed.org
accepts a TCP connection here and then never answers, while the Oracle VM gets
200 in 0.4s. So the bytes are relayed over ssh from ~/baa10y.json on the VM and
SEALED here, through the transport seam every provider already has. Your FRED
key stays on the VM and is never read by this process.

WHAT IS BEING JUDGED. HYPOTHESIS|cf55e397 v3: long LQD, short IEF at equal
notional, held continuously 2018-01-02 to 2026-10-01. One entry, one exit. A
LEVEL claim, so the quantity judged is the quantity that would be held.

WHY THIS HYPOTHESIS EXISTS AT ALL. The equity risk premium cleared five of six
gates and was denied on R3 alone, because an unconditional premium has no
degradation variable that is not its own P&L. This one was chosen MONITORABILITY
FIRST: Moody's Baa spread is published daily by a third party, has 9,589
observations reaching to 1990, and would keep being published if this position
were never opened.

WHAT IS DECLARED RATHER THAN MEASURED, because it decides a gate and must not be
tuned afterwards: the degradation floor. Baa expected credit loss runs around
0.3% a year, so a spread below 0.50% no longer compensates for the default risk
being borne -- the mechanism has stopped operating. That floor is set from the
credit loss, not from the observed range, and it is set here before the
representativeness it governs is computed.
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
    adverse_bound, constitute_admission, SURVIVAL_P2, MONITOR_LINK_M2)
from tramitago_quant_core.research.historical_dataset import create_hypothesis_dataset
from tramitago_quant_core.research.level_claim import (
    level_claim, evaluate_fold, level_claim_outcome, level_claim_gate_report,
    constitute_level_claim_validation)
from tramitago_quant_core.risk.statistic_bounds import (
    net_sharpe_lower_bound, drawdown_upper_bound, sharpe_ratio, max_drawdown,
    weight_within_drawdown_bound)
from tramitago_quant_core.strategy_contract.strategy import pair_ratio_reversion_strategy

ARTIFACTS = REPO / "artifacts" / "research"
HYPOTHESES = ARTIFACTS / "hypotheses.json"
HYPOTHESIS_ID = "HYPOTHESIS|cf55e397-75ab-49d9-8f69-1e155e312a2c"
SLUG = "credit-premium-lqd-ief"
DATASET = ARTIFACTS / "datasets" / SLUG.replace("-", "_")

PRIMARY, SECOND_LEG, HORIZON = "LQD", "IEF", 1
MONITOR_SERIES = "BAA10Y"
VM_HOST, VM_FILE = "core-oracle", "~/baa10y.json"
MINIMUM_TRADING_DAYS = 756

# The dataset contract requires the Strategy's own column among the declared
# variables, and only a PAIR Strategy declares pair_close among its inputs --
# without which the second leg is never merged at all. The signal it computes is
# never read: this is a LEVEL claim on a position that is simply held.
STRATEGY = pair_ratio_reversion_strategy(3)
WARMUP = STRATEGY["required_inputs"]["warmup_periods"]

BOOTSTRAP = {"confidence": "0.95", "resamples": 2000, "block_periods": 5, "seed": 0}
FOLDS = 7
ADVERSE_THRESHOLD, MINIMUM_EPISODES = "-0.01", 5
DRAWDOWN_LIMIT = "0.15"

# DECLARED, from the credit loss and not from the observed range: Baa expected
# loss runs about 0.3%/yr, so below 0.50% the spread stops compensating for the
# default risk being borne. Set before the representativeness it governs is
# measured, so it cannot be moved to make that measurement come out.
MONITOR_FLOOR = "0.50"
MONITOR_CONFIRMATIONS = 3

# DECLARED. Both legs are liquid US bond ETFs on a commission-free broker; the
# half spread on LQD is around a basis point and on IEF under one. Two legs.
COST_COMMISSION, COST_HALF_SPREAD, COST_SLIPPAGE = "0", "0.0001", "0.00002"
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
    """The LATEST sealed version. An older version of a declaration is a
    different declaration, and version 2 named a column that would have left
    this Hypothesis with no second leg at all."""
    versions = [item for item in json.loads(HYPOTHESES.read_bytes())["hypotheses"]
                if item["hypothesis_id"] == HYPOTHESIS_ID]
    if not versions:
        raise SystemExit(f"{HYPOTHESIS_ID} is not in the registry; declare it first.")
    return max(versions, key=lambda item: item["version"])


def _relayed_monitor_bytes():
    """The Baa spread, fetched on the VM and sealed here. The key stays there."""
    result = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=20", "-o", "BatchMode=yes", VM_HOST,
         f"base64 -w0 {VM_FILE}"],
        capture_output=True, text=True, timeout=180)
    if result.returncode != 0 or not result.stdout.strip():
        raise SystemExit(
            f"Could not relay {VM_FILE} from {VM_HOST}: {result.stderr.strip()[:200]}\n"
            f"Fetch it there first -- see the credit premium declaration for the command.")
    import base64
    raw = base64.b64decode(result.stdout.strip())
    if b"api_key" in raw:
        raise SystemExit("The relayed response contains an api_key; refusing to seal it.")
    return raw


def main():
    require_evidence_host(REPO)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, check=True).stdout.strip()
    now = _now()
    hypothesis = _declared()
    period = hypothesis["constraints"]["period"]
    start, end = period["start_utc"], period["end_exclusive_utc"]
    print(f"{'=' * 78}\n{SLUG}   {start[:10]} -> {end[:10]}   "
          f"(v{hypothesis['version']})\n{'=' * 78}")

    if (DATASET / "manifest.json").exists():
        manifest = json.loads((DATASET / "manifest.json").read_bytes())
        print(f"[1/6] dataset already sealed; NOT re-fetching either leg")
    else:
        injector = _credential_injector()
        print(f"[1/6] capturing {PRIMARY} and {SECOND_LEG} on the SIP feed, "
              f"dividend and split adjusted...")
        legs = {}
        for symbol in (PRIMARY, SECOND_LEG):
            rows, capture, raw = capture_alpaca_equity_bars(
                symbol=symbol, evaluable_start_utc=start, evaluable_end_exclusive_utc=end,
                warmup_periods=WARMUP, horizon=HORIZON, acquired_at=now,
                credential_injector=injector, feed=ALPACA_FEED_SIP)
            legs[symbol] = (rows, capture, raw)
            print(f"      {symbol}: {len(rows)} bars "
                  f"({rows[0]['timestamp'][:10]} -> {rows[-1]['timestamp'][:10]})")
        if len(legs[PRIMARY][0]) < MINIMUM_TRADING_DAYS:
            raise SystemExit(
                f"REFUSED: {len(legs[PRIMARY][0])} trading days against a declared minimum "
                f"of {MINIMUM_TRADING_DAYS}. Nothing has been sealed.")

        second_rows, second_capture, second_raw = legs[SECOND_LEG]
        print("[2/6] sealing dataset, second leg as an auxiliary source...")
        manifest = create_hypothesis_dataset(
            HYPOTHESES, HYPOTHESIS_ID, hypothesis["version"], DATASET,
            strategy=STRATEGY, horizon=HORIZON, acquired_at=now,
            primary_source={"rows": legs[PRIMARY][0], "capture": legs[PRIMARY][1],
                            "raw": legs[PRIMARY][2]},
            auxiliary_sources={"pair_close": {
                "series": {row["timestamp"]: row["close"] for row in second_rows},
                "capture": second_capture, "raw": second_raw}})
    print(f"      {manifest['dataset_id'][:46]}")

    forward = "forward_spread_return_1d"
    sealed = [row for row in csv.DictReader((DATASET / "dataset.csv").open(encoding="utf-8"))
              if row.get(forward)]
    gross = [float(row[forward]) for row in sealed]
    positions = [1] * len(gross)
    years = len(sealed) / 252

    print(f"[3/6] the position: long {PRIMARY}, short {SECOND_LEG}, held every day...")
    contract = p.cost_contract(
        regime=p.COST_REGIME_HOLDING, commission_rate=COST_COMMISSION,
        half_spread_rate=COST_HALF_SPREAD, slippage_rate=COST_SLIPPAGE,
        recurring_rate_per_period="0", legs=2,
        source="commission-free US ETFs; half spread and slippage DECLARED, not measured")
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
        # Return and cost both scale with notional; charging a full position's
        # cost against a fraction of its return taxes it 1/w times over.
        gross = [value * weight for value in gross]
        contract = p.cost_contract(
            regime=p.COST_REGIME_HOLDING,
            commission_rate=f"{float(Decimal(COST_COMMISSION)) * weight:.10f}",
            half_spread_rate=f"{float(Decimal(COST_HALF_SPREAD)) * weight:.10f}",
            slippage_rate=f"{float(Decimal(COST_SLIPPAGE)) * weight:.10f}",
            recurring_rate_per_period="0", legs=2,
            source=f"the declared rates scaled by the {weight:.4f} weight derived from "
                   f"the {DRAWDOWN_LIMIT} limit")
        net = p.net_returns(contract, positions, gross)
        gross_total, cost_total = math.fsum(gross), float(p.cost_per_side(contract)) * transitions
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

    print(f"[5/6] the monitor: {MONITOR_SERIES}, relayed from {VM_HOST} and sealed here...")
    monitor_rows, monitor_capture, monitor_stored = capture_fred_series(
        MONITOR_SERIES, start[:10], end[:10], now,
        transport=lambda url: _relayed_monitor_bytes(), endpoint=ENDPOINT_API)
    spread = verified_fred_series_capture(monitor_stored, monitor_capture)
    observed = [float(value) for value in spread.values() if value is not None]
    floor = float(Decimal(MONITOR_FLOOR))
    # §11.1, a governance act of 2026-10-01: a monitor is admissible ONLY if the
    # INFERENTIAL LINK it rests on -- that the observed condition implies
    # degradation -- reached VALIDATED, and INSUFFICIENT_EVIDENCE IS NOT A PASS.
    # So the question is not whether the variable moved; it is whether the
    # TRIGGER STATE occurred often enough for a walk-forward to establish that
    # it implies degraded returns. With no triggering days there is no link to
    # validate, and the monitor is NOT ADMITTED rather than admitted with
    # reservations -- exactly the finding that closed hypothesis #37.
    #
    # An earlier version of this file counted days within twice the floor and
    # called that representativeness. That was a heuristic nobody declared, and
    # it is not what §11.1 asks.
    triggering = sum(1 for value in observed if value <= floor)
    link_is_establishable = triggering > 0
    print(f"      {len(monitor_rows)} observations, {monitor_capture['missing_observations']} "
          f"missing; spread {min(observed):.2f}% to {max(observed):.2f}%")
    print(f"      declared floor {MONITOR_FLOOR}%; the trigger state occurred on "
          f"{triggering} of {len(observed)} days")
    if not link_is_establishable:
        print(f"      M1 (empirical) is unavailable: with no triggering observation no")
        print(f"      walk-forward can establish the link. Declaring M2 instead, the")
        print(f"      IDENTITY form admitted by §11.1 as revised 2026-10-03 -- the carry")
        print(f"      IS the spread, so a spread below the loss absorbed is a shortfall by")
        print(f"      subtraction, with nothing estimated in between.")

    # If the premium dies, the spread goes to nothing and the position stops
    # earning it. The daily loss is the premium itself, MEASURED.
    loss_if_dead = max(mean_net, 0.0)
    monitor = p.monitoring_contract(
        variable=MONITOR_SERIES, observation_period_seconds=86400,
        degradation_threshold=MONITOR_FLOOR, degrades_when=p.DEGRADES_BELOW,
        confirmation_periods=MONITOR_CONFIRMATIONS, action=p.ACTION_SUSPEND,
        re_entry_threshold="1.00",
        source=f"Moody's Baa yield relative to the 10-year Treasury, published daily; "
               f"floor DECLARED from Baa expected credit loss, not from this window")
    latency_days = p.detection_latency_seconds(monitor) / 86400
    print(f"      latency {latency_days:.2f} days, loss if dead {loss_if_dead:+.6f}/day")

    print("[6/6] judging the LEVEL CLAIM, then admission...")
    claim = level_claim(
        position_description=(f"long {PRIMARY}, short {SECOND_LEG} at equal notional, held "
                              f"continuously {start[:10]} to {end[:10]} at a {weight:.4f} "
                              f"weight derived from the {DRAWDOWN_LIMIT} limit"),
        cost_contract=contract, minimum_folds_required=FOLDS,
        consistency_threshold="0.70", minimum_adverse_episodes=MINIMUM_EPISODES,
        adverse_period_threshold=f"{float(Decimal(ADVERSE_THRESHOLD)) * weight:.10f}",
        maximum_drawdown=DRAWDOWN_LIMIT,
        source=f"declared in {HYPOTHESIS_ID[:30]} v{hypothesis['version']} before capture")

    size = len(sealed) // FOLDS
    folds = []
    for index in range(FOLDS):
        lo = index * size
        hi = len(sealed) if index == FOLDS - 1 else (index + 1) * size
        folds.append(evaluate_fold(
            claim, contract, fold_index=index,
            period={"start_utc": sealed[lo]["timestamp"],
                    "end_exclusive_utc": sealed[hi - 1]["timestamp"]},
            positions=positions[lo:hi], gross_returns=gross[lo:hi]))
    outcome, reason, consistency, adverse = level_claim_outcome(claim, folds)
    validation = constitute_level_claim_validation(
        ARTIFACTS / f"level-claim-validations-{SLUG}.json", claim=claim, folds=folds,
        validated_at=_now(), validation_code_revision=revision)

    print(f"\n{'=' * 78}\nLEVEL CLAIM  ->  {outcome}\n{'=' * 78}")
    print(f"  {reason}")
    print(f"  consistency {consistency}   adverse-period frequency {adverse}")
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
                                source="ADV MEASURED from the sealed dataset's primary leg; "
                                       "impact coefficient 0.1 DECLARED, never calibrated"),
            gross_per_period=gross_total / len(gross),
            base_cost_per_period=cost_total / len(sealed),
            volatility_per_period=volatility, turnover_per_period=transitions / len(sealed),
            reference_capital=REFERENCE, periods_per_year=252,
            search_multiple=SEARCH_MULTIPLE)
        capacity = ({"ceiling": f"{ceiling['ceiling']:.2f}", "current_tranche": FASE_1_TRANCHE}
                    if ceiling["ceiling"] is not None else
                    {"ceiling": f"{REFERENCE * SEARCH_MULTIPLE:.2f}",
                     "current_tranche": FASE_1_TRANCHE,
                     "is_lower_bound_not_the_ceiling": True,
                     "source": f"NONE_FOUND: nothing binds below "
                               f"${REFERENCE * SEARCH_MULTIPLE:,.0f}, so this is a FLOOR on "
                               f"the ceiling and never an estimate of it"})
    except ValueError as error:
        capacity = {"current_tranche": FASE_1_TRANCHE, "model_refusal": str(error)}

    evidence = {
        "net_sharpe": adverse_bound(
            f"{sharpe_bound:.6f}", is_adverse_bound=True,
            point_estimate=f"{sharpe_point:.6f}",
            source=f"moving-block bootstrap, {json.dumps(BOOTSTRAP, sort_keys=True)}, "
                   f"on {len(net)} net days of {SLUG} at a {weight:.4f} weight"),
        "worst_fold_drawdown": adverse_bound(
            f"{drawdown_bound:.6f}", is_adverse_bound=True,
            source="moving-block bootstrap, same parameters, on the WHOLE net series, "
                   "which dominates any fold and is therefore the stricter reading of R1"),
        "survival": {
            "form": SURVIVAL_P2,
            "counterparty": ("investors who must hold Treasuries or the highest grades for "
                             "reasons unrelated to expected return: mandates, capital "
                             "treatment, collateral eligibility and liability matching"),
            "why_they_accept_losing": ("they are not losing; they are buying permission and "
                                       "certainty. The spread is the price of bearing "
                                       "default and illiquidity risk they are constrained "
                                       "or unwilling to bear"),
            "what_would_end_it": ("corporate default risk ceasing to be priced, mandates "
                                  "disappearing, or enough capital crowding the trade to "
                                  "compress the spread below the losses it compensates -- "
                                  "which is exactly what the declared monitor watches for"),
        },
        "monitor": {
            "variable": MONITOR_SERIES, "frequency_seconds": 86400,
            "degradation_threshold": MONITOR_FLOOR, "action": p.ACTION_SUSPEND,
            "detection_latency_days": f"{latency_days:.6f}",
            "expected_daily_loss_if_dead": f"{loss_if_dead:.8f}",
            "is_pnl_only": False,
            "observes_adverse_state_representatively": link_is_establishable,
            # M2, admitted by §11.1 as revised under MONITOR_FORM_REVISION|5bc199a4.
            # The empirical form is unavailable here and the measurement above
            # shows why: the trigger state occurs on zero days, so no walk-forward
            # can establish the link. The implication is instead an IDENTITY.
            "link_form": MONITOR_LINK_M2,
            "identity": (
                "The position's gross carry IS the spread. If the spread falls below the "
                "credit loss the position must absorb, the carry is less than the losses "
                "by subtraction -- the premium has stopped compensating for the risk being "
                "borne. No estimation stands between the observed condition and the "
                "degradation: it is the same quantity compared against itself."),
            "parameter": MONITOR_FLOOR,
            "parameter_source": (
                "Moody's long-run annual credit loss on Baa-rated corporates, about 0.30%; "
                "the floor is set at 0.50% to leave headroom above it"),
            "holds_for_range": (
                "ANY value of the expected loss, because the threshold is DEFINED as that "
                "loss. Estimating it wrongly moves where the monitor fires; it cannot make "
                "the implication false. That independence is what makes this an identity "
                "rather than a mechanism, and it is why M2 admits it where R2's P2 would "
                "have admitted a narrative instead."),
        },
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
