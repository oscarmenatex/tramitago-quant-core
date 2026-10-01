"""Resolve an Investment Committee `evidence_reference` against the sealed
research registries (audit "Vinculo Evidencia-Operacion", 2026-09-30).

Before this module the reference was validated only as "a non-empty string", so
`evidence_reference: "looks good"` passed, and the one run that reached the
broker approved IC-1 with an identifier that resolved to nothing while citing a
verdict that argued for the opposite position. The committee had the field and
never read it.

This lives in its own file, and is INJECTED into the committee rather than
imported by it, so the governance layer stays ignorant of research vocabulary:
the committee asks "does this support capital?", not "what is a Disposition?".
The same separation the dataset layer keeps with its verifiers.
"""

import json
from pathlib import Path

# What each kind of sealed verdict has to say for capital to be defensible.
# A Disposition is the full-sample adjudication; a Statistical Validation is the
# walk-forward one. INSUFFICIENT_EVIDENCE is not a pass: it is the machinery
# declining to produce a number, which is the opposite of support.
DISPOSITION_SUPPORTS = ("ACCEPTED",)
VALIDATION_SUPPORTS = ("VALIDATED",)


def _records(path, keys):
    try:
        payload = json.loads(Path(path).read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in keys:
        if isinstance(payload.get(key), list):
            return payload[key]
    return []


def _disposition_outcome(disposition_paths, disposition_id):
    """The sealed outcome of one Disposition, or None when it is not registered."""
    for path in disposition_paths:
        for record in _records(path, ("dispositions", "records")):
            if record.get("disposition_id") == disposition_id:
                return record.get("outcome")
    return None


def _unresolved(reference, detail):
    return {"resolved": False, "supports_capital": False,
            "reference": reference, "detail": detail}


def sealed_evidence_resolver(*, disposition_paths=(), validation_paths=(),
                             knowledge_paths=()):
    """Build the callable the committee injects.

    Returns resolver(evidence_reference) -> {resolved, supports_capital,
    reference, detail}. It never raises on a missing artifact: an unresolvable
    reference is a RESOLUTION whose `resolved` is False, so the committee can
    report precisely why it refused rather than failing opaquely.
    """
    def resolver(evidence_reference):
        if not isinstance(evidence_reference, str) or not evidence_reference.strip():
            return _unresolved(evidence_reference, "empty reference")
        reference = evidence_reference.strip()

        for path in knowledge_paths:
            for record in _records(path, ("knowledge", "knowledge_records", "records")):
                if record.get("knowledge_id") != reference:
                    continue
                # A Knowledge Record preserves negative results too (DOC-004
                # REQ-004-004), so its mere existence proves nothing. It points
                # at the adjudication by identifier; follow that pointer rather
                # than treating the record itself as a verdict.
                target = ((record.get("references") or {}).get("disposition")
                          or {}).get("disposition_id")
                if not target:
                    return _unresolved(
                        reference, "knowledge record carries no disposition reference")
                outcome = _disposition_outcome(disposition_paths, target)
                if outcome is None:
                    return _unresolved(
                        reference,
                        "knowledge record points at an absent disposition " + str(target))
                return {"resolved": True,
                        "supports_capital": outcome in DISPOSITION_SUPPORTS,
                        "reference": reference,
                        "detail": "knowledge record -> disposition " + str(outcome)}

        outcome = _disposition_outcome(disposition_paths, reference)
        if outcome is not None:
            return {"resolved": True,
                    "supports_capital": outcome in DISPOSITION_SUPPORTS,
                    "reference": reference, "detail": "disposition " + str(outcome)}

        for path in validation_paths:
            for record in _records(path, ("statistical_validations", "validations", "records")):
                if record.get("statistical_validation_id") != reference:
                    continue
                outcome = record.get("outcome")
                return {"resolved": True,
                        "supports_capital": outcome in VALIDATION_SUPPORTS,
                        "reference": reference,
                        "detail": "statistical validation " + str(outcome)}

        return _unresolved(
            reference, "no sealed disposition, statistical validation or knowledge record "
                       "carries this identifier")

    return resolver
