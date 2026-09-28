"""CAP-003 Knowledge -- PAPER Evidence (M2.5-T1): incorporates a
read-only pointer to an already-existing M1.3 activation receipt into
Knowledge. The operational producer keeps sole ownership and authority
over the referenced result; Knowledge only ever re-reads it.

Moved out of pipeline.py per DOC-014/DOC-015 (adaptado) SS4 (Etapa 4, M4.1
module decomposition). No behavior change.

Depends on M1.3 operational functions (receipt path, status recomputation)
that have not yet been extracted to operations/ -- imported lazily from
`pipeline` at call time (like _pipeline_source_bytes) until that module
exists; will be updated to import directly from operations/ once it does.
"""

import json
import re
from pathlib import Path

from tramitago_quant_core.shared.util import (
    digest, encoded, _atomic_write, _explicit_utc, _hypothesis_code_revision_is_valid,
    _pipeline_source_bytes,
)


def _operations():
    """Lazy access to M1.3 operational functions not yet extracted to
    operations/ -- deferred import avoids a circular import at module-load
    time (pipeline.py imports this module too). Will be replaced with a
    direct import from operations/ once that module is extracted."""
    import pipeline as _pipeline_module
    return _pipeline_module


PAPER_EVIDENCE_REGISTRY_SCHEMA_VERSION = "1"
PAPER_EVIDENCE_SCHEMA_VERSION = "1"
PAPER_EVIDENCE_KIND = "FORWARD_PAPER_ACTIVATION_RECEIPT"
PAPER_EVIDENCE_STATUS = "LINKED"


def _paper_evidence_id_is_valid(value):
    return (isinstance(value, str)
            and bool(re.fullmatch(r"PAPER_EVIDENCE\|[0-9a-f]{64}", value)))


def _paper_evidence_reference(receipt):
    return {"activation_id": receipt["activation_id"], "receipt_identity": receipt["receipt_identity"]}


def _paper_evidence_id(reference):
    content = {"schema_version": PAPER_EVIDENCE_SCHEMA_VERSION,
              "receipt_identity": reference["receipt_identity"]}
    return "PAPER_EVIDENCE|" + digest(encoded(content))


def _paper_evidence_snapshot(receipt):
    """A self-describing pointer, not a copy of M1.2's substantive content.

    The operational producer (M1.x) keeps sole ownership and authority
    over the referenced result; Knowledge only ever re-reads it.
    """
    return {
        "kind": PAPER_EVIDENCE_KIND,
        "activation_id": receipt["activation_id"],
        "scheduled_for_utc": receipt["scheduled_for_utc"],
        "policy_id": receipt["policy_id"],
        "configuration_id": receipt["configuration_id"],
        "m12_terminal_result": receipt["m12_terminal_result"],
        "m12_invocation_result_id": receipt["m12_invocation_result_id"],
        "m12_result_reference": receipt["m12_result_reference"],
        "m12_result_hash": receipt["m12_result_hash"],
    }


def _paper_evidence_materialization(linked_at, linking_code_revision):
    if (not _explicit_utc(linked_at)
            or not _hypothesis_code_revision_is_valid(linking_code_revision)):
        raise ValueError("PAPER Evidence linking time and code revision are required")
    source = _pipeline_source_bytes()
    return {
        "linked_at": linked_at, "linking_code_revision": linking_code_revision,
        "pipeline_sha256": digest(source),
    }


def _paper_evidence_content(evidence_id, reference, snapshot, materialization):
    return {
        "evidence_id": evidence_id,
        "schema_version": PAPER_EVIDENCE_SCHEMA_VERSION,
        "reference": reference,
        "snapshot": snapshot,
        "materialization": materialization,
        "status": PAPER_EVIDENCE_STATUS,
    }


def _paper_evidence_record(evidence_id, reference, snapshot, materialization):
    content = _paper_evidence_content(evidence_id, reference, snapshot, materialization)
    return {**content, "record_id": "PAPER_EVIDENCE_RECORD|" + evidence_id + "|"
            + digest(encoded(content))}


def _paper_evidence_record_is_valid(record):
    fields = {
        "evidence_id", "schema_version", "reference", "snapshot", "materialization", "status",
        "record_id"}
    if not isinstance(record, dict) or set(record) != fields:
        return False
    reference = record.get("reference")
    snapshot = record.get("snapshot")
    materialization = record.get("materialization")
    if (not _paper_evidence_id_is_valid(record.get("evidence_id"))
            or record.get("schema_version") != PAPER_EVIDENCE_SCHEMA_VERSION
            or not isinstance(reference, dict) or set(reference) != {
                "activation_id", "receipt_identity"}
            or not isinstance(reference.get("activation_id"), str) or not reference["activation_id"]
            or not isinstance(reference.get("receipt_identity"), str)
            or not reference["receipt_identity"].startswith("FORWARD_PAPER_ACTIVATION_RECEIPT|")
            or record["evidence_id"] != _paper_evidence_id(reference)
            or not isinstance(snapshot, dict) or set(snapshot) != {
                "kind", "activation_id", "scheduled_for_utc", "policy_id", "configuration_id",
                "m12_terminal_result", "m12_invocation_result_id", "m12_result_reference",
                "m12_result_hash"}
            or snapshot.get("kind") != PAPER_EVIDENCE_KIND
            or snapshot.get("activation_id") != reference["activation_id"]
            or not _explicit_utc(snapshot.get("scheduled_for_utc"))
            or not isinstance(snapshot.get("policy_id"), str) or not snapshot["policy_id"]
            or not isinstance(snapshot.get("configuration_id"), str)
            or not snapshot["configuration_id"]
            or snapshot.get("m12_terminal_result")
            not in _operations().FORWARD_PAPER_ACTIVATION_M12_TERMINAL_RESULTS
            or not isinstance(snapshot.get("m12_invocation_result_id"), str)
            or not snapshot["m12_invocation_result_id"]
            or not isinstance(snapshot.get("m12_result_reference"), str)
            or not snapshot["m12_result_reference"]
            or not re.fullmatch(r"[0-9a-f]{64}", snapshot.get("m12_result_hash", ""))
            or not isinstance(materialization, dict) or set(materialization) != {
                "linked_at", "linking_code_revision", "pipeline_sha256"}
            or not _explicit_utc(materialization.get("linked_at"))
            or not _hypothesis_code_revision_is_valid(
                materialization.get("linking_code_revision"))
            or not re.fullmatch(r"[0-9a-f]{64}", materialization.get("pipeline_sha256", ""))
            or record.get("status") != PAPER_EVIDENCE_STATUS):
        return False
    expected = _paper_evidence_record(record["evidence_id"], reference, snapshot, materialization)
    return record == expected


def _paper_evidence_registry_is_valid(registry):
    if (not isinstance(registry, dict) or set(registry) != {"schema_version", "evidence"}
            or registry.get("schema_version") != PAPER_EVIDENCE_REGISTRY_SCHEMA_VERSION
            or not isinstance(registry.get("evidence"), list)
            or not all(_paper_evidence_record_is_valid(item) for item in registry["evidence"])):
        return False
    identifiers = [item["evidence_id"] for item in registry["evidence"]]
    return len(identifiers) == len(set(identifiers))


def _load_paper_evidence_registry(registry_path):
    try:
        registry = json.loads(Path(registry_path).read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Persisted PAPER Evidence registry cannot be read") from error
    if not _paper_evidence_registry_is_valid(registry):
        raise ValueError("Persisted PAPER Evidence registry is invalid")
    return registry


def _persist_paper_evidence(registry_path, record):
    if not _paper_evidence_record_is_valid(record):
        raise ValueError("PAPER Evidence record is invalid")
    path = Path(registry_path)
    registry = (_load_paper_evidence_registry(path) if path.exists() else {
        "schema_version": PAPER_EVIDENCE_REGISTRY_SCHEMA_VERSION, "evidence": []})
    matches = [item for item in registry["evidence"] if item["evidence_id"] == record["evidence_id"]]
    if matches:
        if len(matches) == 1 and matches[0] == record:
            return matches[0]
        raise ValueError("PAPER Evidence identity already exists with different content")
    registry["evidence"].append(record)
    if not _paper_evidence_registry_is_valid(registry):
        raise ValueError("Constructed PAPER Evidence registry is invalid")
    _atomic_write(path, encoded(registry))
    return record


def _paper_evidence_receipt(policy_path, configuration_path, receipt_directory, activation_id):
    """Re-read the exact live receipt this activation_id names -- never the newest one."""
    policy, configuration = _operations()._forward_paper_activation_policy_inputs(policy_path, configuration_path)
    activation = _operations()._forward_paper_activation_from_persisted_id(policy, configuration, activation_id)
    if activation is None:
        raise ValueError("PAPER Evidence activation identity is invalid or unbound")
    receipt_path = _operations()._forward_paper_activation_receipt_path(receipt_directory, activation_id)
    try:
        raw = json.loads(Path(receipt_path).read_bytes())
    except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("PAPER Evidence receipt cannot be read") from error
    expected = _operations()._forward_paper_activation_status_receipt_expected(
        raw, activation, policy, configuration)
    return _operations()._load_forward_paper_activation_receipt(receipt_path, expected)


def constitute_paper_evidence(registry_path, *, policy_path, configuration_path,
                              ledger_directory, receipt_directory, linked_at,
                              linking_code_revision):
    """M2.5-T1: link the most recently completed PAPER activation receipt.

    Read-only over M1.x's own state -- never invokes M1.2, never writes to
    the receipt, ledger, or lease. The operational producer keeps sole
    authority over its result; Knowledge only ever links a verifiable
    pointer to it, tagged 'kind': FORWARD_PAPER_ACTIVATION_RECEIPT so it is
    never mistaken for a historical Research Result.
    """
    policy, configuration = _operations()._forward_paper_activation_policy_inputs(policy_path, configuration_path)
    persisted = _operations()._forward_paper_last_persisted_activation(
        policy, configuration, ledger_directory, receipt_directory)
    if persisted is None or persisted.get("receipt") is None:
        raise ValueError("No PAPER activation receipt exists yet to incorporate as evidence")
    receipt = persisted["receipt"]
    if receipt["status"] != "M12_RESULT_RECORDED":
        raise ValueError("PAPER evidence requires a receipt with a recorded M1.2 result")
    if _operations()._forward_paper_activation_receipt_result_evidence(receipt) is None:
        raise ValueError("PAPER evidence's linked M1.2 result cannot be independently verified")

    reference = _paper_evidence_reference(receipt)
    snapshot = _paper_evidence_snapshot(receipt)
    evidence_id = _paper_evidence_id(reference)
    path = Path(registry_path)
    if path.exists():
        registry = _load_paper_evidence_registry(path)
        matches = [item for item in registry["evidence"] if item["evidence_id"] == evidence_id]
        if matches:
            if len(matches) != 1:
                raise ValueError("PAPER Evidence identity is ambiguous")
            return verified_paper_evidence(
                registry_path, evidence_id, policy_path=policy_path,
                configuration_path=configuration_path, receipt_directory=receipt_directory)
    record = _paper_evidence_record(
        evidence_id, reference, snapshot,
        _paper_evidence_materialization(linked_at, linking_code_revision))
    return _persist_paper_evidence(registry_path, record)


def load_paper_evidence(registry_path, evidence_id):
    if not _paper_evidence_id_is_valid(evidence_id):
        raise ValueError("A valid PAPER Evidence identity is required")
    registry = _load_paper_evidence_registry(registry_path)
    matches = [item for item in registry["evidence"] if item["evidence_id"] == evidence_id]
    if len(matches) != 1:
        raise ValueError("PAPER Evidence is not registered unambiguously")
    return matches[0]


def query_paper_evidence(registry_path, *, configuration_id=None):
    """Read-only consultation surface -- never mutates state, no side effects."""
    path = Path(registry_path)
    if not path.exists():
        return []
    records = _load_paper_evidence_registry(path)["evidence"]
    if configuration_id is not None:
        records = [item for item in records
                   if item["snapshot"]["configuration_id"] == configuration_id]
    return records


def verified_paper_evidence(registry_path, evidence_id, *, policy_path, configuration_path,
                            receipt_directory):
    """Reload PAPER Evidence and reverify its link against the live, unmodified receipt."""
    record = load_paper_evidence(registry_path, evidence_id)
    reference = record["reference"]
    receipt = _paper_evidence_receipt(
        policy_path, configuration_path, receipt_directory, reference["activation_id"])
    if receipt["receipt_identity"] != reference["receipt_identity"]:
        raise ValueError("PAPER Evidence receipt reference is invalid")
    if _operations()._forward_paper_activation_receipt_result_evidence(receipt) is None:
        raise ValueError("PAPER Evidence's linked M1.2 result cannot be independently verified")
    snapshot = _paper_evidence_snapshot(receipt)
    expected = _paper_evidence_record(
        record["evidence_id"], reference, snapshot, record["materialization"])
    if record != expected:
        raise ValueError("PAPER Evidence snapshot is invalid or has drifted from its receipt")
    return record


