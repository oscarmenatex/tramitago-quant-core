"""CAP-010 Governance -- Governance Authorization (M2.5-T3): decides
whether a RECOMMENDED proposal is authorized against the current
configuration, producing a new configuration_version only when
authorized. Rejection is a valid, complete decision.

Moved out of pipeline.py per DOC-014/DOC-015 (adaptado) SS4 (Etapa 4, M4.1
module decomposition). No behavior change.

Depends on M1.3 operational functions (configuration loading) not yet
extracted to operations/ -- imported lazily via the same deferred
_operations() pattern as knowledge/paper_evidence.py.
"""

import json
import re
from pathlib import Path

from tramitago_quant_core.shared.util import (
    digest, encoded, _atomic_write, _explicit_utc, _hypothesis_text_is_valid,
    _hypothesis_code_revision_is_valid, _pipeline_source_bytes,
)
from tramitago_quant_core.strategy_evaluation.recommendation import (
    verified_recommendation, _recommendation_id_is_valid, RECOMMENDATION_OUTCOMES,
)


def _operations():
    """Lazy access to M1.3 operational functions not yet extracted to
    operations/ -- deferred import avoids a circular import at module-load
    time. Will be replaced with a direct import from operations/ once that
    module is extracted."""
    import pipeline as _pipeline_module
    return _pipeline_module


GOVERNANCE_AUTHORIZATION_REGISTRY_SCHEMA_VERSION = "1"
GOVERNANCE_AUTHORIZATION_SCHEMA_VERSION = "1"
GOVERNANCE_AUTHORIZATION_STATUS = "DECIDED"
GOVERNANCE_AUTHORIZATION_OUTCOMES = {"AUTHORIZED", "REJECTED"}


def _governance_authorization_id_is_valid(value):
    return (isinstance(value, str)
            and bool(re.fullmatch(r"GOVERNANCE_AUTHORIZATION\|[0-9a-f]{64}", value)))


def _governance_authorization_reference(recommendation):
    return {"recommendation": {
        "recommendation_id": recommendation["recommendation_id"],
        "record_id": recommendation["record_id"]}}


def _governance_authorization_id(reference):
    content = {
        "schema_version": GOVERNANCE_AUTHORIZATION_SCHEMA_VERSION,
        "recommendation": reference["recommendation"],
    }
    return "GOVERNANCE_AUTHORIZATION|" + digest(encoded(content))


def _governance_authorization_recommendation_snapshot(recommendation):
    return {"outcome": recommendation["outcome"], "outcome_reason": recommendation["outcome_reason"]}


def _governance_authorization_decision(recommendation_snapshot):
    """MR-008-004/005: rejection is a valid result; authorization requires evidence.

    Governance only ever authorizes a RECOMMENDED proposal -- never a
    rejected or evidence-insufficient one -- and never redefines what
    Strategy Evaluation already concluded.
    """
    if recommendation_snapshot["outcome"] != "RECOMMENDED":
        return "REJECTED", "RECOMMENDATION_" + recommendation_snapshot["outcome"]
    return "AUTHORIZED", None


def _governance_authorization_materialization(decided_at, decision_code_revision):
    if (not _explicit_utc(decided_at)
            or not _hypothesis_code_revision_is_valid(decision_code_revision)):
        raise ValueError("Governance decision time and code revision are required")
    source = _pipeline_source_bytes()
    return {
        "decided_at": decided_at, "decision_code_revision": decision_code_revision,
        "pipeline_sha256": digest(source),
    }


def _governance_authorization_content(authorization_id, reference, recommendation_snapshot,
                                      configuration_id, configuration, outcome, outcome_reason,
                                      configuration_version, materialization):
    return {
        "authorization_id": authorization_id,
        "schema_version": GOVERNANCE_AUTHORIZATION_SCHEMA_VERSION,
        "reference": reference,
        "recommendation_snapshot": recommendation_snapshot,
        "configuration_id": configuration_id,
        "configuration": configuration,
        "outcome": outcome,
        "outcome_reason": outcome_reason,
        "configuration_version": configuration_version,
        "materialization": materialization,
        "status": GOVERNANCE_AUTHORIZATION_STATUS,
    }


def _governance_authorization_record(authorization_id, reference, recommendation_snapshot,
                                     configuration_id, configuration, outcome, outcome_reason,
                                     configuration_version, materialization):
    content = _governance_authorization_content(
        authorization_id, reference, recommendation_snapshot, configuration_id, configuration,
        outcome, outcome_reason, configuration_version, materialization)
    return {**content, "record_id": "GOVERNANCE_AUTHORIZATION_RECORD|" + authorization_id + "|"
            + digest(encoded(content))}


def _governance_authorization_record_is_valid(record):
    fields = {
        "authorization_id", "schema_version", "reference", "recommendation_snapshot",
        "configuration_id", "configuration", "outcome", "outcome_reason",
        "configuration_version", "materialization", "status", "record_id"}
    if not isinstance(record, dict) or set(record) != fields:
        return False
    reference = record.get("reference")
    snapshot = record.get("recommendation_snapshot")
    configuration = record.get("configuration")
    materialization = record.get("materialization")
    outcome = record.get("outcome")
    version = record.get("configuration_version")
    if (not _governance_authorization_id_is_valid(record.get("authorization_id"))
            or record.get("schema_version") != GOVERNANCE_AUTHORIZATION_SCHEMA_VERSION
            or not isinstance(reference, dict) or set(reference) != {"recommendation"}
            or not isinstance(reference["recommendation"], dict)
            or set(reference["recommendation"]) != {"recommendation_id", "record_id"}
            or not _recommendation_id_is_valid(reference["recommendation"].get("recommendation_id"))
            or not _hypothesis_text_is_valid(reference["recommendation"].get("record_id"))
            or record["authorization_id"] != _governance_authorization_id(reference)
            or not isinstance(snapshot, dict) or set(snapshot) != {"outcome", "outcome_reason"}
            or snapshot.get("outcome") not in RECOMMENDATION_OUTCOMES
            or (snapshot.get("outcome") == "RECOMMENDED") != (snapshot.get("outcome_reason") is None)
            or not isinstance(record.get("configuration_id"), str) or not record["configuration_id"]
            or not _operations()._forward_paper_configuration_is_valid(configuration)
            or record["configuration_id"] != configuration.get("configuration_id")
            or outcome not in GOVERNANCE_AUTHORIZATION_OUTCOMES
            or (outcome == "REJECTED") != (record.get("outcome_reason") is not None)
            or (record.get("outcome_reason") is not None
                and not _hypothesis_text_is_valid(record["outcome_reason"]))
            or (outcome == "AUTHORIZED") != (version is not None)
            or (version is not None and (
                not isinstance(version, int) or isinstance(version, bool) or version < 1))
            or not isinstance(materialization, dict) or set(materialization) != {
                "decided_at", "decision_code_revision", "pipeline_sha256"}
            or not _explicit_utc(materialization.get("decided_at"))
            or not _hypothesis_code_revision_is_valid(
                materialization.get("decision_code_revision"))
            or not re.fullmatch(r"[0-9a-f]{64}", materialization.get("pipeline_sha256", ""))
            or record.get("status") != GOVERNANCE_AUTHORIZATION_STATUS):
        return False
    expected_outcome, expected_reason = _governance_authorization_decision(snapshot)
    if outcome != expected_outcome or record.get("outcome_reason") != expected_reason:
        return False
    expected = _governance_authorization_record(
        record["authorization_id"], reference, snapshot, record["configuration_id"],
        configuration, outcome, record.get("outcome_reason"), version, materialization)
    return record == expected


def _governance_authorization_registry_is_valid(registry):
    if (not isinstance(registry, dict)
            or set(registry) != {"schema_version", "authorizations"}
            or registry.get("schema_version") != GOVERNANCE_AUTHORIZATION_REGISTRY_SCHEMA_VERSION
            or not isinstance(registry.get("authorizations"), list)
            or not all(_governance_authorization_record_is_valid(item)
                      for item in registry["authorizations"])):
        return False
    records = registry["authorizations"]
    identifiers = [item["authorization_id"] for item in records]
    if len(identifiers) != len(set(identifiers)):
        return False
    for configuration_id in {item["configuration_id"] for item in records}:
        versions = sorted(
            item["configuration_version"] for item in records
            if item["configuration_id"] == configuration_id and item["outcome"] == "AUTHORIZED")
        if versions != list(range(1, len(versions) + 1)):
            return False
    return True


def _load_governance_authorization_registry(registry_path):
    try:
        registry = json.loads(Path(registry_path).read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Persisted Governance Authorization registry cannot be read") from error
    if not _governance_authorization_registry_is_valid(registry):
        raise ValueError("Persisted Governance Authorization registry is invalid")
    return registry


def _persist_governance_authorization(registry_path, record):
    if not _governance_authorization_record_is_valid(record):
        raise ValueError("Governance Authorization record is invalid")
    path = Path(registry_path)
    registry = (_load_governance_authorization_registry(path) if path.exists() else {
        "schema_version": GOVERNANCE_AUTHORIZATION_REGISTRY_SCHEMA_VERSION, "authorizations": []})
    matches = [item for item in registry["authorizations"]
               if item["authorization_id"] == record["authorization_id"]]
    if matches:
        if len(matches) == 1 and matches[0] == record:
            return matches[0]
        raise ValueError("Governance Authorization identity already exists with different content")
    registry["authorizations"].append(record)
    if not _governance_authorization_registry_is_valid(registry):
        raise ValueError("Constructed Governance Authorization registry is invalid")
    _atomic_write(path, encoded(registry))
    return record


def constitute_governance_authorization(registry_path, *, recommendation_registry_path,
                                        recommendation_id, recommendation_record_id,
                                        paper_evidence_registry_path, policy_path,
                                        configuration_path, receipt_directory, session_path,
                                        decided_at, decision_code_revision):
    """M2.5-T3: Governance authorizes or rejects a next configuration version.

    Verifies the operational configuration owner's own boundary --
    M1.2's load_forward_paper_configuration, read-only, never bypassed
    or reimplemented -- before deciding. Only a RECOMMENDED proposal can
    be authorized (MR-008-004/005); a rejection creates no new version.
    Writes only to its own registry: the session, configuration,
    ledger, lease, and receipt files are never touched.
    """
    recommendation = verified_recommendation(
        recommendation_registry_path, recommendation_id,
        paper_evidence_registry_path=paper_evidence_registry_path, policy_path=policy_path,
        configuration_path=configuration_path, receipt_directory=receipt_directory)
    if recommendation["record_id"] != recommendation_record_id:
        raise ValueError("Recommendation seal does not match the requested Authorization")

    configuration = _operations().load_forward_paper_configuration(session_path, configuration_path)

    reference = _governance_authorization_reference(recommendation)
    recommendation_snapshot = _governance_authorization_recommendation_snapshot(recommendation)
    authorization_id = _governance_authorization_id(reference)
    path = Path(registry_path)
    registry = _load_governance_authorization_registry(path) if path.exists() else None
    if registry is not None:
        matches = [item for item in registry["authorizations"]
                   if item["authorization_id"] == authorization_id]
        if matches:
            if len(matches) != 1:
                raise ValueError("Governance Authorization identity is ambiguous")
            return verified_governance_authorization(
                registry_path, authorization_id,
                recommendation_registry_path=recommendation_registry_path,
                paper_evidence_registry_path=paper_evidence_registry_path,
                policy_path=policy_path, configuration_path=configuration_path,
                receipt_directory=receipt_directory, session_path=session_path)

    outcome, outcome_reason = _governance_authorization_decision(recommendation_snapshot)
    configuration_version = None
    if outcome == "AUTHORIZED":
        prior = [item["configuration_version"] for item in (
            registry["authorizations"] if registry is not None else [])
            if item["configuration_id"] == configuration["configuration_id"]
            and item["outcome"] == "AUTHORIZED"]
        configuration_version = (max(prior) if prior else 0) + 1

    record = _governance_authorization_record(
        authorization_id, reference, recommendation_snapshot, configuration["configuration_id"],
        configuration, outcome, outcome_reason, configuration_version,
        _governance_authorization_materialization(decided_at, decision_code_revision))
    return _persist_governance_authorization(registry_path, record)


def load_governance_authorization(registry_path, authorization_id):
    if not _governance_authorization_id_is_valid(authorization_id):
        raise ValueError("A valid Governance Authorization identity is required")
    registry = _load_governance_authorization_registry(registry_path)
    matches = [item for item in registry["authorizations"]
               if item["authorization_id"] == authorization_id]
    if len(matches) != 1:
        raise ValueError("Governance Authorization is not registered unambiguously")
    return matches[0]


def query_governance_authorization(registry_path, *, outcome=None, configuration_id=None):
    """Read-only consultation surface -- never mutates state, no side effects."""
    path = Path(registry_path)
    if not path.exists():
        return []
    records = _load_governance_authorization_registry(path)["authorizations"]
    if outcome is not None:
        records = [item for item in records if item["outcome"] == outcome]
    if configuration_id is not None:
        records = [item for item in records if item["configuration_id"] == configuration_id]
    return records


def verified_governance_authorization(registry_path, authorization_id, *,
                                      recommendation_registry_path, paper_evidence_registry_path,
                                      policy_path, configuration_path, receipt_directory,
                                      session_path):
    """Reload a Governance Authorization and reproduce its decision from the live chain."""
    record = load_governance_authorization(registry_path, authorization_id)
    recommendation_reference = record["reference"]["recommendation"]
    recommendation = verified_recommendation(
        recommendation_registry_path, recommendation_reference["recommendation_id"],
        paper_evidence_registry_path=paper_evidence_registry_path, policy_path=policy_path,
        configuration_path=configuration_path, receipt_directory=receipt_directory)
    if recommendation["record_id"] != recommendation_reference["record_id"]:
        raise ValueError("Governance Authorization Recommendation reference is invalid")
    configuration = _operations().load_forward_paper_configuration(session_path, configuration_path)
    if configuration["configuration_id"] != record["configuration_id"]:
        raise ValueError("Governance Authorization configuration reference is invalid")
    recommendation_snapshot = _governance_authorization_recommendation_snapshot(recommendation)
    outcome, outcome_reason = _governance_authorization_decision(recommendation_snapshot)
    expected = _governance_authorization_record(
        record["authorization_id"], record["reference"], recommendation_snapshot,
        record["configuration_id"], configuration, outcome, outcome_reason,
        record["configuration_version"], record["materialization"])
    if record != expected:
        raise ValueError("Governance Authorization outcome or configuration is invalid")
    return record


