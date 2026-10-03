"""Capture 8.75 years of SPY and judge the equity risk premium against the gates.

RUN THIS YOURSELF, OSCAR -- it needs credentials, and credentials never reach me:

    ALPACA_PAPER_API_KEY_ID=...  ALPACA_PAPER_API_SECRET_KEY=...  \\
        python3.11 -B scripts/research/run_equity_risk_premium.py

The Hypothesis was sealed BEFORE this file could read anything
(HYPOTHESIS|426cd26a, by declare_equity_risk_premium.py). This runner captures
and judges it; it cannot re-declare it, and it refuses to proceed if the window
comes back shorter than the three years the declaration requires.

WHAT IS DELIBERATELY NOT FABRICATED HERE. R3 asks for a degradation monitor
whose variable is NOT the position's own profit and loss. For a signal there is
always one -- the carry watches the funding rate. For an UNCONDITIONAL risk
premium there is nothing to watch: the only observable that says the equity risk
premium has stopped paying is the equity risk premium not paying, which is PnL,
which the monitor contract refuses by design. So no monitor is declared and R3
will come back NOT_EVALUABLE. That is the honest answer and it is informative --
it says a buy-and-hold premium is structurally HARDER to supervise than a
signal, not easier. Inventing a monitor to make the gate green would be the
single worst thing this file could do.

AND ADMISSION IS NOT REACHED UNLESS THE LEVEL CLAIM VALIDATES. The lifecycle is
Validation -> VALIDADA -> ADMITIDA, so this runner judges the level claim first
with its own sealed judge and stops there if the verdict is anything else.
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
from tramitago_quant_core.strategy_contract.strategy import sma_crossover_strategy
from tramitago_quant_core.data.alpaca_equity_series import (
    capture_alpaca_equity_bars, ALPACA_FEED_SIP)
from tramitago_quant_core.governance.evidence_host import require_evidence_host
from tramitago_quant_core.governance.admission import (
    adverse_bound, constitute_admission, SURVIVAL_P2)
from tramitago_quant_core.research.historical_dataset import create_hypothesis_dataset
from tramitago_quant_core.research.level_claim import (
    level_claim, evaluate_fold, level_claim_outcome, level_claim_gate_report,
    constitute_level_claim_validation)
from tramitago_quant_core.risk.statistic_bounds import (
    net_sharpe_lower_bound, drawdown_upper_bound, sharpe_ratio, max_drawdown)

ARTIFACTS = REPO / "artifacts" / "research"
HYPOTHESES = ARTIFACTS / "hypotheses.json"
HYPOTHESIS_ID = "HYPOTHESIS|426cd26a-10ab-4c5e-ba5d-9e88510a4613"
SLUG = "equity-risk-premium-spy"
DATASET = ARTIFACTS / "datasets" / SLUG.replace("-", "_")

SYMBOL, HORIZON = "SPY", 1
# The dataset contract imposes the default Strategy on every Hypothesis (see the
# declaration's note). It is never used by the claim, but its warmup is real: ask
# for too few bars and the first rows have no indicator and are dropped, which
# would silently shorten the very window this Hypothesis is about.
STRATEGY = sma_crossover_strategy(3)
WARMUP = STRATEGY["required_inputs"]["warmup_periods"]
MINIMUM_TRADING_DAYS = 756          # three years; the declaration's own floor
BOOTSTRAP = {"confidence": "0.95", "resamples": 2000, "block_periods": 5, "seed": 0}

# DECLARED BEFORE THE DATA IS READ. Seven folds of about fifteen months each:
# comfortably above the five a batch of one requires, and long enough that a
# fold can contain a whole bear market rather than slice one in half.
FOLDS = 7

# The adverse state of the equity risk premium is a day it is visibly NOT being
# paid. 2% down on SPY is a stress day, not noise -- and the magnitude matters,
# because counting any losing day as adverse is exactly how the carry's tail
# gate once passed on a technicality. At least five distinct episodes; the
# window was fixed to contain 2018Q4, 2020 and 2022.
ADVERSE_THRESHOLD, MINIMUM_EPISODES = "-0.02", 5

# DECLARED. Alpaca charges no US equity commission; the half spread on SPY is
# around half a basis point. Charged on 2 transitions across 8.75 years, so it
# is a rounding error BY CONSTRUCTION -- which is the whole point of this
# Hypothesis, and exactly why it is still charged rather than waved away.
COST_COMMISSION, COST_HALF_SPREAD, COST_SLIPPAGE = "0", "0.00005", "0.00001"
FASE_1_TRANCHE = "1000"

# DECLARED BY OSCAR, 2026-10-02, as Director: the platform is already built, so
# the marginal cost of operating this position is zero. R5 was previously
# evaluated against $600 over three years, a figure this runner invented and
# nobody approved -- and it was deciding the gate.
DECISION_BUILD_COST, DECISION_LIFE_YEARS = "0", "3"

# THE DRAWDOWN LIMIT, against which SIZE IS RESOLVED. DOC-011: "El LIMITE DE
# DRAWDOWN no se toca: es aquello contra lo que se resuelve el tamano", and
# §10.4 asks whether the worst case fits inside 15% AT THE OPERATED SIZE. So the
# weight is DERIVED from the limit, never chosen -- and the declaration's own
# caveat pre-authorised this re-judgement for the case where the drawdown gate
# is the only one refusing, which is where it landed.
#
# Resolved against the 95% UPPER BOUND, not the realised drawdown, because the
# bound is what the gate reads. At SPY's CAP-006 weight of 40.9% the realised
# drawdown is exactly 15.00% and the bound is 21.31%: sized, reading as
# compliant, still refused.
DRAWDOWN_LIMIT = "0.15"


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
    """The LATEST sealed version. Taking the first match would silently capture
    against version 1, whose variable list the dataset contract refuses -- and
    an older version of a declaration is a different declaration."""
    versions = [item for item in json.loads(HYPOTHESES.read_bytes())["hypotheses"]
                if item["hypothesis_id"] == HYPOTHESIS_ID]
    if not versions:
        raise SystemExit(f"{HYPOTHESIS_ID} is not in the registry; declare it first.")
    return max(versions, key=lambda item: item["version"])


def main():
    require_evidence_host(REPO)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, check=True).stdout.strip()
    now = _now()
    hypothesis = _declared()
    period = hypothesis["constraints"]["period"]
    start, end = period["start_utc"], period["end_exclusive_utc"]

    print(f"{'=' * 78}\n{SLUG}   {start[:10]} -> {end[:10]}\n{'=' * 78}")
    # RESUMABLE, and not as a convenience. Once the dataset is sealed it IS the
    # evidence: re-fetching could return different bars for the same window --
    # a vendor revision, a late correction -- and quietly judge something other
    # than what was sealed. If the manifest is here, the capture is done.
    if (DATASET / "manifest.json").exists():
        manifest = json.loads((DATASET / "manifest.json").read_bytes())
        print(f"[1/5] dataset already sealed; NOT re-fetching")
        print(f"[2/5] {manifest['dataset_id'][:46]}")
    else:
        print(f"[1/5] capturing {SYMBOL} daily bars on the {ALPACA_FEED_SIP.upper()} "
              f"feed, dividend and split adjusted...")
        rows, capture, raw = capture_alpaca_equity_bars(
            symbol=SYMBOL, evaluable_start_utc=start, evaluable_end_exclusive_utc=end,
            warmup_periods=WARMUP, horizon=HORIZON, acquired_at=now,
            credential_injector=_credential_injector(), feed=ALPACA_FEED_SIP)
        print(f"      {len(rows)} bars ({rows[0]['timestamp'][:10]} -> "
              f"{rows[-1]['timestamp'][:10]})")

        # The declaration rests on a window long enough for a LOWER bound to mean
        # something. A short capture is not a smaller version of this Hypothesis;
        # it is a different one, and silently judging it would be the oldest
        # mistake in this project's record.
        if len(rows) < MINIMUM_TRADING_DAYS:
            raise SystemExit(
                f"REFUSED: {len(rows)} trading days, and the declaration requires at "
                f"least {MINIMUM_TRADING_DAYS} (three years). Judging a shorter window "
                f"would answer a question nobody declared. Nothing has been sealed.")

        print("[2/5] sealing dataset...")
        manifest = create_hypothesis_dataset(
            HYPOTHESES, HYPOTHESIS_ID, hypothesis["version"], DATASET,
            strategy=STRATEGY, horizon=HORIZON, acquired_at=now,
            primary_source={"rows": rows, "capture": capture, "raw": raw})
        print(f"      {manifest['dataset_id'][:46]}")

    sealed = [row for row in csv.DictReader((DATASET / "dataset.csv").open(encoding="utf-8"))
              if row.get("forward_return_1d")]
    gross = [float(row["forward_return_1d"]) for row in sealed]

    print("[3/5] building the position: held every day, bought once, sold once...")
    positions = [1] * len(gross)
    contract = p.cost_contract(
        regime=p.COST_REGIME_HOLDING, commission_rate=COST_COMMISSION,
        half_spread_rate=COST_HALF_SPREAD, slippage_rate=COST_SLIPPAGE,
        recurring_rate_per_period="0", legs=1,
        source=("Alpaca charges no US equity commission; half spread and slippage on SPY "
                "DECLARED, not measured on a book"))
    net = p.net_returns(contract, positions, gross)
    # TWO crossings, not one. net_returns already charges a side on the first
    # held period AND on the last -- getting in and getting out -- so the net
    # series is right either way; what this number feeds is the capacity model's
    # turnover and the line printed below, and counting only the entry would
    # understate both. Verified against the contract: a position held for four
    # periods at a 1% side cost returns [0.09, 0.10, 0.10, 0.09].
    transitions = 2
    years = len(sealed) / 252
    gross_total = math.fsum(gross)
    cost_total = float(p.cost_per_side(contract)) * transitions
    mean_net = math.fsum(net) / len(net)
    print(f"      {len(sealed)} days ({years:.2f} yr), {transitions} transition(s)")
    print(f"      GROSS {gross_total:+.4f} ({gross_total / years:+.2%}/yr)"
          f"   COST {cost_total:.6f} ({cost_total / abs(gross_total):.3%} of gross)")
    print(f"      NET   {math.fsum(net):+.4f} ({math.fsum(net) / years:+.2%}/yr)")

    # --- size against the limit, which moves THIS GATE AND NO OTHER ----------
    weight = p.weight_within_drawdown_bound(net, drawdown_limit=DRAWDOWN_LIMIT, **BOOTSTRAP)
    print(f"      sizing against the declared {DRAWDOWN_LIMIT} limit -> weight {weight:.1%}")
    if weight < 1.0:
        # Both the return AND the cost scale with notional. Charging a full
        # position's cost against a fraction of its return taxes it 1/w times
        # over -- a mistake this project already made once, in the CAP-006
        # runner, where it read exactly like "the weight made it worse".
        gross = [value * weight for value in gross]
        contract = p.cost_contract(
            regime=p.COST_REGIME_HOLDING,
            commission_rate=f"{float(Decimal(COST_COMMISSION)) * weight:.10f}",
            half_spread_rate=f"{float(Decimal(COST_HALF_SPREAD)) * weight:.10f}",
            slippage_rate=f"{float(Decimal(COST_SLIPPAGE)) * weight:.10f}",
            recurring_rate_per_period="0", legs=1,
            source=(f"same declared rates as the unsized position, scaled by the "
                    f"{weight:.4f} weight derived from the {DRAWDOWN_LIMIT} limit"))
        net = p.net_returns(contract, positions, gross)
        gross_total = math.fsum(gross)
        cost_total = float(p.cost_per_side(contract)) * transitions
        mean_net = math.fsum(net) / len(net)
        print(f"      NET at that weight {math.fsum(net):+.4f} "
              f"({math.fsum(net) / years:+.2%}/yr)")

    print("[4/5] bounding the statistics...")
    sharpe_point = sharpe_ratio(net, periods_per_year=252)
    sharpe_bound = net_sharpe_lower_bound(net, periods_per_year=252, **BOOTSTRAP)
    drawdown_point, drawdown_bound = max_drawdown(net), drawdown_upper_bound(net, **BOOTSTRAP)
    print(f"      net Sharpe  point {sharpe_point:+.4f}   LOWER bound {sharpe_bound:+.4f}"
          f"   (gate >= +0.50)")
    print(f"      drawdown    point {drawdown_point:.2%}      UPPER bound {drawdown_bound:.2%}"
          f"     (gate <= 15%)")

    volume = math.fsum(float(r["close"]) * float(r["volume"]) for r in sealed) / len(sealed)
    volatility = (math.fsum((v - mean_net) ** 2 for v in net) / (len(net) - 1)) ** 0.5
    SEARCH_MULTIPLE = 10_000
    REFERENCE = 200.0
    try:
        ceiling = p.capacity_ceiling(
            p.capacity_contract(average_daily_volume=f"{volume:.2f}", impact_coefficient="0.1",
                                degradation_tolerance="0.20",
                                source=("ADV MEASURED from the sealed dataset; impact "
                                        "coefficient 0.1 DECLARED, never calibrated here")),
            gross_per_period=gross_total / len(gross),
            base_cost_per_period=cost_total / len(sealed),
            volatility_per_period=volatility, turnover_per_period=transitions / len(sealed),
            reference_capital=REFERENCE, periods_per_year=252,
            search_multiple=SEARCH_MULTIPLE)
        if ceiling["ceiling"] is not None:
            capacity_evidence = {"ceiling": f"{ceiling['ceiling']:.2f}",
                                 "current_tranche": FASE_1_TRANCHE}
            note = f"ceiling ${ceiling['ceiling']:,.0f} ({ceiling['binding_constraint']})"
        else:
            # NONE_FOUND: nothing binds anywhere in the searched range, and the
            # model refuses to invent a number for it -- rightly, because "no
            # ceiling found" is a finding and not a licence to scale. What IS
            # known is that the ceiling exceeds the top of the search, so that
            # top is recorded as a FLOOR on it. R4 asks whether the ceiling is
            # at least the tranche, and answering a ">=" question with a lower
            # bound is the adverse direction, not the convenient one.
            floor = REFERENCE * SEARCH_MULTIPLE
            capacity_evidence = {
                "ceiling": f"{floor:.2f}", "current_tranche": FASE_1_TRANCHE,
                "is_lower_bound_not_the_ceiling": True,
                "source": (f"capacity_ceiling returned NONE_FOUND: no constraint binds "
                           f"between ${REFERENCE:,.0f} and ${floor:,.0f}, so the true "
                           f"ceiling is ABOVE ${floor:,.0f} and this figure is a floor "
                           f"on it, never an estimate of it. A position crossing the "
                           f"market twice in {years:.1f} years barely touches a book "
                           f"whose ADV is ${volume:,.0f}."),
            }
            note = f"NONE_FOUND -- nothing binds below ${floor:,.0f}"
    except ValueError as error:
        capacity_evidence = {"current_tranche": FASE_1_TRANCHE, "model_refusal": str(error)}
        note = f"REFUSED: {error}"
    print(f"      capacity    ADV ${volume:,.0f}   {note}")

    print("[5/5] judging the LEVEL CLAIM, which is what was declared...")
    claim = level_claim(
        position_description=(f"{SYMBOL} total return held continuously from {start[:10]} "
                              f"to {end[:10]}; bought once, sold once, never rebalanced"),
        cost_contract=contract, minimum_folds_required=FOLDS,
        consistency_threshold="0.70", minimum_adverse_episodes=MINIMUM_EPISODES,
        # THE ADVERSE THRESHOLD SCALES WITH THE WEIGHT, for the same reason the
        # cost rates do. -2% was declared as a stress day for the UNSIZED
        # position. At a 28% weight the identical market event moves the
        # position -0.56%, so an absolute threshold stops asking "was the tail
        # observed" and starts asking "was it observed at full size". Left
        # unscaled it reported adverse frequency 0.0009 against 0.0400 and
        # refused the claim for a tail that was fully present: the tail did not
        # go away, the measuring stick stopped matching what it measured.
        adverse_period_threshold=f"{float(Decimal(ADVERSE_THRESHOLD)) * weight:.10f}",
        maximum_drawdown=DRAWDOWN_LIMIT,
        source=f"declared in {HYPOTHESIS_ID[:30]} before any bar was captured")

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

    # ADMISSION IS ONLY REACHED BY SOMETHING VALIDATED. Declaring VALIDATED here
    # for a claim that was not would fabricate the single fact the whole
    # lifecycle rests on, so the chain stops instead and says exactly where.
    if outcome != "VALIDATED":
        print(f"\n  Admission not reached: it is open only to a VALIDATED claim, and this")
        print(f"  one is {outcome}. The figures above ARE the evidence; no admission")
        print(f"  record has been sealed, and none should be.")
        print("=" * 78)
        return 0

    evidence = {
        "net_sharpe": adverse_bound(
            f"{sharpe_bound:.6f}", is_adverse_bound=True,
            # BOTH numbers since the 2026-10-02 correction: 0.50 judges the
            # effect's SIZE on the point estimate, the bound judges whether it
            # is real at all, against zero.
            point_estimate=f"{sharpe_point:.6f}",
            source=f"moving-block bootstrap, {json.dumps(BOOTSTRAP, sort_keys=True)}, "
                   f"on {len(net)} net days of {SLUG}"),
        "worst_fold_drawdown": adverse_bound(
            f"{drawdown_bound:.6f}", is_adverse_bound=True,
            source=("moving-block bootstrap, same parameters, on the WHOLE net series. "
                    "The full window dominates any fold -- every peak-to-trough pair "
                    "inside one is also one in the whole -- so this is the stricter "
                    "reading of R1, stated rather than left to be noticed")),
        "survival": {
            "form": SURVIVAL_P2,
            "counterparty": ("every investor who must reduce equity exposure for reasons "
                             "unrelated to expected return: liability matching, mandates, "
                             "liquidity needs, risk limits and horizon"),
            "why_they_accept_losing": ("they are not losing; they are buying certainty. The "
                                       "premium is the price of bearing a risk they are "
                                       "paying to shed, which is why it is a premium and "
                                       "not a mispricing to be competed away"),
            "what_would_end_it": ("a world in which equity risk is no longer "
                                  "undiversifiable or no longer disliked. Neither has a "
                                  "plausible route, which is the strongest survival case "
                                  "in this project and also the reason this Hypothesis "
                                  "tests the GATES"),
        },
        "capacity": capacity_evidence,
        "decision_cost": {
            "build_cost": DECISION_BUILD_COST,
            "minimum_declared_life_years": DECISION_LIFE_YEARS,
            "recurring_cost_per_year": "0",
            "expected_annual_net_return": f"{mean_net * 252 * float(FASE_1_TRANCHE):.2f}",
            "surveillance_is_automatable": True,
        },
        # NO "monitor" KEY, DELIBERATELY. See this file's docstring: the only
        # observable that reports an unconditional premium dying is the premium
        # not paying, and a PnL-only monitor is refused by contract. R3 comes
        # back NOT_EVALUABLE and that is the finding, not a gap to be filled.
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
