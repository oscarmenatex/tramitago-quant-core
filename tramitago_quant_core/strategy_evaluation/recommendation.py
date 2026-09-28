"""CAP-004 Strategy Evaluation -- Recommendation (M2.5-T2): judges PAPER
Evidence against declared limits, producing a traceable, effect-free
recommendation (RECOMMENDED/NOT_RECOMMENDED/INSUFFICIENT_EVIDENCE).

Moved out of pipeline.py per DOC-014/DOC-015 (adaptado) SS4 (Etapa 4, M4.1
module decomposition). No behavior change.

Depends on M1.3 operational functions (policy/configuration loading) not
yet extracted to operations/ -- imported lazily via the same deferred
_operations() pattern as knowledge/paper_evidence.py.
"""

import json
import re
from pathlib import Path

from tramitago_quant_core.shared.util import (
    digest, encoded, _atomic_write, _explicit_utc, _hypothesis_text_is_valid,
    _hypothesis_code_revision_is_valid, _pipeline_source_bytes,
)
from tramitago_quant_core.knowledge.paper_evidence import (
    verified_paper_evidence, _paper_evidence_id_is_valid, PAPER_EVIDENCE_KIND,
)


def _operations():
    """Lazy access to M1.3 operational functions not yet extracted to
    operations/ -- deferred import avoids a circular import at module-load
    time. Will be replaced with a direct import from operations/ once that
    module is extracted."""
    import pipeline as _pipeline_module
    return _pipeline_module


RECOMMENDATION_REGISTRY_SCHEMA_VERSION = "1"
RECOMMENDATION_SCHEMA_VERSION = "1"
RECOMMENDATION_STATUS = "PROPOSED"
RECOMMENDATION_OUTCOMES = {"RECOMMENDED", "NOT_RECOMMENDED", "INSUFFICIENT_EVIDENCE"}


def _recommendation_id_is_valid(value):
    return (isinstance(value, str)
            and bool(re.fullmatch(r"RECOMMENDATION\|[0-9a-f]{64}", value)))


def _recommendation_limits_are_valid(limits):
    """The boundary of applicability -- which receipt outcomes even count as evidence."""
    if not isinstance(limits, dict) or set(limits) != {"acceptable_terminal_results"}:
        return False
    acceptable = limits["acceptable_terminal_results"]
    return (isinstance(acceptable, list) and bool(acceptable)
            and all(item in _operations().FORWARD_PAPER_ACTIVATION_M12_TERMINAL_RESULTS
                    for item in acceptable)
            and len(acceptable) == len(set(acceptable)))


def _recommendation_outcome(limits, evidence_snapshot):
    """Never redefines what M1.3 already observed -- only judges it against the limits."""
    terminal = evidence_snapshot["m12_terminal_result"]
    if terminal not in limits["acceptable_terminal_results"]:
        return "INSUFFICIENT_EVIDENCE", "TERMINAL_RESULT_OUTSIDE_LIMITS"
    if terminal == "COMPLETED":
        return "RECOMMENDED", None
    return "NOT_RECOMMENDED", "TERMINAL_RESULT_" + terminal


def _recommendation_reference(evidence):
    return {"paper_evidence": {
        "evidence_id": evidence["evidence_id"], "record_id": evidence["record_id"]}}


def _recommendation_id(reference, limits):
    content = {
        "schema_version": RECOMMENDATION_SCHEMA_VERSION,
        "paper_evidence": reference["paper_evidence"], "limits": limits,
    }
    return "RECOMMENDATION|" + digest(encoded(content))


def _recommendation_materialization(proposed_at, recommendation_code_revision):
    if (not _explicit_utc(proposed_at)
            or not _hypothesis_code_revision_is_valid(recommendation_code_revision)):
        raise ValueError("Recommendation proposal time and code revision are required")
    source = _pipeline_source_bytes()
    return {
        "proposed_at": proposed_at, "recommendation_code_revision": recommendation_code_revision,
        "pipeline_sha256": digest(source),
    }


def _recommendation_content(recommendation_id, reference, limits, evidence_snapshot, outcome,
                            outcome_reason, materialization):
    return {
        "recommendation_id": recommendation_id,
        "schema_version": RECOMMENDATION_SCHEMA_VERSION,
        "reference": reference,
        "limits": limits,
        "evidence_snapshot": evidence_snapshot,
        "outcome": outcome,
        "outcome_reason": outcome_reason,
        "materialization": materialization,
        "status": RECOMMENDATION_STATUS,
    }


def _recommendation_record(recommendation_id, reference, limits, evidence_snapshot, outcome,
                           outcome_reason, materialization):
    content = _recommendation_content(
        recommendation_id, reference, limits, evidence_snapshot, outcome, outcome_reason,
        materialization)
    return {**content, "record_id": "RECOMMENDATION_RECORD|" + recommendation_id + "|"
            + digest(encoded(content))}


def _recommendation_record_is_valid(record):
    fields = {
        "recommendation_id", "schema_version", "reference", "limits", "evidence_snapshot",
        "outcome", "outcome_reason", "materialization", "status", "record_id"}
    if not isinstance(record, dict) or set(record) != fields:
        return False
    reference = record.get("reference")
    limits = record.get("limits")
    snapshot = record.get("evidence_snapshot")
    materialization = record.get("materialization")
    outcome = record.get("outcome")
    if (not _recommendation_id_is_valid(record.get("recommendation_id"))
            or record.get("schema_version") != RECOMMENDATION_SCHEMA_VERSION
            or not isinstance(reference, dict) or set(reference) != {"paper_evidence"}
            or not isinstance(reference["paper_evidence"], dict)
            or set(reference["paper_evidence"]) != {"evidence_id", "record_id"}
            or not _paper_evidence_id_is_valid(reference["paper_evidence"].get("evidence_id"))
            or not _hypothesis_text_is_valid(reference["paper_evidence"].get("record_id"))
            or not _recommendation_limits_are_valid(limits)
            or record["recommendation_id"] != _recommendation_id(reference, limits)
            or not isinstance(snapshot, dict)
            or snapshot.get("kind") != PAPER_EVIDENCE_KIND
            or outcome not in RECOMMENDATION_OUTCOMES
            or (outcome == "RECOMMENDED") == (record.get("outcome_reason") is not None)
            or (record.get("outcome_reason") is not None
                and not _hypothesis_text_is_valid(record["outcome_reason"]))
            or not isinstance(materialization, dict) or set(materialization) != {
                "proposed_at", "recommendation_code_revision", "pipeline_sha256"}
            or not _explicit_utc(materialization.get("proposed_at"))
            or not _hypothesis_code_revision_is_valid(
                materialization.get("recommendation_code_revision"))
            or not re.fullmatch(r"[0-9a-f]{64}", materialization.get("pipeline_sha256", ""))
            or record.get("status") != RECOMMENDATION_STATUS):
        return False
    expected_outcome, expected_reason = _recommendation_outcome(limits, snapshot)
    if outcome != expected_outcome or record.get("outcome_reason") != expected_reason:
        return False
    expected = _recommendation_record(
        record["recommendation_id"], reference, limits, snapshot, outcome,
        record.get("outcome_reason"), materialization)
    return record == expected


def _recommendation_registry_is_valid(registry):
    if (not isinstance(registry, dict)
            or set(registry) != {"schema_version", "recommendations"}
            or registry.get("schema_version") != RECOMMENDATION_REGISTRY_SCHEMA_VERSION
            or not isinstance(registry.get("recommendations"), list)
            or not all(_recommendation_record_is_valid(item)
                      for item in registry["recommendations"])):
        return False
    identifiers = [item["recommendation_id"] for item in registry["recommendations"]]
    return len(identifiers) == len(set(identifiers))


def _load_recommendation_registry(registry_path):
    try:
        registry = json.loads(Path(registry_path).read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Persisted Recommendation registry cannot be read") from error
    if not _recommendation_registry_is_valid(registry):
        raise ValueError("Persisted Recommendation registry is invalid")
    return registry


def _persist_recommendation(registry_path, record):
    if not _recommendation_record_is_valid(record):
        raise ValueError("Recommendation record is invalid")
    path = Path(registry_path)
    registry = (_load_recommendation_registry(path) if path.exists() else {
        "schema_version": RECOMMENDATION_REGISTRY_SCHEMA_VERSION, "recommendations": []})
    matches = [item for item in registry["recommendations"]
               if item["recommendation_id"] == record["recommendation_id"]]
    if matches:
        if len(matches) == 1 and matches[0] == record:
            return matches[0]
        raise ValueError("Recommendation identity already exists with different content")
    registry["recommendations"].append(record)
    if not _recommendation_registry_is_valid(registry):
        raise ValueError("Constructed Recommendation registry is invalid")
    _atomic_write(path, encoded(registry))
    return record


def constitute_recommendation(registry_path, *, paper_evidence_registry_path, evidence_id,
                              evidence_record_id, policy_path, configuration_path,
                              receipt_directory, limits, proposed_at,
                              recommendation_code_revision):
    """M2.5-T2: relate PAPER Evidence and limits to a traceable, effect-free proposal.

    Strategy Evaluation only judges the already-linked evidence against
    caller-supplied limits (DOC-005/006) -- it never touches a
    configuration, session, or policy file, and a favorable
    recommendation has zero effect on the next cycle by itself (that is
    Governance's exclusive authority, M2.5-T3).
    """
    evidence = verified_paper_evidence(
        paper_evidence_registry_path, evidence_id, policy_path=policy_path,
        configuration_path=configuration_path, receipt_directory=receipt_directory)
    if evidence["record_id"] != evidence_record_id:
        raise ValueError("PAPER Evidence seal does not match the requested Recommendation")
    if not _recommendation_limits_are_valid(limits):
        raise ValueError("Recommendation limits must declare a nonempty set of acceptable terminal results")

    reference = _recommendation_reference(evidence)
    recommendation_id = _recommendation_id(reference, limits)
    path = Path(registry_path)
    if path.exists():
        registry = _load_recommendation_registry(path)
        matches = [item for item in registry["recommendations"]
                   if item["recommendation_id"] == recommendation_id]
        if matches:
            if len(matches) != 1:
                raise ValueError("Recommendation identity is ambiguous")
            return verified_recommendation(
                registry_path, recommendation_id,
                paper_evidence_registry_path=paper_evidence_registry_path,
                policy_path=policy_path, configuration_path=configuration_path,
                receipt_directory=receipt_directory)
    outcome, outcome_reason = _recommendation_outcome(limits, evidence["snapshot"])
    record = _recommendation_record(
        recommendation_id, reference, limits, evidence["snapshot"], outcome, outcome_reason,
        _recommendation_materialization(proposed_at, recommendation_code_revision))
    return _persist_recommendation(registry_path, record)


def load_recommendation(registry_path, recommendation_id):
    if not _recommendation_id_is_valid(recommendation_id):
        raise ValueError("A valid Recommendation identity is required")
    registry = _load_recommendation_registry(registry_path)
    matches = [item for item in registry["recommendations"]
               if item["recommendation_id"] == recommendation_id]
    if len(matches) != 1:
        raise ValueError("Recommendation is not registered unambiguously")
    return matches[0]


def query_recommendation(registry_path, *, outcome=None):
    """Read-only consultation surface -- never mutates state, no side effects."""
    path = Path(registry_path)
    if not path.exists():
        return []
    records = _load_recommendation_registry(path)["recommendations"]
    if outcome is not None:
        records = [item for item in records if item["outcome"] == outcome]
    return records


def verified_recommendation(registry_path, recommendation_id, *, paper_evidence_registry_path,
                            policy_path, configuration_path, receipt_directory):
    """Reload a Recommendation and reproduce its outcome from the live PAPER Evidence."""
    record = load_recommendation(registry_path, recommendation_id)
    evidence_reference = record["reference"]["paper_evidence"]
    evidence = verified_paper_evidence(
        paper_evidence_registry_path, evidence_reference["evidence_id"], policy_path=policy_path,
        configuration_path=configuration_path, receipt_directory=receipt_directory)
    if evidence["record_id"] != evidence_reference["record_id"]:
        raise ValueError("Recommendation PAPER Evidence reference is invalid")
    outcome, outcome_reason = _recommendation_outcome(record["limits"], evidence["snapshot"])
    expected = _recommendation_record(
        record["recommendation_id"], record["reference"], record["limits"],
        evidence["snapshot"], outcome, outcome_reason, record["materialization"])
    if record != expected:
        raise ValueError("Recommendation outcome or evidence is invalid")
    return record


