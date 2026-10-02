"""Judge a Discovery Finding on the BitMEX holdout, with a test that can be passed.

The companion to run_carry_exit_discovery_bitmex.py, and deliberately a separate
command: the scan looks, this one commits. Nothing here chooses anything -- the
candidate, the multiplicity, the holdout and the fold count all come from the
sealed Finding and the space it was produced in.

  candidate    the scan's rank-0, rebuilt from the sealed record
  correction   Bonferroni over the space's own candidate count
  folds        minimum_folds_for_batch(that count), so the test is passable
  period       the space's declared holdout, which the scan never loaded

WHY THAT LAST LINE MATTERS. The previous attempt at this used 5 folds against a
search of 8 and came back NOT_VALIDATED after passing 5 of 5 -- the smallest
p-value five folds can produce is 0.03125 and the bar was 0.00625, so no outcome
could have cleared it. The fold count is now derived rather than chosen, and
constitute_statistical_validation refuses the combination outright if it is ever
wrong again.

    python3.11 -B scripts/research/run_carry_surge_holdout_bitmex.py --finding FINDING|...
"""

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pipeline as p
from tramitago_quant_core.data.acquisition import normalize, _coinbase_public_request
from tramitago_quant_core.data.bitmex_perpetual_price import (
    capture_bitmex_perpetual_price, verified_bitmex_perpetual_price_capture,
)
from tramitago_quant_core.data.bitmex_funding_rate import (
    capture_bitmex_funding_rate, verified_bitmex_funding_rate_capture,
)
from tramitago_quant_core.research.discovery import (
    load_discovery_scan, load_discovery_space, rank_observations, candidate_strategy,
)
from tramitago_quant_core.research.finding import load_finding, promote_finding
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
    constitute_statistical_validation, minimum_folds_for_batch,
)
from tramitago_quant_core.knowledge.knowledge_record import constitute_knowledge_record

SYMBOL, SPOT, INSTRUMENT, HORIZON = "XBTUSD", "BTC-USD", "BTC-USD", 1
DISCOVERY = REPO / "artifacts" / "discovery"
ARTIFACTS = REPO / "artifacts" / "research"
HYPOTHESES = ARTIFACTS / "hypotheses.json"
SLUG = "carry-exit-bitmex-holdout"
MAX_CANDLES_PER_REQUEST = 300
NOW = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
AUXILIARY_VERIFIERS = {
    "funding_rate": verified_bitmex_funding_rate_capture,
    "perp_close": verified_bitmex_perpetual_price_capture,
}


def _fmt(value):
    return "None" if value is None else f"{value:.6f}"


def _code_revision():
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                          text=True, check=True).stdout.strip()


def _evidence(finding, prefix):
    matches = [value for value in finding["supporting_evidence"] if value.startswith(prefix)]
    if len(matches) != 1:
        raise SystemExit(f"Finding does not name exactly one {prefix}")
    return matches[0]


def _spot_closes(window, warmup):
    start = p.epoch(window["start_utc"]) - warmup * 86400
    end = p.epoch(window["end_exclusive_utc"]) + HORIZON * 86400
    closes, digests = {}, []
    cursor = start
    while cursor < end:
        stop = min(cursor + MAX_CANDLES_PER_REQUEST * 86400, end)
        url = (f"https://api.exchange.coinbase.com/products/{SPOT}/candles"
               f"?granularity=86400&start={p.iso(cursor)}&end={p.iso(stop)}")
        with urlopen(_coinbase_public_request(url), timeout=30) as response:
            raw = response.read()
        digests.append(p.digest(raw))
        rows, report = normalize(raw, cursor, stop, SPOT)
        if report["errors"]:
            raise SystemExit(f"{SPOT} {p.iso(cursor)}: {report['errors'][:2]}")
        for row in rows:
            closes[row["timestamp"]] = row["close"]
        cursor = stop
    return digests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--finding", required=True, help="FINDING|<uuid> from the BitMEX scan")
    arguments = parser.parse_args()
    revision = _code_revision()

    finding = load_finding(DISCOVERY / "findings.json", arguments.finding)
    if finding["status"] != "OPEN":
        raise SystemExit(f"Finding is {finding['status']}, not OPEN; nothing to promote")
    scan = load_discovery_scan(DISCOVERY / "discovery-scans.json",
                               _evidence(finding, "DISCOVERY_SCAN|"))
    space = load_discovery_space(DISCOVERY / "discovery-spaces.json", scan["space_id"])
    rank = int(_evidence(finding, "SELECTED_RANK|").split("|")[1])
    chosen = rank_observations(scan["observations"])[rank]

    batch_size = scan["summary"]["candidates_examined"]
    folds = minimum_folds_for_batch(batch_size)
    period = {"start_utc": space["holdout_window"]["start_utc"],
              "end_exclusive_utc": space["holdout_window"]["end_exclusive_utc"]}
    strategy = candidate_strategy({"strategy_id": chosen["strategy_id"],
                                   "parameters": chosen["parameters"]})

    print("=" * 76)
    print("FINDING -> HYPOTHESIS, judged on the BitMEX holdout")
    print("=" * 76)
    print(f"finding      {finding['finding_id']}  (rank {rank} of {batch_size})")
    print(f"strategy     {strategy['strategy_id']}  {strategy['parameters']}")
    print(f"period       {period['start_utc'][:10]} .. {period['end_exclusive_utc'][:10]}  HOLDOUT")
    print(f"correction   Bonferroni over {batch_size}, alpha {0.05 / batch_size:.5f}, "
          f"{folds} folds -> smallest attainable p {0.5 ** folds:.5f}")

    column = strategy["column_name"]
    forward = strategy["outcome"]["column"](HORIZON)
    warmup = strategy["required_inputs"]["warmup_periods"]
    metric = (f"mean_{forward}({strategy['upper_group_description']}) - "
              f"mean_{forward}({strategy['lower_or_equal_group_description']})")
    variables = sorted(set(strategy["required_inputs"]["variables"])
                       | set(strategy["outcome"]["required_inputs"]["variables"])
                       | {column, forward})
    paths = {name: ARTIFACTS / f"{name}-{SLUG}.json" for name in (
        "experiments", "experiment-results", "research-executions", "research-results",
        "dispositions", "walk-forward-partitions", "walk-forward-fold-results",
        "statistical-validations", "knowledge")}
    dataset_dir = ARTIFACTS / "datasets" / SLUG.replace("-", "_")

    cap_start = p.iso(p.epoch(period["start_utc"]) - warmup * 86400)
    cap_end = p.iso(p.epoch(period["end_exclusive_utc"]) + HORIZON * 86400)
    print(f"\n[1/11] capturing BitMEX and Coinbase ({cap_start[:10]} -> {cap_end[:10]})...")
    perp, perp_capture, perp_raw = capture_bitmex_perpetual_price(
        SYMBOL, cap_start, cap_end, NOW, bin_size="1d")
    funding, funding_capture, funding_raw = capture_bitmex_funding_rate(
        SYMBOL, cap_start, cap_end, NOW)
    assert verified_bitmex_perpetual_price_capture(perp_raw, perp_capture) == perp
    assert verified_bitmex_funding_rate_capture(funding_raw, funding_capture) == funding
    spot_digests = _spot_closes(period, warmup)
    print(f"       perp {len(perp)} | funding {len(funding)} days, both re-verified")
    auxiliary_sources = {
        "funding_rate": {"series": funding, "capture": funding_capture, "raw": funding_raw},
        "perp_close": {"series": perp, "capture": perp_capture, "raw": perp_raw},
    }

    print("[2/11] constituting hypothesis...")
    hypothesis = constitute_hypothesis(
        HYPOTHESES,
        description=(
            f"For a MARKET-NEUTRAL CARRY in BTC -- long {INSTRUMENT} spot, short BitMEX "
            f"{SYMBOL} at equal notional -- the mean forward carry return in the group "
            f"'{strategy['upper_group_description']}' is HIGHER than in "
            f"'{strategy['lower_or_equal_group_description']}'. Direction INCREASE "
            f"pre-declared.\n\n"
            f"PROVENANCE AND CORRECTION: selected as rank {rank} of {batch_size} candidates in "
            f"sealed Discovery scan {scan['scan_id']}, which read "
            f"{space['discovery_window']['start_utc'][:10]} to "
            f"{space['discovery_window']['end_exclusive_utc'][:10]} and nothing else. This "
            f"period is that space's declared holdout. Bonferroni over {batch_size}, and "
            f"{folds} folds so the corrected test is passable at all -- the previous attempt "
            f"used five against a search of eight, where the smallest attainable p-value "
            f"(0.03125) exceeded the bar (0.00625) and no outcome could have passed.\n\n"
            f"FUNDING AT {strategy['parameters'].get('payments_per_period', 1)} PAYMENTS A DAY, "
            f"which is BitMEX's eight-hour settlement. The outcome is the funding received "
            f"MINUS the change in basis: a cash flow plus a spread mark, not a price change.\n\n"
            f"NO LOOKAHEAD: the row at t is classified by funding at t-1 and the outcome reads "
            f"t+1, two periods apart. The {warmup}-day warmup precedes the period and feeds the "
            f"indicator, never the measured outcome."),
        target_metric=metric, expected_direction="INCREASE",
        constraints={"period": period, "universe": [INSTRUMENT], "variables": variables},
        acceptance_criterion={"comparison": "GT", "expected_direction": "INCREASE",
                              "metric": metric, "threshold": "0"},
        creation_timestamp=NOW, status="CONSTITUTED", created_by="Oscar Carmenate Rodriguez",
        provenance=[finding["finding_id"], scan["scan_id"], space["space_id"],
                    f"CANDIDATES_EXAMINED|{batch_size}",
                    "ANALYSIS|carry-exit-bitmex-predeclared-holdout"],
        code_revision=revision, system_version="0.1.0")
    hid, hver = hypothesis["hypothesis_id"], hypothesis["version"]
    print(f"       {hid}")

    print("[3/11] sealing dataset...")
    manifest = create_hypothesis_dataset(
        HYPOTHESES, hid, hver, dataset_dir, strategy=strategy, horizon=HORIZON,
        auxiliary_sources=auxiliary_sources, acquired_at=NOW)
    print(f"       rows={manifest['rows']}  columns={len(manifest['columns'])}")

    print("[4/11] experiment conditions...")
    exp = constitute_experiment_conditions(
        paths["experiments"], hypothesis_registry_path=HYPOTHESES,
        dataset_directory=dataset_dir, hypothesis_id=hid, hypothesis_version=hver,
        dataset_id=manifest["dataset_id"], created_at=NOW,
        revision_reason="Carry exit rule, BitMEX pre-declared holdout",
        strategy=strategy, horizon=HORIZON, auxiliary_verifiers=AUXILIARY_VERIFIERS)
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

    print(f"[9/11] walk-forward partition ({folds} folds)...")
    part = constitute_walk_forward_partition(
        paths["walk-forward-partitions"], dataset_directory=dataset_dir,
        hypothesis_registry_path=HYPOTHESES, fold_count=folds, partitioned_at=NOW,
        partition_code_revision=revision, strategy=strategy, horizon=HORIZON,
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
            strategy=strategy, horizon=HORIZON, auxiliary_verifiers=AUXILIARY_VERIFIERS)
        fe = fold["evaluation"]
        fg = fe.get("groups", {})
        print(f"        fold {index}: {fe['criterion_result']:13} metric={_fmt(fe['metric'])}"
              f"  n={fg.get('upper', {}).get('count')}/"
              f"{fg.get('lower_or_equal', {}).get('count')}")

    print(f"[11/11] statistical validation, Bonferroni over {batch_size}...")
    val = constitute_statistical_validation(
        paths["statistical-validations"],
        partition_registry_path=paths["walk-forward-partitions"],
        partition_id=part["partition_id"], partition_record_id=part["record_id"],
        fold_result_registry_path=paths["walk-forward-fold-results"],
        experiment_registry_path=paths["experiments"], hypothesis_registry_path=HYPOTHESES,
        dataset_directory=dataset_dir, minimum_folds_required=folds,
        consistency_threshold="0.70", validated_at=NOW, validation_code_revision=revision,
        batch_size=batch_size, strategy=strategy, horizon=HORIZON,
        auxiliary_verifiers=AUXILIARY_VERIFIERS)
    passing = sum(1 for s in val.get("fold_summaries", []) if s["criterion_result"] == "MET")
    batch = val.get("batch") or {}
    print(f"        {val['outcome']}  consistency={val['consistency_ratio']}  "
          f"{passing}/{folds} MET")
    print(f"        binomial p={batch.get('binomial_p_value')}  "
          f"corrected alpha={batch.get('corrected_significance_level')}")

    interpretation = (
        f"Regla de salida del carry BTC en BitMEX {SYMBOL}, evaluada sobre un HOLDOUT "
        f"declarado antes del scan. Candidata: {strategy['strategy_id']} "
        f"{strategy['parameters']}, rango {rank} de {batch_size} en el scan {scan['scan_id']}.\n\n"
        f"El test es PASABLE por construccion: {folds} folds, p minimo alcanzable "
        f"{0.5 ** folds:.5f}, barra corregida {0.05 / batch_size:.5f}. El intento anterior "
        f"sobre Hyperliquid uso cinco folds contra una busqueda de ocho y no podia pasar.\n\n"
        f"Resultado: metric={_fmt(ev.get('metric'))}, grupos {upper_n}/{lower_n}, "
        f"{disp['outcome']}. Walk-forward {passing}/{folds} MET, consistencia "
        f"{val['consistency_ratio']}, p binomial {batch.get('binomial_p_value')} contra alfa "
        f"{batch.get('corrected_significance_level')} -> {val['outcome']}.")
    limitations = [
        "Valida la REGLA DE SALIDA, no si la prima compensa el riesgo. Esa es una afirmacion "
        "de nivel, y level_claim ya devolvio NOT_VALIDATED para el carry mantenido de forma "
        "continua en 2019-2021 por romper el limite de drawdown.",
        "Bruto de costes.",
        "Un venue, un instrumento, un horizonte.",
        f"El warmup de {warmup} dias precede al periodo y alimenta el indicador, nunca el "
        f"outcome medido.",
        f"La candidata fue seleccionada como la mejor de {batch_size} sobre la ventana de "
        f"descubrimiento. La correccion por esa seleccion se aplica aqui; no la deshace.",
        "BitMEX XBTUSD es un contrato INVERSO: el P&L en USD de un corto no es lineal en el "
        "precio, y el Outcome lo trata como si lo fuera. Es una aproximacion, no una "
        "identidad.",
    ]

    know = constitute_knowledge_record(
        paths["knowledge"], disposition_registry_path=paths["dispositions"],
        disposition_id=disp["disposition_id"], disposition_record_id=disp["record_id"],
        research_result_registry_path=paths["research-results"],
        research_execution_registry_path=paths["research-executions"],
        legacy_result_registry_path=paths["experiment-results"],
        experiment_registry_path=paths["experiments"], hypothesis_registry_path=HYPOTHESES,
        dataset_directory=dataset_dir, interpretation=interpretation,
        limitations=limitations, preserved_at=NOW, knowledge_code_revision=revision,
        auxiliary_verifiers=AUXILIARY_VERIFIERS)
    print(f"\nknowledge    {know['knowledge_id']}")
    promoted = promote_finding(DISCOVERY / "findings.json", finding["finding_id"],
                               hypothesis_id=hid, at=NOW)
    print(f"finding      {promoted['status']} -> {hid}")
    print(f"spot sha256  {spot_digests}")

    print("\n" + "=" * 76)
    print(f"VERDICT   {val['outcome']}   disposition {disp['outcome']}   "
          f"metric {_fmt(ev.get('metric'))}")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    sys.exit(main())
