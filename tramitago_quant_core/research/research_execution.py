"""CAP-002 Research -- Research Execution and Research Result (M2.3):
materializes a Research Execution and Research Result post-hoc from an
already-sealed legacy Experiment Result, without recomputing or
reinterpreting it.

Moved out of pipeline.py per DOC-014/DOC-015 (adaptado) SS4 (Etapa 4, M4.1
module decomposition). No behavior change.
"""

import json
import re
from pathlib import Path

from tramitago_quant_core.shared.util import (
    digest, encoded, epoch, iso, _atomic_write, _explicit_utc, _hypothesis_text_is_valid,
    _hypothesis_system_version_is_valid, _hypothesis_code_revision_is_valid,
    _pipeline_source_bytes,
)
from tramitago_quant_core.research.hypothesis import _hypothesis_id_is_valid, load_hypothesis
from tramitago_quant_core.research.historical_dataset import verified_hypothesis_dataset
from tramitago_quant_core.research.experiment import (
    _experiment_id_is_valid, verified_experiment_conditions,
    _experiment_result_id_is_valid, load_experiment_result, verified_experiment_result,
    _verified_experiment_result_inputs, EXPERIMENT_RESULT_OUTCOMES, EXPERIMENT_COSTS_NOT_APPLICABLE,
)


RESEARCH_EXECUTION_REGISTRY_SCHEMA_VERSION = "1"
RESEARCH_EXECUTION_SCHEMA_VERSION = "1"
RESEARCH_EXECUTION_STATUS = "MATERIALIZED_FROM_LEGACY_RESULT"
RESEARCH_RESULT_REGISTRY_SCHEMA_VERSION = "1"
RESEARCH_RESULT_SCHEMA_VERSION = "1"
RESEARCH_RESULT_STATUS = "PRESERVED_SCIENTIFIC_RESULT"
RESEARCH_MATERIALIZATION_PROVENANCE = (
    "CONSTITUTED_AFTER_ORIGINAL_EXECUTION_FROM_LEGACY_SEALED_RESULT")


def _research_execution_id_is_valid(value):
    return (isinstance(value, str)
            and bool(re.fullmatch(r"RESEARCH_EXECUTION\|[0-9a-f]{64}", value)))


def _research_result_id_is_valid(value):
    return (isinstance(value, str)
            and bool(re.fullmatch(r"RESEARCH_RESULT\|[0-9a-f]{64}", value)))


def _research_materialization(materialized_at, materialization_code_revision):
    if (not _explicit_utc(materialized_at)
            or not _hypothesis_code_revision_is_valid(materialization_code_revision)):
        raise ValueError("Research materialization time and code revision are required")
    source = _pipeline_source_bytes()
    return {
        "constituted_at": materialized_at,
        "materialization_code_revision": materialization_code_revision,
        "pipeline_sha256": digest(source),
        "provenance": RESEARCH_MATERIALIZATION_PROVENANCE,
        "original_execution_time": "NOT_RECORDED",
    }


def _research_execution_references(legacy_result):
    return {
        "legacy_result": {
            "result_id": legacy_result["result_id"],
            "record_id": legacy_result["record_id"],
        },
        "experiment": legacy_result["references"]["experiment"],
        "hypothesis": legacy_result["references"]["hypothesis"],
        "dataset": legacy_result["references"]["dataset"],
    }


def _research_execution_id(references):
    content = {
        "schema_version": RESEARCH_EXECUTION_SCHEMA_VERSION,
        "legacy_result": references["legacy_result"],
        "experiment": references["experiment"],
        "hypothesis_record_id": references["hypothesis"]["record_id"],
        "dataset_id": references["dataset"]["dataset_id"],
        "dataset_sha256": references["dataset"]["dataset_sha256"],
    }
    return "RESEARCH_EXECUTION|" + digest(encoded(content))


def _research_execution_input_hashes(legacy_result, legacy_result_registry_path):
    return {
        "legacy_result_registry_sha256": digest(Path(legacy_result_registry_path).read_bytes()),
        "legacy_result_record_sha256": digest(encoded(legacy_result)),
        "experiment_record_sha256": legacy_result["inputs"]["experiment_record_sha256"],
        "hypothesis_record_sha256": legacy_result["inputs"]["hypothesis_record_sha256"],
        "dataset_manifest_sha256": legacy_result["inputs"]["dataset_manifest_sha256"],
        "dataset_hashes": legacy_result["inputs"]["dataset_hashes"],
    }


def _research_execution_content(execution_id, references, input_hashes, original_execution,
                                materialization, scientific_result, causal_evidence):
    return {
        "execution_id": execution_id,
        "schema_version": RESEARCH_EXECUTION_SCHEMA_VERSION,
        "references": references,
        "input_hashes": input_hashes,
        "original_execution": original_execution,
        "materialization": materialization,
        "evaluated_period": scientific_result["period"],
        "excluded_support_rows": references["dataset"]["support_rows"],
        "causal_evidence": causal_evidence,
        "scientific_result": scientific_result,
        "terminal_result": scientific_result["criterion_result"],
        "status": RESEARCH_EXECUTION_STATUS,
    }


def _research_execution_record(execution_id, references, input_hashes, original_execution,
                               materialization, scientific_result, causal_evidence):
    content = _research_execution_content(
        execution_id, references, input_hashes, original_execution, materialization,
        scientific_result, causal_evidence)
    return {**content, "record_id": "RESEARCH_EXECUTION_RECORD|" + execution_id + "|"
            + digest(encoded(content))}


def _research_execution_record_is_valid(record):
    fields = {
        "execution_id", "schema_version", "references", "input_hashes", "original_execution",
        "materialization", "evaluated_period", "excluded_support_rows", "causal_evidence",
        "scientific_result", "terminal_result", "status", "record_id"}
    if not isinstance(record, dict) or set(record) != fields:
        return False
    references = record.get("references")
    hashes = record.get("input_hashes")
    materialization = record.get("materialization")
    if (not _research_execution_id_is_valid(record.get("execution_id"))
            or record.get("schema_version") != RESEARCH_EXECUTION_SCHEMA_VERSION
            or not isinstance(references, dict) or set(references) != {
                "legacy_result", "experiment", "hypothesis", "dataset"}
            or not isinstance(references["legacy_result"], dict)
            or set(references["legacy_result"]) != {"result_id", "record_id"}
            or not _experiment_result_id_is_valid(references["legacy_result"].get("result_id"))
            or not _hypothesis_text_is_valid(references["legacy_result"].get("record_id"))
            or not isinstance(references["experiment"], dict)
            or set(references["experiment"]) != {"experiment_id", "version", "record_id"}
            or not _experiment_id_is_valid(references["experiment"].get("experiment_id"))
            or not isinstance(references["experiment"].get("version"), int)
            or not _hypothesis_text_is_valid(references["experiment"].get("record_id"))
            or not isinstance(references["hypothesis"], dict)
            or set(references["hypothesis"]) != {
                "hypothesis_id", "version", "record_id", "system_version", "code_revision"}
            or not _hypothesis_id_is_valid(references["hypothesis"].get("hypothesis_id"))
            or not isinstance(references["dataset"], dict)
            or set(references["dataset"]) != {
                "dataset_id", "dataset_sha256", "hypothesis_id", "hypothesis_version",
                "evaluable_period", "support_rows"}
            or not _research_execution_id(references) == record["execution_id"]
            or not isinstance(hashes, dict) or set(hashes) != {
                "legacy_result_registry_sha256", "legacy_result_record_sha256",
                "experiment_record_sha256", "hypothesis_record_sha256",
                "dataset_manifest_sha256", "dataset_hashes"}
            or not all(re.fullmatch(r"[0-9a-f]{64}", hashes.get(name, "")) for name in (
                "legacy_result_registry_sha256", "legacy_result_record_sha256",
                "experiment_record_sha256", "hypothesis_record_sha256",
                "dataset_manifest_sha256"))
            or not isinstance(hashes["dataset_hashes"], dict)
            or set(hashes["dataset_hashes"]) != {
                "dataset_sha256", "selection_sha256", "validation_sha256", "raw_sha256",
                "capture_sha256", "code_sha256"}
            or not all(re.fullmatch(r"[0-9a-f]{64}", value)
                       for value in hashes["dataset_hashes"].values())
            or not isinstance(record.get("original_execution"), dict)
            or set(record["original_execution"]) != {"code_revision", "pipeline_sha256"}
            or not _hypothesis_code_revision_is_valid(
                record["original_execution"].get("code_revision"))
            or not re.fullmatch(r"[0-9a-f]{64}",
                               record["original_execution"].get("pipeline_sha256", ""))
            or not isinstance(materialization, dict)
            or set(materialization) != {
                "constituted_at", "materialization_code_revision", "pipeline_sha256",
                "provenance", "original_execution_time"}
            or not _explicit_utc(materialization.get("constituted_at"))
            or not _hypothesis_code_revision_is_valid(
                materialization.get("materialization_code_revision"))
            or not re.fullmatch(r"[0-9a-f]{64}", materialization.get("pipeline_sha256", ""))
            or materialization.get("provenance") != RESEARCH_MATERIALIZATION_PROVENANCE
            or materialization.get("original_execution_time") != "NOT_RECORDED"
            or not isinstance(record.get("evaluated_period"), dict)
            or record["evaluated_period"] != references["dataset"]["evaluable_period"]
            or not isinstance(record.get("excluded_support_rows"), list)
            or record["excluded_support_rows"] != references["dataset"]["support_rows"]
            or not isinstance(record.get("causal_evidence"), list)
            or not isinstance(record.get("scientific_result"), dict)
            or record["scientific_result"].get("period") != record["evaluated_period"]
            or record["terminal_result"] != record["scientific_result"].get("criterion_result")
            or record["terminal_result"] not in EXPERIMENT_RESULT_OUTCOMES
            or record.get("status") != RESEARCH_EXECUTION_STATUS):
        return False
    expected = _research_execution_record(
        record["execution_id"], references, hashes, record["original_execution"], materialization,
        record["scientific_result"], record["causal_evidence"])
    return record == expected


def _research_execution_registry_is_valid(registry):
    if (not isinstance(registry, dict) or set(registry) != {"schema_version", "executions"}
            or registry.get("schema_version") != RESEARCH_EXECUTION_REGISTRY_SCHEMA_VERSION
            or not isinstance(registry.get("executions"), list)
            or not all(_research_execution_record_is_valid(item) for item in registry["executions"])):
        return False
    identifiers = [item["execution_id"] for item in registry["executions"]]
    return len(identifiers) == len(set(identifiers))


def _load_research_execution_registry(registry_path):
    try:
        registry = json.loads(Path(registry_path).read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Persisted Research Execution registry cannot be read") from error
    if not _research_execution_registry_is_valid(registry):
        raise ValueError("Persisted Research Execution registry is invalid")
    return registry


def _persist_research_execution(registry_path, record):
    if not _research_execution_record_is_valid(record):
        raise ValueError("Research Execution record is invalid")
    path = Path(registry_path)
    registry = (_load_research_execution_registry(path) if path.exists() else {
        "schema_version": RESEARCH_EXECUTION_REGISTRY_SCHEMA_VERSION, "executions": []})
    matches = [item for item in registry["executions"]
               if item["execution_id"] == record["execution_id"]]
    if matches:
        if len(matches) == 1 and matches[0] == record:
            return matches[0]
        raise ValueError("Research Execution identity already exists with different content")
    registry["executions"].append(record)
    if not _research_execution_registry_is_valid(registry):
        raise ValueError("Constructed Research Execution registry is invalid")
    _atomic_write(path, encoded(registry))
    return record


def _verified_legacy_result(legacy_result_registry_path, legacy_result_id,
                            legacy_result_record_id, experiment_registry_path,
                            hypothesis_registry_path, dataset_directory,
                            auxiliary_verifiers=None):
    legacy = verified_experiment_result(
        legacy_result_registry_path, legacy_result_id,
        experiment_registry_path=experiment_registry_path,
        hypothesis_registry_path=hypothesis_registry_path, dataset_directory=dataset_directory,
        auxiliary_verifiers=auxiliary_verifiers)
    if legacy["record_id"] != legacy_result_record_id:
        raise ValueError("Legacy Research Result seal does not match")
    return legacy


def constitute_research_execution(registry_path, *, legacy_result_registry_path,
                                  legacy_result_id, legacy_result_record_id,
                                  experiment_registry_path, hypothesis_registry_path,
                                  dataset_directory, materialized_at,
                                  materialization_code_revision, auxiliary_verifiers=None):
    """Append a post-hoc Research Execution from a verified immutable legacy result."""
    legacy = _verified_legacy_result(
        legacy_result_registry_path, legacy_result_id, legacy_result_record_id,
        experiment_registry_path, hypothesis_registry_path, dataset_directory,
        auxiliary_verifiers)
    references = _research_execution_references(legacy)
    execution_id = _research_execution_id(references)
    path = Path(registry_path)
    if path.exists():
        registry = _load_research_execution_registry(path)
        matches = [item for item in registry["executions"]
                   if item["execution_id"] == execution_id]
        if matches:
            if len(matches) != 1:
                raise ValueError("Research Execution identity is ambiguous")
            return verified_research_execution(
                registry_path, execution_id,
                legacy_result_registry_path=legacy_result_registry_path,
                experiment_registry_path=experiment_registry_path,
                hypothesis_registry_path=hypothesis_registry_path,
                dataset_directory=dataset_directory, auxiliary_verifiers=auxiliary_verifiers)
    record = _research_execution_record(
        execution_id, references,
        _research_execution_input_hashes(legacy, legacy_result_registry_path),
        legacy["execution"],
        _research_materialization(materialized_at, materialization_code_revision),
        legacy["evaluation"], legacy["evidence"])
    return _persist_research_execution(registry_path, record)


def load_research_execution(registry_path, execution_id):
    if not _research_execution_id_is_valid(execution_id):
        raise ValueError("A valid Research Execution identity is required")
    registry = _load_research_execution_registry(registry_path)
    matches = [item for item in registry["executions"] if item["execution_id"] == execution_id]
    if len(matches) != 1:
        raise ValueError("Research Execution is not registered unambiguously")
    return matches[0]


def verified_research_execution(registry_path, execution_id, *, legacy_result_registry_path,
                                experiment_registry_path, hypothesis_registry_path,
                                dataset_directory, auxiliary_verifiers=None):
    """Reload the post-hoc execution and reproduce its legacy sealed scientific result."""
    record = load_research_execution(registry_path, execution_id)
    legacy_reference = record["references"]["legacy_result"]
    legacy = _verified_legacy_result(
        legacy_result_registry_path, legacy_reference["result_id"], legacy_reference["record_id"],
        experiment_registry_path, hypothesis_registry_path, dataset_directory,
        auxiliary_verifiers)
    references = _research_execution_references(legacy)
    expected = _research_execution_record(
        record["execution_id"], references,
        _research_execution_input_hashes(legacy, legacy_result_registry_path),
        legacy["execution"], record["materialization"], legacy["evaluation"], legacy["evidence"])
    if record != expected:
        raise ValueError("Research Execution evidence, inputs, or aggregate is invalid")
    return record


def _research_result_references(execution):
    return {
        "research_execution": {
            "execution_id": execution["execution_id"], "record_id": execution["record_id"]},
        "legacy_result": execution["references"]["legacy_result"],
        "experiment": execution["references"]["experiment"],
    }


def _research_result_id(references):
    content = {
        "schema_version": RESEARCH_RESULT_SCHEMA_VERSION,
        "research_execution": references["research_execution"],
        "legacy_result": references["legacy_result"],
    }
    return "RESEARCH_RESULT|" + digest(encoded(content))


def _research_result_costs(experiment, execution):
    return {
        "source_experiment": execution["references"]["experiment"],
        "declaration": experiment["conditions"]["costs"]["declaration"],
    }


def _research_result_content(research_result_id, references, materialization, scientific_result,
                             costs):
    return {
        "research_result_id": research_result_id,
        "schema_version": RESEARCH_RESULT_SCHEMA_VERSION,
        "references": references,
        "materialization": materialization,
        "scientific_result": scientific_result,
        "costs": costs,
        "status": RESEARCH_RESULT_STATUS,
    }


def _research_result_record(research_result_id, references, materialization, scientific_result,
                            costs):
    content = _research_result_content(
        research_result_id, references, materialization, scientific_result, costs)
    return {**content, "record_id": "RESEARCH_RESULT_RECORD|" + research_result_id + "|"
            + digest(encoded(content))}


def _research_result_record_is_valid(record):
    fields = {
        "research_result_id", "schema_version", "references", "materialization",
        "scientific_result", "costs", "status", "record_id"}
    if not isinstance(record, dict) or set(record) != fields:
        return False
    references = record.get("references")
    materialization = record.get("materialization")
    if (not _research_result_id_is_valid(record.get("research_result_id"))
            or record.get("schema_version") != RESEARCH_RESULT_SCHEMA_VERSION
            or not isinstance(references, dict) or set(references) != {
                "research_execution", "legacy_result", "experiment"}
            or not isinstance(references["research_execution"], dict)
            or set(references["research_execution"]) != {"execution_id", "record_id"}
            or not _research_execution_id_is_valid(
                references["research_execution"].get("execution_id"))
            or not _hypothesis_text_is_valid(references["research_execution"].get("record_id"))
            or not isinstance(references["legacy_result"], dict)
            or set(references["legacy_result"]) != {"result_id", "record_id"}
            or not _experiment_result_id_is_valid(references["legacy_result"].get("result_id"))
            or not _hypothesis_text_is_valid(references["legacy_result"].get("record_id"))
            or not isinstance(references["experiment"], dict)
            or set(references["experiment"]) != {"experiment_id", "version", "record_id"}
            or not _experiment_id_is_valid(references["experiment"].get("experiment_id"))
            or not isinstance(references["experiment"].get("version"), int)
            or not _hypothesis_text_is_valid(references["experiment"].get("record_id"))
            or record["research_result_id"] != _research_result_id(references)
            or not isinstance(materialization, dict)
            or set(materialization) != {
                "constituted_at", "materialization_code_revision", "pipeline_sha256",
                "provenance", "original_execution_time"}
            or not _explicit_utc(materialization.get("constituted_at"))
            or not _hypothesis_code_revision_is_valid(
                materialization.get("materialization_code_revision"))
            or not re.fullmatch(r"[0-9a-f]{64}", materialization.get("pipeline_sha256", ""))
            or materialization.get("provenance") != RESEARCH_MATERIALIZATION_PROVENANCE
            or materialization.get("original_execution_time") != "NOT_RECORDED"
            or not isinstance(record.get("scientific_result"), dict)
            or not isinstance(record.get("costs"), dict)
            or set(record["costs"]) != {"source_experiment", "declaration"}
            or record["costs"].get("source_experiment") != references["experiment"]
            or record["costs"].get("declaration") != EXPERIMENT_COSTS_NOT_APPLICABLE
            or record.get("status") != RESEARCH_RESULT_STATUS):
        return False
    expected = _research_result_record(
        record["research_result_id"], references, materialization,
        record["scientific_result"], record["costs"])
    return record == expected


def _research_result_registry_is_valid(registry):
    if (not isinstance(registry, dict) or set(registry) != {"schema_version", "results"}
            or registry.get("schema_version") != RESEARCH_RESULT_REGISTRY_SCHEMA_VERSION
            or not isinstance(registry.get("results"), list)
            or not all(_research_result_record_is_valid(item) for item in registry["results"])):
        return False
    identifiers = [item["research_result_id"] for item in registry["results"]]
    return len(identifiers) == len(set(identifiers))


def _load_research_result_registry(registry_path):
    try:
        registry = json.loads(Path(registry_path).read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Persisted Research Result registry cannot be read") from error
    if not _research_result_registry_is_valid(registry):
        raise ValueError("Persisted Research Result registry is invalid")
    return registry


def _persist_research_result(registry_path, record):
    if not _research_result_record_is_valid(record):
        raise ValueError("Research Result record is invalid")
    path = Path(registry_path)
    registry = (_load_research_result_registry(path) if path.exists() else {
        "schema_version": RESEARCH_RESULT_REGISTRY_SCHEMA_VERSION, "results": []})
    matches = [item for item in registry["results"]
               if item["research_result_id"] == record["research_result_id"]]
    if matches:
        if len(matches) == 1 and matches[0] == record:
            return matches[0]
        raise ValueError("Research Result identity already exists with different content")
    registry["results"].append(record)
    if not _research_result_registry_is_valid(registry):
        raise ValueError("Constructed Research Result registry is invalid")
    _atomic_write(path, encoded(registry))
    return record


def constitute_research_result(registry_path, *, research_execution_registry_path,
                                research_execution_id, research_execution_record_id,
                                legacy_result_registry_path, experiment_registry_path,
                                hypothesis_registry_path, dataset_directory, materialized_at,
                                materialization_code_revision, auxiliary_verifiers=None):
    """Append a Research Result that owns aggregates and links to its causal execution."""
    execution = verified_research_execution(
        research_execution_registry_path, research_execution_id,
        legacy_result_registry_path=legacy_result_registry_path,
        experiment_registry_path=experiment_registry_path,
        hypothesis_registry_path=hypothesis_registry_path, dataset_directory=dataset_directory,
        auxiliary_verifiers=auxiliary_verifiers)
    if execution["record_id"] != research_execution_record_id:
        raise ValueError("Research Execution seal does not match the requested Research Result")
    experiment_reference = execution["references"]["experiment"]
    experiment = verified_experiment_conditions(
        experiment_registry_path, experiment_reference["experiment_id"], experiment_reference["version"],
        hypothesis_registry_path=hypothesis_registry_path, dataset_directory=dataset_directory,
        auxiliary_verifiers=auxiliary_verifiers)
    if experiment["record_id"] != experiment_reference["record_id"]:
        raise ValueError("Research Result experiment reference is invalid")
    references = _research_result_references(execution)
    research_result_id = _research_result_id(references)
    path = Path(registry_path)
    if path.exists():
        registry = _load_research_result_registry(path)
        matches = [item for item in registry["results"]
                   if item["research_result_id"] == research_result_id]
        if matches:
            if len(matches) != 1:
                raise ValueError("Research Result identity is ambiguous")
            return verified_research_result(
                registry_path, research_result_id,
                research_execution_registry_path=research_execution_registry_path,
                legacy_result_registry_path=legacy_result_registry_path,
                experiment_registry_path=experiment_registry_path,
                hypothesis_registry_path=hypothesis_registry_path,
                dataset_directory=dataset_directory, auxiliary_verifiers=auxiliary_verifiers)
    record = _research_result_record(
        research_result_id, references,
        _research_materialization(materialized_at, materialization_code_revision),
        execution["scientific_result"], _research_result_costs(experiment, execution))
    return _persist_research_result(registry_path, record)


def load_research_result(registry_path, research_result_id):
    if not _research_result_id_is_valid(research_result_id):
        raise ValueError("A valid Research Result identity is required")
    registry = _load_research_result_registry(registry_path)
    matches = [item for item in registry["results"]
               if item["research_result_id"] == research_result_id]
    if len(matches) != 1:
        raise ValueError("Research Result is not registered unambiguously")
    return matches[0]


def verified_research_result(registry_path, research_result_id, *,
                             research_execution_registry_path, legacy_result_registry_path,
                             experiment_registry_path, hypothesis_registry_path,
                             dataset_directory, auxiliary_verifiers=None):
    """Reload a Research Result and prove its exact causal link to Research Execution."""
    record = load_research_result(registry_path, research_result_id)
    execution_reference = record["references"]["research_execution"]
    execution = verified_research_execution(
        research_execution_registry_path, execution_reference["execution_id"],
        legacy_result_registry_path=legacy_result_registry_path,
        experiment_registry_path=experiment_registry_path,
        hypothesis_registry_path=hypothesis_registry_path, dataset_directory=dataset_directory,
        auxiliary_verifiers=auxiliary_verifiers)
    if execution["record_id"] != execution_reference["record_id"]:
        raise ValueError("Research Result Research Execution reference is invalid")
    experiment_reference = execution["references"]["experiment"]
    experiment = verified_experiment_conditions(
        experiment_registry_path, experiment_reference["experiment_id"], experiment_reference["version"],
        hypothesis_registry_path=hypothesis_registry_path, dataset_directory=dataset_directory,
        auxiliary_verifiers=auxiliary_verifiers)
    references = _research_result_references(execution)
    expected = _research_result_record(
        record["research_result_id"], references, record["materialization"],
        execution["scientific_result"], _research_result_costs(experiment, execution))
    if record != expected:
        raise ValueError("Research Result aggregate, costs, or causal reference is invalid")
    return record


