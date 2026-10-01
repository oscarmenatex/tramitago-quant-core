"""CAP-010 Governance -- Investment Committee (Etapa 4.5, M4.5-T3,
2026-09-29): the five independent evaluations of DOC-008 §4, replacing the
single collapsed AUTHORIZED/REJECTED of Governance Authorization (M2.5-T3).

DOC-008 §4.2 defines the committee as INDEPENDENT evaluations, and MR-008-002
(risk independent of strategy) plus MR-008-003 (limit risk before maximizing
return) fix how they combine: a risk or capital veto is absolute. The five:
  IC-1 Estrategia -- expected return, statistical robustness, temporal stability.
  IC-2 Riesgo     -- drawdown, volatility, portfolio correlation, tail risk.
  IC-3 Mercado    -- market regime, liquidity, macro conditions.
  IC-4 Capital    -- position size, total exposure, portfolio limits.
  IC-5 Fiscal     -- OPTIONAL / future phase (DOC-008 §4.2; out of scope until
                     DOC-001 Fase 3). Omit it and the committee still decides.

This module holds the five evaluations and derives the final decision; it does
NOT recompute the underlying analytics -- each evaluation REFERENCES the sealed
artifact that grounds it (a Knowledge/Disposition/Validation for IC-1, a Risk
Analytics summary and the M4.5-T1 Risk Control gate for IC-2/IC-4, ...), which
is exactly how the Director's requirement "prior research work must serve
future stages" is honored: the committee is the consumer of that chain. Each
evaluation stays SEPARATELY TRACEABLE (its own verdict, evidence reference and
rationale), never collapsed.

Only the AGGREGATE is re-derived on verification -- it is a deterministic,
risk-first function of the five verdicts, so verified_investment_committee
recomputes it from the sealed evaluations and fails closed on any tampering.
"""

import json
import re
from pathlib import Path

from tramitago_quant_core.shared.util import (
    digest, encoded, _atomic_write, _explicit_utc, _hypothesis_text_is_valid,
    _hypothesis_code_revision_is_valid, _pipeline_source_bytes,
)

INVESTMENT_COMMITTEE_REGISTRY_SCHEMA_VERSION = "1"
INVESTMENT_COMMITTEE_SCHEMA_VERSION = "2"
INVESTMENT_COMMITTEE_LEGACY_SCHEMA_VERSION = "1"
INVESTMENT_COMMITTEE_STATUS = "DECIDED"

# Audit "Vinculo Evidencia-Operacion" (2026-09-30). A run through the chain can
# mean one of two things, and until now the record could not say which, so the
# committee form offered only strategy judgments and a machinery test had to be
# dressed as one. Declaring it is what makes the evidence requirement
# conditional instead of absolute -- an absolute requirement would forbid
# exercising the execution path at all while no Hypothesis passes DOC-011 2.1.
COMMITTEE_PURPOSE_MACHINERY = "MACHINERY_VERIFICATION"
COMMITTEE_PURPOSE_CAPITAL = "CAPITAL_ALLOCATION"
COMMITTEE_PURPOSES = (COMMITTEE_PURPOSE_MACHINERY, COMMITTEE_PURPOSE_CAPITAL)

# The member whose verdict is a claim ABOUT THE STRATEGY, and therefore the one
# whose evidence has to resolve. IC-2/IC-3/IC-4 rest on risk, market and capital
# artifacts, which the research registries do not hold.
IC_STRATEGY_MEMBER = "IC-1"

IC_CORE_MEMBERS = ("IC-1", "IC-2", "IC-3", "IC-4")
IC_OPTIONAL_MEMBER = "IC-5"
IC_MEMBERS = IC_CORE_MEMBERS + (IC_OPTIONAL_MEMBER,)
IC_MEMBER_ROLES = {
    "IC-1": "Estrategia", "IC-2": "Riesgo", "IC-3": "Mercado",
    "IC-4": "Capital", "IC-5": "Fiscal",
}
IC_VERDICTS = {"APPROVE", "REDUCE", "REJECT", "INCONCLUSIVE"}

# The four decision types of DOC-008 §6.
COMMITTEE_DECISIONS = {
    "APPROVED": "D-008-001",   # every core evaluation approves
    "REJECTED": "D-008-002",   # any core evaluation vetoes (risk/capital/strategy)
    "REDUCED": "D-008-003",    # no veto, but at least one asks to size down
    "DEFERRED": "D-008-004",   # an evaluation is inconclusive -> observe, re-evaluate
}


def _committee_id_is_valid(value):
    return (isinstance(value, str)
            and bool(re.fullmatch(r"INVESTMENT_COMMITTEE\|[0-9a-f]{64}", value)))


def committee_evaluation(*, member, verdict, evidence_reference, rationale):
    """One independent evaluation. `evidence_reference` is the sealed artifact
    the verdict rests on (e.g. a knowledge_id, a statistical_validation_id, a
    risk-control gate outcome) -- this is what keeps the committee a CONSUMER
    of the research/risk chain rather than a recomputation of it."""
    if member not in IC_MEMBERS:
        raise ValueError("Unknown committee member")
    if verdict not in IC_VERDICTS:
        raise ValueError("Unknown evaluation verdict")
    if not _hypothesis_text_is_valid(evidence_reference):
        raise ValueError("Each evaluation must reference the evidence it rests on")
    if not _hypothesis_text_is_valid(rationale):
        raise ValueError("Each evaluation must record its rationale")
    return {
        "member": member,
        "role": IC_MEMBER_ROLES[member],
        "verdict": verdict,
        "evidence_reference": evidence_reference,
        "rationale": rationale,
    }


def _evaluation_is_valid(evaluation):
    return (isinstance(evaluation, dict)
            and set(evaluation) == {"member", "role", "verdict", "evidence_reference", "rationale"}
            and evaluation.get("member") in IC_MEMBERS
            and evaluation.get("role") == IC_MEMBER_ROLES.get(evaluation.get("member"))
            and evaluation.get("verdict") in IC_VERDICTS
            and _hypothesis_text_is_valid(evaluation.get("evidence_reference"))
            and _hypothesis_text_is_valid(evaluation.get("rationale")))


def committee_decision(evaluations):
    """The deterministic, risk-first aggregation (MR-008-001/003: capital
    preservation first; limit risk before maximizing return).

    A veto from ANY core evaluation is absolute -> REJECTED. Otherwise an
    inconclusive core evaluation means observe-and-re-evaluate -> DEFERRED
    (a missing verdict must never be read as approval). Otherwise a request to
    size down -> REDUCED. Only when every core evaluation clearly approves ->
    APPROVED. IC-5 (Fiscal) is advisory here: it can veto/defer but its absence
    never blocks, matching its optional status."""
    by_member = {e["member"]: e["verdict"] for e in evaluations}
    core = [by_member[m] for m in IC_CORE_MEMBERS]
    fiscal = by_member.get(IC_OPTIONAL_MEMBER)  # may be absent

    considered = list(core) + ([fiscal] if fiscal is not None else [])
    if "REJECT" in considered:
        outcome = "REJECTED"
    elif "INCONCLUSIVE" in considered:
        outcome = "DEFERRED"
    elif "REDUCE" in considered:
        outcome = "REDUCED"
    else:
        outcome = "APPROVED"
    return {"outcome": outcome, "code": COMMITTEE_DECISIONS[outcome]}


def _committee_content(recommendation_reference, evaluations, decision, purpose,
                       schema_version=INVESTMENT_COMMITTEE_SCHEMA_VERSION):
    """Schema 1 records carry no purpose and are still verifiable as written.
    Only schema 2 is ever constituted, so the gap cannot reopen going forward
    while the one pre-existing sealed record stays readable as evidence."""
    if schema_version == INVESTMENT_COMMITTEE_LEGACY_SCHEMA_VERSION:
        return {
            "schema_version": INVESTMENT_COMMITTEE_LEGACY_SCHEMA_VERSION,
            "recommendation_reference": recommendation_reference,
            "evaluations": evaluations,
            "decision": decision,
        }
    return {
        "schema_version": INVESTMENT_COMMITTEE_SCHEMA_VERSION,
        "recommendation_reference": recommendation_reference,
        "purpose": purpose,
        "evaluations": evaluations,
        "decision": decision,
    }


def _committee_evidence_is_bound(purpose, evaluations, evidence_resolver):
    """Refuse to seal a capital-allocation APPROVE whose strategy evidence does
    not resolve, or resolves to a verdict that does not support it.

    A machinery-verification run is exempt BY DESIGN, not by oversight: it
    asserts that the order path works, not that the trade is good, so a rejected
    signal is a legitimate -- and currently the only available -- input. The
    exemption is safe only because the purpose is sealed into the record and
    `execute_alpaca_live_order` refuses anything but CAPITAL_ALLOCATION.
    """
    if purpose != COMMITTEE_PURPOSE_CAPITAL:
        return
    strategy = [e for e in evaluations if e["member"] == IC_STRATEGY_MEMBER]
    if len(strategy) != 1:
        raise ValueError("A capital-allocation committee requires the IC-1 evaluation")
    if strategy[0]["verdict"] != "APPROVE":
        return          # a veto or deferral needs no supporting evidence to stand
    if evidence_resolver is None:
        raise ValueError(
            "A capital-allocation committee requires an evidence resolver for IC-1")
    resolution = evidence_resolver(strategy[0]["evidence_reference"])
    if not isinstance(resolution, dict) or "supports_capital" not in resolution:
        raise ValueError("Evidence resolver returned an unusable resolution")
    if not resolution.get("resolved"):
        raise ValueError(
            "IC-1 evidence does not resolve to a sealed artifact: "
            + str(resolution.get("detail", strategy[0]["evidence_reference"])))
    if not resolution["supports_capital"]:
        raise ValueError(
            "IC-1 cannot approve capital allocation on evidence that does not support it: "
            + str(resolution.get("detail", "")))


def _committee_materialization(decided_at, committee_code_revision):
    if (not _explicit_utc(decided_at)
            or not _hypothesis_code_revision_is_valid(committee_code_revision)):
        raise ValueError("Committee decision time and code revision are required")
    return {
        "decided_at": decided_at,
        "committee_code_revision": committee_code_revision,
        "pipeline_sha256": digest(_pipeline_source_bytes()),
    }


def _validated_committee(recommendation_reference, evaluations, materialization, purpose=None,
                         schema_version=INVESTMENT_COMMITTEE_SCHEMA_VERSION,
                         evidence_resolver=None):
    if not _hypothesis_text_is_valid(recommendation_reference):
        raise ValueError("A recommendation reference is required")
    if not isinstance(evaluations, list) or not all(_evaluation_is_valid(e) for e in evaluations):
        raise ValueError("Committee evaluations are invalid")
    members = [e["member"] for e in evaluations]
    if len(members) != len(set(members)):
        raise ValueError("Each committee member evaluates at most once")
    if not set(IC_CORE_MEMBERS).issubset(set(members)):
        raise ValueError("All four core evaluations (IC-1..IC-4) are required")
    if schema_version != INVESTMENT_COMMITTEE_LEGACY_SCHEMA_VERSION:
        if purpose not in COMMITTEE_PURPOSES:
            raise ValueError("A committee must declare its purpose: "
                             + " or ".join(COMMITTEE_PURPOSES))
    ordered = sorted(evaluations, key=lambda e: e["member"])
    decision = committee_decision(ordered)
    content = _committee_content(recommendation_reference, ordered, decision, purpose,
                                 schema_version)
    committee_id = "INVESTMENT_COMMITTEE|" + digest(encoded(content))
    record = {
        "committee_id": committee_id,
        **content,
        "materialization": materialization,
        "status": INVESTMENT_COMMITTEE_STATUS,
    }
    record["record_id"] = ("INVESTMENT_COMMITTEE_RECORD|" + committee_id + "|"
                           + digest(encoded({k: record[k] for k in record})))
    return record


def _load_committee_registry(registry_path):
    try:
        registry = json.loads(Path(registry_path).read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Persisted Investment Committee registry cannot be read") from error
    if (not isinstance(registry, dict)
            or set(registry) != {"schema_version", "committees"}
            or registry.get("schema_version") != INVESTMENT_COMMITTEE_REGISTRY_SCHEMA_VERSION
            or not isinstance(registry.get("committees"), list)):
        raise ValueError("Persisted Investment Committee registry is invalid")
    return registry


def constitute_investment_committee(registry_path, *, recommendation_reference, evaluations,
                                    decided_at, committee_code_revision, purpose,
                                    evidence_resolver=None):
    """M4.5-T3: seal one committee decision -- the five independent evaluations
    (IC-5 optional) plus the risk-first aggregate. Idempotent on identical
    content. A REJECTED or DEFERRED decision is preserved exactly like an
    APPROVED one (DOC-004 REQ-004-004 spirit).

    `purpose` is one of COMMITTEE_PURPOSES and is sealed into the identity.
    `evidence_resolver` is a callable(evidence_reference) -> {"resolved": bool,
    "supports_capital": bool, "detail": str}; it is REQUIRED when the purpose is
    CAPITAL_ALLOCATION and IC-1 approves, and unused otherwise. Injected rather
    than imported so this module stays ignorant of research vocabulary, the same
    way the dataset layer takes its verifiers from the caller."""
    materialization = _committee_materialization(decided_at, committee_code_revision)
    if purpose not in COMMITTEE_PURPOSES:
        raise ValueError("A committee must declare its purpose: " + " or ".join(COMMITTEE_PURPOSES))
    if isinstance(evaluations, list) and all(_evaluation_is_valid(e) for e in evaluations):
        _committee_evidence_is_bound(purpose, evaluations, evidence_resolver)
    record = _validated_committee(recommendation_reference, evaluations, materialization,
                                  purpose, INVESTMENT_COMMITTEE_SCHEMA_VERSION)
    registry_path = Path(registry_path)
    registry = (_load_committee_registry(registry_path) if registry_path.exists()
                else {"schema_version": INVESTMENT_COMMITTEE_REGISTRY_SCHEMA_VERSION,
                      "committees": []})
    existing = [item for item in registry["committees"]
                if item["committee_id"] == record["committee_id"]]
    if existing:
        if len(existing) != 1 or existing[0] != record:
            raise ValueError("Investment Committee identity is ambiguous or inconsistent")
        return existing[0]
    registry["committees"].append(record)
    _atomic_write(registry_path, encoded(registry))
    return record


def load_investment_committee(registry_path, committee_id):
    if not _committee_id_is_valid(committee_id):
        raise ValueError("A valid Investment Committee identity is required")
    registry = _load_committee_registry(registry_path)
    matches = [item for item in registry["committees"]
               if item["committee_id"] == committee_id]
    if len(matches) != 1:
        raise ValueError("Investment Committee is not registered unambiguously")
    return matches[0]


def verified_investment_committee(registry_path, committee_id):
    """Reload a committee record and RE-DERIVE its aggregate decision and both
    identities from the sealed evaluations, failing closed on any tampering --
    a flipped verdict, an altered decision, or a broken hash chain."""
    record = load_investment_committee(registry_path, committee_id)
    schema_version = record.get("schema_version")
    if schema_version not in (INVESTMENT_COMMITTEE_SCHEMA_VERSION,
                              INVESTMENT_COMMITTEE_LEGACY_SCHEMA_VERSION):
        raise ValueError("Investment Committee schema version is unsupported")
    # Re-derivation only, never re-adjudication: the resolver is deliberately not
    # called here. Whether the evidence supported capital was settled when the
    # record was sealed, and a verdict must not silently change later because a
    # registry moved. Verification detects tampering; it does not re-decide.
    expected = _validated_committee(
        record.get("recommendation_reference"), record.get("evaluations"),
        record.get("materialization"), record.get("purpose"), schema_version)
    if record != expected:
        raise ValueError("Investment Committee evaluations or decision are invalid")
    return record
