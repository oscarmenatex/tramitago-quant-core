"""CAP-002 Research -- Discovery / Finding (2026-09-29): the "Idea" box of
DOC-004 §4's official cycle, which the platform skipped.

DOC-004 §4 states the cycle as `Idea -> Hipótesis -> Diseño experimental -> ...`
and §5 defines an Idea as "observación inicial que podría aportar valor" and a
Hypothesis as "transformación de una idea en una afirmación medible". The
platform implemented everything from Hypothesis onward and never built the
Idea phase, so exploration ("let's try ATOM", "let's try 2023") had nowhere to
live except inside the frozen Hypothesis contract -- inflating the Knowledge
Core with exploration dressed as sealed science.

A Finding is the record of an OBSERVATION produced by exploration. It is
deliberately WEAKER than a Hypothesis, and every difference has a reason:
  - No quantitative acceptance criterion: a Finding is observed, not accepted
    or rejected.
  - No frozen period / universe / variables: that is exactly what a Hypothesis
    fixes AFTERWARD, at the transition.
  - No walk-forward, no Bonferroni, no Knowledge Record: there is no verdict to
    protect yet. In Discovery you are looking at the result on purpose -- that
    is the point -- so anti-snooping protection is imposed at the transition,
    never before it.

The transition Finding -> Hypothesis is the ONLY place the frozen contract is
imposed, and it is deliberately MANUAL for now (see the design doc): an
automatic promotion rule applied to the same data that produced the
observation would re-create the data-snooping the whole separation exists to
prevent -- exactly the Etapa 2.7 failure mode. The transition is two explicit
steps, kept decoupled: constitute the Hypothesis with `FINDING|<finding_id>`
in its provenance (making it traceable to the observation that motivated it),
then promote_finding(...) with the resulting hypothesis_id. This module
therefore never imports the Hypothesis contract; the link is a plain
provenance string on one side and a recorded hypothesis_id on the other.

Identity is a UUID (like a Hypothesis's), not a content hash, because a
Finding's status is mutable (OPEN -> PROMOTED_TO_HYPOTHESIS | DISCARDED) while
its observation is immutable. There is no verification CHAIN here (no
re-derivation from raw bytes): a Finding is not verdict evidence, so it carries
none of the sealed-capture machinery.
"""

import json
import uuid
from pathlib import Path

from tramitago_quant_core.shared.util import (
    encoded, _atomic_write, _explicit_utc, _hypothesis_text_is_valid,
)

FINDING_REGISTRY_SCHEMA_VERSION = "1"
FINDING_STATUS_OPEN = "OPEN"
FINDING_STATUS_PROMOTED = "PROMOTED_TO_HYPOTHESIS"
FINDING_STATUS_DISCARDED = "DISCARDED"
FINDING_STATUSES = {FINDING_STATUS_OPEN, FINDING_STATUS_PROMOTED, FINDING_STATUS_DISCARDED}


def _finding_id_is_valid(value):
    if not isinstance(value, str) or not value.startswith("FINDING|"):
        return False
    try:
        return str(uuid.UUID(value.removeprefix("FINDING|"))) == value.removeprefix("FINDING|")
    except ValueError:
        return False


def _new_finding_id():
    return "FINDING|" + str(uuid.uuid4())


def _finding_supporting_evidence_is_valid(evidence):
    """Free-form references to the data/numbers that suggested the
    observation. Unlike a Hypothesis's dataset, these MAY come from unsealed
    exploration -- that is permitted here precisely because a Finding is not a
    verdict."""
    return (isinstance(evidence, list) and bool(evidence)
            and all(_hypothesis_text_is_valid(item) for item in evidence)
            and len(evidence) == len(set(evidence)))


def _finding_record_is_valid(record):
    if (not isinstance(record, dict)
            or set(record) != {"finding_id", "schema_version", "observation",
                               "exploration_context", "supporting_evidence", "status",
                               "created_by", "created_at", "history"}
            or record.get("schema_version") != FINDING_REGISTRY_SCHEMA_VERSION
            or not _finding_id_is_valid(record.get("finding_id"))
            or not _hypothesis_text_is_valid(record.get("observation"))
            or not _hypothesis_text_is_valid(record.get("exploration_context"))
            or not _finding_supporting_evidence_is_valid(record.get("supporting_evidence"))
            or record.get("status") not in FINDING_STATUSES
            or not _hypothesis_text_is_valid(record.get("created_by"))
            or not _explicit_utc(record.get("created_at"))
            or not isinstance(record.get("history"), list)):
        return False
    for entry in record["history"]:
        if (not isinstance(entry, dict) or set(entry) != {"status", "at", "note"}
                or entry.get("status") not in FINDING_STATUSES
                or not _explicit_utc(entry.get("at"))
                or not _hypothesis_text_is_valid(entry.get("note"))):
            return False
    return True


def _finding_registry_is_valid(registry):
    if (not isinstance(registry, dict)
            or set(registry) != {"schema_version", "findings"}
            or registry.get("schema_version") != FINDING_REGISTRY_SCHEMA_VERSION
            or not isinstance(registry.get("findings"), list)):
        return False
    ids = [item.get("finding_id") for item in registry["findings"]]
    return (all(_finding_record_is_valid(item) for item in registry["findings"])
            and len(ids) == len(set(ids)))


def _load_finding_registry(registry_path):
    try:
        registry = json.loads(Path(registry_path).read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Persisted Finding registry cannot be read") from error
    if not _finding_registry_is_valid(registry):
        raise ValueError("Persisted Finding registry is invalid or has a version conflict")
    return registry


def _persist_finding_registry(registry_path, registry):
    if not _finding_registry_is_valid(registry):
        raise ValueError("Finding registry is invalid")
    _atomic_write(Path(registry_path), encoded(registry))


def constitute_finding(registry_path, *, observation, exploration_context, supporting_evidence,
                       created_by, created_at):
    """Record one observation from exploration. Never freezes anything: no
    acceptance criterion, no period, no dataset -- those belong to the
    Hypothesis a Finding may later become."""
    if not _explicit_utc(created_at):
        raise ValueError("Finding creation time must be canonical UTC")
    record = {
        "finding_id": _new_finding_id(),
        "schema_version": FINDING_REGISTRY_SCHEMA_VERSION,
        "observation": observation,
        "exploration_context": exploration_context,
        "supporting_evidence": supporting_evidence,
        "status": FINDING_STATUS_OPEN,
        "created_by": created_by,
        "created_at": created_at,
        "history": [{"status": FINDING_STATUS_OPEN, "at": created_at, "note": "constituted"}],
    }
    if not _finding_record_is_valid(record):
        raise ValueError("Finding must declare an observation, its exploration context, and "
                         "the evidence that suggested it")
    registry_path = Path(registry_path)
    registry = (_load_finding_registry(registry_path) if registry_path.exists()
                else {"schema_version": FINDING_REGISTRY_SCHEMA_VERSION, "findings": []})
    registry["findings"].append(record)
    _persist_finding_registry(registry_path, registry)
    return record


def load_finding(registry_path, finding_id):
    if not _finding_id_is_valid(finding_id):
        raise ValueError("A valid Finding identity is required")
    registry = _load_finding_registry(registry_path)
    matches = [item for item in registry["findings"] if item["finding_id"] == finding_id]
    if len(matches) != 1:
        raise ValueError("Finding is not registered unambiguously")
    return matches[0]


def _transition_finding(registry_path, finding_id, new_status, at, note):
    if not _explicit_utc(at):
        raise ValueError("Finding transition time must be canonical UTC")
    registry_path = Path(registry_path)
    registry = _load_finding_registry(registry_path)
    matches = [item for item in registry["findings"] if item["finding_id"] == finding_id]
    if len(matches) != 1:
        raise ValueError("Finding is not registered unambiguously")
    record = matches[0]
    if record["status"] != FINDING_STATUS_OPEN:
        raise ValueError("Only an OPEN Finding can be promoted or discarded")
    record["status"] = new_status
    record["history"] = record["history"] + [{"status": new_status, "at": at, "note": note}]
    _persist_finding_registry(registry_path, registry)
    return record


def promote_finding(registry_path, finding_id, *, hypothesis_id, at):
    """Mark that an OPEN Finding became a formal Hypothesis. The Hypothesis
    must already have been constituted with `FINDING|<finding_id>` in its
    provenance; this records the reverse link, closing the loop. Deliberately
    a separate, manual step from constitute_hypothesis -- the transition is
    where the frozen contract is imposed, and keeping it manual is the
    anti-snooping safeguard (see this module's docstring)."""
    if not _hypothesis_text_is_valid(hypothesis_id):
        raise ValueError("A Hypothesis identity is required to promote a Finding")
    return _transition_finding(
        registry_path, finding_id, FINDING_STATUS_PROMOTED, at,
        "promoted to " + hypothesis_id)


def discard_finding(registry_path, finding_id, *, reason, at):
    """Mark that an OPEN Finding was judged not worth testing. A discarded
    Finding stays in its own lightweight registry and never enters the sealed
    Knowledge Core -- so exploration that led nowhere does not pollute it."""
    if not _hypothesis_text_is_valid(reason):
        raise ValueError("A reason is required to discard a Finding")
    return _transition_finding(
        registry_path, finding_id, FINDING_STATUS_DISCARDED, at, "discarded: " + reason)


def query_findings(registry_path, status=None):
    """List Findings, optionally filtered by status. The everyday Discovery
    tool: what is still OPEN and awaiting a decision to test or drop."""
    if status is not None and status not in FINDING_STATUSES:
        raise ValueError("Unknown Finding status filter")
    registry = _load_finding_registry(registry_path)
    return [item for item in registry["findings"]
            if status is None or item["status"] == status]
