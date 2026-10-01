"""The carry exit rule, tested in sample and out of sample, both declared first.

The destination is harvesting risk premia, whose defining clause is "stop
exploiting it when the evidence stops supporting it". That exit rule lives today
as a DECLARED threshold inside a monitoring contract -- a conjecture nobody has
tested. This is the Hypothesis that turns it into evidence, and if it survives
it is what lets the survival and monitorability gates rest on measurement rather
than on assertion.

THE CLAIM
    For a market-neutral carry in BTC -- long spot, short perpetual -- the mean
    forward carry return when funding is AT OR ABOVE zero is HIGHER than when it
    is below. Direction INCREASE, pre-declared.

    The outcome is not a price change. It is the funding received over the
    period MINUS the change in basis: a cash flow plus a spread mark. Measuring
    only the funding would describe a position that cannot lose.

BOTH PERIODS ARE DECLARED HERE BEFORE EITHER RUNS, and that ordering is the
point. 2024 is in sample; 2025 is the pre-declared out-of-sample test, with its
success criterion fixed below. Today a hypothesis cleared every criterion in
sample and was refuted out of sample, so building that step in from the start
costs nothing and is what the result will stand or fall on.

SINGLE VENUE, DELIBERATELY. Funding and perpetual both come from Hyperliquid.
Pairing one exchange's funding with another's price would compute a basis nobody
could trade. That constraint fixes the period: Hyperliquid serves no funding
before 2024 and its 2023 candles are oracle backfill with zero trades, so
2024-2025 is the whole of what exists where both series are real.

Requires Python 3.11+. No credentials: Hyperliquid and Coinbase are both public.

    python -B scripts/research/run_carry_exit_rule.py
"""

import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pipeline as p
from tramitago_quant_core.data.hyperliquid_funding_rate import (
    capture_hyperliquid_funding_rate, verified_hyperliquid_funding_rate_capture,
)
from tramitago_quant_core.data.hyperliquid_perpetual_price import (
    capture_hyperliquid_perpetual_price, verified_hyperliquid_perpetual_price_capture,
)
from tramitago_quant_core.research.hypothesis import constitute_hypothesis
from tramitago_quant_core.research.historical_dataset import create_hypothesis_dataset
from tramitago_quant_core.research.experiment import (
    constitute_experiment_conditions, execute_experiment_result,
)
from tramitago_quant_core.research.research_execution import (
    constitute_research_execution, constitute_research_result,
)
from tramitago_quant_core.strategy_evaluation.disposition import constitute_disposition
from tramitago_quant_core.research.walk_forward import (
    constitute_walk_forward_partition, constitute_walk_forward_fold_result,
    constitute_statistical_validation,
)
from tramitago_quant_core.knowledge.knowledge_record import constitute_knowledge_record

ARTIFACTS = REPO / "artifacts" / "research"
HYPOTHESES = ARTIFACTS / "hypotheses.json"
COIN, INSTRUMENT, HORIZON = "BTC", "BTC-USD", 1
STRATEGY = p.carry_funding_threshold_strategy()
NOW = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")

AUXILIARY_VERIFIERS = {
    "funding_rate": verified_hyperliquid_funding_rate_capture,
    "perp_close": verified_hyperliquid_perpetual_price_capture,
}

# ---------------------------------------------------------------------------
# DECLARED BEFORE EXECUTION. Neither period is adjusted after seeing the other.
# ---------------------------------------------------------------------------
PERIODS = [
    {"label": "2024", "role": "IN SAMPLE", "folds": 6,
     "start": "2024-01-01T00:00:00Z", "end": "2025-01-01T00:00:00Z"},
    {"label": "2025", "role": "OUT OF SAMPLE", "folds": 5,
     "start": "2025-01-01T00:00:00Z", "end": "2026-01-01T00:00:00Z"},
]

OUT_OF_SAMPLE_CRITERION = (
    "The claim survives only if the full-sample metric is POSITIVE in BOTH 2024 "
    "and 2025 -- one chance in four under the null. A negative metric in the "
    "out-of-sample year means the in-sample result is not reproducible and NO "
    "further period is tried; trying periods until one agrees is the failure "
    "this test exists to prevent. Walk-forward consistency is secondary "
    "evidence, reported but not decisive.")

LIMITATIONS = [
    "Validates the EXIT RULE, not whether the premium is adequate for the risk "
    "borne. That question is a comparison of compensation against tail risk and "
    "does not fit the M2.x associative apparatus at all.",
    "Gross of transaction costs. The cost model exists but is not wired into the "
    "Outcome; a carry is held rather than rotated, so costs amortise, but that is "
    "argued rather than measured here.",
    "Single instrument, single venue, single horizon, two years -- the whole of "
    "what exists where Hyperliquid funding and real perpetual trading overlap.",
    "Says nothing about venue failure, which is the risk no basis number "
    "expresses and which the 2020-03-13 capture failure showed as absence.",
    "The adverse basis bound measured elsewhere may be regime-dependent: the "
    "worst excursions came from 2019 and 2021, not from the years tested here.",
]


def _fmt(metric):
    return "None" if metric is None else f"{metric:.6f}"


def _code_revision():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise SystemExit("Cannot determine git HEAD; run this from a git checkout.")


def run_period(period, revision):
    label, folds = period["label"], period["folds"]
    slug = f"carry-exit-btc-{label}"
    column = STRATEGY["column_name"]
    forward = STRATEGY["outcome"]["column"](HORIZON)
    warmup = STRATEGY["required_inputs"]["warmup_periods"]
    metric = (f"mean_{forward}(funding_t-1 >= 0) - mean_{forward}(funding_t-1 < 0)")
    variables = sorted(set(STRATEGY["required_inputs"]["variables"])
                       | set(STRATEGY["outcome"]["required_inputs"]["variables"])
                       | {column, forward})

    paths = {name: ARTIFACTS / f"{name}-{slug}.json" for name in (
        "experiments", "experiment-results", "research-executions", "research-results",
        "dispositions", "walk-forward-partitions", "walk-forward-fold-results",
        "statistical-validations", "knowledge")}
    dataset_dir = ARTIFACTS / "datasets" / slug.replace("-", "_")

    print(f"\n{'=' * 76}\n{slug}   [{period['role']}]   {folds} folds\n{'=' * 76}")

    # The capture window must cover warmup before and the horizon after, because
    # the signal reads t-1 and the outcome reads t+1.
    cap_start = p.iso(p.epoch(period["start"]) - warmup * 86400)
    cap_end = p.iso(p.epoch(period["end"]) + HORIZON * 86400)

    print(f"[1/11] capturing funding and perpetual from Hyperliquid ({cap_start[:10]} -> {cap_end[:10]})...")
    funding, funding_capture, funding_raw = capture_hyperliquid_funding_rate(
        COIN, cap_start, cap_end, NOW)
    perp, perp_capture, perp_raw = capture_hyperliquid_perpetual_price(
        COIN, cap_start, cap_end, NOW, interval="1d")
    print(f"       funding {len(funding)} days | perpetual {len(perp)} days, both re-verified")
    assert verified_hyperliquid_funding_rate_capture(funding_raw, funding_capture) == funding
    assert verified_hyperliquid_perpetual_price_capture(perp_raw, perp_capture) == perp

    auxiliary_sources = {
        "funding_rate": {"series": funding, "capture": funding_capture, "raw": funding_raw},
        "perp_close": {"series": perp, "capture": perp_capture, "raw": perp_raw},
    }

    print("[2/11] constituting hypothesis...")
    hypothesis = constitute_hypothesis(
        HYPOTHESES,
        description=(
            f"For a MARKET-NEUTRAL CARRY in BTC -- long {INSTRUMENT} spot, short the "
            f"Hyperliquid perpetual at equal notional -- the mean forward carry return "
            f"when funding is AT OR ABOVE zero is HIGHER than when it is below. "
            f"Direction INCREASE pre-declared.\n\n"
            f"The outcome is NOT a price change: it is the funding received over the "
            f"period MINUS the change in basis, a cash flow plus a spread mark. "
            f"Measuring only the funding would describe a position that cannot lose, "
            f"which is not the position held -- and would hide that a liquidation "
            f"cascade, driving the perpetual below spot, is FAVOURABLE to the short leg.\n\n"
            f"WHAT THIS TESTS AND WHY: not whether the premium exists -- that is already "
            f"measured, and is a level claim about one group that the M2.x apparatus "
            f"structurally cannot express. It tests whether the EXIT RULE discriminates, "
            f"which is a two-group comparison the apparatus judges natively, and which "
            f"is the only thing the destination's clause 'stop when the evidence stops "
            f"supporting it' rests on. Today that rule is a declared threshold nobody "
            f"has tested.\n\n"
            f"THE THRESHOLD IS ZERO because it is the only parameter-free boundary: the "
            f"point where the economics invert, from being paid to hold to paying to "
            f"hold. Any other number would come from looking at the data first.\n\n"
            f"NO LOOKAHEAD: the row at t is classified by funding at t-1, never its own "
            f"still-accruing rate, and the outcome reads funding at t+1. Signal and "
            f"outcome are two periods apart and cannot overlap.\n\n"
            f"SINGLE VENUE: funding and perpetual both from Hyperliquid. Pairing one "
            f"exchange's funding with another's price would compute a basis nobody could "
            f"trade. That fixes the period -- 2024-2025 is the whole of what exists where "
            f"both series are real.\n\n"
            f"Period role: {period['role']}. {OUT_OF_SAMPLE_CRITERION}"),
        target_metric=metric, expected_direction="INCREASE",
        constraints={"period": {"start_utc": period["start"], "end_exclusive_utc": period["end"]},
                     "universe": [INSTRUMENT], "variables": variables},
        acceptance_criterion={"comparison": "GT", "expected_direction": "INCREASE",
                              "metric": metric, "threshold": "0"},
        creation_timestamp=NOW, status="CONSTITUTED", created_by="Oscar Carmenate Rodriguez",
        provenance=["DISCOVERY|artifacts/live-run-1",
                    "SNAPSHOT_SHA256|a1b6a0bbe47c247c7e5d4adc5ab9c8d578dd0c347d39c4bdb6f6dc2c089ec00f",
                    "KNOWLEDGE|b154409262618e2436ad19dbfdab4da2e43c97678d56ec5192cc992b4c2cd521",
                    f"ANALYSIS|risk-premia-carry-exit-rule-{label}"],
        code_revision=revision, system_version="0.1.0")
    hid, hver = hypothesis["hypothesis_id"], hypothesis["version"]
    print(f"       {hid}")

    print("[3/11] sealing dataset (TWO auxiliary series)...")
    manifest = create_hypothesis_dataset(
        HYPOTHESES, hid, hver, dataset_dir, strategy=STRATEGY, horizon=HORIZON,
        auxiliary_sources=auxiliary_sources, acquired_at=NOW)
    print(f"       rows={manifest['rows']}  columns={len(manifest['columns'])}")

    print("[4/11] experiment conditions...")
    exp = constitute_experiment_conditions(
        paths["experiments"], hypothesis_registry_path=HYPOTHESES,
        dataset_directory=dataset_dir, hypothesis_id=hid, hypothesis_version=hver,
        dataset_id=manifest["dataset_id"], created_at=NOW,
        revision_reason=f"Carry exit rule, {period['role']} ({label})",
        strategy=STRATEGY, horizon=HORIZON, auxiliary_verifiers=AUXILIARY_VERIFIERS)
    eid, ever, erec = exp["experiment_id"], exp["version"], exp["record_id"]

    print("[5/11] executing experiment...")
    res = execute_experiment_result(
        paths["experiment-results"], experiment_registry_path=paths["experiments"],
        hypothesis_registry_path=HYPOTHESES, dataset_directory=dataset_dir,
        experiment_id=eid, experiment_version=ever, experiment_record_id=erec,
        execution_code_revision=revision, auxiliary_verifiers=AUXILIARY_VERIFIERS)
    ev = res.get("evaluation", {})
    groups = ev.get("groups", {})
    upper_n = groups.get("upper", {}).get("count")
    lower_n = groups.get("lower_or_equal", {}).get("count")
    print(f"       {ev.get('criterion_result')}  metric={_fmt(ev.get('metric'))}  n={upper_n}/{lower_n}")
    if ev.get("inconclusive_reason"):
        print(f"       inconclusive_reason: {ev['inconclusive_reason']}")

    print("[6/11] research execution...")
    rexec = constitute_research_execution(
        paths["research-executions"], legacy_result_registry_path=paths["experiment-results"],
        legacy_result_id=res["result_id"], legacy_result_record_id=res["record_id"],
        experiment_registry_path=paths["experiments"], hypothesis_registry_path=HYPOTHESES,
        dataset_directory=dataset_dir, materialized_at=NOW,
        materialization_code_revision=revision, auxiliary_verifiers=AUXILIARY_VERIFIERS)

    print("[7/11] research result...")
    rres = constitute_research_result(
        paths["research-results"], research_execution_registry_path=paths["research-executions"],
        research_execution_id=rexec["execution_id"], research_execution_record_id=rexec["record_id"],
        legacy_result_registry_path=paths["experiment-results"],
        experiment_registry_path=paths["experiments"], hypothesis_registry_path=HYPOTHESES,
        dataset_directory=dataset_dir, materialized_at=NOW,
        materialization_code_revision=revision, auxiliary_verifiers=AUXILIARY_VERIFIERS)

    print("[8/11] disposition...")
    disp = constitute_disposition(
        paths["dispositions"], research_result_registry_path=paths["research-results"],
        research_result_id=rres["research_result_id"], research_result_record_id=rres["record_id"],
        research_execution_registry_path=paths["research-executions"],
        legacy_result_registry_path=paths["experiment-results"],
        experiment_registry_path=paths["experiments"], hypothesis_registry_path=HYPOTHESES,
        dataset_directory=dataset_dir, disposed_at=NOW, disposition_code_revision=revision,
        auxiliary_verifiers=AUXILIARY_VERIFIERS)
    print(f"       {disp['outcome']}")

    print(f"[9/11] walk-forward partition ({folds} folds)...")
    part = constitute_walk_forward_partition(
        paths["walk-forward-partitions"], dataset_directory=dataset_dir,
        hypothesis_registry_path=HYPOTHESES, fold_count=folds, partitioned_at=NOW,
        partition_code_revision=revision, strategy=STRATEGY, horizon=HORIZON,
        auxiliary_verifiers=AUXILIARY_VERIFIERS)

    print(f"[10/11] running {folds} folds...")
    for index in range(folds):
        fold = constitute_walk_forward_fold_result(
            paths["walk-forward-fold-results"],
            partition_registry_path=paths["walk-forward-partitions"],
            partition_id=part["partition_id"], partition_record_id=part["record_id"],
            fold_index=index, experiment_registry_path=paths["experiments"],
            experiment_id=eid, experiment_version=ever, experiment_record_id=erec,
            hypothesis_registry_path=HYPOTHESES, dataset_directory=dataset_dir,
            computed_at=NOW, computation_code_revision=revision,
            strategy=STRATEGY, horizon=HORIZON, auxiliary_verifiers=AUXILIARY_VERIFIERS)
        fe = fold["evaluation"]
        fg = fe.get("groups", {})
        print(f"        fold {index}: {fe['criterion_result']:13} metric={_fmt(fe['metric'])}"
              f"  n={fg.get('upper', {}).get('count')}/{fg.get('lower_or_equal', {}).get('count')}")

    print("[11/11] statistical validation...")
    val = constitute_statistical_validation(
        paths["statistical-validations"],
        partition_registry_path=paths["walk-forward-partitions"],
        partition_id=part["partition_id"], partition_record_id=part["record_id"],
        fold_result_registry_path=paths["walk-forward-fold-results"],
        experiment_registry_path=paths["experiments"], hypothesis_registry_path=HYPOTHESES,
        dataset_directory=dataset_dir, minimum_folds_required=folds,
        consistency_threshold="0.70", validated_at=NOW, validation_code_revision=revision,
        strategy=STRATEGY, horizon=HORIZON, auxiliary_verifiers=AUXILIARY_VERIFIERS)
    passing = sum(1 for s in val.get("fold_summaries", []) if s["criterion_result"] == "MET")
    print(f"        {val['outcome']}  consistency={val['consistency_ratio']}  {passing}/{folds} MET")

    metric_value = ev.get("metric")
    sign_holds = metric_value is not None and metric_value > 0
    interpretation = (
        f"Regla de salida del carry BTC, {period['role']} ({label}). Posicion neutral: "
        f"largo spot, corto perpetuo de Hyperliquid. Umbral CERO, el unico punto sin "
        f"parametros. Sin lookahead: la senal lee t-1, el outcome lee t+1.\n\n"
        f"Resultado: metric={_fmt(metric_value)}, grupos {upper_n}/{lower_n}, criterio "
        f"GT 0 -> {disp['outcome']}. Signo "
        f"{'POSITIVO, concuerda' if sign_holds else 'NO POSITIVO, CONTRADICE'}. "
        f"Walk-forward {passing}/{folds} MET, consistencia {val['consistency_ratio']}, "
        f"{val['outcome']}.\n\n"
        f"Valida la REGLA DE SALIDA, no si la prima es adecuada al riesgo. Esa pregunta "
        f"no cabe en el aparato asociativo de M2.x y seguira sin caber.")

    know = constitute_knowledge_record(
        paths["knowledge"], disposition_registry_path=paths["dispositions"],
        disposition_id=disp["disposition_id"], disposition_record_id=disp["record_id"],
        research_result_registry_path=paths["research-results"],
        research_execution_registry_path=paths["research-executions"],
        legacy_result_registry_path=paths["experiment-results"],
        experiment_registry_path=paths["experiments"], hypothesis_registry_path=HYPOTHESES,
        dataset_directory=dataset_dir, interpretation=interpretation,
        limitations=LIMITATIONS + [f"Period role: {period['role']}. {OUT_OF_SAMPLE_CRITERION}"],
        preserved_at=NOW, knowledge_code_revision=revision,
        auxiliary_verifiers=AUXILIARY_VERIFIERS)

    return {"label": label, "role": period["role"], "metric": metric_value,
            "sign_holds": sign_holds, "disposition": disp["outcome"],
            "validation": val["outcome"], "consistency": val["consistency_ratio"],
            "passing": passing, "folds": folds, "upper": upper_n, "lower": lower_n,
            "knowledge_id": know["knowledge_id"]}


def main():
    print("=" * 76)
    print("CARRY EXIT RULE -- in sample and out of sample, both declared first")
    print("=" * 76)
    revision = _code_revision()
    print(f"code_revision: {revision}")
    print(f"strategy: {STRATEGY['strategy_id']}  outcome: {STRATEGY['outcome']['outcome_id']}")
    for period in PERIODS:
        print(f"  declared: {period['label']}  {period['role']}  {period['folds']} folds")
    print(f"\ncriterion: {OUT_OF_SAMPLE_CRITERION}")

    results, failures = [], []
    for period in PERIODS:
        try:
            results.append(run_period(period, revision))
        except Exception:
            failures.append(period["label"])
            print(f"\n!!! {period['label']} FAILED -- recorded, not retried\n")
            traceback.print_exc()

    print("\n" + "=" * 76)
    print("VERDICT")
    print("=" * 76)
    for r in results:
        print(f"  {r['label']} {r['role']:14} metric={_fmt(r['metric'])} n={r['upper']}/{r['lower']} "
              f"sign {'HOLDS' if r['sign_holds'] else 'BREAKS'}  {r['validation']} "
              f"{r['passing']}/{r['folds']} cons={r['consistency']}")
    print()
    if failures:
        print(f"  INCOMPLETE -- {', '.join(failures)} did not run. No verdict.")
    elif all(r["sign_holds"] for r in results) and len(results) == len(PERIODS):
        print("  SURVIVES: the sign held in both periods (one chance in four under the null).")
        print("  Necessary, not sufficient -- still gross of costs, one venue, two years,")
        print("  and it says nothing about whether the premium is adequate for the risk.")
    else:
        broke = [r["label"] for r in results if not r["sign_holds"]]
        print(f"  NOT REPRODUCIBLE: the sign broke in {', '.join(broke)}.")
        print("  Per the pre-declared criterion, no further period is tried.")
    print()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
