"""CAP-002 Research -- Hypothesis (M2.1): a falsifiable claim with a
predeclared, immutable acceptance criterion (HY-AC-001/002, DOC-007 SS15B).

Moved out of pipeline.py per DOC-014/DOC-015 (adaptado) SS4 (Etapa 4, M4.1
module decomposition). No behavior change. Depends only on shared/util.py --
verified to have zero references to data/acquisition.py.
"""

import json
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

from tramitago_quant_core.shared.util import (
    digest, encoded, epoch, _atomic_write, _explicit_utc, _hypothesis_text_is_valid,
    _hypothesis_system_version_is_valid, _hypothesis_code_revision_is_valid,
)

HYPOTHESIS_REGISTRY_SCHEMA_VERSION = "2"
HYPOTHESIS_ACCEPTANCE_COMPARISONS = {
    "INCREASE": {"GT", "GE"},
    "DECREASE": {"LT", "LE"},
}


def _hypothesis_id_is_valid(value):
    if not isinstance(value, str) or not value.startswith("HYPOTHESIS|"):
        return False
    try:
        return str(uuid.UUID(value.removeprefix("HYPOTHESIS|"))) == value.removeprefix(
            "HYPOTHESIS|")
    except ValueError:
        return False


def _hypothesis_constraints_are_valid(constraints):
    """Require the variables, period, and universe that bound a falsifiable claim."""
    if not isinstance(constraints, dict) or set(constraints) != {
            "variables", "period", "universe"}:
        return False
    variables = constraints.get("variables")
    universe = constraints.get("universe")
    period = constraints.get("period")
    if (not isinstance(variables, list) or not variables
            or not isinstance(universe, list) or not universe
            or not isinstance(period, dict) or set(period) != {
                "start_utc", "end_exclusive_utc"}):
        return False
    if (not all(_hypothesis_text_is_valid(item) for item in variables)
            or not all(_hypothesis_text_is_valid(item) for item in universe)
            or len(variables) != len(set(variables))
            or len(universe) != len(set(universe))):
        return False
    start = period.get("start_utc")
    end = period.get("end_exclusive_utc")
    return _explicit_utc(start) and _explicit_utc(end) and epoch(start) < epoch(end)


def _hypothesis_acceptance_criterion_is_valid(criterion, target_metric,
                                              expected_direction):
    """Validate a declared rule without applying it to scientific evidence."""
    if not isinstance(criterion, dict) or set(criterion) != {
            "metric", "comparison", "threshold", "expected_direction"}:
        return False
    metric = criterion.get("metric")
    comparison = criterion.get("comparison")
    threshold = criterion.get("threshold")
    direction = criterion.get("expected_direction")
    if (not _hypothesis_text_is_valid(metric)
            or not _hypothesis_text_is_valid(comparison)
            or not _hypothesis_text_is_valid(threshold)
            or not _hypothesis_text_is_valid(direction)
            or metric != target_metric or direction != expected_direction
            or comparison not in HYPOTHESIS_ACCEPTANCE_COMPARISONS.get(direction, ())):
        return False
    try:
        return Decimal(threshold).is_finite()
    except (InvalidOperation, ValueError):
        return False


def _hypothesis_creation_context_is_valid(context):
    if not isinstance(context, dict) or set(context) != {"created_by", "provenance"}:
        return False
    provenance = context.get("provenance")
    return (
        _hypothesis_text_is_valid(context.get("created_by"))
        and isinstance(provenance, list) and bool(provenance)
        and all(_hypothesis_text_is_valid(item) for item in provenance)
        and len(provenance) == len(set(provenance))
    )


def _hypothesis_record_content(hypothesis_id, version, system_version, code_revision,
                               description, target_metric, expected_direction, constraints,
                               acceptance_criterion, creation_timestamp, status,
                               creation_context):
    return {
        "hypothesis_id": hypothesis_id,
        "version": version,
        "system_version": system_version,
        "code_revision": code_revision,
        "description": description,
        "target_metric": target_metric,
        "expected_direction": expected_direction,
        "constraints": constraints,
        "acceptance_criterion": acceptance_criterion,
        "creation_timestamp": creation_timestamp,
        "status": status,
        "creation_context": creation_context,
    }


def _hypothesis_record(hypothesis_id, version, system_version, code_revision,
                       description, target_metric, expected_direction, constraints,
                       acceptance_criterion, creation_timestamp, status, creation_context):
    content = _hypothesis_record_content(
        hypothesis_id, version, system_version, code_revision, description, target_metric,
        expected_direction, constraints, acceptance_criterion, creation_timestamp, status,
        creation_context)
    return {
        **content,
        "record_id": "HYPOTHESIS_VERSION|" + hypothesis_id + "|" + str(version)
        + "|" + digest(encoded(content)),
    }


def _hypothesis_record_is_valid(record):
    fields = {
        "hypothesis_id", "version", "system_version", "code_revision", "description",
        "target_metric", "expected_direction", "constraints", "acceptance_criterion",
        "creation_timestamp", "status", "creation_context", "record_id",
    }
    if not isinstance(record, dict) or set(record) != fields:
        return False
    version = record.get("version")
    if (not _hypothesis_id_is_valid(record.get("hypothesis_id"))
            or not isinstance(version, int) or isinstance(version, bool) or version < 1
            or not _hypothesis_system_version_is_valid(record.get("system_version"))
            or not _hypothesis_code_revision_is_valid(record.get("code_revision"))
            or not _hypothesis_text_is_valid(record.get("description"))
            or not _hypothesis_text_is_valid(record.get("target_metric"))
            or record.get("expected_direction") not in HYPOTHESIS_ACCEPTANCE_COMPARISONS
            or not _hypothesis_constraints_are_valid(record.get("constraints"))
            or not _hypothesis_acceptance_criterion_is_valid(
                record.get("acceptance_criterion"), record["target_metric"],
                record["expected_direction"])
            or not _explicit_utc(record.get("creation_timestamp"))
            or not _hypothesis_text_is_valid(record.get("status"))
            or not _hypothesis_creation_context_is_valid(record.get("creation_context"))):
        return False
    expected = _hypothesis_record(
        record["hypothesis_id"], version, record["system_version"],
        record["code_revision"], record["description"], record["target_metric"],
        record["expected_direction"], record["constraints"],
        record["acceptance_criterion"], record["creation_timestamp"], record["status"],
        record["creation_context"])
    return record == expected


def _hypothesis_registry_is_valid(registry):
    if (not isinstance(registry, dict) or set(registry) != {
            "schema_version", "hypotheses"}
            or registry.get("schema_version") != HYPOTHESIS_REGISTRY_SCHEMA_VERSION
            or not isinstance(registry.get("hypotheses"), list)
            or not all(_hypothesis_record_is_valid(item) for item in registry["hypotheses"])):
        return False
    records = registry["hypotheses"]
    pairs = [(item["hypothesis_id"], item["version"]) for item in records]
    if len(pairs) != len(set(pairs)):
        return False
    for hypothesis_id in {item["hypothesis_id"] for item in records}:
        versions = sorted(item["version"] for item in records
                          if item["hypothesis_id"] == hypothesis_id)
        if versions != list(range(1, len(versions) + 1)):
            return False
    return True


def _load_hypothesis_registry(registry_path):
    try:
        registry = json.loads(Path(registry_path).read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Persisted Hypothesis registry cannot be read") from error
    if not _hypothesis_registry_is_valid(registry):
        raise ValueError("Persisted Hypothesis registry is invalid or has a version conflict")
    return registry


def _new_hypothesis_id():
    return "HYPOTHESIS|" + str(uuid.uuid4())


def _validated_hypothesis_record(hypothesis_id, version, *, system_version,
                                 code_revision, description, target_metric,
                                 expected_direction, constraints, acceptance_criterion,
                                 creation_timestamp, status, created_by, provenance):
    record = _hypothesis_record(
        hypothesis_id, version, system_version, code_revision, description, target_metric,
        expected_direction, constraints, acceptance_criterion, creation_timestamp, status,
        {"created_by": created_by, "provenance": provenance})
    if not _hypothesis_record_is_valid(record):
        raise ValueError("Hypothesis must declare a falsifiable proposition, explicit conditions, an unambiguous acceptance criterion, and valid system provenance")
    return record


def constitute_hypothesis(registry_path, *, description, target_metric,
                          expected_direction, constraints, acceptance_criterion,
                          creation_timestamp, status, created_by, provenance,
                          system_version=None, code_revision=None):
    """Constitute and durably retain version one of one scientific Hypothesis."""
    registry_path = Path(registry_path)
    hypothesis_id = _new_hypothesis_id()
    record = _validated_hypothesis_record(
        hypothesis_id, 1, system_version=system_version, code_revision=code_revision,
        description=description, target_metric=target_metric,
        expected_direction=expected_direction, constraints=constraints,
        acceptance_criterion=acceptance_criterion, creation_timestamp=creation_timestamp,
        status=status, created_by=created_by, provenance=provenance)
    registry = (_load_hypothesis_registry(registry_path) if registry_path.exists()
                else {"schema_version": HYPOTHESIS_REGISTRY_SCHEMA_VERSION,
                      "hypotheses": []})
    if any(item["hypothesis_id"] == hypothesis_id for item in registry["hypotheses"]):
        raise ValueError("Hypothesis identity conflict")
    registry["hypotheses"].append(record)
    if not _hypothesis_registry_is_valid(registry):
        raise ValueError("Constructed Hypothesis registry is invalid")
    _atomic_write(registry_path, encoded(registry))
    return record


def revise_hypothesis(registry_path, hypothesis_id, *, description, target_metric,
                      expected_direction, constraints, acceptance_criterion,
                      creation_timestamp, status, created_by, provenance,
                      system_version=None, code_revision=None):
    """Append a new semantic version; prior Hypothesis versions remain immutable."""
    if not _hypothesis_id_is_valid(hypothesis_id):
        raise ValueError("A valid Hypothesis identity is required")
    registry_path = Path(registry_path)
    registry = _load_hypothesis_registry(registry_path)
    prior = [item for item in registry["hypotheses"]
             if item["hypothesis_id"] == hypothesis_id]
    if not prior:
        raise ValueError("Hypothesis identity is not registered")
    record = _validated_hypothesis_record(
        hypothesis_id, max(item["version"] for item in prior) + 1,
        system_version=system_version, code_revision=code_revision,
        description=description, target_metric=target_metric,
        expected_direction=expected_direction, constraints=constraints,
        acceptance_criterion=acceptance_criterion, creation_timestamp=creation_timestamp,
        status=status, created_by=created_by, provenance=provenance)
    registry["hypotheses"].append(record)
    if not _hypothesis_registry_is_valid(registry):
        raise ValueError("Revised Hypothesis registry is invalid")
    _atomic_write(registry_path, encoded(registry))
    return record


def load_hypothesis(registry_path, hypothesis_id, version):
    """Resolve exactly one immutable Hypothesis version, or fail closed."""
    if (not _hypothesis_id_is_valid(hypothesis_id) or not isinstance(version, int)
            or isinstance(version, bool) or version < 1):
        raise ValueError("A valid Hypothesis identity and version are required")
    registry = _load_hypothesis_registry(registry_path)
    records = [item for item in registry["hypotheses"]
               if item["hypothesis_id"] == hypothesis_id and item["version"] == version]
    if len(records) != 1:
        raise ValueError("Hypothesis version is not registered unambiguously")
    return records[0]


def load_hypothesis_acceptance_criterion(registry_path, hypothesis_id, version):
    """Resolve the exact predeclared criterion owned by one Hypothesis version."""
    return load_hypothesis(registry_path, hypothesis_id, version)["acceptance_criterion"]
