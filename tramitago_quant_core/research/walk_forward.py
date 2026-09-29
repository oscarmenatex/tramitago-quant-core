"""CAP-002 Research -- Walk-Forward (M2.6): partitions the sealed
Hypothesis Dataset into contiguous, non-overlapping folds; executes each
fold's already-sealed Experiment out-of-sample against a fixed baseline
declared before observing results; aggregates into one traceable
verdict (VALIDATED/NOT_VALIDATED/INSUFFICIENT_EVIDENCE), never left
unclassified. Additive to M2.4-T1's single-sample Disposition, never a
replacement.

Moved out of pipeline.py per DOC-014/DOC-015 (adaptado) SS4 (Etapa 4, M4.1
module decomposition). No behavior change.
"""

import json
import math
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from tramitago_quant_core.shared.util import (
    digest, encoded, epoch, iso, _atomic_write, _explicit_utc, _hypothesis_text_is_valid,
    _hypothesis_code_revision_is_valid, _pipeline_source_bytes,
)
from tramitago_quant_core.research.hypothesis import load_hypothesis
from tramitago_quant_core.research.historical_dataset import verified_hypothesis_dataset
from tramitago_quant_core.research.experiment import (
    verified_experiment_conditions, _experiment_id_is_valid, _experiment_result_dataset_rows,
    _experiment_result_summary, EXPERIMENT_RESULT_OUTCOMES, _experiment_result_inputs,
    _verified_experiment_result_inputs,
)


WALK_FORWARD_PARTITION_REGISTRY_SCHEMA_VERSION = "1"
WALK_FORWARD_PARTITION_SCHEMA_VERSION = "1"
WALK_FORWARD_PARTITION_STATUS = "SEALED"
WALK_FORWARD_MINIMUM_FOLDS = 2


def _walk_forward_partition_id_is_valid(value):
    return (isinstance(value, str)
            and bool(re.fullmatch(r"WALK_FORWARD_PARTITION\|[0-9a-f]{64}", value)))


def _walk_forward_partition_reference(manifest):
    return {"dataset_id": manifest["dataset_id"], "dataset_sha256": manifest["dataset_sha256"]}


def _walk_forward_folds(evaluable_period, fold_count):
    """M2.6-T1: an immutable, gapless partition of an already-sealed evaluable
    period. Never touches the dataset itself -- pure arithmetic over its
    already-declared boundaries."""
    start = epoch(evaluable_period["start_utc"])
    end = epoch(evaluable_period["end_exclusive_utc"])
    total_seconds = end - start
    if total_seconds <= 0 or total_seconds % 86400:
        raise ValueError("Walk-forward evaluable period must be a whole number of days")
    total_days = total_seconds // 86400
    if (not isinstance(fold_count, int) or isinstance(fold_count, bool)
            or fold_count < WALK_FORWARD_MINIMUM_FOLDS or total_days % fold_count):
        raise ValueError("Walk-forward fold count must evenly divide the evaluable period")
    fold_days = total_days // fold_count
    folds = []
    for index in range(fold_count):
        fold_start = start + index * fold_days * 86400
        fold_end = fold_start + fold_days * 86400
        folds.append({"start_utc": iso(fold_start), "end_exclusive_utc": iso(fold_end)})
    return folds


def _walk_forward_partition_id(reference, fold_count):
    content = {
        "schema_version": WALK_FORWARD_PARTITION_SCHEMA_VERSION,
        "dataset": reference, "fold_count": fold_count,
    }
    return "WALK_FORWARD_PARTITION|" + digest(encoded(content))


def _walk_forward_partition_materialization(partitioned_at, partition_code_revision):
    if (not _explicit_utc(partitioned_at)
            or not _hypothesis_code_revision_is_valid(partition_code_revision)):
        raise ValueError("Walk-forward partition time and code revision are required")
    source = _pipeline_source_bytes()
    return {
        "partitioned_at": partitioned_at, "partition_code_revision": partition_code_revision,
        "pipeline_sha256": digest(source),
    }


def _walk_forward_partition_content(partition_id, reference, fold_count, folds, materialization):
    return {
        "partition_id": partition_id,
        "schema_version": WALK_FORWARD_PARTITION_SCHEMA_VERSION,
        "reference": reference,
        "fold_count": fold_count,
        "folds": folds,
        "materialization": materialization,
        "status": WALK_FORWARD_PARTITION_STATUS,
    }


def _walk_forward_partition_record(partition_id, reference, fold_count, folds, materialization):
    content = _walk_forward_partition_content(
        partition_id, reference, fold_count, folds, materialization)
    return {**content, "record_id": "WALK_FORWARD_PARTITION_RECORD|" + partition_id + "|"
            + digest(encoded(content))}


def _walk_forward_partition_record_is_valid(record):
    fields = {
        "partition_id", "schema_version", "reference", "fold_count", "folds", "materialization",
        "status", "record_id"}
    if not isinstance(record, dict) or set(record) != fields:
        return False
    reference = record.get("reference")
    folds = record.get("folds")
    fold_count = record.get("fold_count")
    materialization = record.get("materialization")
    if (not _walk_forward_partition_id_is_valid(record.get("partition_id"))
            or record.get("schema_version") != WALK_FORWARD_PARTITION_SCHEMA_VERSION
            or not isinstance(reference, dict) or set(reference) != {"dataset_id", "dataset_sha256"}
            or not isinstance(reference.get("dataset_id"), str)
            or not reference["dataset_id"].startswith("HISTORICAL_HYPOTHESIS_DATASET|")
            or not re.fullmatch(r"[0-9a-f]{64}", reference.get("dataset_sha256", ""))
            or not isinstance(fold_count, int) or isinstance(fold_count, bool)
            or fold_count < WALK_FORWARD_MINIMUM_FOLDS
            or record["partition_id"] != _walk_forward_partition_id(reference, fold_count)
            or not isinstance(folds, list) or len(folds) != fold_count
            or not isinstance(materialization, dict) or set(materialization) != {
                "partitioned_at", "partition_code_revision", "pipeline_sha256"}
            or not _explicit_utc(materialization.get("partitioned_at"))
            or not _hypothesis_code_revision_is_valid(
                materialization.get("partition_code_revision"))
            or not re.fullmatch(r"[0-9a-f]{64}", materialization.get("pipeline_sha256", ""))
            or record.get("status") != WALK_FORWARD_PARTITION_STATUS):
        return False
    for index, fold in enumerate(folds):
        if (not isinstance(fold, dict) or set(fold) != {"start_utc", "end_exclusive_utc"}
                or not _explicit_utc(fold.get("start_utc"))
                or not _explicit_utc(fold.get("end_exclusive_utc"))
                or epoch(fold["start_utc"]) >= epoch(fold["end_exclusive_utc"])):
            return False
        if index > 0 and fold["start_utc"] != folds[index - 1]["end_exclusive_utc"]:
            return False
    expected = _walk_forward_partition_record(
        record["partition_id"], reference, fold_count, folds, materialization)
    return record == expected


def _walk_forward_partition_registry_is_valid(registry):
    if (not isinstance(registry, dict) or set(registry) != {"schema_version", "partitions"}
            or registry.get("schema_version") != WALK_FORWARD_PARTITION_REGISTRY_SCHEMA_VERSION
            or not isinstance(registry.get("partitions"), list)
            or not all(_walk_forward_partition_record_is_valid(item)
                      for item in registry["partitions"])):
        return False
    identifiers = [item["partition_id"] for item in registry["partitions"]]
    return len(identifiers) == len(set(identifiers))


def _load_walk_forward_partition_registry(registry_path):
    try:
        registry = json.loads(Path(registry_path).read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Persisted Walk-Forward Partition registry cannot be read") from error
    if not _walk_forward_partition_registry_is_valid(registry):
        raise ValueError("Persisted Walk-Forward Partition registry is invalid")
    return registry


def _persist_walk_forward_partition(registry_path, record):
    if not _walk_forward_partition_record_is_valid(record):
        raise ValueError("Walk-Forward Partition record is invalid")
    path = Path(registry_path)
    registry = (_load_walk_forward_partition_registry(path) if path.exists() else {
        "schema_version": WALK_FORWARD_PARTITION_REGISTRY_SCHEMA_VERSION, "partitions": []})
    matches = [item for item in registry["partitions"]
               if item["partition_id"] == record["partition_id"]]
    if matches:
        if len(matches) == 1 and matches[0] == record:
            return matches[0]
        raise ValueError("Walk-Forward Partition identity already exists with different content")
    registry["partitions"].append(record)
    if not _walk_forward_partition_registry_is_valid(registry):
        raise ValueError("Constructed Walk-Forward Partition registry is invalid")
    _atomic_write(path, encoded(registry))
    return record


def constitute_walk_forward_partition(registry_path, *, dataset_directory,
                                      hypothesis_registry_path, fold_count, partitioned_at,
                                      partition_code_revision, strategy=None, horizon=1,
                                      auxiliary_verifiers=None):
    """M2.6-T1: seal an immutable walk-forward partition of a sealed Hypothesis
    Dataset. Never mutates the dataset and never redefines the Hypothesis's
    acceptance criterion (HY-AC-002) -- folds are pure boundaries.

    Etapa 2.7 (M2.7-T4): `strategy` defaults to sma_crossover_strategy(3),
    reproducing the exact M2.6 behavior every already-sealed real Partition
    (BTC-USD, ETH-USD) was built with. A Partition over a dataset built with
    a different Strategy must pass that same Strategy explicitly.
    `horizon` likewise defaults to 1, the forward-return distance every
    already-sealed real Partition was built with. Etapa 2.8:
    `auxiliary_verifiers` is required when the Strategy needs a variable
    outside the base OHLCV set (see verified_hypothesis_dataset).
    """
    manifest = verified_hypothesis_dataset(
        dataset_directory, hypothesis_registry_path, strategy, horizon, auxiliary_verifiers)
    reference = _walk_forward_partition_reference(manifest)
    folds = _walk_forward_folds(manifest["config"]["evaluable_period"], fold_count)
    partition_id = _walk_forward_partition_id(reference, fold_count)
    path = Path(registry_path)
    if path.exists():
        registry = _load_walk_forward_partition_registry(path)
        matches = [item for item in registry["partitions"]
                   if item["partition_id"] == partition_id]
        if matches:
            if len(matches) != 1:
                raise ValueError("Walk-Forward Partition identity is ambiguous")
            return verified_walk_forward_partition(
                registry_path, partition_id, dataset_directory=dataset_directory,
                hypothesis_registry_path=hypothesis_registry_path, strategy=strategy,
                horizon=horizon, auxiliary_verifiers=auxiliary_verifiers)
    record = _walk_forward_partition_record(
        partition_id, reference, fold_count, folds,
        _walk_forward_partition_materialization(partitioned_at, partition_code_revision))
    return _persist_walk_forward_partition(registry_path, record)


def load_walk_forward_partition(registry_path, partition_id):
    if not _walk_forward_partition_id_is_valid(partition_id):
        raise ValueError("A valid Walk-Forward Partition identity is required")
    registry = _load_walk_forward_partition_registry(registry_path)
    matches = [item for item in registry["partitions"] if item["partition_id"] == partition_id]
    if len(matches) != 1:
        raise ValueError("Walk-Forward Partition is not registered unambiguously")
    return matches[0]


def verified_walk_forward_partition(registry_path, partition_id, *, dataset_directory,
                                    hypothesis_registry_path, strategy=None, horizon=1,
                                    auxiliary_verifiers=None):
    """Reload a Walk-Forward Partition and reproduce its folds from the sealed dataset."""
    record = load_walk_forward_partition(registry_path, partition_id)
    manifest = verified_hypothesis_dataset(
        dataset_directory, hypothesis_registry_path, strategy, horizon, auxiliary_verifiers)
    reference = _walk_forward_partition_reference(manifest)
    folds = _walk_forward_folds(manifest["config"]["evaluable_period"], record["fold_count"])
    expected = _walk_forward_partition_record(
        record["partition_id"], reference, record["fold_count"], folds,
        record["materialization"])
    if record != expected:
        raise ValueError("Walk-Forward Partition folds or dataset reference is invalid")
    return record


WALK_FORWARD_FOLD_RESULT_REGISTRY_SCHEMA_VERSION = "1"
WALK_FORWARD_FOLD_RESULT_SCHEMA_VERSION = "1"
WALK_FORWARD_FOLD_RESULT_STATUS = "COMPUTED"
WALK_FORWARD_BASELINE_RULE = "BUY_AND_HOLD_MEAN_FORWARD_RETURN_1D"


def _walk_forward_fold_result_id_is_valid(value):
    return (isinstance(value, str)
            and bool(re.fullmatch(r"WALK_FORWARD_FOLD_RESULT\|[0-9a-f]{64}", value)))


def _walk_forward_fold_reference(partition, experiment):
    return {
        "partition": {
            "partition_id": partition["partition_id"], "record_id": partition["record_id"]},
        "experiment": {
            "experiment_id": experiment["experiment_id"], "version": experiment["version"],
            "record_id": experiment["record_id"]},
    }


def _walk_forward_fold_id(reference, fold_index):
    content = {
        "schema_version": WALK_FORWARD_FOLD_RESULT_SCHEMA_VERSION,
        "partition": reference["partition"], "experiment": reference["experiment"],
        "fold_index": fold_index,
    }
    return "WALK_FORWARD_FOLD_RESULT|" + digest(encoded(content))


def _walk_forward_fold_evidence(dataset_directory, conditions, fold_period):
    all_evidence = _experiment_result_dataset_rows(dataset_directory, conditions)
    start = epoch(fold_period["start_utc"])
    end = epoch(fold_period["end_exclusive_utc"])
    return [item for item in all_evidence if start <= epoch(item["timestamp"]) < end]


RISK_ANALYTICS_RULE = "SMA_CROSSOVER_LONG_ONLY_DESCRIPTIVE"
RISK_ANALYTICS_PERIODS_PER_YEAR = 365


_WALK_FORWARD_LONG_GROUP_BY_DIRECTION = {"INCREASE": "UPPER", "DECREASE": "LOWER_OR_EQUAL"}
_WALK_FORWARD_LONG_GROUP_SUMMARY_KEY = {"UPPER": "upper", "LOWER_OR_EQUAL": "lower_or_equal"}


def _walk_forward_long_group(criterion):
    """Reversal extension (2026-09-29): which group is the Hypothesis's own
    implied long position -- UPPER for an INCREASE-direction criterion (the
    only shape every already-sealed real Hypothesis used), LOWER_OR_EQUAL
    for a DECREASE-direction one (e.g. a short-term-reversal Hypothesis).
    Derived from the criterion, never hardcoded to UPPER."""
    return _WALK_FORWARD_LONG_GROUP_BY_DIRECTION[criterion["expected_direction"]]


def _risk_analytics_strategy_returns(evidence, long_group="UPPER"):
    """Daily returns a long-only implementation of the Hypothesis's own
    criterion would have actually captured: long (captures
    return_t_plus_1) while group == long_group, flat (0 return) otherwise.

    M2.6-T4 (Risk Analytics, extension of M2.6): purely descriptive --
    reports the risk side of DOC-003 §8's "Retorno esperado / Riesgo
    asumido" relationship. Never changes the Hypothesis's acceptance
    criterion, never gates the VALIDATED/NOT_VALIDATED/
    INSUFFICIENT_EVIDENCE verdict, never blocks or authorizes anything --
    that is Risk Control (Etapa 4.5), a distinct, unbuilt capability that
    requires real capital and a Broker Adapter. This requires neither.

    Reversal extension (2026-09-29): `long_group` defaults to "UPPER" --
    the group every already-sealed real fold's criterion (always
    INCREASE-direction) implies -- so this reproduces every one of them
    unchanged. A DECREASE-direction criterion passes "LOWER_OR_EQUAL" via
    _walk_forward_long_group instead.
    """
    return [item["return_t_plus_1"] if item["group"] == long_group else 0.0
            for item in evidence]


def _risk_analytics_sharpe_ratio(returns, periods_per_year=RISK_ANALYTICS_PERIODS_PER_YEAR):
    """Annualized Sharpe ratio assuming a zero risk-free rate (no natural
    risk-free benchmark declared for a daily BTC-USD series). None when
    there is no variance to normalize by."""
    if not returns:
        return None
    mean_return = math.fsum(returns) / len(returns)
    variance = math.fsum((r - mean_return) ** 2 for r in returns) / len(returns)
    std_dev = math.sqrt(variance)
    if std_dev == 0:
        return None
    return (mean_return / std_dev) * math.sqrt(periods_per_year)


def _risk_analytics_annualized_volatility(returns, periods_per_year=RISK_ANALYTICS_PERIODS_PER_YEAR):
    if not returns:
        return None
    mean_return = math.fsum(returns) / len(returns)
    variance = math.fsum((r - mean_return) ** 2 for r in returns) / len(returns)
    return math.sqrt(variance) * math.sqrt(periods_per_year)


def _risk_analytics_max_drawdown(returns):
    """Maximum peak-to-trough decline of the cumulative equity curve
    implied by `returns`, starting at 1.0. 0.0 when the curve never
    declines (including an empty series)."""
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for r in returns:
        equity *= (1.0 + r)
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
    return max_drawdown


def _risk_analytics_summary(evidence, long_group="UPPER"):
    returns = _risk_analytics_strategy_returns(evidence, long_group)
    return {
        "rule": RISK_ANALYTICS_RULE,
        "sharpe_ratio": _risk_analytics_sharpe_ratio(returns),
        "annualized_volatility": _risk_analytics_annualized_volatility(returns),
        "max_drawdown": _risk_analytics_max_drawdown(returns),
    }


def _walk_forward_fold_evaluation(fold_period, evidence, criterion, analytical_rule):
    """Apply the experiment's own sealed criterion to one fold, plus the fixed
    baseline rule declared once in code -- never chosen after seeing results."""
    summary = _experiment_result_summary(evidence, criterion, analytical_rule)
    returns = [item["return_t_plus_1"] for item in evidence]
    baseline_mean = math.fsum(returns) / len(returns) if returns else None
    long_group = _walk_forward_long_group(criterion)
    long_group_mean = summary["groups"][
        _WALK_FORWARD_LONG_GROUP_SUMMARY_KEY[long_group]]["mean_return_t_plus_1"]
    beats_baseline = (long_group_mean > baseline_mean
                      if baseline_mean is not None and long_group_mean is not None else None)
    return {
        "period": fold_period,
        "observation_count": len(evidence),
        **summary,
        "baseline": {"rule": WALK_FORWARD_BASELINE_RULE, "mean_return_t_plus_1": baseline_mean},
        "beats_baseline": beats_baseline,
        "risk_analytics": _risk_analytics_summary(evidence, long_group),
    }


def _walk_forward_fold_materialization(computed_at, computation_code_revision):
    if (not _explicit_utc(computed_at)
            or not _hypothesis_code_revision_is_valid(computation_code_revision)):
        raise ValueError("Walk-forward fold computation time and code revision are required")
    source = _pipeline_source_bytes()
    return {
        "computed_at": computed_at, "computation_code_revision": computation_code_revision,
        "pipeline_sha256": digest(source),
    }


def _walk_forward_fold_result_content(fold_result_id, reference, fold_index, fold_period, inputs,
                                      evaluation, materialization):
    return {
        "fold_result_id": fold_result_id,
        "schema_version": WALK_FORWARD_FOLD_RESULT_SCHEMA_VERSION,
        "reference": reference,
        "fold_index": fold_index,
        "fold_period": fold_period,
        "inputs": inputs,
        "evaluation": evaluation,
        "materialization": materialization,
        "status": WALK_FORWARD_FOLD_RESULT_STATUS,
    }


def _walk_forward_fold_result_record(fold_result_id, reference, fold_index, fold_period, inputs,
                                     evaluation, materialization):
    content = _walk_forward_fold_result_content(
        fold_result_id, reference, fold_index, fold_period, inputs, evaluation, materialization)
    return {**content, "record_id": "WALK_FORWARD_FOLD_RESULT_RECORD|" + fold_result_id + "|"
            + digest(encoded(content))}


def _walk_forward_fold_result_record_is_valid(record):
    fields = {
        "fold_result_id", "schema_version", "reference", "fold_index", "fold_period", "inputs",
        "evaluation", "materialization", "status", "record_id"}
    if not isinstance(record, dict) or set(record) != fields:
        return False
    reference = record.get("reference")
    fold_period = record.get("fold_period")
    evaluation = record.get("evaluation")
    materialization = record.get("materialization")
    if (not _walk_forward_fold_result_id_is_valid(record.get("fold_result_id"))
            or record.get("schema_version") != WALK_FORWARD_FOLD_RESULT_SCHEMA_VERSION
            or not isinstance(reference, dict) or set(reference) != {"partition", "experiment"}
            or not isinstance(reference["partition"], dict)
            or set(reference["partition"]) != {"partition_id", "record_id"}
            or not _walk_forward_partition_id_is_valid(reference["partition"].get("partition_id"))
            or not _hypothesis_text_is_valid(reference["partition"].get("record_id"))
            or not isinstance(reference["experiment"], dict)
            or set(reference["experiment"]) != {"experiment_id", "version", "record_id"}
            or not _experiment_id_is_valid(reference["experiment"].get("experiment_id"))
            or not isinstance(reference["experiment"].get("version"), int)
            or not _hypothesis_text_is_valid(reference["experiment"].get("record_id"))
            or not isinstance(record.get("fold_index"), int)
            or isinstance(record.get("fold_index"), bool) or record["fold_index"] < 0
            or record["fold_result_id"] != _walk_forward_fold_id(reference, record["fold_index"])
            or not isinstance(fold_period, dict) or set(fold_period) != {
                "start_utc", "end_exclusive_utc"}
            or not _explicit_utc(fold_period.get("start_utc"))
            or not _explicit_utc(fold_period.get("end_exclusive_utc"))
            or not isinstance(record.get("inputs"), dict)
            or not isinstance(evaluation, dict) or evaluation.get("period") != fold_period
            or evaluation.get("criterion_result") not in EXPERIMENT_RESULT_OUTCOMES
            or not isinstance(evaluation.get("baseline"), dict)
            or evaluation["baseline"].get("rule") != WALK_FORWARD_BASELINE_RULE
            or not isinstance(evaluation.get("risk_analytics"), dict)
            or evaluation["risk_analytics"].get("rule") != RISK_ANALYTICS_RULE
            or not isinstance(materialization, dict) or set(materialization) != {
                "computed_at", "computation_code_revision", "pipeline_sha256"}
            or not _explicit_utc(materialization.get("computed_at"))
            or not _hypothesis_code_revision_is_valid(
                materialization.get("computation_code_revision"))
            or not re.fullmatch(r"[0-9a-f]{64}", materialization.get("pipeline_sha256", ""))
            or record.get("status") != WALK_FORWARD_FOLD_RESULT_STATUS):
        return False
    expected = _walk_forward_fold_result_record(
        record["fold_result_id"], reference, record["fold_index"], fold_period,
        record["inputs"], evaluation, materialization)
    return record == expected


def _walk_forward_fold_result_registry_is_valid(registry):
    if (not isinstance(registry, dict)
            or set(registry) != {"schema_version", "fold_results"}
            or registry.get("schema_version") != WALK_FORWARD_FOLD_RESULT_REGISTRY_SCHEMA_VERSION
            or not isinstance(registry.get("fold_results"), list)
            or not all(_walk_forward_fold_result_record_is_valid(item)
                      for item in registry["fold_results"])):
        return False
    identifiers = [item["fold_result_id"] for item in registry["fold_results"]]
    return len(identifiers) == len(set(identifiers))


def _load_walk_forward_fold_result_registry(registry_path):
    try:
        registry = json.loads(Path(registry_path).read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Persisted Walk-Forward Fold Result registry cannot be read") from error
    if not _walk_forward_fold_result_registry_is_valid(registry):
        raise ValueError("Persisted Walk-Forward Fold Result registry is invalid")
    return registry


def _persist_walk_forward_fold_result(registry_path, record):
    if not _walk_forward_fold_result_record_is_valid(record):
        raise ValueError("Walk-Forward Fold Result record is invalid")
    path = Path(registry_path)
    registry = (_load_walk_forward_fold_result_registry(path) if path.exists() else {
        "schema_version": WALK_FORWARD_FOLD_RESULT_REGISTRY_SCHEMA_VERSION, "fold_results": []})
    matches = [item for item in registry["fold_results"]
               if item["fold_result_id"] == record["fold_result_id"]]
    if matches:
        if len(matches) == 1 and matches[0] == record:
            return matches[0]
        raise ValueError("Walk-Forward Fold Result identity already exists with different content")
    registry["fold_results"].append(record)
    if not _walk_forward_fold_result_registry_is_valid(registry):
        raise ValueError("Constructed Walk-Forward Fold Result registry is invalid")
    _atomic_write(path, encoded(registry))
    return record


def constitute_walk_forward_fold_result(registry_path, *, partition_registry_path, partition_id,
                                        partition_record_id, fold_index, experiment_registry_path,
                                        experiment_id, experiment_version, experiment_record_id,
                                        hypothesis_registry_path, dataset_directory, computed_at,
                                        computation_code_revision, strategy=None, horizon=1,
                                        auxiliary_verifiers=None):
    """M2.6-T2: execute one walk-forward fold against its declared baseline.

    Reuses the already-sealed Experiment (M2.2-T2) unmodified -- never
    redefines its criterion or period. The baseline rule
    (BUY_AND_HOLD_MEAN_FORWARD_RETURN_1D) is fixed in code, applied
    identically to every fold, declared before any fold is computed.

    Etapa 2.7 (M2.7-T4): `strategy`/`horizon` are only needed to verify the
    Partition itself (see verified_walk_forward_partition); the Experiment/
    dataset resolution below already reconstructs its own Strategy and
    return horizon from the Experiment's sealed conditions (M4.1 production
    wiring). Etapa 2.8: `auxiliary_verifiers` is needed by both.
    """
    partition = verified_walk_forward_partition(
        partition_registry_path, partition_id, dataset_directory=dataset_directory,
        hypothesis_registry_path=hypothesis_registry_path, strategy=strategy, horizon=horizon,
        auxiliary_verifiers=auxiliary_verifiers)
    if partition["record_id"] != partition_record_id:
        raise ValueError("Walk-Forward Partition seal does not match the requested fold result")
    if (not isinstance(fold_index, int) or isinstance(fold_index, bool)
            or not 0 <= fold_index < partition["fold_count"]):
        raise ValueError("Fold index is out of range for this Walk-Forward Partition")
    fold_period = partition["folds"][fold_index]

    experiment, hypothesis, dataset, _ = _verified_experiment_result_inputs(
        experiment_registry_path, hypothesis_registry_path, dataset_directory, experiment_id,
        experiment_version, experiment_record_id, auxiliary_verifiers)

    evidence = _walk_forward_fold_evidence(dataset_directory, experiment["conditions"], fold_period)
    evaluation = _walk_forward_fold_evaluation(
        fold_period, evidence, experiment["conditions"]["acceptance_criterion"],
        experiment["conditions"]["analytical_rule"])

    reference = _walk_forward_fold_reference(partition, experiment)
    fold_result_id = _walk_forward_fold_id(reference, fold_index)
    path = Path(registry_path)
    if path.exists():
        registry = _load_walk_forward_fold_result_registry(path)
        matches = [item for item in registry["fold_results"]
                   if item["fold_result_id"] == fold_result_id]
        if matches:
            if len(matches) != 1:
                raise ValueError("Walk-Forward Fold Result identity is ambiguous")
            return verified_walk_forward_fold_result(
                registry_path, fold_result_id, partition_registry_path=partition_registry_path,
                experiment_registry_path=experiment_registry_path,
                hypothesis_registry_path=hypothesis_registry_path,
                dataset_directory=dataset_directory, strategy=strategy, horizon=horizon,
                auxiliary_verifiers=auxiliary_verifiers)
    record = _walk_forward_fold_result_record(
        fold_result_id, reference, fold_index, fold_period,
        _experiment_result_inputs(experiment, hypothesis, dataset, dataset_directory), evaluation,
        _walk_forward_fold_materialization(computed_at, computation_code_revision))
    return _persist_walk_forward_fold_result(registry_path, record)


def load_walk_forward_fold_result(registry_path, fold_result_id):
    if not _walk_forward_fold_result_id_is_valid(fold_result_id):
        raise ValueError("A valid Walk-Forward Fold Result identity is required")
    registry = _load_walk_forward_fold_result_registry(registry_path)
    matches = [item for item in registry["fold_results"]
               if item["fold_result_id"] == fold_result_id]
    if len(matches) != 1:
        raise ValueError("Walk-Forward Fold Result is not registered unambiguously")
    return matches[0]


def verified_walk_forward_fold_result(registry_path, fold_result_id, *, partition_registry_path,
                                      experiment_registry_path, hypothesis_registry_path,
                                      dataset_directory, strategy=None, horizon=1,
                                      auxiliary_verifiers=None):
    """Reload a fold result and reproduce its evidence and baseline from the
    sealed partition, experiment, and dataset."""
    record = load_walk_forward_fold_result(registry_path, fold_result_id)
    partition_reference = record["reference"]["partition"]
    partition = verified_walk_forward_partition(
        partition_registry_path, partition_reference["partition_id"],
        dataset_directory=dataset_directory, hypothesis_registry_path=hypothesis_registry_path,
        strategy=strategy, horizon=horizon, auxiliary_verifiers=auxiliary_verifiers)
    if partition["record_id"] != partition_reference["record_id"]:
        raise ValueError("Walk-Forward Fold Result partition reference is invalid")
    experiment_reference = record["reference"]["experiment"]
    experiment, hypothesis, dataset, _ = _verified_experiment_result_inputs(
        experiment_registry_path, hypothesis_registry_path, dataset_directory,
        experiment_reference["experiment_id"], experiment_reference["version"],
        experiment_reference["record_id"], auxiliary_verifiers)
    fold_period = partition["folds"][record["fold_index"]]
    if fold_period != record["fold_period"]:
        raise ValueError("Walk-Forward Fold Result fold period is invalid")
    evidence = _walk_forward_fold_evidence(dataset_directory, experiment["conditions"], fold_period)
    evaluation = _walk_forward_fold_evaluation(
        fold_period, evidence, experiment["conditions"]["acceptance_criterion"],
        experiment["conditions"]["analytical_rule"])
    reference = _walk_forward_fold_reference(partition, experiment)
    expected = _walk_forward_fold_result_record(
        record["fold_result_id"], reference, record["fold_index"], fold_period,
        _experiment_result_inputs(experiment, hypothesis, dataset, dataset_directory), evaluation,
        record["materialization"])
    if record != expected:
        raise ValueError("Walk-Forward Fold Result evidence or baseline is invalid")
    return record


STATISTICAL_VALIDATION_REGISTRY_SCHEMA_VERSION = "1"
STATISTICAL_VALIDATION_SCHEMA_VERSION = "1"
STATISTICAL_VALIDATION_STATUS = "ISSUED"
STATISTICAL_VALIDATION_OUTCOMES = {"VALIDATED", "NOT_VALIDATED", "INSUFFICIENT_EVIDENCE"}


def _statistical_validation_id_is_valid(value):
    return (isinstance(value, str)
            and bool(re.fullmatch(r"STATISTICAL_VALIDATION\|[0-9a-f]{64}", value)))


def _statistical_validation_reference(partition, fold_results):
    return {
        "partition": {"partition_id": partition["partition_id"], "record_id": partition["record_id"]},
        "fold_results": [
            {"fold_result_id": item["fold_result_id"], "record_id": item["record_id"]}
            for item in fold_results],
    }


STATISTICAL_VALIDATION_BASE_SIGNIFICANCE_LEVEL = "0.05"


def _statistical_validation_id(reference, minimum_folds_required, consistency_threshold,
                               batch_size=None):
    content = {
        "schema_version": STATISTICAL_VALIDATION_SCHEMA_VERSION,
        "reference": reference,
        "minimum_folds_required": minimum_folds_required,
        "consistency_threshold": consistency_threshold,
    }
    if batch_size is not None:
        content["batch_size"] = batch_size
    return "STATISTICAL_VALIDATION|" + digest(encoded(content))


def _statistical_validation_fold_summary(fold_result):
    evaluation = fold_result["evaluation"]
    return {
        "fold_index": fold_result["fold_index"],
        "period": fold_result["fold_period"],
        "criterion_result": evaluation["criterion_result"],
        "metric": evaluation["metric"],
        "beats_baseline": evaluation["beats_baseline"],
        "risk_analytics": evaluation["risk_analytics"],
    }


def _statistical_validation_sensitivity(fold_summaries):
    """Descriptive spread across folds (DOC-004 §8 'análisis de sensibilidad')
    -- reported alongside the verdict, never gates it."""
    metrics = [item["metric"] for item in fold_summaries if item["metric"] is not None]
    if not metrics:
        return {"fold_metric_min": None, "fold_metric_max": None, "fold_metric_mean": None}
    return {
        "fold_metric_min": min(metrics), "fold_metric_max": max(metrics),
        "fold_metric_mean": math.fsum(metrics) / len(metrics),
    }


def _statistical_validation_risk_analytics_summary(fold_summaries):
    """M2.6-T4 (Risk Analytics, extension of M2.6): descriptive aggregate of
    the risk side of DOC-003 §8's Retorno/Riesgo relationship across folds
    -- reported alongside the verdict, exactly like sensitivity, never
    gates VALIDATED/NOT_VALIDATED/INSUFFICIENT_EVIDENCE. Distinct from Risk
    Control (Etapa 4.5): purely descriptive, never blocks or authorizes
    anything."""
    sharpes = [item["risk_analytics"]["sharpe_ratio"] for item in fold_summaries
               if item["risk_analytics"]["sharpe_ratio"] is not None]
    drawdowns = [item["risk_analytics"]["max_drawdown"] for item in fold_summaries]
    return {
        "sharpe_ratio_min": min(sharpes) if sharpes else None,
        "sharpe_ratio_max": max(sharpes) if sharpes else None,
        "sharpe_ratio_mean": math.fsum(sharpes) / len(sharpes) if sharpes else None,
        "max_drawdown_worst": max(drawdowns) if drawdowns else None,
    }


def _statistical_validation_binomial_p_value(passing, usable_count):
    """Exact one-sided binomial test p-value under H0 (no real edge: each
    usable fold's pass/fail is a fair coin flip, p=0.5). Etapa 2.7, M2.7-T2
    (R-2.7-002): the statistic a Bonferroni correction is applied to."""
    return sum(math.comb(usable_count, k) for k in range(passing, usable_count + 1)) / (2 ** usable_count)


def _statistical_validation_bonferroni_correction(
        batch_size, base_significance_level=STATISTICAL_VALIDATION_BASE_SIGNIFICANCE_LEVEL):
    """R-2.7-002: the significance level a single hypothesis's binomial
    p-value must clear shrinks with the batch size N it was tested
    alongside -- never the same fixed bar used to evaluate one hypothesis
    in isolation."""
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
        raise ValueError("Batch size must be a positive integer")
    return float(base_significance_level) / batch_size


def _statistical_validation_outcome(fold_summaries, minimum_folds_required, consistency_threshold,
                                    batch_size=None):
    """Never leaves a validation unclassified (DOC-004 REQ-004-005): usable
    folds below the declared minimum is INSUFFICIENT_EVIDENCE, not a
    quiet pass on whatever happened to be available.

    Etapa 2.7, M2.7-T2 (R-2.7-002): `batch_size=None` preserves the exact
    single-hypothesis verdict this function already produced (M2.6) -- the
    already-sealed real Statistical Validations (BTC-USD, ETH-USD) were
    both computed this way and must keep reproducing unchanged. An explicit
    `batch_size` (>1) additionally requires the exact binomial p-value of
    the observed fold pass count to clear a Bonferroni-corrected
    significance level, layered on top of -- never replacing -- the
    existing consistency_ratio>=consistency_threshold check.
    """
    usable = [item for item in fold_summaries if item["criterion_result"] != "INCONCLUSIVE"]
    if len(usable) < minimum_folds_required:
        return "INSUFFICIENT_EVIDENCE", "USABLE_FOLDS_BELOW_MINIMUM", None, None
    passing = sum(
        1 for item in usable
        if item["criterion_result"] == "MET" and item["beats_baseline"] is True)
    consistency_ratio = passing / len(usable)
    binomial_p_value = _statistical_validation_binomial_p_value(passing, len(usable))
    if consistency_ratio < float(consistency_threshold):
        return "NOT_VALIDATED", "CONSISTENCY_BELOW_THRESHOLD", consistency_ratio, binomial_p_value
    if batch_size is not None:
        corrected_alpha = _statistical_validation_bonferroni_correction(batch_size)
        if binomial_p_value > corrected_alpha:
            return "NOT_VALIDATED", "BONFERRONI_CORRECTION_NOT_MET", consistency_ratio, binomial_p_value
    return "VALIDATED", None, consistency_ratio, binomial_p_value


def _statistical_validation_materialization(validated_at, validation_code_revision):
    if (not _explicit_utc(validated_at)
            or not _hypothesis_code_revision_is_valid(validation_code_revision)):
        raise ValueError("Statistical validation time and code revision are required")
    source = _pipeline_source_bytes()
    return {
        "validated_at": validated_at, "validation_code_revision": validation_code_revision,
        "pipeline_sha256": digest(source),
    }


def _statistical_validation_content(validation_id, reference, minimum_folds_required,
                                    consistency_threshold, fold_summaries, sensitivity,
                                    risk_analytics_summary, outcome, outcome_reason,
                                    consistency_ratio, materialization, batch=None):
    content = {
        "validation_id": validation_id,
        "schema_version": STATISTICAL_VALIDATION_SCHEMA_VERSION,
        "reference": reference,
        "minimum_folds_required": minimum_folds_required,
        "consistency_threshold": consistency_threshold,
        "fold_summaries": fold_summaries,
        "sensitivity": sensitivity,
        "risk_analytics_summary": risk_analytics_summary,
        "outcome": outcome,
        "outcome_reason": outcome_reason,
        "consistency_ratio": consistency_ratio,
        "materialization": materialization,
        "status": STATISTICAL_VALIDATION_STATUS,
    }
    if batch is not None:
        content["batch"] = batch
    return content


def _statistical_validation_record(validation_id, reference, minimum_folds_required,
                                   consistency_threshold, fold_summaries, sensitivity,
                                   risk_analytics_summary, outcome, outcome_reason,
                                   consistency_ratio, materialization, batch=None):
    content = _statistical_validation_content(
        validation_id, reference, minimum_folds_required, consistency_threshold, fold_summaries,
        sensitivity, risk_analytics_summary, outcome, outcome_reason, consistency_ratio,
        materialization, batch)
    return {**content, "record_id": "STATISTICAL_VALIDATION_RECORD|" + validation_id + "|"
            + digest(encoded(content))}


def _statistical_validation_record_is_valid(record):
    base_fields = {
        "validation_id", "schema_version", "reference", "minimum_folds_required",
        "consistency_threshold", "fold_summaries", "sensitivity", "risk_analytics_summary",
        "outcome", "outcome_reason", "consistency_ratio", "materialization", "status",
        "record_id"}
    if not isinstance(record, dict):
        return False
    has_batch = "batch" in record
    if set(record) != (base_fields | {"batch"} if has_batch else base_fields):
        return False
    reference = record.get("reference")
    fold_summaries = record.get("fold_summaries")
    materialization = record.get("materialization")
    minimum = record.get("minimum_folds_required")
    threshold = record.get("consistency_threshold")
    outcome = record.get("outcome")
    batch = record.get("batch")
    batch_size = None
    if has_batch:
        if (not isinstance(batch, dict) or set(batch) != {
                "batch_size", "base_significance_level", "bonferroni_corrected_alpha",
                "binomial_p_value"}
                or not isinstance(batch.get("batch_size"), int)
                or isinstance(batch.get("batch_size"), bool) or batch["batch_size"] < 1
                or not _hypothesis_text_is_valid(batch.get("base_significance_level"))
                or not isinstance(batch.get("binomial_p_value"), float)):
            return False
        try:
            if not Decimal(batch["base_significance_level"]).is_finite():
                return False
        except (InvalidOperation, ValueError, TypeError):
            return False
        batch_size = batch["batch_size"]
        if batch.get("bonferroni_corrected_alpha") != _statistical_validation_bonferroni_correction(
                batch_size, batch["base_significance_level"]):
            return False
    if (not _statistical_validation_id_is_valid(record.get("validation_id"))
            or record.get("schema_version") != STATISTICAL_VALIDATION_SCHEMA_VERSION
            or not isinstance(reference, dict) or set(reference) != {"partition", "fold_results"}
            or not isinstance(reference["partition"], dict)
            or set(reference["partition"]) != {"partition_id", "record_id"}
            or not _walk_forward_partition_id_is_valid(reference["partition"].get("partition_id"))
            or not _hypothesis_text_is_valid(reference["partition"].get("record_id"))
            or not isinstance(reference["fold_results"], list) or not reference["fold_results"]
            or not all(
                isinstance(item, dict) and set(item) == {"fold_result_id", "record_id"}
                and _walk_forward_fold_result_id_is_valid(item.get("fold_result_id"))
                and _hypothesis_text_is_valid(item.get("record_id"))
                for item in reference["fold_results"])
            or not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 1
            or minimum > len(reference["fold_results"])
            or not _hypothesis_text_is_valid(threshold)
            or record["validation_id"] != _statistical_validation_id(
                reference, minimum, threshold, batch_size)
            or not isinstance(fold_summaries, list)
            or len(fold_summaries) != len(reference["fold_results"])
            or outcome not in STATISTICAL_VALIDATION_OUTCOMES
            or (outcome == "VALIDATED") != (record.get("outcome_reason") is None)
            or (record.get("outcome_reason") is not None
                and not _hypothesis_text_is_valid(record["outcome_reason"]))
            or (outcome == "INSUFFICIENT_EVIDENCE") != (record.get("consistency_ratio") is None)
            or not isinstance(record.get("sensitivity"), dict)
            or not isinstance(record.get("risk_analytics_summary"), dict)
            or record["risk_analytics_summary"] != _statistical_validation_risk_analytics_summary(
                fold_summaries)
            or not isinstance(materialization, dict) or set(materialization) != {
                "validated_at", "validation_code_revision", "pipeline_sha256"}
            or not _explicit_utc(materialization.get("validated_at"))
            or not _hypothesis_code_revision_is_valid(
                materialization.get("validation_code_revision"))
            or not re.fullmatch(r"[0-9a-f]{64}", materialization.get("pipeline_sha256", ""))
            or record.get("status") != STATISTICAL_VALIDATION_STATUS):
        return False
    try:
        Decimal(threshold)
    except (InvalidOperation, ValueError, TypeError):
        return False
    expected_outcome, expected_reason, expected_ratio, expected_p_value = _statistical_validation_outcome(
        fold_summaries, minimum, threshold, batch_size)
    if (outcome != expected_outcome or record.get("outcome_reason") != expected_reason
            or record.get("consistency_ratio") != expected_ratio
            or (has_batch and batch.get("binomial_p_value") != expected_p_value)):
        return False
    expected = _statistical_validation_record(
        record["validation_id"], reference, minimum, threshold, fold_summaries,
        record["sensitivity"], record["risk_analytics_summary"], outcome,
        record.get("outcome_reason"), record.get("consistency_ratio"), materialization, batch)
    return record == expected


def _statistical_validation_registry_is_valid(registry):
    if (not isinstance(registry, dict)
            or set(registry) != {"schema_version", "validations"}
            or registry.get("schema_version") != STATISTICAL_VALIDATION_REGISTRY_SCHEMA_VERSION
            or not isinstance(registry.get("validations"), list)
            or not all(_statistical_validation_record_is_valid(item)
                      for item in registry["validations"])):
        return False
    identifiers = [item["validation_id"] for item in registry["validations"]]
    return len(identifiers) == len(set(identifiers))


def _load_statistical_validation_registry(registry_path):
    try:
        registry = json.loads(Path(registry_path).read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Persisted Statistical Validation registry cannot be read") from error
    if not _statistical_validation_registry_is_valid(registry):
        raise ValueError("Persisted Statistical Validation registry is invalid")
    return registry


def _persist_statistical_validation(registry_path, record):
    if not _statistical_validation_record_is_valid(record):
        raise ValueError("Statistical Validation record is invalid")
    path = Path(registry_path)
    registry = (_load_statistical_validation_registry(path) if path.exists() else {
        "schema_version": STATISTICAL_VALIDATION_REGISTRY_SCHEMA_VERSION, "validations": []})
    matches = [item for item in registry["validations"]
               if item["validation_id"] == record["validation_id"]]
    if matches:
        if len(matches) == 1 and matches[0] == record:
            return matches[0]
        raise ValueError("Statistical Validation identity already exists with different content")
    registry["validations"].append(record)
    if not _statistical_validation_registry_is_valid(registry):
        raise ValueError("Constructed Statistical Validation registry is invalid")
    _atomic_write(path, encoded(registry))
    return record


def _statistical_validation_all_fold_results(fold_result_registry_path, partition, *,
                                             partition_registry_path, experiment_registry_path,
                                             hypothesis_registry_path, dataset_directory,
                                             strategy=None, horizon=1, auxiliary_verifiers=None):
    """Resolve and independently reverify every fold of one partition -- fails
    closed unless exactly one verified result exists per fold index."""
    registry = _load_walk_forward_fold_result_registry(fold_result_registry_path)
    matches = [item for item in registry["fold_results"]
               if item["reference"]["partition"]["partition_id"] == partition["partition_id"]]
    by_index = {}
    for item in matches:
        if item["fold_index"] in by_index:
            raise ValueError("Walk-Forward Fold Result index is ambiguous")
        by_index[item["fold_index"]] = item
    if set(by_index) != set(range(partition["fold_count"])):
        raise ValueError("Not every fold of this Walk-Forward Partition has a result yet")
    return [
        verified_walk_forward_fold_result(
            fold_result_registry_path, by_index[index]["fold_result_id"],
            partition_registry_path=partition_registry_path,
            experiment_registry_path=experiment_registry_path,
            hypothesis_registry_path=hypothesis_registry_path,
            dataset_directory=dataset_directory, strategy=strategy, horizon=horizon,
            auxiliary_verifiers=auxiliary_verifiers)
        for index in range(partition["fold_count"])]


def constitute_statistical_validation(registry_path, *, partition_registry_path, partition_id,
                                      partition_record_id, fold_result_registry_path,
                                      experiment_registry_path, hypothesis_registry_path,
                                      dataset_directory, minimum_folds_required,
                                      consistency_threshold, validated_at,
                                      validation_code_revision, batch_size=None, strategy=None,
                                      horizon=1, auxiliary_verifiers=None):
    """M2.6-T3: aggregate every fold of one partition into one traceable
    verdict -- VALIDATED, NOT_VALIDATED, or INSUFFICIENT_EVIDENCE, never
    unclassified (DOC-004 REQ-004-005). Purely additive: never touches the
    single-sample Disposition already issued for this Hypothesis (M2.4-T1),
    any PAPER state, or T9.

    Etapa 2.7, M2.7-T2 (R-2.7-002): `batch_size` declares how many
    hypotheses were tested in the same batch as this one, so the
    Bonferroni-corrected significance level in _statistical_validation_outcome
    reflects the real number of comparisons -- omit it (default None) for a
    single hypothesis evaluated on its own, exactly as M2.6 always has.
    Etapa 2.7, M2.7-T4: `strategy`/`horizon` are only needed to verify the
    underlying Partition/dataset for a non-default Strategy or horizon.
    Etapa 2.8: `auxiliary_verifiers` is needed by both when required.
    """
    partition = verified_walk_forward_partition(
        partition_registry_path, partition_id, dataset_directory=dataset_directory,
        hypothesis_registry_path=hypothesis_registry_path, strategy=strategy, horizon=horizon,
        auxiliary_verifiers=auxiliary_verifiers)
    if partition["record_id"] != partition_record_id:
        raise ValueError("Walk-Forward Partition seal does not match the requested validation")
    fold_results = _statistical_validation_all_fold_results(
        fold_result_registry_path, partition, partition_registry_path=partition_registry_path,
        experiment_registry_path=experiment_registry_path,
        hypothesis_registry_path=hypothesis_registry_path, dataset_directory=dataset_directory,
        strategy=strategy, horizon=horizon, auxiliary_verifiers=auxiliary_verifiers)

    if not _hypothesis_text_is_valid(consistency_threshold):
        raise ValueError("Consistency threshold is required")
    try:
        if not Decimal(consistency_threshold).is_finite():
            raise ValueError("Consistency threshold must be finite")
    except InvalidOperation as error:
        raise ValueError("Consistency threshold must be a decimal string") from error
    if (not isinstance(minimum_folds_required, int) or isinstance(minimum_folds_required, bool)
            or not 1 <= minimum_folds_required <= len(fold_results)):
        raise ValueError("Minimum folds required must be between 1 and the fold count")

    if batch_size is not None and (
            not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1):
        raise ValueError("Batch size must be a positive integer")

    reference = _statistical_validation_reference(partition, fold_results)
    validation_id = _statistical_validation_id(
        reference, minimum_folds_required, consistency_threshold, batch_size)
    path = Path(registry_path)
    if path.exists():
        registry = _load_statistical_validation_registry(path)
        matches = [item for item in registry["validations"]
                   if item["validation_id"] == validation_id]
        if matches:
            if len(matches) != 1:
                raise ValueError("Statistical Validation identity is ambiguous")
            return verified_statistical_validation(
                registry_path, validation_id, partition_registry_path=partition_registry_path,
                fold_result_registry_path=fold_result_registry_path,
                experiment_registry_path=experiment_registry_path,
                hypothesis_registry_path=hypothesis_registry_path,
                dataset_directory=dataset_directory, strategy=strategy, horizon=horizon,
                auxiliary_verifiers=auxiliary_verifiers)

    fold_summaries = [_statistical_validation_fold_summary(item) for item in fold_results]
    sensitivity = _statistical_validation_sensitivity(fold_summaries)
    risk_analytics_summary = _statistical_validation_risk_analytics_summary(fold_summaries)
    outcome, outcome_reason, consistency_ratio, binomial_p_value = _statistical_validation_outcome(
        fold_summaries, minimum_folds_required, consistency_threshold, batch_size)
    batch = None
    if batch_size is not None:
        batch = {
            "batch_size": batch_size,
            "base_significance_level": STATISTICAL_VALIDATION_BASE_SIGNIFICANCE_LEVEL,
            "bonferroni_corrected_alpha": _statistical_validation_bonferroni_correction(batch_size),
            "binomial_p_value": binomial_p_value,
        }
    record = _statistical_validation_record(
        validation_id, reference, minimum_folds_required, consistency_threshold, fold_summaries,
        sensitivity, risk_analytics_summary, outcome, outcome_reason, consistency_ratio,
        _statistical_validation_materialization(validated_at, validation_code_revision), batch)
    return _persist_statistical_validation(registry_path, record)


def load_statistical_validation(registry_path, validation_id):
    if not _statistical_validation_id_is_valid(validation_id):
        raise ValueError("A valid Statistical Validation identity is required")
    registry = _load_statistical_validation_registry(registry_path)
    matches = [item for item in registry["validations"] if item["validation_id"] == validation_id]
    if len(matches) != 1:
        raise ValueError("Statistical Validation is not registered unambiguously")
    return matches[0]


def query_statistical_validation(registry_path, *, outcome=None):
    """Read-only consultation surface -- never mutates state, no side effects."""
    path = Path(registry_path)
    if not path.exists():
        return []
    records = _load_statistical_validation_registry(path)["validations"]
    if outcome is not None:
        records = [item for item in records if item["outcome"] == outcome]
    return records


def verified_statistical_validation(registry_path, validation_id, *, partition_registry_path,
                                    fold_result_registry_path, experiment_registry_path,
                                    hypothesis_registry_path, dataset_directory, strategy=None,
                                    horizon=1, auxiliary_verifiers=None):
    """Reload a Statistical Validation and reproduce its verdict from every
    fold, re-verified fresh from the sealed partition/experiment/dataset."""
    record = load_statistical_validation(registry_path, validation_id)
    partition_reference = record["reference"]["partition"]
    partition = verified_walk_forward_partition(
        partition_registry_path, partition_reference["partition_id"],
        dataset_directory=dataset_directory, hypothesis_registry_path=hypothesis_registry_path,
        strategy=strategy, horizon=horizon, auxiliary_verifiers=auxiliary_verifiers)
    if partition["record_id"] != partition_reference["record_id"]:
        raise ValueError("Statistical Validation partition reference is invalid")
    fold_results = _statistical_validation_all_fold_results(
        fold_result_registry_path, partition, partition_registry_path=partition_registry_path,
        experiment_registry_path=experiment_registry_path,
        hypothesis_registry_path=hypothesis_registry_path, dataset_directory=dataset_directory,
        strategy=strategy, horizon=horizon, auxiliary_verifiers=auxiliary_verifiers)
    reference = _statistical_validation_reference(partition, fold_results)
    if reference != record["reference"]:
        raise ValueError("Statistical Validation fold result references are invalid")
    fold_summaries = [_statistical_validation_fold_summary(item) for item in fold_results]
    sensitivity = _statistical_validation_sensitivity(fold_summaries)
    risk_analytics_summary = _statistical_validation_risk_analytics_summary(fold_summaries)
    stored_batch = record.get("batch")
    batch_size = stored_batch["batch_size"] if stored_batch is not None else None
    outcome, outcome_reason, consistency_ratio, binomial_p_value = _statistical_validation_outcome(
        fold_summaries, record["minimum_folds_required"], record["consistency_threshold"],
        batch_size)
    batch = None
    if stored_batch is not None:
        base_significance_level = stored_batch.get(
            "base_significance_level", STATISTICAL_VALIDATION_BASE_SIGNIFICANCE_LEVEL)
        batch = {
            "batch_size": batch_size,
            "base_significance_level": base_significance_level,
            "bonferroni_corrected_alpha": _statistical_validation_bonferroni_correction(
                batch_size, base_significance_level),
            "binomial_p_value": binomial_p_value,
        }
    expected = _statistical_validation_record(
        record["validation_id"], reference, record["minimum_folds_required"],
        record["consistency_threshold"], fold_summaries, sensitivity, risk_analytics_summary,
        outcome, outcome_reason, consistency_ratio, record["materialization"], batch)
    if record != expected:
        raise ValueError("Statistical Validation verdict, folds, sensitivity, or risk analytics is invalid")
    return record


