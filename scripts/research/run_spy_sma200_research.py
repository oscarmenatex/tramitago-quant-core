"""SPY SMA(200) research pipeline -- Nivel 5 (US equities via Alpaca).

Academic basis: Brock, Lakonishok & LeBaron (1992); Faber (2007).
Instrument: SPY (S&P 500 ETF), 2024 full year, direction INCREASE.

Runs the full 11-step microciclo (Hypothesis -> Dataset -> Experiment ->
Research Execution -> Research Result -> Disposition -> Walk-Forward ->
Statistical Validation -> Knowledge) and seals every artifact under
artifacts/research/.

Requires Python 3.11+ (the whole project does; see deploy/README.md) and
the two Alpaca credentials as ENVIRONMENT VARIABLES -- never in a file:

    export ALPACA_PAPER_API_KEY_ID=...
    export ALPACA_PAPER_API_SECRET_KEY=...
    python3.11 -B scripts/research/run_spy_sma200_research.py
"""

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pipeline as p
from tramitago_quant_core.data.alpaca_equity_series import (
    ALPACA_EQUITY_BARS_SOURCE,
    capture_alpaca_equity_bars,
)
from tramitago_quant_core.research.hypothesis import constitute_hypothesis
from tramitago_quant_core.research.historical_dataset import create_hypothesis_dataset
from tramitago_quant_core.research.experiment import (
    constitute_experiment_conditions,
    execute_experiment_result,
)
from tramitago_quant_core.research.research_execution import (
    constitute_research_execution,
    constitute_research_result,
)
from tramitago_quant_core.strategy_evaluation.disposition import constitute_disposition
from tramitago_quant_core.research.walk_forward import (
    constitute_walk_forward_partition,
    constitute_walk_forward_fold_result,
    constitute_statistical_validation,
)
from tramitago_quant_core.knowledge.knowledge_record import constitute_knowledge_record

# -- CONFIG --------------------------------------------------------------------
ARTIFACTS           = REPO / "artifacts" / "research"
HYPOTHESES          = ARTIFACTS / "hypotheses.json"
DATASET_DIR         = ARTIFACTS / "datasets" / "spy_sma200_2024"

EXPERIMENTS         = ARTIFACTS / "experiments-spy-sma200-2024.json"
EXPERIMENT_RESULTS  = ARTIFACTS / "experiment-results-spy-sma200-2024.json"
RESEARCH_EXECUTIONS = ARTIFACTS / "research-executions-spy-sma200-2024.json"
RESEARCH_RESULTS    = ARTIFACTS / "research-results-spy-sma200-2024.json"
DISPOSITIONS        = ARTIFACTS / "dispositions-spy-sma200-2024.json"
PARTITIONS          = ARTIFACTS / "walk-forward-partitions-spy-sma200-2024.json"
FOLD_RESULTS        = ARTIFACTS / "walk-forward-fold-results-spy-sma200-2024.json"
STATISTICAL_VALS    = ARTIFACTS / "statistical-validations-spy-sma200-2024.json"
KNOWLEDGE           = ARTIFACTS / "knowledge-spy-sma200-2024.json"

SYMBOL          = "SPY"
WINDOW          = 200
HORIZON         = 1
STRATEGY        = p.sma_crossover_strategy(WINDOW)
EVALUABLE_START = "2024-01-01T00:00:00Z"
EVALUABLE_END   = "2025-01-01T00:00:00Z"

# Derived from the Strategy contract rather than written by hand: the column the
# Strategy actually emits ("sma_close_200") and the warmup it actually needs are
# what _hypothesis_dataset_config validates the Hypothesis against.
SIGNAL_COLUMN   = STRATEGY["column_name"]
FORWARD_COLUMN  = f"forward_return_{HORIZON}d"
WARMUP_PERIODS  = STRATEGY["required_inputs"]["warmup_periods"]   # trading days
VARIABLES       = ["close", SIGNAL_COLUMN, FORWARD_COLUMN]
METRIC = (f"mean_{FORWARD_COLUMN}(close_t > {SIGNAL_COLUMN}) - "
          f"mean_{FORWARD_COLUMN}(close_t <= {SIGNAL_COLUMN})")

NOW = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")

LIMITATIONS = [
    "SPY SMA(200) is a widely-known signal -- any edge may be crowded out or arbitraged.",
    "2024 was a strong bull-market year. The INCREASE direction benefits from positive "
    "drift; walk-forward consistency across sub-periods is the real test, not the "
    "full-sample mean.",
    "Daily bars only (IEX feed). No transaction costs (Nivel 4 not built) -- the measured "
    "edge is gross of bid-ask spread and commissions.",
    "Single instrument, single period, single window: one data point on the equity axis.",
    "The 199-day SMA warmup comes from 2023, a year outside the evaluable period.",
]


def _code_revision():
    """Record the exact commit that produced this evidence."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True,
            check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise SystemExit("Cannot determine git HEAD; run this from a git checkout.")


def _alpaca_credential_injector():
    key_id = os.environ.get("ALPACA_PAPER_API_KEY_ID")
    secret = os.environ.get("ALPACA_PAPER_API_SECRET_KEY")
    if not key_id or not secret:
        raise SystemExit(
            "Set ALPACA_PAPER_API_KEY_ID and ALPACA_PAPER_API_SECRET_KEY as environment "
            "variables before running this script. Never put credentials in a file.")

    def injector(headers):
        return {**headers, "APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}

    return injector


def main():
    print("=" * 70)
    print("SPY SMA(200) Research Pipeline -- Nivel 5 (US equities, Alpaca)")
    print("=" * 70)

    code_revision = _code_revision()
    credential_injector = _alpaca_credential_injector()
    print(f"code_revision: {code_revision}")

    # -- STEP 1: capture Alpaca equity bars ------------------------------------
    print(f"\n[1/11] Capturing {SYMBOL} daily bars from Alpaca "
          f"(2024 + {WARMUP_PERIODS} trading-day warmup)...")
    rows, capture, raw_bytes = capture_alpaca_equity_bars(
        symbol=SYMBOL,
        evaluable_start_utc=EVALUABLE_START,
        evaluable_end_exclusive_utc=EVALUABLE_END,
        warmup_periods=WARMUP_PERIODS,
        horizon=HORIZON,
        acquired_at=NOW,
        credential_injector=credential_injector,
    )
    print(f"       {len(rows)} trading-day bars captured "
          f"({rows[0]['timestamp'][:10]} -> {rows[-1]['timestamp'][:10]})")

    # -- STEP 2: constitute hypothesis -----------------------------------------
    print("\n[2/11] Constituting hypothesis...")
    hyp_rec = constitute_hypothesis(
        HYPOTHESES,
        description=(
            "For SPY (S&P 500 ETF), the mean t+1 return when the close is ABOVE its own "
            "trailing 200-day simple moving average is HIGHER than when it is below -- "
            "SMA_CROSSOVER(200) on SPY, 2024 full year. Direction INCREASE pre-declared on "
            "the trend-following prior (Brock, Lakonishok & LeBaron 1992; Faber 2007: "
            "holding only above the 200-day average filters bear markets and improves "
            "risk-adjusted returns). First Nivel 5 Hypothesis of the project (US equities, "
            "a market with overnight gaps, sessions and holidays that crypto does not have). "
            "Pre-declared caveats: (1) SPY SMA(200) is among the most widely known signals "
            "in finance and may be fully arbitraged; (2) 2024 was a strong bull year, so "
            "positive drift favours the INCREASE direction -- walk-forward consistency "
            "across sub-periods, not the full-sample mean, is the real test."
        ),
        target_metric=METRIC,
        expected_direction="INCREASE",
        constraints={
            "period": {
                "start_utc": EVALUABLE_START,
                "end_exclusive_utc": EVALUABLE_END,
            },
            "universe": [SYMBOL],
            "variables": VARIABLES,
        },
        acceptance_criterion={
            "comparison": "GT",
            "expected_direction": "INCREASE",
            "metric": METRIC,
            "threshold": "0",
        },
        creation_timestamp=NOW,
        status="CONSTITUTED",
        created_by="Oscar Carmenate Rodriguez",
        provenance=[
            "DISCOVERY|artifacts/live-run-1",
            "SNAPSHOT_SHA256|a1b6a0bbe47c247c7e5d4adc5ab9c8d578dd0c347d39c4bdb6f6dc2c089ec00f",
            "KNOWLEDGE|b154409262618e2436ad19dbfdab4da2e43c97678d56ec5192cc992b4c2cd521",
            "ANALYSIS|nivel-5-spy-sma200-us-equities-2024",
        ],
        code_revision=code_revision,
        system_version="0.1.0",
    )
    HYP_ID, HYP_VER = hyp_rec["hypothesis_id"], hyp_rec["version"]
    print(f"       hypothesis_id: {HYP_ID}")

    # -- STEP 3: create sealed dataset (equity primary source) -----------------
    # publish() creates DATASET_DIR itself and refuses to write over an existing
    # directory whose contents differ, so pre-creating it would trip that guard.
    print("\n[3/11] Creating sealed hypothesis dataset (Alpaca equity bars)...")
    manifest = create_hypothesis_dataset(
        HYPOTHESES, HYP_ID, HYP_VER, DATASET_DIR,
        strategy=STRATEGY,
        horizon=HORIZON,
        acquired_at=NOW,
        primary_source={
            "rows": rows,
            "capture": capture,
            "raw": raw_bytes,
            "source": ALPACA_EQUITY_BARS_SOURCE,
            "capture_period": capture["capture_period"],
        },
    )
    DATASET_ID = manifest["dataset_id"]
    print(f"       dataset_id: {DATASET_ID}")
    print(f"       rows: {manifest['rows']}")

    # -- STEP 4: experiment conditions ----------------------------------------
    print("\n[4/11] Constituting experiment conditions...")
    exp_rec = constitute_experiment_conditions(
        EXPERIMENTS,
        hypothesis_registry_path=HYPOTHESES,
        dataset_directory=DATASET_DIR,
        hypothesis_id=HYP_ID,
        hypothesis_version=HYP_VER,
        dataset_id=DATASET_ID,
        created_at=NOW,
        revision_reason=(
            "First experiment for SPY SMA(200) hypothesis "
            "(Nivel 5, US equities, Alpaca daily bars, 2024)"
        ),
        strategy=STRATEGY,
        horizon=HORIZON,
    )
    EXP_ID, EXP_VER, EXP_REC_ID = (
        exp_rec["experiment_id"], exp_rec["version"], exp_rec["record_id"])
    print(f"       experiment_id: {EXP_ID}")

    # -- STEP 5: execute experiment -------------------------------------------
    print("\n[5/11] Executing experiment (full sample)...")
    res_rec = execute_experiment_result(
        EXPERIMENT_RESULTS,
        experiment_registry_path=EXPERIMENTS,
        hypothesis_registry_path=HYPOTHESES,
        dataset_directory=DATASET_DIR,
        experiment_id=EXP_ID,
        experiment_version=EXP_VER,
        experiment_record_id=EXP_REC_ID,
        execution_code_revision=code_revision,
    )
    RES_ID, RES_REC_ID = res_rec["result_id"], res_rec["record_id"]
    evaluation = res_rec.get("evaluation", {})
    print(f"       result: {evaluation.get('criterion_result')}  "
          f"metric={evaluation.get('metric')}")

    # -- STEP 6: research execution -------------------------------------------
    print("\n[6/11] Constituting research execution...")
    exec_rec = constitute_research_execution(
        RESEARCH_EXECUTIONS,
        legacy_result_registry_path=EXPERIMENT_RESULTS,
        legacy_result_id=RES_ID,
        legacy_result_record_id=RES_REC_ID,
        experiment_registry_path=EXPERIMENTS,
        hypothesis_registry_path=HYPOTHESES,
        dataset_directory=DATASET_DIR,
        materialized_at=NOW,
        materialization_code_revision=code_revision,
    )
    EXEC_ID, EXEC_REC_ID = exec_rec["execution_id"], exec_rec["record_id"]
    print(f"       execution_id: {EXEC_ID}")

    # -- STEP 7: research result ----------------------------------------------
    print("\n[7/11] Constituting research result...")
    rres_rec = constitute_research_result(
        RESEARCH_RESULTS,
        research_execution_registry_path=RESEARCH_EXECUTIONS,
        research_execution_id=EXEC_ID,
        research_execution_record_id=EXEC_REC_ID,
        legacy_result_registry_path=EXPERIMENT_RESULTS,
        experiment_registry_path=EXPERIMENTS,
        hypothesis_registry_path=HYPOTHESES,
        dataset_directory=DATASET_DIR,
        materialized_at=NOW,
        materialization_code_revision=code_revision,
    )
    RRES_ID, RRES_REC_ID = rres_rec["research_result_id"], rres_rec["record_id"]
    print(f"       research_result_id: {RRES_ID}")

    # -- STEP 8: disposition ---------------------------------------------------
    print("\n[8/11] Constituting disposition...")
    disp_rec = constitute_disposition(
        DISPOSITIONS,
        research_result_registry_path=RESEARCH_RESULTS,
        research_result_id=RRES_ID,
        research_result_record_id=RRES_REC_ID,
        research_execution_registry_path=RESEARCH_EXECUTIONS,
        legacy_result_registry_path=EXPERIMENT_RESULTS,
        experiment_registry_path=EXPERIMENTS,
        hypothesis_registry_path=HYPOTHESES,
        dataset_directory=DATASET_DIR,
        disposed_at=NOW,
        disposition_code_revision=code_revision,
    )
    DISP_ID, DISP_REC_ID = disp_rec["disposition_id"], disp_rec["record_id"]
    print(f"       disposition: {disp_rec['outcome']}")

    # -- STEP 9: walk-forward partition ---------------------------------------
    print("\n[9/11] Constituting walk-forward partition (5 folds)...")
    part_rec = constitute_walk_forward_partition(
        PARTITIONS,
        dataset_directory=DATASET_DIR,
        hypothesis_registry_path=HYPOTHESES,
        fold_count=5,
        partitioned_at=NOW,
        partition_code_revision=code_revision,
        strategy=STRATEGY,
        horizon=HORIZON,
    )
    PART_ID, PART_REC_ID = part_rec["partition_id"], part_rec["record_id"]
    print(f"       partition_id: {PART_ID}")

    # -- STEP 10: walk-forward folds ------------------------------------------
    print("\n[10/11] Running 5 walk-forward folds...")
    for fold_index in range(5):
        fold_rec = constitute_walk_forward_fold_result(
            FOLD_RESULTS,
            partition_registry_path=PARTITIONS,
            partition_id=PART_ID,
            partition_record_id=PART_REC_ID,
            fold_index=fold_index,
            experiment_registry_path=EXPERIMENTS,
            experiment_id=EXP_ID,
            experiment_version=EXP_VER,
            experiment_record_id=EXP_REC_ID,
            hypothesis_registry_path=HYPOTHESES,
            dataset_directory=DATASET_DIR,
            computed_at=NOW,
            computation_code_revision=code_revision,
            strategy=STRATEGY,
            horizon=HORIZON,
        )
        ev = fold_rec["evaluation"]
        print(f"       fold {fold_index}: {ev['criterion_result']}"
              f"  metric={ev['metric']:.6f}"
              f"  {fold_rec['fold_period']['start_utc'][:10]}"
              f" -> {fold_rec['fold_period']['end_exclusive_utc'][:10]}")

    # -- STEP 11: statistical validation --------------------------------------
    print("\n[11/11] Constituting statistical validation...")
    val_rec = constitute_statistical_validation(
        STATISTICAL_VALS,
        partition_registry_path=PARTITIONS,
        partition_id=PART_ID,
        partition_record_id=PART_REC_ID,
        fold_result_registry_path=FOLD_RESULTS,
        experiment_registry_path=EXPERIMENTS,
        hypothesis_registry_path=HYPOTHESES,
        dataset_directory=DATASET_DIR,
        minimum_folds_required=5,
        consistency_threshold="0.70",
        validated_at=NOW,
        validation_code_revision=code_revision,
        strategy=STRATEGY,
        horizon=HORIZON,
    )
    passing = sum(1 for s in val_rec.get("fold_summaries", [])
                  if s["criterion_result"] == "MET")
    print(f"       outcome: {val_rec['outcome']}")
    print(f"       consistency: {val_rec['consistency_ratio']}")
    print(f"       passing_folds: {passing} / 5")

    # -- knowledge record ------------------------------------------------------
    validated = val_rec["outcome"] == "VALIDATED"
    interpretation = (
        f"SMA_CROSSOVER(200) sobre SPY (S&P 500 ETF), ano 2024 completo. Primera hipotesis "
        f"de Nivel 5 del proyecto: renta variable de EE.UU. via Alpaca (barras diarias, feed "
        f"IEX, ajustadas por splits), un mercado con huecos de overnight, sesiones y festivos "
        f"que el crypto no tiene. Direccion INCREASE pre-declarada sobre el prior de Brock, "
        f"Lakonishok & LeBaron (1992) y Faber (2007): mantenerse solo por encima de la media "
        f"de 200 dias filtra mercados bajistas y mejora el retorno ajustado al riesgo.\n\n"
        f"Resultado muestra completa: metric={evaluation.get('metric')}, criterio GT 0 -> "
        f"{disp_rec['outcome']}. Walk-forward independiente (5 folds sobre 2024): "
        f"{passing}/5 folds MET, consistencia {val_rec['consistency_ratio']}, "
        f"{val_rec['outcome']} (umbral 70%).\n\n"
        f"Contexto: 30a hipotesis real del proyecto. El eje Nivel 5 (otro mercado) era el "
        f"ultimo item del catalogo sin explorar; este es su primer dato. "
        + ("Bloqueador 1 RESUELTO: primera hipotesis que supera la barra Fase 0->1."
           if validated else
           "Bloqueador 1 sigue abierto: el eje Nivel 5 no queda cerrado por un solo "
           "resultado, pero este instrumento/senal/periodo concreto queda descartado.")
    )

    print("\n[Knowledge record] Saving...")
    kr_rec = constitute_knowledge_record(
        KNOWLEDGE,
        disposition_registry_path=DISPOSITIONS,
        disposition_id=DISP_ID,
        disposition_record_id=DISP_REC_ID,
        research_result_registry_path=RESEARCH_RESULTS,
        research_execution_registry_path=RESEARCH_EXECUTIONS,
        legacy_result_registry_path=EXPERIMENT_RESULTS,
        experiment_registry_path=EXPERIMENTS,
        hypothesis_registry_path=HYPOTHESES,
        dataset_directory=DATASET_DIR,
        interpretation=interpretation,
        limitations=LIMITATIONS,
        preserved_at=NOW,
        knowledge_code_revision=code_revision,
    )
    print(f"       knowledge_id: {kr_rec['knowledge_id']}")

    print("\n" + "=" * 70)
    print("RESEARCH PIPELINE COMPLETE")
    print("=" * 70)
    print(f"  Hypothesis:          {HYP_ID}")
    print(f"  Disposition:         {disp_rec['outcome']}")
    print(f"  Statistical verdict: {val_rec['outcome']} "
          f"({val_rec['consistency_ratio']} consistency, {passing}/5 folds)")
    print(f"  Knowledge:           {kr_rec['knowledge_id']}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
