"""The Finding promoted: CARRY_FUNDING_SURGE(30) judged on the holdout.

This is the transition Finding -> Hypothesis, and it is the first time this
project has made it. Thirty-nine Hypotheses were constituted and not one carried
a FINDING in its provenance, because until now nothing produced Findings.

WHAT MAKES THIS DIFFERENT FROM THE THIRTY-NINE. The candidate was not invented
and then tested: it was selected as the best of EIGHT in a sealed scan whose
multiplicity travels with it, over a window this Hypothesis does not use. 2024
was the discovery window; 2025 was declared the holdout before the scan ran and
was never loaded by it. So the correction is Bonferroni over 8, not over 1, and
the data is out of sample by construction rather than by promise.

Selecting the best of eight and then testing it as though it were the only idea
anyone had is precisely the laundering the whole separation exists to prevent,
and the only thing that stops it is that the number 8 is sealed into the Finding
and passed to the validation here.

ONE HONEST LEAK, named rather than buried: a 30-day trailing window needs 31 days
of warmup, which come from December 2024 -- inside the discovery window. Those
days feed the INDICATOR, never the measured outcome, which is the same convention
every walk-forward here uses. It is a leak of the signal's starting value, not of
anything the verdict is computed on.

Requires Python 3.11+. No credentials: Hyperliquid and Coinbase are both public.

    python3.11 -B scripts/research/run_carry_surge_holdout.py
"""

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pipeline as p
from tramitago_quant_core.governance.evidence_host import require_evidence_host
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
from tramitago_quant_core.research.finding import load_finding, promote_finding

ARTIFACTS = REPO / "artifacts" / "research"
HYPOTHESES = ARTIFACTS / "hypotheses.json"
FINDINGS = REPO / "artifacts" / "discovery" / "findings.json"
FINDING_ID = "FINDING|ad7ea4e9-be8a-4787-86ba-3b7c0befe0f1"

COIN, INSTRUMENT, HORIZON = "BTC", "BTC-USD", 1
WINDOW, PAYMENTS_PER_DAY = 30, 24
STRATEGY = p.carry_funding_surge_strategy(WINDOW, payments_per_period=PAYMENTS_PER_DAY)
SLUG = "carry-surge-30-btc-2025-holdout"
PERIOD = {"start": "2025-01-01T00:00:00Z", "end": "2026-01-01T00:00:00Z"}
FOLDS = 5

# The multiplicity the Finding carries. Not a guess and not a judgement call:
# this is the number of candidates the sealed scan examined to produce it.
BATCH_SIZE = 8

NOW = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")

LIMITATIONS = [
    "THE TEST WAS UNPASSABLE BY CONSTRUCTION, and that is a property of the design, not "
    "of the result. With 5 folds the smallest attainable binomial p-value is (1/2)^5 = "
    "0.03125, while Bonferroni over 8 candidates sets the bar at 0.05/8 = 0.00625. Every "
    "fold passing still misses it. A test corrected for N needs at least log2(20N) folds "
    "-- 8 for N=8 -- or no outcome can clear it. This was knowable before running and was "
    "not checked.",
    "Validates the EXIT RULE, not whether the premium compensates the risk. That is a "
    "level claim, and level_claim already returned NOT_VALIDATED for the continuously held "
    "carry over 2019-2021 on a drawdown breach.",
    "Gross of transaction costs.",
    "Single venue, single instrument, single year, single horizon.",
    "The 31-day warmup comes from December 2024, inside the discovery window. It feeds the "
    "INDICATOR and never the measured outcome, but it is a leak of the signal's starting "
    "value and is stated rather than buried.",
    "The window was selected as the best of eight on 2024. The correction for that is "
    "applied here; it does not make the selection disappear.",
]
AUXILIARY_VERIFIERS = {
    "funding_rate": verified_hyperliquid_funding_rate_capture,
    "perp_close": verified_hyperliquid_perpetual_price_capture,
}


def _fmt(value):
    return "None" if value is None else f"{value:.6f}"


def _code_revision():
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                          text=True, check=True).stdout.strip()


def main():
    require_evidence_host(REPO)
    revision = _code_revision()
    finding = load_finding(FINDINGS, FINDING_ID)
    if finding["status"] != "OPEN":
        raise SystemExit(f"Finding is {finding['status']}, not OPEN; nothing to promote")
    print("=" * 76)
    print("FINDING -> HYPOTHESIS, judged on the holdout")
    print("=" * 76)
    print(f"finding      {FINDING_ID}")
    print(f"strategy     {STRATEGY['strategy_id']}({WINDOW})  outcome "
          f"{STRATEGY['outcome']['outcome_id']}  funding at {PAYMENTS_PER_DAY}/day")
    print(f"period       {PERIOD['start'][:10]} .. {PERIOD['end'][:10]}  (HOLDOUT, never scanned)")
    print(f"correction   Bonferroni over {BATCH_SIZE} candidates, "
          f"alpha {0.05 / BATCH_SIZE:.5f}")

    column = STRATEGY["column_name"]
    forward = STRATEGY["outcome"]["column"](HORIZON)
    warmup = STRATEGY["required_inputs"]["warmup_periods"]
    metric = (f"mean_{forward}(funding_t-1 > CARRYSURGE{WINDOW}) - "
              f"mean_{forward}(funding_t-1 <= CARRYSURGE{WINDOW})")
    variables = sorted(set(STRATEGY["required_inputs"]["variables"])
                       | set(STRATEGY["outcome"]["required_inputs"]["variables"])
                       | {column, forward})
    paths = {name: ARTIFACTS / f"{name}-{SLUG}.json" for name in (
        "experiments", "experiment-results", "research-executions", "research-results",
        "dispositions", "walk-forward-partitions", "walk-forward-fold-results",
        "statistical-validations", "knowledge")}
    dataset_dir = ARTIFACTS / "datasets" / SLUG.replace("-", "_")

    cap_start = p.iso(p.epoch(PERIOD["start"]) - warmup * 86400)
    cap_end = p.iso(p.epoch(PERIOD["end"]) + HORIZON * 86400)
    print(f"\n[1/11] capturing ({cap_start[:10]} -> {cap_end[:10]}; "
          f"{warmup} warmup days reach into the discovery window, indicator only)...")
    funding, funding_capture, funding_raw = capture_hyperliquid_funding_rate(
        COIN, cap_start, cap_end, NOW)
    perp, perp_capture, perp_raw = capture_hyperliquid_perpetual_price(
        COIN, cap_start, cap_end, NOW, interval="1d")
    assert verified_hyperliquid_funding_rate_capture(funding_raw, funding_capture) == funding
    assert verified_hyperliquid_perpetual_price_capture(perp_raw, perp_capture) == perp
    print(f"       funding {len(funding)} days | perpetual {len(perp)} days, both re-verified")
    auxiliary_sources = {
        "funding_rate": {"series": funding, "capture": funding_capture, "raw": funding_raw},
        "perp_close": {"series": perp, "capture": perp_capture, "raw": perp_raw},
    }

    print("[2/11] constituting hypothesis...")
    hypothesis = constitute_hypothesis(
        HYPOTHESES,
        description=(
            f"For a MARKET-NEUTRAL CARRY in BTC -- long {INSTRUMENT} spot, short the "
            f"Hyperliquid perpetual at equal notional -- the mean forward carry return on "
            f"days when funding stood ABOVE its own {WINDOW}-day trailing average is HIGHER "
            f"than on days when it stood at or below. Direction INCREASE pre-declared.\n\n"
            f"WHY A RELATIVE THRESHOLD AND NOT ZERO. The absolute version (Hypothesis #37) "
            f"classifies by sign, and the inverted group holds 11 of 366 days in 2024 and 18 "
            f"of 365 in 2025, with one walk-forward fold each year containing NONE -- so the "
            f"validation can only answer INSUFFICIENT_EVIDENCE, and more data never fixes it "
            f"because the split is degenerate at any length. funding_rate_surge_strategy "
            f"documented that exact failure for the funding signal in September and fixed it "
            f"by comparing the series to its own history; this carries the fix across to the "
            f"exit rule. Measured split at this window: roughly 135/199 instead of 353/11.\n\n"
            f"THE WINDOW IS A REAL CONCESSION. Zero needed no parameter; a trailing average "
            f"does. What makes it defensible is that comparing a series to its own history is "
            f"a STRUCTURAL choice, and that this window was not picked because it worked: it "
            f"was selected as the best of EIGHT candidates in sealed Discovery scan "
            f"{[e for e in finding['supporting_evidence'] if e.startswith('DISCOVERY_SCAN')][0]}, "
            f"whose multiplicity is carried here and corrected for.\n\n"
            f"THE PERIOD IS THE HOLDOUT. The scan read 2024 and only 2024; 2025 was declared "
            f"its holdout before the scan ran and was never loaded. This evaluation is out of "
            f"sample by construction, not by promise. The one leak, stated: the {warmup}-day "
            f"warmup comes from December 2024 and feeds the INDICATOR, never the measured "
            f"outcome.\n\n"
            f"The outcome is NOT a price change: it is the funding received over the period "
            f"MINUS the change in basis, at {PAYMENTS_PER_DAY} payments a day, which is what "
            f"Hyperliquid actually pays. An earlier run of this family read that series as a "
            f"daily rate when it is an hourly one, understating the funding leg 24-fold.\n\n"
            f"NO LOOKAHEAD: the row at t is classified by funding at t-1 against the {WINDOW} "
            f"days before it, and the outcome reads t+1."),
        target_metric=metric, expected_direction="INCREASE",
        constraints={"period": {"start_utc": PERIOD["start"], "end_exclusive_utc": PERIOD["end"]},
                     "universe": [INSTRUMENT], "variables": variables},
        acceptance_criterion={"comparison": "GT", "expected_direction": "INCREASE",
                              "metric": metric, "threshold": "0"},
        creation_timestamp=NOW, status="CONSTITUTED", created_by="Oscar Carmenate Rodriguez",
        provenance=[FINDING_ID,
                    [e for e in finding["supporting_evidence"]
                     if e.startswith("DISCOVERY_SCAN")][0],
                    [e for e in finding["supporting_evidence"]
                     if e.startswith("DISCOVERY_SPACE")][0],
                    f"CANDIDATES_EXAMINED|{BATCH_SIZE}",
                    "ANALYSIS|carry-exit-relative-threshold-holdout-2025"],
        code_revision=revision, system_version="0.1.0")
    hid, hver = hypothesis["hypothesis_id"], hypothesis["version"]
    print(f"       {hid}")
    print(f"       provenance carries {FINDING_ID}")

    print("[3/11] sealing dataset...")
    manifest = create_hypothesis_dataset(
        HYPOTHESES, hid, hver, dataset_dir, strategy=STRATEGY, horizon=HORIZON,
        auxiliary_sources=auxiliary_sources, acquired_at=NOW)
    print(f"       rows={manifest['rows']}  columns={len(manifest['columns'])}")

    print("[4/11] experiment conditions...")
    exp = constitute_experiment_conditions(
        paths["experiments"], hypothesis_registry_path=HYPOTHESES,
        dataset_directory=dataset_dir, hypothesis_id=hid, hypothesis_version=hver,
        dataset_id=manifest["dataset_id"], created_at=NOW,
        revision_reason=f"Carry exit rule, relative threshold, HOLDOUT 2025",
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
    print(f"       {ev.get('criterion_result')}  metric={_fmt(ev.get('metric'))}  "
          f"n={upper_n}/{lower_n}")

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
        research_execution_id=rexec["execution_id"],
        research_execution_record_id=rexec["record_id"],
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

    print(f"[9/11] walk-forward partition ({FOLDS} folds)...")
    part = constitute_walk_forward_partition(
        paths["walk-forward-partitions"], dataset_directory=dataset_dir,
        hypothesis_registry_path=HYPOTHESES, fold_count=FOLDS, partitioned_at=NOW,
        partition_code_revision=revision, strategy=STRATEGY, horizon=HORIZON,
        auxiliary_verifiers=AUXILIARY_VERIFIERS)

    print(f"[10/11] running {FOLDS} folds...")
    for index in range(FOLDS):
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
              f"  n={fg.get('upper', {}).get('count')}/"
              f"{fg.get('lower_or_equal', {}).get('count')}")

    print(f"[11/11] statistical validation, Bonferroni over {BATCH_SIZE}...")
    val = constitute_statistical_validation(
        paths["statistical-validations"],
        partition_registry_path=paths["walk-forward-partitions"],
        partition_id=part["partition_id"], partition_record_id=part["record_id"],
        fold_result_registry_path=paths["walk-forward-fold-results"],
        experiment_registry_path=paths["experiments"], hypothesis_registry_path=HYPOTHESES,
        dataset_directory=dataset_dir, minimum_folds_required=FOLDS,
        consistency_threshold="0.70", validated_at=NOW, validation_code_revision=revision,
        batch_size=BATCH_SIZE, strategy=STRATEGY, horizon=HORIZON,
        auxiliary_verifiers=AUXILIARY_VERIFIERS)
    passing = sum(1 for s in val.get("fold_summaries", []) if s["criterion_result"] == "MET")
    batch = val.get("batch") or {}
    print(f"        {val['outcome']}  consistency={val['consistency_ratio']}  "
          f"{passing}/{FOLDS} MET")
    print(f"        binomial p={batch.get('binomial_p_value')}  "
          f"corrected alpha={batch.get('corrected_significance_level')}")

    metric_value = ev.get("metric")
    interpretation = (
        f"Regla de salida del carry BTC con UMBRAL RELATIVO, ventana {WINDOW}, evaluada "
        f"sobre el HOLDOUT 2025. Posicion neutral: largo spot, corto perpetuo de "
        f"Hyperliquid, funding a {PAYMENTS_PER_DAY} pagos diarios.\n\n"
        f"PROCEDENCIA: seleccionada como la mejor de {BATCH_SIZE} candidatas en un scan "
        f"sellado de Discovery sobre 2024, y corregida por esa multiplicidad aqui. 2025 se "
        f"declaro holdout antes del scan y el scan nunca lo leyo.\n\n"
        f"Resultado: metric={_fmt(metric_value)}, grupos {upper_n}/{lower_n}, criterio "
        f"GT 0 -> {disp['outcome']}. Walk-forward {passing}/{FOLDS} MET, consistencia "
        f"{val['consistency_ratio']}, p binomial {batch.get('binomial_p_value')} contra "
        f"alfa corregida {batch.get('corrected_significance_level')} -> {val['outcome']}.\n\n"
        f"LIMITES: valida la REGLA DE SALIDA, no si la prima compensa el riesgo -- esa es "
        f"una afirmacion de nivel y vive en level_claim, donde el carry mantenido de forma "
        f"continua ya quedo NOT_VALIDATED por romper el limite de drawdown en 2019-2021. "
        f"Bruto de costes. Un venue, un instrumento, un ano. El warmup de {warmup} dias "
        f"procede de diciembre de 2024, dentro de la ventana de descubrimiento, y alimenta "
        f"el indicador, nunca el outcome medido.")

    know = constitute_knowledge_record(
        paths["knowledge"], disposition_registry_path=paths["dispositions"],
        disposition_id=disp["disposition_id"], disposition_record_id=disp["record_id"],
        research_result_registry_path=paths["research-results"],
        research_execution_registry_path=paths["research-executions"],
        legacy_result_registry_path=paths["experiment-results"],
        experiment_registry_path=paths["experiments"], hypothesis_registry_path=HYPOTHESES,
        dataset_directory=dataset_dir, interpretation=interpretation,
        limitations=LIMITATIONS, preserved_at=NOW, knowledge_code_revision=revision,
        auxiliary_verifiers=AUXILIARY_VERIFIERS)
    print(f"\nknowledge    {know['knowledge_id']}")

    promoted = promote_finding(FINDINGS, FINDING_ID, hypothesis_id=hid, at=NOW)
    print(f"finding      {promoted['status']} -> {hid}")

    print("\n" + "=" * 76)
    print(f"VERDICT   {val['outcome']}   disposition {disp['outcome']}   "
          f"metric {_fmt(metric_value)}")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    sys.exit(main())
