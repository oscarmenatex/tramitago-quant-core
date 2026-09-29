"""CAP-003 Knowledge -- Knowledge Record (M2.4-T2): preserves hypothesis,
methodology, results, interpretation, and limitations. A rejected or
inconclusive disposition is preserved exactly like an accepted one
(DOC-004 REQ-004-004/P-004-004). Never modifies operational state --
only reads the already-sealed research chain and appends.

Moved out of pipeline.py per DOC-014/DOC-015 (adaptado) SS4 (Etapa 4, M4.1
module decomposition). No behavior change.
"""

import json
import re
from pathlib import Path

from tramitago_quant_core.shared.util import (
    digest, encoded, _atomic_write, _explicit_utc, _hypothesis_text_is_valid,
    _hypothesis_code_revision_is_valid, _pipeline_source_bytes,
)
from tramitago_quant_core.research.hypothesis import load_hypothesis, _hypothesis_id_is_valid
from tramitago_quant_core.research.historical_dataset import verified_hypothesis_dataset
from tramitago_quant_core.research.experiment import (
    verified_experiment_conditions, _experiment_id_is_valid,
)
from tramitago_quant_core.research.research_execution import (
    verified_research_result, verified_research_execution, _research_result_id_is_valid,
)
from tramitago_quant_core.strategy_evaluation.disposition import (
    verified_disposition, _disposition_id_is_valid, DISPOSITION_OUTCOMES,
)


KNOWLEDGE_REGISTRY_SCHEMA_VERSION = "1"
KNOWLEDGE_SCHEMA_VERSION = "1"
KNOWLEDGE_STATUS = "PRESERVED"


def _knowledge_id_is_valid(value):
    return (isinstance(value, str)
            and bool(re.fullmatch(r"KNOWLEDGE\|[0-9a-f]{64}", value)))


def _knowledge_hypothesis_snapshot(hypothesis):
    return {
        "hypothesis_id": hypothesis["hypothesis_id"], "version": hypothesis["version"],
        "record_id": hypothesis["record_id"], "description": hypothesis["description"],
        "target_metric": hypothesis["target_metric"],
        "expected_direction": hypothesis["expected_direction"],
        "acceptance_criterion": hypothesis["acceptance_criterion"],
    }


def _knowledge_results(disposition):
    return {
        "scientific_result": disposition["scientific_result"],
        "outcome": disposition["outcome"],
        "outcome_reason": disposition["outcome_reason"],
    }


def _knowledge_references(disposition, result, experiment):
    return {
        "disposition": {
            "disposition_id": disposition["disposition_id"], "record_id": disposition["record_id"]},
        "research_result": disposition["references"]["research_result"],
        "hypothesis": disposition["references"]["hypothesis"],
        "experiment": {
            "experiment_id": experiment["experiment_id"], "version": experiment["version"],
            "record_id": experiment["record_id"]},
    }


def _knowledge_id(references):
    content = {"schema_version": KNOWLEDGE_SCHEMA_VERSION, "disposition": references["disposition"]}
    return "KNOWLEDGE|" + digest(encoded(content))


def _knowledge_materialization(preserved_at, knowledge_code_revision):
    if (not _explicit_utc(preserved_at)
            or not _hypothesis_code_revision_is_valid(knowledge_code_revision)):
        raise ValueError("Knowledge preservation time and code revision are required")
    source = _pipeline_source_bytes()
    return {
        "preserved_at": preserved_at, "knowledge_code_revision": knowledge_code_revision,
        "pipeline_sha256": digest(source),
    }


def _knowledge_limitations_are_valid(limitations):
    return (isinstance(limitations, list) and bool(limitations)
            and all(_hypothesis_text_is_valid(item) for item in limitations)
            and len(limitations) == len(set(limitations)))


def _knowledge_content(knowledge_id, references, hypothesis, methodology, results,
                       interpretation, limitations, materialization):
    return {
        "knowledge_id": knowledge_id,
        "schema_version": KNOWLEDGE_SCHEMA_VERSION,
        "references": references,
        "hypothesis": hypothesis,
        "methodology": methodology,
        "results": results,
        "interpretation": interpretation,
        "limitations": limitations,
        "materialization": materialization,
        "status": KNOWLEDGE_STATUS,
    }


def _knowledge_record(knowledge_id, references, hypothesis, methodology, results,
                      interpretation, limitations, materialization):
    content = _knowledge_content(
        knowledge_id, references, hypothesis, methodology, results, interpretation,
        limitations, materialization)
    return {**content, "record_id": "KNOWLEDGE_RECORD|" + knowledge_id + "|"
            + digest(encoded(content))}


def _knowledge_record_is_valid(record):
    fields = {
        "knowledge_id", "schema_version", "references", "hypothesis", "methodology", "results",
        "interpretation", "limitations", "materialization", "status", "record_id"}
    if not isinstance(record, dict) or set(record) != fields:
        return False
    references = record.get("references")
    hypothesis = record.get("hypothesis")
    results = record.get("results")
    materialization = record.get("materialization")
    if (not _knowledge_id_is_valid(record.get("knowledge_id"))
            or record.get("schema_version") != KNOWLEDGE_SCHEMA_VERSION
            or not isinstance(references, dict) or set(references) != {
                "disposition", "research_result", "hypothesis", "experiment"}
            or not isinstance(references["disposition"], dict)
            or set(references["disposition"]) != {"disposition_id", "record_id"}
            or not _disposition_id_is_valid(references["disposition"].get("disposition_id"))
            or not _hypothesis_text_is_valid(references["disposition"].get("record_id"))
            or not isinstance(references["research_result"], dict)
            or set(references["research_result"]) != {"research_result_id", "record_id"}
            or not _research_result_id_is_valid(
                references["research_result"].get("research_result_id"))
            or not isinstance(references["hypothesis"], dict)
            or set(references["hypothesis"]) != {
                "hypothesis_id", "version", "record_id", "system_version", "code_revision"}
            or not _hypothesis_id_is_valid(references["hypothesis"].get("hypothesis_id"))
            or not isinstance(references["experiment"], dict)
            or set(references["experiment"]) != {"experiment_id", "version", "record_id"}
            or not _experiment_id_is_valid(references["experiment"].get("experiment_id"))
            or record["knowledge_id"] != _knowledge_id(references)
            or not isinstance(hypothesis, dict) or set(hypothesis) != {
                "hypothesis_id", "version", "record_id", "description", "target_metric",
                "expected_direction", "acceptance_criterion"}
            or hypothesis.get("hypothesis_id") != references["hypothesis"]["hypothesis_id"]
            or hypothesis.get("version") != references["hypothesis"]["version"]
            or hypothesis.get("record_id") != references["hypothesis"]["record_id"]
            or not _hypothesis_text_is_valid(hypothesis.get("description"))
            or not isinstance(record.get("methodology"), dict)
            or not isinstance(results, dict) or set(results) != {
                "scientific_result", "outcome", "outcome_reason"}
            or results.get("outcome") not in DISPOSITION_OUTCOMES
            or (results.get("outcome") == "INCONCLUSIVE") != (
                results.get("outcome_reason") is not None)
            or not isinstance(results.get("scientific_result"), dict)
            or not _hypothesis_text_is_valid(record.get("interpretation"))
            or not _knowledge_limitations_are_valid(record.get("limitations"))
            or not isinstance(materialization, dict) or set(materialization) != {
                "preserved_at", "knowledge_code_revision", "pipeline_sha256"}
            or not _explicit_utc(materialization.get("preserved_at"))
            or not _hypothesis_code_revision_is_valid(
                materialization.get("knowledge_code_revision"))
            or not re.fullmatch(r"[0-9a-f]{64}", materialization.get("pipeline_sha256", ""))
            or record.get("status") != KNOWLEDGE_STATUS):
        return False
    expected = _knowledge_record(
        record["knowledge_id"], references, hypothesis, record["methodology"], results,
        record["interpretation"], record["limitations"], materialization)
    return record == expected


def _knowledge_registry_is_valid(registry):
    if (not isinstance(registry, dict) or set(registry) != {"schema_version", "records"}
            or registry.get("schema_version") != KNOWLEDGE_REGISTRY_SCHEMA_VERSION
            or not isinstance(registry.get("records"), list)
            or not all(_knowledge_record_is_valid(item) for item in registry["records"])):
        return False
    identifiers = [item["knowledge_id"] for item in registry["records"]]
    return len(identifiers) == len(set(identifiers))


def _load_knowledge_registry(registry_path):
    try:
        registry = json.loads(Path(registry_path).read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Persisted Knowledge registry cannot be read") from error
    if not _knowledge_registry_is_valid(registry):
        raise ValueError("Persisted Knowledge registry is invalid")
    return registry


def _persist_knowledge_record(registry_path, record):
    if not _knowledge_record_is_valid(record):
        raise ValueError("Knowledge record is invalid")
    path = Path(registry_path)
    registry = (_load_knowledge_registry(path) if path.exists() else {
        "schema_version": KNOWLEDGE_REGISTRY_SCHEMA_VERSION, "records": []})
    matches = [item for item in registry["records"] if item["knowledge_id"] == record["knowledge_id"]]
    if matches:
        if len(matches) == 1 and matches[0] == record:
            return matches[0]
        raise ValueError("Knowledge identity already exists with different content")
    registry["records"].append(record)
    if not _knowledge_registry_is_valid(registry):
        raise ValueError("Constructed Knowledge registry is invalid")
    _atomic_write(path, encoded(registry))
    return record


def _knowledge_experiment(result, experiment_registry_path, hypothesis_registry_path,
                          dataset_directory, auxiliary_verifiers=None):
    experiment_reference = result["references"]["experiment"]
    experiment = verified_experiment_conditions(
        experiment_registry_path, experiment_reference["experiment_id"],
        experiment_reference["version"], hypothesis_registry_path=hypothesis_registry_path,
        dataset_directory=dataset_directory, auxiliary_verifiers=auxiliary_verifiers)
    if experiment["record_id"] != experiment_reference["record_id"]:
        raise ValueError("Knowledge experiment reference is invalid")
    return experiment


def constitute_knowledge_record(registry_path, *, disposition_registry_path, disposition_id,
                                disposition_record_id, research_result_registry_path,
                                research_execution_registry_path, legacy_result_registry_path,
                                experiment_registry_path, hypothesis_registry_path,
                                dataset_directory, interpretation, limitations, preserved_at,
                                knowledge_code_revision, auxiliary_verifiers=None):
    """M2.4-T2: preserve hypothesis, methodology, results, interpretation, and limitations.

    Structured, query-able, and permanent (DOC-004 REQ-004-004/P-004-004):
    a rejected or inconclusive disposition is preserved exactly like an
    accepted one. Never modifies a decision or any operational state --
    Knowledge only reads the already-sealed research chain and appends.
    """
    disposition = verified_disposition(
        disposition_registry_path, disposition_id,
        research_result_registry_path=research_result_registry_path,
        research_execution_registry_path=research_execution_registry_path,
        legacy_result_registry_path=legacy_result_registry_path,
        experiment_registry_path=experiment_registry_path,
        hypothesis_registry_path=hypothesis_registry_path, dataset_directory=dataset_directory,
        auxiliary_verifiers=auxiliary_verifiers)
    if disposition["record_id"] != disposition_record_id:
        raise ValueError("Disposition seal does not match the requested Knowledge record")
    hypothesis_reference = disposition["references"]["hypothesis"]
    hypothesis = load_hypothesis(
        hypothesis_registry_path, hypothesis_reference["hypothesis_id"],
        hypothesis_reference["version"])
    if hypothesis["record_id"] != hypothesis_reference["record_id"]:
        raise ValueError("Knowledge Hypothesis reference is invalid")
    result_reference = disposition["references"]["research_result"]
    result = verified_research_result(
        research_result_registry_path, result_reference["research_result_id"],
        research_execution_registry_path=research_execution_registry_path,
        legacy_result_registry_path=legacy_result_registry_path,
        experiment_registry_path=experiment_registry_path,
        hypothesis_registry_path=hypothesis_registry_path, dataset_directory=dataset_directory,
        auxiliary_verifiers=auxiliary_verifiers)
    experiment = _knowledge_experiment(
        result, experiment_registry_path, hypothesis_registry_path, dataset_directory,
        auxiliary_verifiers)

    if not _knowledge_limitations_are_valid(limitations):
        raise ValueError("Knowledge limitations must be a nonempty list of distinct statements")
    if not _hypothesis_text_is_valid(interpretation):
        raise ValueError("Knowledge interpretation is required")

    references = _knowledge_references(disposition, result, experiment)
    knowledge_id = _knowledge_id(references)
    path = Path(registry_path)
    if path.exists():
        registry = _load_knowledge_registry(path)
        matches = [item for item in registry["records"] if item["knowledge_id"] == knowledge_id]
        if matches:
            if len(matches) != 1:
                raise ValueError("Knowledge identity is ambiguous")
            return verified_knowledge(
                registry_path, knowledge_id,
                disposition_registry_path=disposition_registry_path,
                research_result_registry_path=research_result_registry_path,
                research_execution_registry_path=research_execution_registry_path,
                legacy_result_registry_path=legacy_result_registry_path,
                experiment_registry_path=experiment_registry_path,
                hypothesis_registry_path=hypothesis_registry_path,
                dataset_directory=dataset_directory, auxiliary_verifiers=auxiliary_verifiers)
    record = _knowledge_record(
        knowledge_id, references, _knowledge_hypothesis_snapshot(hypothesis),
        experiment["conditions"], _knowledge_results(disposition), interpretation, limitations,
        _knowledge_materialization(preserved_at, knowledge_code_revision))
    return _persist_knowledge_record(registry_path, record)


def load_knowledge(registry_path, knowledge_id):
    if not _knowledge_id_is_valid(knowledge_id):
        raise ValueError("A valid Knowledge identity is required")
    registry = _load_knowledge_registry(registry_path)
    matches = [item for item in registry["records"] if item["knowledge_id"] == knowledge_id]
    if len(matches) != 1:
        raise ValueError("Knowledge record is not registered unambiguously")
    return matches[0]


def query_knowledge(registry_path, *, hypothesis_id=None, outcome=None):
    """Read-only consultation surface -- never mutates state, no side effects.

    A missing registry means no Knowledge has been preserved yet, not an
    error: an empty query result is valid.
    """
    path = Path(registry_path)
    if not path.exists():
        return []
    records = _load_knowledge_registry(path)["records"]
    if hypothesis_id is not None:
        records = [item for item in records if item["hypothesis"]["hypothesis_id"] == hypothesis_id]
    if outcome is not None:
        records = [item for item in records if item["results"]["outcome"] == outcome]
    return records


def verified_knowledge(registry_path, knowledge_id, *, disposition_registry_path,
                       research_result_registry_path, research_execution_registry_path,
                       legacy_result_registry_path, experiment_registry_path,
                       hypothesis_registry_path, dataset_directory, auxiliary_verifiers=None):
    """Reload a Knowledge record and reproduce it from the sealed research chain."""
    record = load_knowledge(registry_path, knowledge_id)
    disposition_reference = record["references"]["disposition"]
    disposition = verified_disposition(
        disposition_registry_path, disposition_reference["disposition_id"],
        research_result_registry_path=research_result_registry_path,
        research_execution_registry_path=research_execution_registry_path,
        legacy_result_registry_path=legacy_result_registry_path,
        experiment_registry_path=experiment_registry_path,
        hypothesis_registry_path=hypothesis_registry_path, dataset_directory=dataset_directory,
        auxiliary_verifiers=auxiliary_verifiers)
    if disposition["record_id"] != disposition_reference["record_id"]:
        raise ValueError("Knowledge Disposition reference is invalid")
    hypothesis_reference = disposition["references"]["hypothesis"]
    hypothesis = load_hypothesis(
        hypothesis_registry_path, hypothesis_reference["hypothesis_id"],
        hypothesis_reference["version"])
    if hypothesis["record_id"] != hypothesis_reference["record_id"]:
        raise ValueError("Knowledge Hypothesis reference is invalid")
    result_reference = disposition["references"]["research_result"]
    result = verified_research_result(
        research_result_registry_path, result_reference["research_result_id"],
        research_execution_registry_path=research_execution_registry_path,
        legacy_result_registry_path=legacy_result_registry_path,
        experiment_registry_path=experiment_registry_path,
        hypothesis_registry_path=hypothesis_registry_path, dataset_directory=dataset_directory,
        auxiliary_verifiers=auxiliary_verifiers)
    experiment = _knowledge_experiment(
        result, experiment_registry_path, hypothesis_registry_path, dataset_directory,
        auxiliary_verifiers)
    references = _knowledge_references(disposition, result, experiment)
    expected = _knowledge_record(
        record["knowledge_id"], references, _knowledge_hypothesis_snapshot(hypothesis),
        experiment["conditions"], _knowledge_results(disposition), record["interpretation"],
        record["limitations"], record["materialization"])
    if record != expected:
        raise ValueError("Knowledge hypothesis, methodology, or results are invalid")
    return record


