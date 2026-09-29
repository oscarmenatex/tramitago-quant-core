"""CAP-002 Research -- Experiment (M2.2-T2: immutable experiment
conditions; M2.2-T3: sealed result over an already-published historical
dataset).

Moved out of pipeline.py per DOC-014/DOC-015 (adaptado) SS4 (Etapa 4, M4.1
module decomposition). No behavior change.
"""

import csv
import io
import json
import math
import re
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

from tramitago_quant_core.shared.util import (
    digest, encoded, epoch, iso, _atomic_write, _explicit_utc, _hypothesis_text_is_valid,
    _hypothesis_system_version_is_valid, _hypothesis_code_revision_is_valid,
    _pipeline_source_bytes,
)
from tramitago_quant_core.research.hypothesis import (
    _hypothesis_id_is_valid, load_hypothesis, HYPOTHESIS_ACCEPTANCE_COMPARISONS,
)
from tramitago_quant_core.research.historical_dataset import (
    HYPOTHESIS_DATASET_WARMUP_ROLE,
    HYPOTHESIS_DATASET_EVALUATION_ROLE, HYPOTHESIS_DATASET_FORWARD_ROLE,
    _hypothesis_dataset_columns, _hypothesis_dataset_warmup_role,
    _hypothesis_dataset_forward_column, _hypothesis_dataset_auxiliary_variables,
    verified_hypothesis_dataset,
)
from tramitago_quant_core.strategy_contract.strategy import (
    sma_crossover_strategy, momentum_crossover_strategy, volume_surge_strategy,
    funding_rate_sign_strategy, sma_volume_confirmation_strategy,
)

# M4.1 production wiring (2026-09-28): an Experiment's indicator is described
# either by the legacy fixed "sma" field (the exact shape both already-sealed
# real Experiments -- BTC-USD, ETH-USD -- were constituted and hashed with,
# always sma_crossover_strategy(3)) or by the generic "indicator" field (any
# other Strategy, reconstructed here from its own strategy_id/parameters so
# results can be independently recalculated without trusting the persisted
# evidence). Never a version flag: the field key present *is* the shape.
_EXPERIMENT_STRATEGY_CONSTRUCTORS = {
    "SMA_CROSSOVER": lambda parameters: sma_crossover_strategy(parameters["window"]),
    "MOMENTUM_CROSSOVER": lambda parameters: momentum_crossover_strategy(parameters["lookback"]),
    "VOLUME_SURGE": lambda parameters: volume_surge_strategy(parameters["window"]),
    "FUNDING_RATE_SIGN": lambda parameters: funding_rate_sign_strategy(),
    "SMA_VOLUME_CONFIRMATION": lambda parameters: sma_volume_confirmation_strategy(
        parameters["sma_window"], parameters["volume_window"]),
}


def _experiment_conditions_strategy(conditions):
    """Reconstruct the exact Strategy a sealed Experiment's conditions describe."""
    if "sma" in conditions:
        return sma_crossover_strategy(conditions["sma"]["window"])
    indicator = conditions["indicator"]
    constructor = _EXPERIMENT_STRATEGY_CONSTRUCTORS.get(indicator["strategy_id"])
    if constructor is None:
        raise ValueError("Experiment indicator strategy is not recognized")
    return constructor(indicator["parameters"])


EXPERIMENT_CONDITIONS_REGISTRY_SCHEMA_VERSION = "1"
EXPERIMENT_CONDITIONS_SCHEMA_VERSION = "1"
EXPERIMENT_CONDITIONS_STATUS = "CONDITIONS_FIXED"
EXPERIMENT_COSTS_NOT_APPLICABLE = (
    "NOT_APPLICABLE: associative comparison has no orders, positions, fills, P&L, or execution")


def _experiment_id_is_valid(value):
    if not isinstance(value, str) or not value.startswith("EXPERIMENT|"):
        return False
    try:
        return str(uuid.UUID(value.removeprefix("EXPERIMENT|"))) == value.removeprefix(
            "EXPERIMENT|")
    except ValueError:
        return False


def _experiment_reference(hypothesis, dataset, selection):
    identity = dataset["identity"]
    return {
        "hypothesis": {
            "hypothesis_id": hypothesis["hypothesis_id"],
            "version": hypothesis["version"],
            "record_id": hypothesis["record_id"],
            "system_version": hypothesis["system_version"],
            "code_revision": hypothesis["code_revision"],
        },
        "dataset": {
            "dataset_id": dataset["dataset_id"],
            "dataset_sha256": dataset["dataset_sha256"],
            "hypothesis_id": identity["hypothesis_id"],
            "hypothesis_version": identity["hypothesis_version"],
            "evaluable_period": dataset["config"]["evaluable_period"],
            "support_rows": selection["support_rows"],
        },
    }


_EXPERIMENT_CRITERION_COMPARATORS = {
    "GT": lambda metric, threshold: metric > threshold,
    "GE": lambda metric, threshold: metric >= threshold,
    "LT": lambda metric, threshold: metric < threshold,
    "LE": lambda metric, threshold: metric <= threshold,
}


def _experiment_threshold_is_valid(threshold):
    if not isinstance(threshold, str):
        return False
    try:
        return Decimal(threshold).is_finite()
    except InvalidOperation:
        return False


def _experiment_criterion_is_compatible(criterion, metric_name):
    """Reversal extension (2026-09-29): any GT/GE (INCREASE) or LT/LE
    (DECREASE) criterion is compatible, not only GT/threshold=0/INCREASE --
    mirrors hypothesis.py's own _hypothesis_acceptance_criterion_is_valid,
    which already supported this. Every already-sealed real Experiment used
    exactly GT/"0"/INCREASE, which is exactly why this still accepts only
    that shape unchanged when nothing else is passed."""
    return (
        isinstance(criterion, dict)
        and set(criterion) == {"metric", "comparison", "threshold", "expected_direction"}
        and criterion.get("metric") == metric_name
        and criterion.get("comparison") in HYPOTHESIS_ACCEPTANCE_COMPARISONS.get(
            criterion.get("expected_direction"), ())
        and _experiment_threshold_is_valid(criterion.get("threshold"))
    )


def _experiment_criterion_result(metric, criterion):
    """Derive MET/NOT_MET from the criterion's own comparison/threshold,
    not a hardcoded `metric > 0` -- every already-sealed real Experiment
    Result used GT/threshold="0", which is exactly why this reproduces the
    same MET/NOT_MET unchanged for them."""
    comparator = _EXPERIMENT_CRITERION_COMPARATORS[criterion["comparison"]]
    return "MET" if comparator(metric, float(criterion["threshold"])) else "NOT_MET"


def _discovery_snapshot(hypothesis):
    values = hypothesis["creation_context"]["provenance"]
    snapshots = [value.removeprefix("SNAPSHOT_SHA256|") for value in values
                 if value.startswith("SNAPSHOT_SHA256|")]
    if len(snapshots) != 1 or not re.fullmatch(r"[0-9a-f]{64}", snapshots[0]):
        raise ValueError("Hypothesis discovery snapshot is not explicit")
    if "DISCOVERY|artifacts/live-run-1" not in values:
        raise ValueError("Hypothesis discovery evidence is not explicit")
    return snapshots[0]


def _experiment_conditions(hypothesis, dataset, selection, strategy=None, horizon=1):
    """Instrument extension (2026-09-28): the instrument is read from the
    dataset's own config, not fixed to "BTC-USD" -- generalized after
    finding it hardcoded while investigating a second instrument (ETH-USD)
    for the same SMA3 Hypothesis, the same way M4.1 generalized the
    indicator window.

    M4.1 production wiring (2026-09-28): `strategy` defaults to
    sma_crossover_strategy(3) -- exactly the Strategy both already-sealed
    real Experiments (BTC-USD, ETH-USD) were constituted with -- and, only
    for that exact default, still emits the legacy "sma" field so those two
    sealed records keep reproducing byte-for-byte. Any other Strategy emits
    the generic "indicator" field instead.

    Etapa 2.7 return-horizon extension (2026-09-28): `horizon` defaults to
    1 -- the exact forward-return distance both already-sealed real
    Experiments were constituted with -- for the same byte-for-byte reason.
    Only when horizon != 1 is an additive "forward_horizon" field emitted;
    the "outcome"/"metric.formula" text is always derived from horizon, so
    it reproduces the legacy "return_t+1" text exactly at the default.
    """
    strategy = strategy or sma_crossover_strategy(3)
    reference = _experiment_reference(hypothesis, dataset, selection)
    criterion = hypothesis["acceptance_criterion"]
    metric = hypothesis["target_metric"]
    instrument = dataset["config"]["instrument"]
    if (not _experiment_criterion_is_compatible(criterion, metric)
            or not isinstance(instrument, str) or not instrument
            or dataset["config"]["frequency_seconds"] != 86400
            or reference["dataset"]["evaluable_period"]
            != hypothesis["constraints"]["period"]):
        raise ValueError("Hypothesis and dataset conditions are incompatible")
    is_legacy_default = (strategy["strategy_id"] == "SMA_CROSSOVER"
                         and strategy["parameters"] == {"window": 3})
    analytical_rule = {
        "upper_group": strategy["upper_group_description"],
        "lower_or_equal_group": strategy["lower_or_equal_group_description"],
    }
    metric_formula = (
        f"mean(return_t+{horizon} | {strategy['upper_group_description']}) - "
        f"mean(return_t+{horizon} | {strategy['lower_or_equal_group_description']})")
    indicator_field = (
        {"sma": {"source": "close", "window": strategy["parameters"]["window"]}}
        if is_legacy_default else
        {"indicator": {
            "strategy_id": strategy["strategy_id"],
            "parameters": strategy["parameters"],
            "column_name": strategy["column_name"],
        }}
    )
    warmup_role = _hypothesis_dataset_warmup_role(strategy)
    conditions = {
        "schema_version": EXPERIMENT_CONDITIONS_SCHEMA_VERSION,
        "instrument": instrument,
        "frequency_seconds": 86400,
        "analytical_rule": analytical_rule,
        **indicator_field,
        "outcome": {"name": f"return_t+{horizon}", "formula": f"(close_t+{horizon} / close_t) - 1"},
        "metric": {
            "name": metric,
            "formula": metric_formula,
        },
        "acceptance_criterion": criterion,
        "population": {
            "included_row_role": HYPOTHESIS_DATASET_EVALUATION_ROLE,
            "excluded_row_roles": [
                warmup_role,
                HYPOTHESIS_DATASET_FORWARD_ROLE,
            ],
            "support_rows": selection["support_rows"],
        },
        "temporal_split": {
            "discovery_evidence": {
                "path": "artifacts/live-run-1",
                "period": "2024",
                "snapshot_sha256": _discovery_snapshot(hypothesis),
                "role": "DISCOVERY_ONLY",
            },
            "independent_evaluation_period": reference["dataset"]["evaluable_period"],
            "training": "NOT_USED",
            "optimization": "NOT_USED",
            "oos_proportion": "NOT_APPLICABLE",
        },
        "costs": {"declaration": EXPERIMENT_COSTS_NOT_APPLICABLE},
        "execution_scope": {
            "metric_calculated": False,
            "experiment_executed": False,
            "backtest_executed": False,
        },
    }
    if horizon != 1:
        conditions["forward_horizon"] = horizon
    return conditions


def _experiment_conditions_horizon(conditions):
    """Etapa 2.7 return-horizon extension (2026-09-28): mirrors
    _experiment_conditions_strategy -- `forward_horizon` is only ever
    present (additive) when it differs from the legacy default of 1."""
    return conditions.get("forward_horizon", 1)


def _experiment_conditions_are_valid(conditions):
    common_fields = {
        "schema_version", "instrument", "frequency_seconds", "analytical_rule",
        "outcome", "metric", "acceptance_criterion", "population", "temporal_split",
        "costs", "execution_scope",
    }
    if not isinstance(conditions, dict):
        return False
    has_horizon = "forward_horizon" in conditions
    if has_horizon:
        horizon = conditions["forward_horizon"]
        if (not isinstance(horizon, int) or isinstance(horizon, bool)
                or horizon < 1 or horizon == 1):
            return False
    else:
        horizon = 1
    base_fields = common_fields | ({"forward_horizon"} if has_horizon else set())
    is_legacy = set(conditions) == base_fields | {"sma"}
    is_generic = set(conditions) == base_fields | {"indicator"}
    if not (is_legacy or is_generic):
        return False
    analytical_rule = conditions.get("analytical_rule")
    if not (isinstance(analytical_rule, dict)
            and set(analytical_rule) == {"upper_group", "lower_or_equal_group"}
            and _hypothesis_text_is_valid(analytical_rule.get("upper_group"))
            and _hypothesis_text_is_valid(analytical_rule.get("lower_or_equal_group"))):
        return False
    if is_legacy:
        if (analytical_rule != {"upper_group": "close_t > SMA3_t",
                                "lower_or_equal_group": "close_t <= SMA3_t"}
                or conditions.get("sma") != {"source": "close", "window": 3}):
            return False
        warmup_role = HYPOTHESIS_DATASET_WARMUP_ROLE
        warmup_count = 2
    else:
        indicator = conditions.get("indicator")
        if (not isinstance(indicator, dict)
                or set(indicator) != {"strategy_id", "parameters", "column_name"}
                or not _hypothesis_text_is_valid(indicator.get("strategy_id"))
                or not isinstance(indicator.get("parameters"), dict)
                or not _hypothesis_text_is_valid(indicator.get("column_name"))):
            return False
        try:
            reconstructed = _experiment_conditions_strategy(conditions)
        except (KeyError, ValueError, TypeError):
            return False
        if (reconstructed["column_name"] != indicator["column_name"]
                or reconstructed["parameters"] != indicator["parameters"]
                or reconstructed["strategy_id"] != indicator["strategy_id"]
                or analytical_rule != {
                    "upper_group": reconstructed["upper_group_description"],
                    "lower_or_equal_group": reconstructed["lower_or_equal_group_description"]}):
            return False
        warmup_role = _hypothesis_dataset_warmup_role(reconstructed)
        warmup_count = reconstructed["required_inputs"]["warmup_periods"]
    expected_formula = (
        f"mean(return_t+{horizon} | {analytical_rule['upper_group']}) - "
        f"mean(return_t+{horizon} | {analytical_rule['lower_or_equal_group']})")
    expected_support_roles = (
        [warmup_role] * warmup_count + [HYPOTHESIS_DATASET_FORWARD_ROLE] * horizon)
    period = conditions["temporal_split"].get("independent_evaluation_period") \
        if isinstance(conditions["temporal_split"], dict) else None
    support_rows = conditions["population"].get("support_rows") \
        if isinstance(conditions["population"], dict) else None
    criterion = conditions.get("acceptance_criterion")
    return (
        conditions.get("schema_version") == EXPERIMENT_CONDITIONS_SCHEMA_VERSION
        and _hypothesis_text_is_valid(conditions.get("instrument"))
        and conditions.get("frequency_seconds") == 86400
        and conditions.get("outcome") == {
            "name": f"return_t+{horizon}", "formula": f"(close_t+{horizon} / close_t) - 1"}
        and isinstance(conditions.get("metric"), dict)
        and _hypothesis_text_is_valid(conditions["metric"].get("name"))
        and conditions["metric"].get("formula") == expected_formula
        and _experiment_criterion_is_compatible(criterion, conditions["metric"]["name"])
        and isinstance(period, dict) and set(period) == {"start_utc", "end_exclusive_utc"}
        and _explicit_utc(period["start_utc"]) and _explicit_utc(period["end_exclusive_utc"])
        and epoch(period["start_utc"]) < epoch(period["end_exclusive_utc"])
        and isinstance(support_rows, list) and len(support_rows) == len(expected_support_roles)
        and [item.get("row_role") if isinstance(item, dict) else None for item in support_rows]
        == expected_support_roles
        and conditions["population"].get("included_row_role") == HYPOTHESIS_DATASET_EVALUATION_ROLE
        and conditions["population"].get("excluded_row_roles") == [
            warmup_role, HYPOTHESIS_DATASET_FORWARD_ROLE]
        and isinstance(conditions["temporal_split"].get("discovery_evidence"), dict)
        and conditions["temporal_split"]["discovery_evidence"].get("path")
        == "artifacts/live-run-1"
        and conditions["temporal_split"]["discovery_evidence"].get("period") == "2024"
        and re.fullmatch(r"[0-9a-f]{64}",
                         conditions["temporal_split"]["discovery_evidence"].get("snapshot_sha256", ""))
        and conditions["temporal_split"]["discovery_evidence"].get("role") == "DISCOVERY_ONLY"
        and conditions["temporal_split"].get("training") == "NOT_USED"
        and conditions["temporal_split"].get("optimization") == "NOT_USED"
        and conditions["temporal_split"].get("oos_proportion") == "NOT_APPLICABLE"
        and conditions.get("costs") == {"declaration": EXPERIMENT_COSTS_NOT_APPLICABLE}
        and conditions.get("execution_scope") == {
            "metric_calculated": False, "experiment_executed": False,
            "backtest_executed": False}
    )


def _experiment_record_content(experiment_id, version, references, conditions, created_at,
                               status, revision_reason):
    return {
        "experiment_id": experiment_id,
        "version": version,
        "references": references,
        "conditions": conditions,
        "created_at": created_at,
        "status": status,
        "revision_reason": revision_reason,
    }


def _experiment_record(experiment_id, version, references, conditions, created_at,
                       status, revision_reason):
    content = _experiment_record_content(
        experiment_id, version, references, conditions, created_at, status, revision_reason)
    return {**content, "record_id": "EXPERIMENT_VERSION|" + experiment_id + "|"
            + str(version) + "|" + digest(encoded(content))}


def _experiment_record_is_valid(record):
    fields = {
        "experiment_id", "version", "references", "conditions", "created_at", "status",
        "revision_reason", "record_id"}
    if not isinstance(record, dict) or set(record) != fields:
        return False
    references = record.get("references")
    if (not _experiment_id_is_valid(record.get("experiment_id"))
            or not isinstance(record.get("version"), int) or isinstance(record["version"], bool)
            or record["version"] < 1 or not isinstance(references, dict)
            or set(references) != {"hypothesis", "dataset"}
            or not isinstance(references["hypothesis"], dict)
            or set(references["hypothesis"]) != {
                "hypothesis_id", "version", "record_id", "system_version", "code_revision"}
            or not _hypothesis_id_is_valid(references["hypothesis"].get("hypothesis_id"))
            or not isinstance(references["hypothesis"].get("version"), int)
            or not _hypothesis_text_is_valid(references["hypothesis"].get("record_id"))
            or not _hypothesis_system_version_is_valid(references["hypothesis"].get("system_version"))
            or not _hypothesis_code_revision_is_valid(references["hypothesis"].get("code_revision"))
            or not isinstance(references["dataset"], dict)
            or set(references["dataset"]) != {
                "dataset_id", "dataset_sha256", "hypothesis_id", "hypothesis_version",
                "evaluable_period", "support_rows"}
            or not isinstance(references["dataset"].get("dataset_id"), str)
            or not references["dataset"]["dataset_id"].startswith("HISTORICAL_HYPOTHESIS_DATASET|")
            or not re.fullmatch(r"[0-9a-f]{64}", references["dataset"].get("dataset_sha256", ""))
            or references["dataset"].get("hypothesis_id") != references["hypothesis"]["hypothesis_id"]
            or references["dataset"].get("hypothesis_version") != references["hypothesis"]["version"]
            or not _experiment_conditions_are_valid(record.get("conditions"))
            or record["conditions"]["temporal_split"]["independent_evaluation_period"]
            != references["dataset"]["evaluable_period"]
            or record["conditions"]["population"]["support_rows"]
            != references["dataset"]["support_rows"]
            or not _explicit_utc(record.get("created_at"))
            or record.get("status") != EXPERIMENT_CONDITIONS_STATUS
            or not _hypothesis_text_is_valid(record.get("revision_reason"))):
        return False
    expected = _experiment_record(
        record["experiment_id"], record["version"], references, record["conditions"],
        record["created_at"], record["status"], record["revision_reason"])
    return record == expected


def _experiment_registry_is_valid(registry):
    if (not isinstance(registry, dict) or set(registry) != {"schema_version", "experiments"}
            or registry.get("schema_version") != EXPERIMENT_CONDITIONS_REGISTRY_SCHEMA_VERSION
            or not isinstance(registry.get("experiments"), list)
            or not all(_experiment_record_is_valid(record) for record in registry["experiments"])):
        return False
    pairs = [(record["experiment_id"], record["version"]) for record in registry["experiments"]]
    if len(pairs) != len(set(pairs)):
        return False
    for experiment_id in {record["experiment_id"] for record in registry["experiments"]}:
        versions = sorted(record["version"] for record in registry["experiments"]
                          if record["experiment_id"] == experiment_id)
        if versions != list(range(1, len(versions) + 1)):
            return False
    return True


def _load_experiment_conditions_registry(registry_path):
    try:
        registry = json.loads(Path(registry_path).read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Persisted experiment conditions registry cannot be read") from error
    if not _experiment_registry_is_valid(registry):
        raise ValueError("Persisted experiment conditions registry is invalid or has a version conflict")
    return registry


def _persist_experiment_conditions(registry_path, record):
    """Append only one sealed identity/version pair; never overwrite prior conditions."""
    if not _experiment_record_is_valid(record):
        raise ValueError("Experiment conditions record is invalid")
    registry_path = Path(registry_path)
    registry = (_load_experiment_conditions_registry(registry_path) if registry_path.exists()
                else {"schema_version": EXPERIMENT_CONDITIONS_REGISTRY_SCHEMA_VERSION,
                      "experiments": []})
    if any(item["experiment_id"] == record["experiment_id"]
           and item["version"] == record["version"] for item in registry["experiments"]):
        raise ValueError("Experiment identity and version already exist")
    prior = [item for item in registry["experiments"]
             if item["experiment_id"] == record["experiment_id"]]
    if record["version"] != len(prior) + 1:
        raise ValueError("Experiment versions must be appended consecutively")
    registry["experiments"].append(record)
    if not _experiment_registry_is_valid(registry):
        raise ValueError("Constructed experiment conditions registry is invalid")
    _atomic_write(registry_path, encoded(registry))
    return record


def _experiment_inputs(hypothesis_registry_path, dataset_directory, hypothesis_id,
                       hypothesis_version, dataset_id, strategy=None, horizon=1,
                       auxiliary_verifiers=None):
    hypothesis = load_hypothesis(hypothesis_registry_path, hypothesis_id, hypothesis_version)
    dataset = verified_hypothesis_dataset(
        dataset_directory, hypothesis_registry_path, strategy, horizon, auxiliary_verifiers)
    selection = json.loads((Path(dataset_directory) / "selection.json").read_bytes())
    if dataset["dataset_id"] != dataset_id:
        raise ValueError("Dataset identity does not match the experiment reference")
    references = _experiment_reference(hypothesis, dataset, selection)
    if (references["dataset"]["hypothesis_id"] != hypothesis_id
            or references["dataset"]["hypothesis_version"] != hypothesis_version
            or references["dataset"]["evaluable_period"] != hypothesis["constraints"]["period"]):
        raise ValueError("Hypothesis and dataset references are incompatible")
    return hypothesis, dataset, selection, references


def constitute_experiment_conditions(registry_path, *, hypothesis_registry_path,
                                     dataset_directory, hypothesis_id, hypothesis_version,
                                     dataset_id, created_at, revision_reason, strategy=None,
                                     horizon=1, auxiliary_verifiers=None):
    """Fix one real experiment definition without calculating or executing it."""
    if not _explicit_utc(created_at) or not _hypothesis_text_is_valid(revision_reason):
        raise ValueError("Experiment creation time and revision reason are required")
    hypothesis, dataset, selection, references = _experiment_inputs(
        hypothesis_registry_path, dataset_directory, hypothesis_id, hypothesis_version,
        dataset_id, strategy, horizon, auxiliary_verifiers)
    experiment_id = "EXPERIMENT|" + str(uuid.uuid4())
    record = _experiment_record(
        experiment_id, 1, references,
        _experiment_conditions(hypothesis, dataset, selection, strategy, horizon),
        created_at, EXPERIMENT_CONDITIONS_STATUS, revision_reason)
    return _persist_experiment_conditions(registry_path, record)


def revise_experiment_conditions(registry_path, experiment_id, *, hypothesis_registry_path,
                                 dataset_directory, hypothesis_id, hypothesis_version,
                                 dataset_id, created_at, revision_reason, strategy=None,
                                 horizon=1, auxiliary_verifiers=None):
    """Append a new sealed version while retaining every prior experiment definition."""
    if not _experiment_id_is_valid(experiment_id) or not _explicit_utc(created_at) \
            or not _hypothesis_text_is_valid(revision_reason):
        raise ValueError("Experiment identity, creation time, and revision reason are required")
    registry = _load_experiment_conditions_registry(registry_path)
    prior = [record for record in registry["experiments"]
             if record["experiment_id"] == experiment_id]
    if not prior:
        raise ValueError("Experiment identity is not registered")
    hypothesis, dataset, selection, references = _experiment_inputs(
        hypothesis_registry_path, dataset_directory, hypothesis_id, hypothesis_version,
        dataset_id, strategy, horizon, auxiliary_verifiers)
    record = _experiment_record(
        experiment_id, len(prior) + 1, references,
        _experiment_conditions(hypothesis, dataset, selection, strategy, horizon), created_at,
        EXPERIMENT_CONDITIONS_STATUS, revision_reason)
    return _persist_experiment_conditions(registry_path, record)


def load_experiment_conditions(registry_path, experiment_id, version):
    """Reload exactly one sealed experiment-conditions version from any process."""
    if (not _experiment_id_is_valid(experiment_id) or not isinstance(version, int)
            or isinstance(version, bool) or version < 1):
        raise ValueError("A valid experiment identity and version are required")
    registry = _load_experiment_conditions_registry(registry_path)
    matches = [record for record in registry["experiments"]
               if record["experiment_id"] == experiment_id and record["version"] == version]
    if len(matches) != 1:
        raise ValueError("Experiment version is not registered unambiguously")
    return matches[0]


def verified_experiment_conditions(registry_path, experiment_id, version, *,
                                   hypothesis_registry_path, dataset_directory,
                                   auxiliary_verifiers=None):
    """Reload a sealed definition and prove that its external immutable references agree."""
    record = load_experiment_conditions(registry_path, experiment_id, version)
    reference = record["references"]
    strategy = _experiment_conditions_strategy(record["conditions"])
    horizon = _experiment_conditions_horizon(record["conditions"])
    hypothesis, dataset, selection, expected_references = _experiment_inputs(
        hypothesis_registry_path, dataset_directory, reference["hypothesis"]["hypothesis_id"],
        reference["hypothesis"]["version"], reference["dataset"]["dataset_id"], strategy, horizon,
        auxiliary_verifiers)
    if (reference != expected_references
            or record["conditions"] != _experiment_conditions(
                hypothesis, dataset, selection, strategy, horizon)):
        raise ValueError("Experiment conditions references are incompatible")
    return record


EXPERIMENT_RESULT_REGISTRY_SCHEMA_VERSION = "1"
EXPERIMENT_RESULT_SCHEMA_VERSION = "1"
EXPERIMENT_RESULT_STATUS = "COMPLETED"
EXPERIMENT_RESULT_OUTCOMES = {"MET", "NOT_MET", "INCONCLUSIVE"}


def _experiment_result_id_is_valid(value):
    return (isinstance(value, str)
            and bool(re.fullmatch(r"EXPERIMENT_RESULT\|[0-9a-f]{64}", value)))


def _experiment_result_references(experiment):
    return {
        "experiment": {
            "experiment_id": experiment["experiment_id"],
            "version": experiment["version"],
            "record_id": experiment["record_id"],
        },
        "hypothesis": experiment["references"]["hypothesis"],
        "dataset": experiment["references"]["dataset"],
    }


def _experiment_result_id(references):
    content = {
        "schema_version": EXPERIMENT_RESULT_SCHEMA_VERSION,
        "experiment": references["experiment"],
        "hypothesis_record_id": references["hypothesis"]["record_id"],
        "dataset_id": references["dataset"]["dataset_id"],
        "dataset_sha256": references["dataset"]["dataset_sha256"],
    }
    return "EXPERIMENT_RESULT|" + digest(encoded(content))


def _experiment_result_inputs(experiment, hypothesis, dataset, dataset_directory):
    directory = Path(dataset_directory)
    return {
        "experiment_record_sha256": digest(encoded(experiment)),
        "hypothesis_record_sha256": digest(encoded(hypothesis)),
        "dataset_manifest_sha256": digest((directory / "manifest.json").read_bytes()),
        "dataset_hashes": {
            name: dataset[name] for name in (
                "dataset_sha256", "selection_sha256", "validation_sha256", "raw_sha256",
                "capture_sha256", "code_sha256")
        },
    }


def _experiment_result_execution(code_revision):
    if not _hypothesis_code_revision_is_valid(code_revision):
        raise ValueError("Execution code revision must be a canonical full object identifier")
    source = _pipeline_source_bytes()
    return {"code_revision": code_revision, "pipeline_sha256": digest(source)}


def _experiment_result_dataset_rows(dataset_directory, conditions):
    """Parse the sealed CSV and derive only its independently evaluable observations.

    M4.1 production wiring (2026-09-28): the indicator is never recomputed
    as a hardcoded SMA3 -- the Strategy is reconstructed from `conditions`
    itself (its own sealed strategy_id/parameters) and its own compute() is
    used, so this stays a genuine independent recalculation for any Strategy,
    not just the legacy default.

    Etapa 2.7 return-horizon extension (2026-09-28): the forward-return
    distance is likewise reconstructed from `conditions` itself (never
    hardcoded to "next day"). "next_timestamp"/"next_close"/
    "return_t_plus_1" evidence field NAMES stay fixed regardless of horizon
    -- they mean "the row `horizon` days ahead", not literally day+1 -- to
    avoid forking the evidence schema a second time on top of the
    indicator-column fork M4.1 already introduced.
    """
    strategy = _experiment_conditions_strategy(conditions)
    horizon = _experiment_conditions_horizon(conditions)
    column_name = strategy["column_name"]
    forward_column = _hypothesis_dataset_forward_column(horizon)
    auxiliary_variables = _hypothesis_dataset_auxiliary_variables(strategy)
    warmup = strategy["required_inputs"]["warmup_periods"]
    columns = _hypothesis_dataset_columns(strategy, horizon)
    try:
        text = (Path(dataset_directory) / "dataset.csv").read_text(encoding="utf-8")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if reader.fieldnames != columns:
            raise ValueError("Historical dataset columns are invalid for experiment execution")
        raw_rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as error:
        raise ValueError("Historical dataset cannot be read for experiment execution") from error
    if not raw_rows:
        raise ValueError("Historical dataset has no rows for experiment execution")

    rows = []
    for number, row in enumerate(raw_rows, 1):
        if set(row) != set(columns):
            raise ValueError("Historical dataset row has unsupported fields")
        try:
            open_price = float(row["open"])
            high = float(row["high"])
            low = float(row["low"])
            close = float(row["close"])
            volume = float(row["volume"])
            indicator_value = None if row[column_name] == "" else float(row[column_name])
            forward = (None if row[forward_column] == ""
                       else float(row[forward_column]))
            auxiliary_values = {name: float(row[name]) for name in auxiliary_variables}
        except (TypeError, ValueError) as error:
            raise ValueError("Historical dataset has a nonnumeric experiment input") from error
        if (not _explicit_utc(row["timestamp"]) or not math.isfinite(close) or close <= 0
                or not math.isfinite(open_price) or not math.isfinite(high)
                or not math.isfinite(low) or not math.isfinite(volume)
                or (indicator_value is not None and not math.isfinite(indicator_value))
                or (forward is not None and not math.isfinite(forward))
                or not all(math.isfinite(value) for value in auxiliary_values.values())):
            raise ValueError("Historical dataset has an invalid experiment input")
        rows.append({
            "instrument": row["instrument"], "timestamp": row["timestamp"],
            "open": open_price, "high": high, "low": low, "close": close, "volume": volume,
            **auxiliary_values,
            column_name: indicator_value, forward_column: forward,
            "row_role": row["row_role"], "source_row": number,
        })

    frequency = conditions["frequency_seconds"]
    for index, row in enumerate(rows):
        if row["instrument"] != conditions["instrument"]:
            raise ValueError("Historical dataset instrument is incompatible with experiment")
        if index and epoch(row["timestamp"]) != epoch(rows[index - 1]["timestamp"]) + frequency:
            raise ValueError("Historical dataset has noncontiguous experiment rows")
    support_rows = [{"timestamp": row["timestamp"], "row_role": row["row_role"]}
                    for row in rows if row["row_role"] != HYPOTHESIS_DATASET_EVALUATION_ROLE]
    if support_rows != conditions["population"]["support_rows"]:
        raise ValueError("Historical dataset support rows are incompatible with experiment")

    period = conditions["temporal_split"]["independent_evaluation_period"]
    start, end = epoch(period["start_utc"]), epoch(period["end_exclusive_utc"])
    expected_timestamps = [iso(value) for value in range(start, end, frequency)]
    evidence = []
    for index, row in enumerate(rows):
        timestamp = epoch(row["timestamp"])
        is_evaluable = start <= timestamp < end
        if is_evaluable != (row["row_role"] == HYPOTHESIS_DATASET_EVALUATION_ROLE):
            raise ValueError("Historical dataset row role is incompatible with experiment period")
        if not is_evaluable:
            continue
        if index < warmup or index + horizon >= len(rows):
            raise ValueError("Historical dataset lacks indicator or forward-return support")
        window_rows = rows[index - warmup:index + 1]
        if any(item["close"] <= 0 for item in window_rows):
            raise ValueError("Historical dataset has invalid indicator source values")
        signal = strategy["compute"](window_rows)
        indicator_value = signal["indicator_value"]
        next_row = rows[index + horizon]
        if (not math.isfinite(indicator_value) or row[column_name] != indicator_value
                or epoch(next_row["timestamp"]) != timestamp + horizon * frequency):
            raise ValueError("Historical dataset indicator or forward-return support is invalid")
        forward_return = next_row["close"] / row["close"] - 1
        if not math.isfinite(forward_return) or row[forward_column] != forward_return:
            raise ValueError("Historical dataset forward return is invalid")
        group = signal["group"]
        evidence.append({
            "timestamp": row["timestamp"],
            "row_role": HYPOTHESIS_DATASET_EVALUATION_ROLE,
            "close": row["close"],
            column_name: indicator_value,
            "next_timestamp": next_row["timestamp"],
            "next_close": next_row["close"],
            "return_t_plus_1": forward_return,
            "group": group,
        })
    if [item["timestamp"] for item in evidence] != expected_timestamps:
        raise ValueError("Historical dataset does not cover the experiment evaluation period")
    return evidence


def _experiment_result_summary(evidence, criterion, analytical_rule):
    upper = [item["return_t_plus_1"] for item in evidence if item["group"] == "UPPER"]
    lower = [item["return_t_plus_1"] for item in evidence
             if item["group"] == "LOWER_OR_EQUAL"]
    upper_mean = math.fsum(upper) / len(upper) if upper else None
    lower_mean = math.fsum(lower) / len(lower) if lower else None
    groups = {
        "upper": {"rule": analytical_rule["upper_group"], "count": len(upper),
                  "mean_return_t_plus_1": upper_mean},
        "lower_or_equal": {"rule": analytical_rule["lower_or_equal_group"], "count": len(lower),
                           "mean_return_t_plus_1": lower_mean},
    }
    if (upper_mean is None or lower_mean is None
            or not math.isfinite(upper_mean) or not math.isfinite(lower_mean)):
        return {"groups": groups, "metric": None, "criterion": criterion,
                "criterion_result": "INCONCLUSIVE", "inconclusive_reason": "GROUP_EMPTY_OR_NONFINITE"}
    metric = upper_mean - lower_mean
    if not math.isfinite(metric):
        return {"groups": groups, "metric": None, "criterion": criterion,
                "criterion_result": "INCONCLUSIVE", "inconclusive_reason": "NONFINITE_METRIC"}
    if not _experiment_criterion_is_compatible(criterion, criterion["metric"]):
        raise ValueError("Experiment acceptance criterion is unsupported")
    return {"groups": groups, "metric": metric, "criterion": criterion,
            "criterion_result": _experiment_criterion_result(metric, criterion),
            "inconclusive_reason": None}


def _experiment_result_content(result_id, references, inputs, execution, evaluation, evidence):
    return {
        "result_id": result_id,
        "schema_version": EXPERIMENT_RESULT_SCHEMA_VERSION,
        "references": references,
        "inputs": inputs,
        "execution": execution,
        "evaluation": evaluation,
        "evidence": evidence,
        "status": EXPERIMENT_RESULT_STATUS,
    }


def _experiment_result_record(result_id, references, inputs, execution, evaluation, evidence):
    content = _experiment_result_content(
        result_id, references, inputs, execution, evaluation, evidence)
    return {**content, "record_id": "EXPERIMENT_RESULT_RECORD|" + result_id + "|"
            + digest(encoded(content))}


def _experiment_result_evaluation(period, evidence, criterion, analytical_rule):
    return {
        "period": period,
        "observation_count": len(evidence),
        **_experiment_result_summary(evidence, criterion, analytical_rule),
    }


def _experiment_result_record_is_valid(record):
    fields = {
        "result_id", "schema_version", "references", "inputs", "execution", "evaluation",
        "evidence", "status", "record_id"}
    if not isinstance(record, dict) or set(record) != fields:
        return False
    references = record.get("references")
    if (not _experiment_result_id_is_valid(record.get("result_id"))
            or record.get("schema_version") != EXPERIMENT_RESULT_SCHEMA_VERSION
            or not isinstance(references, dict) or set(references) != {
                "experiment", "hypothesis", "dataset"}
            or not isinstance(references["experiment"], dict)
            or set(references["experiment"]) != {"experiment_id", "version", "record_id"}
            or not _experiment_id_is_valid(references["experiment"].get("experiment_id"))
            or not isinstance(references["experiment"].get("version"), int)
            or not _hypothesis_text_is_valid(references["experiment"].get("record_id"))
            or not isinstance(references["hypothesis"], dict)
            or set(references["hypothesis"]) != {
                "hypothesis_id", "version", "record_id", "system_version", "code_revision"}
            or not _hypothesis_id_is_valid(references["hypothesis"].get("hypothesis_id"))
            or not isinstance(references["hypothesis"].get("version"), int)
            or not _hypothesis_text_is_valid(references["hypothesis"].get("record_id"))
            or not _hypothesis_system_version_is_valid(
                references["hypothesis"].get("system_version"))
            or not _hypothesis_code_revision_is_valid(
                references["hypothesis"].get("code_revision"))
            or not isinstance(references["dataset"], dict)
            or set(references["dataset"]) != {
                "dataset_id", "dataset_sha256", "hypothesis_id", "hypothesis_version",
                "evaluable_period", "support_rows"}
            or not references["dataset"].get("dataset_id", "").startswith(
                "HISTORICAL_HYPOTHESIS_DATASET|")
            or not re.fullmatch(r"[0-9a-f]{64}", references["dataset"].get("dataset_sha256", ""))
            or references["dataset"].get("hypothesis_id") != references["hypothesis"]["hypothesis_id"]
            or references["dataset"].get("hypothesis_version") != references["hypothesis"]["version"]
            or record["result_id"] != _experiment_result_id(references)
            or not isinstance(record.get("inputs"), dict)
            or set(record["inputs"]) != {
                "experiment_record_sha256", "hypothesis_record_sha256", "dataset_manifest_sha256",
                "dataset_hashes"}
            or not all(re.fullmatch(r"[0-9a-f]{64}", record["inputs"].get(name, ""))
                       for name in ("experiment_record_sha256", "hypothesis_record_sha256",
                                    "dataset_manifest_sha256"))
            or not isinstance(record["inputs"]["dataset_hashes"], dict)
            or set(record["inputs"]["dataset_hashes"]) != {
                "dataset_sha256", "selection_sha256", "validation_sha256", "raw_sha256",
                "capture_sha256", "code_sha256"}
            or not all(re.fullmatch(r"[0-9a-f]{64}", value)
                       for value in record["inputs"]["dataset_hashes"].values())
            or not isinstance(record.get("execution"), dict)
            or set(record["execution"]) != {"code_revision", "pipeline_sha256"}
            or not _hypothesis_code_revision_is_valid(record["execution"].get("code_revision"))
            or not re.fullmatch(r"[0-9a-f]{64}", record["execution"].get("pipeline_sha256", ""))
            or not isinstance(record.get("evidence"), list)
            or record.get("status") != EXPERIMENT_RESULT_STATUS):
        return False
    try:
        evaluation = record["evaluation"]
        if (not isinstance(evaluation, dict) or set(evaluation) != {
                "period", "observation_count", "groups", "metric", "criterion",
                "criterion_result", "inconclusive_reason"}
                or evaluation["observation_count"] != len(record["evidence"])
                or not isinstance(evaluation["period"], dict)
                or set(evaluation["period"]) != {"start_utc", "end_exclusive_utc"}
                or not _explicit_utc(evaluation["period"]["start_utc"])
                or not _explicit_utc(evaluation["period"]["end_exclusive_utc"])
                or epoch(evaluation["period"]["start_utc"])
                >= epoch(evaluation["period"]["end_exclusive_utc"])):
            return False
        period = evaluation["period"]
        expected_timestamps = [iso(value) for value in range(
            epoch(period["start_utc"]), epoch(period["end_exclusive_utc"]), 86400)]
        if len(record["evidence"]) != len(expected_timestamps):
            return False
        # M4.1 production wiring (2026-09-28): this record carries no
        # "conditions" (only a reference to the sealed Experiment that
        # defines them), so the indicator column name and analytical-rule
        # text can only be read from this record's own evidence/evaluation
        # shape here, not independently re-derived -- that grounding against
        # the true sealed Experiment happens in verified_experiment_result,
        # which reloads conditions and fully recalculates the evidence.
        groups = evaluation.get("groups")
        if (not isinstance(groups, dict) or set(groups) != {"upper", "lower_or_equal"}
                or not isinstance(groups.get("upper"), dict)
                or not isinstance(groups.get("lower_or_equal"), dict)
                or not _hypothesis_text_is_valid(groups["upper"].get("rule"))
                or not _hypothesis_text_is_valid(groups["lower_or_equal"].get("rule"))):
            return False
        analytical_rule = {
            "upper_group": groups["upper"]["rule"],
            "lower_or_equal_group": groups["lower_or_equal"]["rule"],
        }
        base_evidence_fields = {
            "timestamp", "row_role", "close", "next_timestamp", "next_close",
            "return_t_plus_1", "group",
        }
        column_name = None
        horizon_seconds = None
        if record["evidence"]:
            if not isinstance(record["evidence"][0], dict):
                return False
            extra_keys = set(record["evidence"][0]) - base_evidence_fields
            if len(extra_keys) != 1:
                return False
            column_name = next(iter(extra_keys))
            first = record["evidence"][0]
            if (not _explicit_utc(first.get("timestamp")) or not _explicit_utc(first.get("next_timestamp"))):
                return False
            horizon_seconds = epoch(first["next_timestamp"]) - epoch(first["timestamp"])
            if horizon_seconds <= 0 or horizon_seconds % 86400:
                return False
        for expected_timestamp, item in zip(expected_timestamps, record["evidence"]):
            if (not isinstance(item, dict) or set(item) != base_evidence_fields | {column_name}
                    or item["timestamp"] != expected_timestamp
                    or item["row_role"] != HYPOTHESIS_DATASET_EVALUATION_ROLE
                    or item["next_timestamp"] != iso(epoch(item["timestamp"]) + horizon_seconds)
                    or item["group"] not in {"UPPER", "LOWER_OR_EQUAL"}
                    or not all(isinstance(item[name], float) and math.isfinite(item[name])
                               for name in ("close", column_name, "next_close", "return_t_plus_1"))
                    or item["return_t_plus_1"] != item["next_close"] / item["close"] - 1):
                return False
            # M4.1 production wiring (2026-09-28): "group" is NOT re-derived
            # here as close-vs-indicator -- that comparison is only correct
            # for strategies whose classification variable IS close (SMA,
            # Momentum). A strategy classifying by another base variable
            # (e.g. VOLUME_SURGE, by volume) would fail this cheap check
            # even when genuinely valid. Full grounding of "group" against
            # the true Strategy still happens in verified_experiment_result,
            # which recomputes it via the real conditions/compute().
        expected_evaluation = _experiment_result_evaluation(
            period, record["evidence"], evaluation["criterion"], analytical_rule)
        if evaluation != expected_evaluation or evaluation["criterion_result"] not in EXPERIMENT_RESULT_OUTCOMES:
            return False
    except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError):
        return False
    expected = _experiment_result_record(
        record["result_id"], references, record["inputs"], record["execution"],
        record["evaluation"], record["evidence"])
    return record == expected


def _experiment_result_registry_is_valid(registry):
    if (not isinstance(registry, dict) or set(registry) != {"schema_version", "results"}
            or registry.get("schema_version") != EXPERIMENT_RESULT_REGISTRY_SCHEMA_VERSION
            or not isinstance(registry.get("results"), list)
            or not all(_experiment_result_record_is_valid(record) for record in registry["results"])):
        return False
    identifiers = [record["result_id"] for record in registry["results"]]
    return len(identifiers) == len(set(identifiers))


def _load_experiment_result_registry(registry_path):
    try:
        registry = json.loads(Path(registry_path).read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Persisted experiment result registry cannot be read") from error
    if not _experiment_result_registry_is_valid(registry):
        raise ValueError("Persisted experiment result registry is invalid")
    return registry


def _persist_experiment_result(registry_path, record):
    if not _experiment_result_record_is_valid(record):
        raise ValueError("Experiment result record is invalid")
    path = Path(registry_path)
    registry = (_load_experiment_result_registry(path) if path.exists()
                else {"schema_version": EXPERIMENT_RESULT_REGISTRY_SCHEMA_VERSION, "results": []})
    matches = [item for item in registry["results"] if item["result_id"] == record["result_id"]]
    if matches:
        if len(matches) == 1 and matches[0] == record:
            return matches[0]
        raise ValueError("Experiment result identity already exists with different content")
    registry["results"].append(record)
    if not _experiment_result_registry_is_valid(registry):
        raise ValueError("Constructed experiment result registry is invalid")
    _atomic_write(path, encoded(registry))
    return record


def _verified_experiment_result_inputs(experiment_registry_path, hypothesis_registry_path,
                                       dataset_directory, experiment_id, experiment_version,
                                       experiment_record_id, auxiliary_verifiers=None):
    experiment = verified_experiment_conditions(
        experiment_registry_path, experiment_id, experiment_version,
        hypothesis_registry_path=hypothesis_registry_path, dataset_directory=dataset_directory,
        auxiliary_verifiers=auxiliary_verifiers)
    if experiment["record_id"] != experiment_record_id:
        raise ValueError("Experiment seal does not match the requested execution")
    hypothesis = load_hypothesis(
        hypothesis_registry_path, experiment["references"]["hypothesis"]["hypothesis_id"],
        experiment["references"]["hypothesis"]["version"])
    strategy = _experiment_conditions_strategy(experiment["conditions"])
    horizon = _experiment_conditions_horizon(experiment["conditions"])
    dataset = verified_hypothesis_dataset(
        dataset_directory, hypothesis_registry_path, strategy, horizon, auxiliary_verifiers)
    references = _experiment_result_references(experiment)
    expected_hypothesis_reference = {
        "hypothesis_id": hypothesis["hypothesis_id"], "version": hypothesis["version"],
        "record_id": hypothesis["record_id"], "system_version": hypothesis["system_version"],
        "code_revision": hypothesis["code_revision"],
    }
    if references["hypothesis"] != expected_hypothesis_reference:
        raise ValueError("Experiment Hypothesis reference is invalid")
    if (dataset["dataset_id"] != references["dataset"]["dataset_id"]
            or dataset["dataset_sha256"] != references["dataset"]["dataset_sha256"]):
        raise ValueError("Experiment dataset reference is invalid")
    return experiment, hypothesis, dataset, references


def execute_experiment_result(registry_path, *, experiment_registry_path,
                              hypothesis_registry_path, dataset_directory, experiment_id,
                              experiment_version, experiment_record_id, execution_code_revision,
                              auxiliary_verifiers=None):
    """Execute one sealed definition locally and persist one deterministic aggregate result."""
    experiment, hypothesis, dataset, references = _verified_experiment_result_inputs(
        experiment_registry_path, hypothesis_registry_path, dataset_directory, experiment_id,
        experiment_version, experiment_record_id, auxiliary_verifiers)
    result_id = _experiment_result_id(references)
    path = Path(registry_path)
    if path.exists():
        registry = _load_experiment_result_registry(path)
        matching = [record for record in registry["results"] if record["result_id"] == result_id]
        if matching:
            if len(matching) != 1:
                raise ValueError("Experiment result identity is ambiguous")
            return verified_experiment_result(
                registry_path, result_id, experiment_registry_path=experiment_registry_path,
                hypothesis_registry_path=hypothesis_registry_path, dataset_directory=dataset_directory,
                auxiliary_verifiers=auxiliary_verifiers)
    evidence = _experiment_result_dataset_rows(dataset_directory, experiment["conditions"])
    evaluation = _experiment_result_evaluation(
        experiment["conditions"]["temporal_split"]["independent_evaluation_period"], evidence,
        experiment["conditions"]["acceptance_criterion"], experiment["conditions"]["analytical_rule"])
    record = _experiment_result_record(
        result_id, references, _experiment_result_inputs(
            experiment, hypothesis, dataset, dataset_directory),
        _experiment_result_execution(execution_code_revision), evaluation, evidence)
    return _persist_experiment_result(registry_path, record)


def load_experiment_result(registry_path, result_id):
    if not _experiment_result_id_is_valid(result_id):
        raise ValueError("A valid experiment result identity is required")
    registry = _load_experiment_result_registry(registry_path)
    matches = [record for record in registry["results"] if record["result_id"] == result_id]
    if len(matches) != 1:
        raise ValueError("Experiment result is not registered unambiguously")
    return matches[0]


def verified_experiment_result(registry_path, result_id, *, experiment_registry_path,
                               hypothesis_registry_path, dataset_directory,
                               auxiliary_verifiers=None):
    """Reload a result and independently recalculate it from the sealed dataset."""
    record = load_experiment_result(registry_path, result_id)
    reference = record["references"]["experiment"]
    experiment, hypothesis, dataset, references = _verified_experiment_result_inputs(
        experiment_registry_path, hypothesis_registry_path, dataset_directory,
        reference["experiment_id"], reference["version"], reference["record_id"],
        auxiliary_verifiers)
    if (record["references"] != references
            or record["inputs"] != _experiment_result_inputs(
                experiment, hypothesis, dataset, dataset_directory)):
        raise ValueError("Experiment result inputs are incompatible")
    evidence = _experiment_result_dataset_rows(dataset_directory, experiment["conditions"])
    evaluation = _experiment_result_evaluation(
        experiment["conditions"]["temporal_split"]["independent_evaluation_period"], evidence,
        experiment["conditions"]["acceptance_criterion"], experiment["conditions"]["analytical_rule"])
    expected = _experiment_result_record(
        record["result_id"], references, record["inputs"], record["execution"],
        evaluation, evidence)
    if record != expected:
        raise ValueError("Experiment result evidence or aggregate is invalid")
    return record


