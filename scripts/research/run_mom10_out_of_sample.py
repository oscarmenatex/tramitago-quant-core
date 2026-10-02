"""Out-of-sample test of the one hypothesis that validated.

spy-mom10-2024 cleared every Fase 0->1 criterion: consistency 83.3%, Sharpe mean
2.207, worst-fold drawdown 6.72%, VALIDATED. It is the first in 31 to do so, and
that is exactly why it gets tested harder than anything that failed.

Three reasons the 2024 result cannot stand alone, all recorded with its evidence:
running four hypotheses at once makes one pass likely by chance (37% under a 0.5
per-fold null, 13.7% under this project's measured 0.389); the sealed stopping
rule declares the five-member equity population exhausted at an upper bound of
0.499; and a Sharpe of 2.2 on -0.09%/day is more plausibly exposure than signal,
since a DECREASE direction is dip-buying and 2024 delivered sharp V-shaped
recoveries in April and August.

This is the confirmatory step, not more searching. NOTHING is free to vary:
instrument, strategy, horizon and DIRECTION are copied verbatim from the sealed
2024 Hypothesis. Only the period moves. The project has done exactly this once
before -- a pre-declared out-of-sample test refuted its one promising funding
rate observation -- and that precedent is the reason to trust the answer either
way.

PRE-DECLARED CRITERIA, fixed here before execution
==================================================
Two periods, both run, neither chosen after seeing the other:

  2022 -- a grinding bear market. The adversarial case BY DESIGN: if the 2024
          result is "dips recover in a bull year", dips that keep falling should
          break it. Chosen because it is the hardest test available, which is
          the opposite of shopping for a favourable one.
  2023 -- a bull year WITHOUT 2024's sharp V-shaped recoveries. Separates "the
          effect exists in rising markets" from "August 2024 happened".

PRIMARY criterion -- the sign of the full-sample metric:
  SURVIVES  only if the metric is NEGATIVE in BOTH periods. Under the null that
            is one chance in four.
  FAILS     if either period produces a positive metric. The 2024 result is then
            treated as not reproducible, and no further period is tried --
            trying periods until one agrees is the exact failure this test
            exists to prevent.

SECONDARY evidence -- walk-forward consistency, reported but not decisive. A
VALIDATED verdict in either period strengthens the case; NOT_VALIDATED in both
weakens it even when the signs agree.

Requires Python 3.11+ and the credentials as ENVIRONMENT VARIABLES:

    export ALPACA_PAPER_API_KEY_ID=...
    export ALPACA_PAPER_API_SECRET_KEY=...
    python -B scripts/research/run_mom10_out_of_sample.py
"""

import os
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pipeline as p
from tramitago_quant_core.governance.evidence_host import require_evidence_host
from tramitago_quant_core.data.alpaca_equity_series import capture_alpaca_equity_bars
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

ARTIFACTS  = REPO / "artifacts" / "research"
HYPOTHESES = ARTIFACTS / "hypotheses.json"

# Copied verbatim from the sealed 2024 Hypothesis. None of this may vary.
SYMBOL     = "SPY"
STRATEGY   = p.momentum_crossover_strategy(10)
DIRECTION  = "DECREASE"
COMPARISON = "LT"
HORIZON    = 1
IN_SAMPLE  = "HYPOTHESIS|7f2dc4c8-0807-4802-a3e2-611cfa03000a"   # spy-mom10-2024

# 365 days each, so 5 folds of 73 divide both evenly.
PERIODS = [
    {"label": "2022", "start": "2022-01-01T00:00:00Z", "end": "2023-01-01T00:00:00Z",
     "folds": 5,
     "why": "Grinding bear market. The adversarial case: if the 2024 result is "
            "'dips recover', dips that keep falling should break it."},
    {"label": "2023", "start": "2023-01-01T00:00:00Z", "end": "2024-01-01T00:00:00Z",
     "folds": 5,
     "why": "Bull year without 2024's sharp V-shaped recoveries. Separates a real "
            "effect in rising markets from the shape of August 2024."},
]

NOW = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _fmt(metric):
    return "None" if metric is None else f"{metric:.6f}"


def _code_revision():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise SystemExit("Cannot determine git HEAD; run this from a git checkout.")


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


def run_period(period, code_revision, injector):
    label, folds = period["label"], period["folds"]
    slug = f"spy-mom10-oos-{label}"
    column = STRATEGY["column_name"]
    forward = f"forward_return_{HORIZON}d"
    warmup = STRATEGY["required_inputs"]["warmup_periods"]
    metric = (f"mean_{forward}(close_t > MOM10_t) - mean_{forward}(close_t <= MOM10_t)")
    variables = sorted(set(STRATEGY["required_inputs"]["variables"]) | {"close", column, forward})

    paths = {name: ARTIFACTS / f"{name}-{slug}.json" for name in (
        "experiments", "experiment-results", "research-executions", "research-results",
        "dispositions", "walk-forward-partitions", "walk-forward-fold-results",
        "statistical-validations", "knowledge")}
    dataset_dir = ARTIFACTS / "datasets" / slug.replace("-", "_")

    print(f"\n{'=' * 74}\n{slug}   [{DIRECTION}, locked]   {folds} folds\n{period['why']}\n{'=' * 74}")

    print(f"[1/11] capturing {SYMBOL} bars for {label}...")
    rows, capture, raw = capture_alpaca_equity_bars(
        symbol=SYMBOL, evaluable_start_utc=period["start"],
        evaluable_end_exclusive_utc=period["end"], warmup_periods=warmup,
        horizon=HORIZON, acquired_at=NOW, credential_injector=injector)
    print(f"       {len(rows)} bars ({rows[0]['timestamp'][:10]} -> {rows[-1]['timestamp'][:10]})")

    print("[2/11] constituting hypothesis...")
    hyp = constitute_hypothesis(
        HYPOTHESES,
        description=(
            f"OUT-OF-SAMPLE TEST of {IN_SAMPLE} (spy-mom10-2024), on SPY {label}. "
            f"For SPY, the mean t+1 return when close_t is ABOVE its own value 10 trading "
            f"days earlier is LOWER than when it is at or below it. Instrument, strategy, "
            f"horizon and direction are copied VERBATIM from the in-sample Hypothesis; only "
            f"the period moves. Direction DECREASE was pre-declared in 2024 on the "
            f"short-horizon reversal prior (Jegadeesh 1990; Lehmann 1990) and is LOCKED "
            f"here -- it cannot be revised by this test.\n\n"
            f"Why this period: {period['why']}\n\n"
            f"Pre-declared success criterion, fixed before execution: the claim survives "
            f"out of sample only if the full-sample metric is NEGATIVE in BOTH 2022 and "
            f"2023 (one chance in four under the null). A positive metric in either period "
            f"means the 2024 result is not reproducible, and no further period will be "
            f"tried. Walk-forward consistency is secondary evidence, reported but not "
            f"decisive.\n\n"
            f"Why the in-sample result needed this: four hypotheses were run together, so "
            f"one passing is likely by chance (37% under a 0.5 per-fold null, 13.7% under "
            f"this project's measured 0.389); the sealed stopping rule declares the "
            f"five-member equity population exhausted at an upper bound of 0.499; and a "
            f"Sharpe of 2.207 on a -0.09%/day difference is more plausibly exposure than "
            f"signal, since a DECREASE direction is dip-buying and 2024 delivered sharp "
            f"V-shaped recoveries."),
        target_metric=metric, expected_direction=DIRECTION,
        constraints={"period": {"start_utc": period["start"], "end_exclusive_utc": period["end"]},
                     "universe": [SYMBOL], "variables": variables},
        acceptance_criterion={"comparison": COMPARISON, "expected_direction": DIRECTION,
                              "metric": metric, "threshold": "0"},
        creation_timestamp=NOW, status="CONSTITUTED", created_by="Oscar Carmenate Rodriguez",
        provenance=["DISCOVERY|artifacts/live-run-1",
                    "SNAPSHOT_SHA256|a1b6a0bbe47c247c7e5d4adc5ab9c8d578dd0c347d39c4bdb6f6dc2c089ec00f",
                    "KNOWLEDGE|b154409262618e2436ad19dbfdab4da2e43c97678d56ec5192cc992b4c2cd521",
                    f"ANALYSIS|nivel-5-{slug}"],
        code_revision=code_revision, system_version="0.1.0")
    hid, hver = hyp["hypothesis_id"], hyp["version"]
    print(f"       {hid}")

    print("[3/11] sealing dataset...")
    manifest = create_hypothesis_dataset(
        HYPOTHESES, hid, hver, dataset_dir, strategy=STRATEGY, horizon=HORIZON,
        acquired_at=NOW, primary_source={"rows": rows, "capture": capture, "raw": raw})
    print(f"       rows={manifest['rows']}")

    print("[4/11] experiment conditions...")
    exp = constitute_experiment_conditions(
        paths["experiments"], hypothesis_registry_path=HYPOTHESES,
        dataset_directory=dataset_dir, hypothesis_id=hid, hypothesis_version=hver,
        dataset_id=manifest["dataset_id"], created_at=NOW,
        revision_reason=f"Out-of-sample test of spy-mom10-2024 on {label}",
        strategy=STRATEGY, horizon=HORIZON)
    eid, ever, erec = exp["experiment_id"], exp["version"], exp["record_id"]

    print("[5/11] executing experiment...")
    res = execute_experiment_result(
        paths["experiment-results"], experiment_registry_path=paths["experiments"],
        hypothesis_registry_path=HYPOTHESES, dataset_directory=dataset_dir,
        experiment_id=eid, experiment_version=ever, experiment_record_id=erec,
        execution_code_revision=code_revision)
    ev = res.get("evaluation", {})
    groups = ev.get("groups", {})
    upper_n = groups.get("upper", {}).get("count")
    lower_n = groups.get("lower_or_equal", {}).get("count")
    print(f"       {ev.get('criterion_result')}  metric={_fmt(ev.get('metric'))}  "
          f"n={upper_n}/{lower_n}")

    print("[6/11] research execution...")
    rexec = constitute_research_execution(
        paths["research-executions"], legacy_result_registry_path=paths["experiment-results"],
        legacy_result_id=res["result_id"], legacy_result_record_id=res["record_id"],
        experiment_registry_path=paths["experiments"], hypothesis_registry_path=HYPOTHESES,
        dataset_directory=dataset_dir, materialized_at=NOW,
        materialization_code_revision=code_revision)

    print("[7/11] research result...")
    rres = constitute_research_result(
        paths["research-results"], research_execution_registry_path=paths["research-executions"],
        research_execution_id=rexec["execution_id"], research_execution_record_id=rexec["record_id"],
        legacy_result_registry_path=paths["experiment-results"],
        experiment_registry_path=paths["experiments"], hypothesis_registry_path=HYPOTHESES,
        dataset_directory=dataset_dir, materialized_at=NOW,
        materialization_code_revision=code_revision)

    print("[8/11] disposition...")
    disp = constitute_disposition(
        paths["dispositions"], research_result_registry_path=paths["research-results"],
        research_result_id=rres["research_result_id"], research_result_record_id=rres["record_id"],
        research_execution_registry_path=paths["research-executions"],
        legacy_result_registry_path=paths["experiment-results"],
        experiment_registry_path=paths["experiments"], hypothesis_registry_path=HYPOTHESES,
        dataset_directory=dataset_dir, disposed_at=NOW, disposition_code_revision=code_revision)
    print(f"       {disp['outcome']}")

    print(f"[9/11] walk-forward partition ({folds} folds)...")
    part = constitute_walk_forward_partition(
        paths["walk-forward-partitions"], dataset_directory=dataset_dir,
        hypothesis_registry_path=HYPOTHESES, fold_count=folds, partitioned_at=NOW,
        partition_code_revision=code_revision, strategy=STRATEGY, horizon=HORIZON)

    print(f"[10/11] running {folds} folds...")
    for index in range(folds):
        fold = constitute_walk_forward_fold_result(
            paths["walk-forward-fold-results"],
            partition_registry_path=paths["walk-forward-partitions"],
            partition_id=part["partition_id"], partition_record_id=part["record_id"],
            fold_index=index, experiment_registry_path=paths["experiments"],
            experiment_id=eid, experiment_version=ever, experiment_record_id=erec,
            hypothesis_registry_path=HYPOTHESES, dataset_directory=dataset_dir,
            computed_at=NOW, computation_code_revision=code_revision,
            strategy=STRATEGY, horizon=HORIZON)
        fe, fg = fold["evaluation"], fold["evaluation"].get("groups", {})
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
        consistency_threshold="0.70", validated_at=NOW,
        validation_code_revision=code_revision, strategy=STRATEGY, horizon=HORIZON)
    passing = sum(1 for s in val.get("fold_summaries", []) if s["criterion_result"] == "MET")
    print(f"        {val['outcome']}  consistency={val['consistency_ratio']}  "
          f"{passing}/{folds} folds MET")

    metric_value = ev.get("metric")
    sign_holds = metric_value is not None and metric_value < 0
    interpretation = (
        f"Test FUERA DE MUESTRA de spy-mom10-2024 sobre SPY {label}. Instrumento, "
        f"estrategia, horizonte y direccion copiados literalmente de la hipotesis en "
        f"muestra; solo cambia el periodo. Direccion DECREASE BLOQUEADA, no revisable por "
        f"este test.\n\n"
        f"Resultado: metric={_fmt(metric_value)}, grupos {upper_n}/{lower_n}, criterio LT 0 "
        f"-> {disp['outcome']}. Signo "
        f"{'NEGATIVO, concuerda con la muestra' if sign_holds else 'POSITIVO, CONTRADICE la muestra'}. "
        f"Walk-forward {passing}/{folds} folds MET, consistencia {val['consistency_ratio']}, "
        f"{val['outcome']}.\n\n"
        f"Criterio primario pre-declarado: la afirmacion sobrevive solo si el signo es "
        f"negativo en 2022 Y 2023. Un signo positivo en cualquiera de los dos significa que "
        f"el resultado de 2024 no es reproducible, y no se prueba ningun periodo adicional.")

    know = constitute_knowledge_record(
        paths["knowledge"], disposition_registry_path=paths["dispositions"],
        disposition_id=disp["disposition_id"], disposition_record_id=disp["record_id"],
        research_result_registry_path=paths["research-results"],
        research_execution_registry_path=paths["research-executions"],
        legacy_result_registry_path=paths["experiment-results"],
        experiment_registry_path=paths["experiments"], hypothesis_registry_path=HYPOTHESES,
        dataset_directory=dataset_dir, interpretation=interpretation,
        limitations=[
            "Out-of-sample on PERIOD only. Same instrument, same daily frequency, same "
            "single strategy -- it does not test transfer across instruments or markets.",
            "Gross of transaction costs; Nivel 4 is not built.",
            "A surviving sign is necessary, not sufficient: two periods agreeing is one "
            "chance in four under the null.",
            f"Period context: {period['why']}",
        ],
        preserved_at=NOW, knowledge_code_revision=code_revision)

    return {"label": label, "metric": metric_value, "sign_holds": sign_holds,
            "disposition": disp["outcome"], "validation": val["outcome"],
            "consistency": val["consistency_ratio"], "passing": passing, "folds": folds,
            "upper": upper_n, "lower": lower_n, "knowledge_id": know["knowledge_id"]}


def main():
    require_evidence_host(REPO)
    print("=" * 74)
    print("OUT-OF-SAMPLE TEST -- spy-mom10-2024, direction DECREASE locked")
    print("=" * 74)
    revision = _code_revision()
    injector = _credential_injector()
    print(f"code_revision: {revision}")
    print(f"in-sample:     {IN_SAMPLE}")
    print("criterion:     survives ONLY if the metric is negative in BOTH periods")

    results, failures = [], []
    for period in PERIODS:
        try:
            results.append(run_period(period, revision, injector))
        except Exception:
            failures.append(period["label"])
            print(f"\n!!! {period['label']} FAILED -- recorded, not retried\n")
            traceback.print_exc()

    print("\n" + "=" * 74)
    print("OUT-OF-SAMPLE VERDICT")
    print("=" * 74)
    for r in results:
        print(f"  {r['label']}  metric={_fmt(r['metric'])}  n={r['upper']}/{r['lower']}  "
              f"sign {'HOLDS' if r['sign_holds'] else 'BREAKS'}  "
              f"{r['validation']} {r['passing']}/{r['folds']} cons={r['consistency']}")
    print()
    if failures:
        print(f"  INCOMPLETE -- {', '.join(failures)} did not run. No verdict.")
    elif len(results) == len(PERIODS) and all(r["sign_holds"] for r in results):
        print("  SURVIVES: the sign held in both periods (one chance in four under the null).")
        print("  Necessary, not sufficient. Still gross of costs, still one instrument.")
    else:
        broke = [r["label"] for r in results if not r["sign_holds"]]
        print(f"  NOT REPRODUCIBLE: the sign broke in {', '.join(broke)}.")
        print("  Per the pre-declared criterion, no further period is tried.")
    print()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
