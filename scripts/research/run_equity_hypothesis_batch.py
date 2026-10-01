"""The four remaining US-equity hypotheses, declared before any of them runs.

The sealed stopping rule ("Regla de Parada de Busqueda - Pre-declaracion",
2026-09-30) requires a minimum of five distinct hypotheses in an evidence
population before its exhaustion test may fire. SPY SMA(50) was the first. These
are the other four.

ALL FOUR ARE DECLARED IN THIS FILE BEFORE ANY IS EXECUTED -- instrument, period,
strategy, direction, academic basis and caveats. Running them in one pass is the
point: it makes it impossible to adjust the third after seeing the second.

Two constraints shaped the selection, and both exist to keep this from becoming
a search:

  1. Every Strategy here ALREADY EXISTS in the repository. No new classifier was
     written for this batch, so none could have been shaped to the data.
  2. Every direction is pre-declared from published literature, not from any
     observation in this project's own evidence.

Class balance was reasoned a priori, never measured first -- the SPY SMA(200)
run was untestable because one group was empty, and SMA(50) still came out 220
vs 32. Volume and range are mean-reverting around their own trailing averages,
so both states recur by construction; a lagged-close comparison skews in a
trending year but both states occur; the conjunction is dominated by its volume
term. The risk is stated per hypothesis below rather than discovered after.

Requires Python 3.11+ and the two Alpaca credentials as ENVIRONMENT VARIABLES:

    export ALPACA_PAPER_API_KEY_ID=...
    export ALPACA_PAPER_API_SECRET_KEY=...
    python -B scripts/research/run_equity_hypothesis_batch.py
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

ARTIFACTS   = REPO / "artifacts" / "research"
HYPOTHESES  = ARTIFACTS / "hypotheses.json"
SYMBOL      = "SPY"
HORIZON     = 1
EVAL_START  = "2024-01-01T00:00:00Z"
EVAL_END    = "2025-01-01T00:00:00Z"
FOLD_COUNT  = 6            # 2024 is a leap year: 366 days, 6 folds of 61
NOW = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")

COMMON_LIMITATIONS = [
    "Daily bars only (IEX feed). No transaction costs -- Nivel 4 is not built, so any "
    "measured edge is gross of spread and commissions.",
    "Single instrument (SPY), single period (2024), single horizon (1 day).",
    "2024 was a strong bull-market year, which flatters any direction that is long-biased.",
    "This is one of four hypotheses declared together before any was executed, to reach the "
    "five-hypothesis minimum the sealed stopping rule requires before its exhaustion test "
    "may fire. It is not a result selected from a wider search.",
]

# ---------------------------------------------------------------------------
# THE FOUR DECLARATIONS. Nothing below this block is adjusted once it runs.
# ---------------------------------------------------------------------------
HYPOTHESES_SPEC = [
    {
        "slug": "spy-mom10-2024",
        "strategy": p.momentum_crossover_strategy(10),
        "direction": "DECREASE",
        "comparison": "LT",
        "signal_label": "MOM10_t",
        "left": "close_t",
        "description": (
            "For SPY, the mean t+1 return when close_t is ABOVE its own value 10 trading "
            "days earlier is LOWER than when it is at or below it. Direction DECREASE "
            "pre-declared on the SHORT-HORIZON REVERSAL prior (Jegadeesh 1990, monthly "
            "reversals; Lehmann 1990, weekly reversals): at daily-to-monthly horizons "
            "reversal dominates, while momentum proper is a 3-12 month phenomenon "
            "(Jegadeesh & Titman 1993), so a 10-day lookback against a 1-day forward "
            "return sits in the reversal regime, not the momentum one."),
        "caveats": [
            "The reversal literature is largely cross-sectional across many stocks; a single "
            "index ETF is a much weaker test of it.",
            "This project's crypto evidence also showed reversal signs. That observation did "
            "NOT motivate this direction -- the Jegadeesh/Lehmann prior predates it and is "
            "independent -- but the coincidence is recorded rather than hidden.",
            "Class balance risk: a lagged-close comparison skews upward in a trending year. "
            "Both states are expected to occur, but not evenly.",
        ],
    },
    {
        "slug": "spy-volsurge20-2024",
        "strategy": p.volume_surge_strategy(20),
        "direction": "INCREASE",
        "comparison": "GT",
        "signal_label": "VOLSURGE20_t",
        "left": "volume_t",
        "description": (
            "For SPY, the mean t+1 return when volume_t is ABOVE its own trailing 20-day "
            "average is HIGHER than when it is at or below it. Direction INCREASE "
            "pre-declared on the HIGH-VOLUME RETURN PREMIUM (Gervais, Kaniel & Mingelgrin "
            "2001): securities experiencing unusually high trading volume subsequently earn "
            "higher returns, attributed to the visibility such volume confers."),
        "caveats": [
            "The original premium is CROSS-SECTIONAL on individual stocks over 1-20 day "
            "horizons. Its transfer to a single broad-index ETF is not established and may "
            "not hold at all.",
            "Volume for an ETF reflects creation/redemption and hedging flow as well as "
            "directional interest, so the mechanism may not be the one documented.",
            "Class balance expected to be reasonable: volume mean-reverts around its own "
            "trailing average, so both states recur by construction.",
        ],
    },
    {
        "slug": "spy-range20-2024",
        "strategy": p.intraday_range_strategy(20),
        "direction": "DECREASE",
        "comparison": "LT",
        "signal_label": "RANGE20",
        "left": "(high-low)/close",
        "description": (
            "For SPY, the mean t+1 return when the intraday range (high-low)/close is ABOVE "
            "its own trailing 20-day average is LOWER than when it is at or below it. "
            "Direction DECREASE pre-declared on the LOW-VOLATILITY ANOMALY (Ang, Hodrick, "
            "Xing & Zhang 2006; Baker, Bradley & Wurgler 2011): high-volatility assets "
            "earn lower returns than the risk-return relation predicts, with realised "
            "intraday range standing in for realised volatility."),
        "caveats": [
            "The anomaly is documented CROSS-SECTIONALLY and at monthly horizons. A 1-day "
            "forward return on one ETF is a far weaker test, and the sign need not survive "
            "at that frequency.",
            "Intraday range also spikes on news days, where the next-day return is dominated "
            "by the news rather than by any volatility premium.",
            "Class balance expected to be reasonable: range mean-reverts around its own "
            "trailing average.",
        ],
    },
    {
        "slug": "spy-smavol-10-20-2024",
        "strategy": p.sma_volume_confirmation_strategy(10, 20),
        "direction": "INCREASE",
        "comparison": "GT",
        "signal_label": "sma_close_10 AND volsurge20",
        "left": "close_t",
        "description": (
            "For SPY, the mean t+1 return when close_t is ABOVE its trailing 10-day average "
            "AND volume_t is ABOVE its trailing 20-day average is HIGHER than otherwise. "
            "Direction INCREASE pre-declared on VOLUME AS CONFIRMATION (Blume, Easley & "
            "O'Hara 1994: volume carries information about the quality of a price signal "
            "that price alone does not), the formal basis for the practitioner premise that "
            "a price move on rising volume is more reliable than one without it."),
        "caveats": [
            "A conjunction is the highest class-imbalance risk in this batch: it can only "
            "shrink the UPPER group relative to either condition alone. It is expected to be "
            "dominated by the volume term, since close above a 10-day average is common.",
            "Combining two signals that may individually carry no edge does not create one; "
            "this project already refuted a four-signal portfolio on those grounds.",
            "Blume/Easley/O'Hara is a theoretical microstructure result, not a documented "
            "trading edge at daily frequency on an index ETF.",
        ],
    },
]


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


def run_one(spec, code_revision, injector):
    slug, strategy = spec["slug"], spec["strategy"]
    signal, left = spec["signal_label"], spec["left"]
    column = strategy["column_name"]
    forward = f"forward_return_{HORIZON}d"
    warmup = strategy["required_inputs"]["warmup_periods"]
    metric = (f"mean_{forward}({left} > {signal}) - mean_{forward}({left} <= {signal})")
    variables = sorted(set(strategy["required_inputs"]["variables"]) | {"close", column, forward})

    paths = {name: ARTIFACTS / f"{name}-{slug}.json" for name in (
        "experiments", "experiment-results", "research-executions", "research-results",
        "dispositions", "walk-forward-partitions", "walk-forward-fold-results",
        "statistical-validations", "knowledge")}
    dataset_dir = ARTIFACTS / "datasets" / slug.replace("-", "_")

    print(f"\n{'=' * 74}\n{slug}   [{spec['direction']}]   warmup={warmup} trading days\n{'=' * 74}")

    print(f"[1/11] capturing {SYMBOL} bars...")
    rows, capture, raw = capture_alpaca_equity_bars(
        symbol=SYMBOL, evaluable_start_utc=EVAL_START, evaluable_end_exclusive_utc=EVAL_END,
        warmup_periods=warmup, horizon=HORIZON, acquired_at=NOW, credential_injector=injector)
    print(f"       {len(rows)} bars ({rows[0]['timestamp'][:10]} -> {rows[-1]['timestamp'][:10]})")

    print("[2/11] constituting hypothesis...")
    hyp = constitute_hypothesis(
        HYPOTHESES,
        description=spec["description"] + "\n\nPre-declared caveats: "
                    + " ".join(f"({i + 1}) {c}" for i, c in enumerate(spec["caveats"])),
        target_metric=metric, expected_direction=spec["direction"],
        constraints={"period": {"start_utc": EVAL_START, "end_exclusive_utc": EVAL_END},
                     "universe": [SYMBOL], "variables": variables},
        acceptance_criterion={"comparison": spec["comparison"],
                              "expected_direction": spec["direction"],
                              "metric": metric, "threshold": "0"},
        creation_timestamp=NOW, status="CONSTITUTED", created_by="Oscar Carmenate Rodriguez",
        provenance=["DISCOVERY|artifacts/live-run-1",
                    "SNAPSHOT_SHA256|a1b6a0bbe47c247c7e5d4adc5ab9c8d578dd0c347d39c4bdb6f6dc2c089ec00f",
                    "KNOWLEDGE|b154409262618e2436ad19dbfdab4da2e43c97678d56ec5192cc992b4c2cd521",
                    f"ANALYSIS|nivel-5-{slug}"],
        code_revision=code_revision, system_version="0.1.0")
    hid, hver, _ = hyp["hypothesis_id"], hyp["version"], hyp["record_id"]
    print(f"       {hid}")

    print("[3/11] sealing dataset...")
    manifest = create_hypothesis_dataset(
        HYPOTHESES, hid, hver, dataset_dir, strategy=strategy, horizon=HORIZON,
        acquired_at=NOW, primary_source={"rows": rows, "capture": capture, "raw": raw})
    did = manifest["dataset_id"]
    print(f"       rows={manifest['rows']}")

    print("[4/11] experiment conditions...")
    exp = constitute_experiment_conditions(
        paths["experiments"], hypothesis_registry_path=HYPOTHESES,
        dataset_directory=dataset_dir, hypothesis_id=hid, hypothesis_version=hver,
        dataset_id=did, created_at=NOW,
        revision_reason=f"First experiment for {slug} (Nivel 5, US equities)",
        strategy=strategy, horizon=HORIZON)
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
    if ev.get("inconclusive_reason"):
        print(f"       inconclusive_reason: {ev['inconclusive_reason']}")

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

    print(f"[9/11] walk-forward partition ({FOLD_COUNT} folds)...")
    part = constitute_walk_forward_partition(
        paths["walk-forward-partitions"], dataset_directory=dataset_dir,
        hypothesis_registry_path=HYPOTHESES, fold_count=FOLD_COUNT, partitioned_at=NOW,
        partition_code_revision=code_revision, strategy=strategy, horizon=HORIZON)

    print(f"[10/11] running {FOLD_COUNT} folds...")
    for index in range(FOLD_COUNT):
        fold = constitute_walk_forward_fold_result(
            paths["walk-forward-fold-results"],
            partition_registry_path=paths["walk-forward-partitions"],
            partition_id=part["partition_id"], partition_record_id=part["record_id"],
            fold_index=index, experiment_registry_path=paths["experiments"],
            experiment_id=eid, experiment_version=ever, experiment_record_id=erec,
            hypothesis_registry_path=HYPOTHESES, dataset_directory=dataset_dir,
            computed_at=NOW, computation_code_revision=code_revision,
            strategy=strategy, horizon=HORIZON)
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
        dataset_directory=dataset_dir, minimum_folds_required=FOLD_COUNT,
        consistency_threshold="0.70", validated_at=NOW,
        validation_code_revision=code_revision, strategy=strategy, horizon=HORIZON)
    passing = sum(1 for s in val.get("fold_summaries", []) if s["criterion_result"] == "MET")
    print(f"        {val['outcome']}  consistency={val['consistency_ratio']}  "
          f"{passing}/{FOLD_COUNT} folds MET")

    interpretation = (
        f"{slug}: SPY 2024, direccion {spec['direction']} pre-declarada. "
        f"Muestra completa metric={_fmt(ev.get('metric'))}, grupos {upper_n}/{lower_n}, "
        f"criterio {spec['comparison']} 0 -> {disp['outcome']}. "
        f"Walk-forward {passing}/{FOLD_COUNT} folds MET, consistencia "
        f"{val['consistency_ratio']}, {val['outcome']} (umbral 70%).\n\n"
        f"Una de las cuatro hipotesis de renta variable declaradas conjuntamente ANTES de "
        f"ejecutar ninguna, para alcanzar el minimo de cinco que la regla de parada sellada "
        f"exige antes de poder declarar agotada la poblacion. La estrategia ya existia en el "
        f"repositorio y la direccion procede de literatura publicada, no de observacion "
        f"previa sobre estos datos.")

    know = constitute_knowledge_record(
        paths["knowledge"], disposition_registry_path=paths["dispositions"],
        disposition_id=disp["disposition_id"], disposition_record_id=disp["record_id"],
        research_result_registry_path=paths["research-results"],
        research_execution_registry_path=paths["research-executions"],
        legacy_result_registry_path=paths["experiment-results"],
        experiment_registry_path=paths["experiments"], hypothesis_registry_path=HYPOTHESES,
        dataset_directory=dataset_dir, interpretation=interpretation,
        limitations=COMMON_LIMITATIONS + spec["caveats"], preserved_at=NOW,
        knowledge_code_revision=code_revision)

    return {"slug": slug, "direction": spec["direction"], "disposition": disp["outcome"],
            "metric": ev.get("metric"), "upper": upper_n, "lower": lower_n,
            "validation": val["outcome"], "consistency": val["consistency_ratio"],
            "passing": passing, "knowledge_id": know["knowledge_id"]}


def main():
    print("=" * 74)
    print("FOUR US-EQUITY HYPOTHESES -- all declared before any execution")
    print("=" * 74)
    revision = _code_revision()
    injector = _credential_injector()
    print(f"code_revision: {revision}")
    for spec in HYPOTHESES_SPEC:
        print(f"  declared: {spec['slug']:24} {spec['direction']:9} {spec['strategy']['column_name']}")

    results, failures = [], []
    for spec in HYPOTHESES_SPEC:
        try:
            results.append(run_one(spec, revision, injector))
        except Exception:
            # One failure must not silently cancel the rest, and must not be
            # quietly retried with different parameters either.
            failures.append(spec["slug"])
            print(f"\n!!! {spec['slug']} FAILED -- recorded, not retried\n")
            traceback.print_exc()

    print("\n" + "=" * 74)
    print("BATCH COMPLETE")
    print("=" * 74)
    for r in results:
        print(f"  {r['slug']:24} {r['direction']:9} {r['disposition']:13} "
              f"metric={_fmt(r['metric'])} n={r['upper']}/{r['lower']} "
              f"{r['validation']} {r['passing']}/{FOLD_COUNT} cons={r['consistency']}")
    if failures:
        print(f"\n  FAILED: {', '.join(failures)}")
    print()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
