"""CAP-004 Strategy Evaluation -- Disposition (M2.4-T1): judges a
Research Result against its Hypothesis's predefined criterion.
ACCEPTED/REJECTED/INCONCLUSIVE, never left unclassified (REQ-004-005,
HY-AC-006). Never promotes a strategy to PAPER or LIVE.

Moved out of pipeline.py per DOC-014/DOC-015 (adaptado) SS4 (Etapa 4, M4.1
module decomposition). No behavior change.
"""

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from tramitago_quant_core.shared.util import (
    digest, encoded, _atomic_write, _explicit_utc, _hypothesis_text_is_valid,
    _hypothesis_code_revision_is_valid, _hypothesis_system_version_is_valid,
    _pipeline_source_bytes,
)
from tramitago_quant_core.research.hypothesis import (
    load_hypothesis, _hypothesis_id_is_valid, HYPOTHESIS_ACCEPTANCE_COMPARISONS,
)
from tramitago_quant_core.research.historical_dataset import verified_hypothesis_dataset
from tramitago_quant_core.research.experiment import (
    verified_experiment_conditions, EXPERIMENT_RESULT_OUTCOMES,
)
from tramitago_quant_core.research.research_execution import (
    verified_research_result, load_research_result, verified_research_execution,
    _research_result_id_is_valid, _research_execution_id_is_valid,
)


DISPOSITION_REGISTRY_SCHEMA_VERSION = "1"
DISPOSITION_SCHEMA_VERSION = "1"
DISPOSITION_STATUS = "ISSUED"
DISPOSITION_OUTCOMES = {"ACCEPTED", "REJECTED", "INCONCLUSIVE"}
_DISPOSITION_COMPARATORS = {
    "GT": lambda value, threshold: value > threshold,
    "GE": lambda value, threshold: value >= threshold,
    "LT": lambda value, threshold: value < threshold,
    "LE": lambda value, threshold: value <= threshold,
}


def _disposition_id_is_valid(value):
    return (isinstance(value, str)
            and bool(re.fullmatch(r"DISPOSITION\|[0-9a-f]{64}", value)))


def _disposition_references(result, execution):
    return {
        "research_result": {
            "research_result_id": result["research_result_id"], "record_id": result["record_id"]},
        "research_execution": result["references"]["research_execution"],
        "hypothesis": execution["references"]["hypothesis"],
    }


def _disposition_id(references):
    content = {
        "schema_version": DISPOSITION_SCHEMA_VERSION,
        "research_result": references["research_result"],
    }
    return "DISPOSITION|" + digest(encoded(content))


def _disposition_outcome(criterion, scientific_result):
    """Independently apply the Hypothesis's predefined criterion to observed evidence.

    Never trusts a Research Result's own criterion_result label -- HY-AC-006
    requires the disposition to be traceable to the criterion applied to the
    evidence, not to echo a classification computed elsewhere.
    """
    if (scientific_result.get("criterion_result") == "INCONCLUSIVE"
            or scientific_result.get("metric") is None):
        reason = scientific_result.get("inconclusive_reason") or "EVIDENCE_INSUFFICIENT"
        return "INCONCLUSIVE", reason
    threshold = Decimal(criterion["threshold"])
    value = Decimal(repr(scientific_result["metric"]))
    met = _DISPOSITION_COMPARATORS[criterion["comparison"]](value, threshold)
    return ("ACCEPTED" if met else "REJECTED"), None


def _disposition_materialization(disposed_at, disposition_code_revision):
    if (not _explicit_utc(disposed_at)
            or not _hypothesis_code_revision_is_valid(disposition_code_revision)):
        raise ValueError("Disposition time and code revision are required")
    source = _pipeline_source_bytes()
    return {
        "disposed_at": disposed_at, "disposition_code_revision": disposition_code_revision,
        "pipeline_sha256": digest(source),
    }


def _disposition_content(disposition_id, references, criterion_applied, scientific_result,
                         outcome, outcome_reason, materialization):
    return {
        "disposition_id": disposition_id,
        "schema_version": DISPOSITION_SCHEMA_VERSION,
        "references": references,
        "criterion_applied": criterion_applied,
        "scientific_result": scientific_result,
        "outcome": outcome,
        "outcome_reason": outcome_reason,
        "materialization": materialization,
        "status": DISPOSITION_STATUS,
    }


def _disposition_record(disposition_id, references, criterion_applied, scientific_result,
                        outcome, outcome_reason, materialization):
    content = _disposition_content(
        disposition_id, references, criterion_applied, scientific_result, outcome,
        outcome_reason, materialization)
    return {**content, "record_id": "DISPOSITION_RECORD|" + disposition_id + "|"
            + digest(encoded(content))}


def _disposition_record_is_valid(record):
    fields = {
        "disposition_id", "schema_version", "references", "criterion_applied",
        "scientific_result", "outcome", "outcome_reason", "materialization", "status",
        "record_id"}
    if not isinstance(record, dict) or set(record) != fields:
        return False
    references = record.get("references")
    criterion = record.get("criterion_applied")
    scientific_result = record.get("scientific_result")
    materialization = record.get("materialization")
    outcome = record.get("outcome")
    if (not _disposition_id_is_valid(record.get("disposition_id"))
            or record.get("schema_version") != DISPOSITION_SCHEMA_VERSION
            or not isinstance(references, dict) or set(references) != {
                "research_result", "research_execution", "hypothesis"}
            or not isinstance(references["research_result"], dict)
            or set(references["research_result"]) != {"research_result_id", "record_id"}
            or not _research_result_id_is_valid(references["research_result"].get(
                "research_result_id"))
            or not _hypothesis_text_is_valid(references["research_result"].get("record_id"))
            or not isinstance(references["research_execution"], dict)
            or set(references["research_execution"]) != {"execution_id", "record_id"}
            or not _research_execution_id_is_valid(
                references["research_execution"].get("execution_id"))
            or not _hypothesis_text_is_valid(references["research_execution"].get("record_id"))
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
            or record["disposition_id"] != _disposition_id(references)
            or not isinstance(criterion, dict) or set(criterion) != {
                "metric", "comparison", "threshold", "expected_direction"}
            or not _hypothesis_text_is_valid(criterion.get("metric"))
            or criterion.get("expected_direction") not in HYPOTHESIS_ACCEPTANCE_COMPARISONS
            or criterion.get("comparison") not in HYPOTHESIS_ACCEPTANCE_COMPARISONS.get(
                criterion.get("expected_direction"), ())
            or not isinstance(scientific_result, dict)
            or scientific_result.get("criterion") != criterion
            or outcome not in DISPOSITION_OUTCOMES
            or (outcome == "INCONCLUSIVE") != (record.get("outcome_reason") is not None)
            or (record.get("outcome_reason") is not None
                and not _hypothesis_text_is_valid(record["outcome_reason"]))
            or not isinstance(materialization, dict)
            or set(materialization) != {
                "disposed_at", "disposition_code_revision", "pipeline_sha256"}
            or not _explicit_utc(materialization.get("disposed_at"))
            or not _hypothesis_code_revision_is_valid(
                materialization.get("disposition_code_revision"))
            or not re.fullmatch(r"[0-9a-f]{64}", materialization.get("pipeline_sha256", ""))
            or record.get("status") != DISPOSITION_STATUS):
        return False
    try:
        Decimal(criterion["threshold"])
    except (InvalidOperation, ValueError, KeyError):
        return False
    expected_outcome, expected_reason = _disposition_outcome(criterion, scientific_result)
    if outcome != expected_outcome or record.get("outcome_reason") != expected_reason:
        return False
    expected = _disposition_record(
        record["disposition_id"], references, criterion, scientific_result, outcome,
        record.get("outcome_reason"), materialization)
    return record == expected


def _disposition_registry_is_valid(registry):
    if (not isinstance(registry, dict)
            or set(registry) != {"schema_version", "dispositions"}
            or registry.get("schema_version") != DISPOSITION_REGISTRY_SCHEMA_VERSION
            or not isinstance(registry.get("dispositions"), list)
            or not all(_disposition_record_is_valid(item) for item in registry["dispositions"])):
        return False
    identifiers = [item["disposition_id"] for item in registry["dispositions"]]
    return len(identifiers) == len(set(identifiers))


def _load_disposition_registry(registry_path):
    try:
        registry = json.loads(Path(registry_path).read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Persisted Disposition registry cannot be read") from error
    if not _disposition_registry_is_valid(registry):
        raise ValueError("Persisted Disposition registry is invalid")
    return registry


def _persist_disposition(registry_path, record):
    if not _disposition_record_is_valid(record):
        raise ValueError("Disposition record is invalid")
    path = Path(registry_path)
    registry = (_load_disposition_registry(path) if path.exists() else {
        "schema_version": DISPOSITION_REGISTRY_SCHEMA_VERSION, "dispositions": []})
    matches = [item for item in registry["dispositions"]
               if item["disposition_id"] == record["disposition_id"]]
    if matches:
        if len(matches) == 1 and matches[0] == record:
            return matches[0]
        raise ValueError("Disposition identity already exists with different content")
    registry["dispositions"].append(record)
    if not _disposition_registry_is_valid(registry):
        raise ValueError("Constructed Disposition registry is invalid")
    _atomic_write(path, encoded(registry))
    return record


def _disposition_hypothesis(execution, hypothesis_registry_path):
    hypothesis_reference = execution["references"]["hypothesis"]
    hypothesis = load_hypothesis(
        hypothesis_registry_path, hypothesis_reference["hypothesis_id"],
        hypothesis_reference["version"])
    if hypothesis["record_id"] != hypothesis_reference["record_id"]:
        raise ValueError("Disposition Hypothesis reference is invalid")
    return hypothesis


def constitute_disposition(registry_path, *, research_result_registry_path, research_result_id,
                           research_result_record_id, research_execution_registry_path,
                           legacy_result_registry_path, experiment_registry_path,
                           hypothesis_registry_path, dataset_directory, disposed_at,
                           disposition_code_revision):
    """M2.4-T1: judge a Research Result against its Hypothesis's predefined criterion.

    Never promotes a strategy to PAPER or LIVE -- it only issues a
    traceable ACCEPTED/REJECTED/INCONCLUSIVE disposition (REQ-004-005,
    HY-AC-006), classifying insufficient evidence as INCONCLUSIVE rather
    than leaving a Research Result unclassified.
    """
    result = verified_research_result(
        research_result_registry_path, research_result_id,
        research_execution_registry_path=research_execution_registry_path,
        legacy_result_registry_path=legacy_result_registry_path,
        experiment_registry_path=experiment_registry_path,
        hypothesis_registry_path=hypothesis_registry_path, dataset_directory=dataset_directory)
    if result["record_id"] != research_result_record_id:
        raise ValueError("Research Result seal does not match the requested Disposition")
    execution_reference = result["references"]["research_execution"]
    execution = verified_research_execution(
        research_execution_registry_path, execution_reference["execution_id"],
        legacy_result_registry_path=legacy_result_registry_path,
        experiment_registry_path=experiment_registry_path,
        hypothesis_registry_path=hypothesis_registry_path, dataset_directory=dataset_directory)
    hypothesis = _disposition_hypothesis(execution, hypothesis_registry_path)
    if result["scientific_result"]["criterion"] != hypothesis["acceptance_criterion"]:
        raise ValueError("Disposition criterion is incompatible with the governing Hypothesis")
    references = _disposition_references(result, execution)
    disposition_id = _disposition_id(references)
    path = Path(registry_path)
    if path.exists():
        registry = _load_disposition_registry(path)
        matches = [item for item in registry["dispositions"]
                   if item["disposition_id"] == disposition_id]
        if matches:
            if len(matches) != 1:
                raise ValueError("Disposition identity is ambiguous")
            return verified_disposition(
                registry_path, disposition_id,
                research_result_registry_path=research_result_registry_path,
                research_execution_registry_path=research_execution_registry_path,
                legacy_result_registry_path=legacy_result_registry_path,
                experiment_registry_path=experiment_registry_path,
                hypothesis_registry_path=hypothesis_registry_path,
                dataset_directory=dataset_directory)
    outcome, outcome_reason = _disposition_outcome(
        hypothesis["acceptance_criterion"], result["scientific_result"])
    record = _disposition_record(
        disposition_id, references, hypothesis["acceptance_criterion"],
        result["scientific_result"], outcome, outcome_reason,
        _disposition_materialization(disposed_at, disposition_code_revision))
    return _persist_disposition(registry_path, record)


def load_disposition(registry_path, disposition_id):
    if not _disposition_id_is_valid(disposition_id):
        raise ValueError("A valid Disposition identity is required")
    registry = _load_disposition_registry(registry_path)
    matches = [item for item in registry["dispositions"]
               if item["disposition_id"] == disposition_id]
    if len(matches) != 1:
        raise ValueError("Disposition is not registered unambiguously")
    return matches[0]


def verified_disposition(registry_path, disposition_id, *, research_result_registry_path,
                         research_execution_registry_path, legacy_result_registry_path,
                         experiment_registry_path, hypothesis_registry_path, dataset_directory):
    """Reload a Disposition and reproduce its outcome from the Hypothesis's own criterion."""
    record = load_disposition(registry_path, disposition_id)
    result_reference = record["references"]["research_result"]
    result = verified_research_result(
        research_result_registry_path, result_reference["research_result_id"],
        research_execution_registry_path=research_execution_registry_path,
        legacy_result_registry_path=legacy_result_registry_path,
        experiment_registry_path=experiment_registry_path,
        hypothesis_registry_path=hypothesis_registry_path, dataset_directory=dataset_directory)
    if result["record_id"] != result_reference["record_id"]:
        raise ValueError("Disposition Research Result reference is invalid")
    execution_reference = result["references"]["research_execution"]
    execution = verified_research_execution(
        research_execution_registry_path, execution_reference["execution_id"],
        legacy_result_registry_path=legacy_result_registry_path,
        experiment_registry_path=experiment_registry_path,
        hypothesis_registry_path=hypothesis_registry_path, dataset_directory=dataset_directory)
    hypothesis = _disposition_hypothesis(execution, hypothesis_registry_path)
    if result["scientific_result"]["criterion"] != hypothesis["acceptance_criterion"]:
        raise ValueError("Disposition criterion is incompatible with the governing Hypothesis")
    references = _disposition_references(result, execution)
    outcome, outcome_reason = _disposition_outcome(
        hypothesis["acceptance_criterion"], result["scientific_result"])
    expected = _disposition_record(
        record["disposition_id"], references, hypothesis["acceptance_criterion"],
        result["scientific_result"], outcome, outcome_reason, record["materialization"])
    if record != expected:
        raise ValueError("Disposition outcome, criterion, or evidence is invalid")
    return record


