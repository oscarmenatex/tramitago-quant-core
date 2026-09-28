"""CAP-002 Research extension -- Etapa 2.7: autonomous hypothesis generation.

See "Etapa 2.7 -- Generacion Autonoma de Hipotesis.txt" (tramitago-quant-core-docs)
for the full risk analysis (data snooping / multiple comparisons, DOC-004 SS14)
this Etapa exists to neutralize by design. R-2.7-001 requires the search
space to be declared and justified in writing BEFORE any hypothesis in the
batch is generated -- never expanded ad hoc after seeing results.

M2.7-T1 (search space, below) declares the bounded batch. M2.7-T2 (the
multiple-comparisons correction) lives in walk_forward.py's
_statistical_validation_outcome. M2.7-T3 (below, batch registry) audits
every hypothesis GENERATED and TESTED in one batch, not just the ones that
validated (R-2.7-003). M2.7-T4 (the autonomous orchestration that actually
runs a batch end to end) is a separate microciclo.
"""

import json
import re
from pathlib import Path

from tramitago_quant_core.shared.util import (
    digest, encoded, _atomic_write, _explicit_utc, _hypothesis_text_is_valid,
    _hypothesis_code_revision_is_valid, _pipeline_source_bytes,
)
from tramitago_quant_core.research.hypothesis import _hypothesis_id_is_valid
from tramitago_quant_core.research.walk_forward import (
    STATISTICAL_VALIDATION_OUTCOMES, _statistical_validation_id_is_valid,
    verified_statistical_validation,
)
from tramitago_quant_core.strategy_contract.strategy import (
    sma_crossover_strategy, momentum_crossover_strategy,
)

# R-2.7-001 -- bounded, pre-justified search space. Written justification,
# fixed at declaration time (2026-09-28):
#
#   - SMA_CROSSOVER windows {3, 5, 7, 10}: the same strategy family already
#     investigated manually for the rejected SMA3 Hypothesis (BTC-USD,
#     ETH-USD, both NOT_VALIDATED against the Fase 0->1 bar). Widening the
#     window is the narrowest possible variation of an already-understood
#     strategy, not an unrelated new idea reached for after a rejection.
#   - MOMENTUM_CROSSOVER lookbacks {3, 5, 7, 10}: the only other Strategy
#     family proven through the Strategy contract (M4.1) at the time this
#     search space was declared. Deliberately the same four period lengths
#     as the SMA family, so the batch cannot be read as favoring one
#     family's parameter range over the other's.
#   - Single instrument (BTC-USD): the instrument every prior Hypothesis in
#     this project has used. Testing a second instrument in the same batch
#     as N=8 new strategy variants would confound which dimension --
#     strategy or instrument -- explains any result.
#
# N = 8. This is the ENTIRE space for this batch. No other window,
# lookback, instrument, or indicator family may be added to THIS declared
# batch after it is generated; a future batch is a new, separately-declared
# search space, never a silent extension of this one.
HYPOTHESIS_GENERATION_SEARCH_SPACE_ID = "SEARCH_SPACE|2026-09-28|SMA_MOMENTUM_BTC_USD"
HYPOTHESIS_GENERATION_INSTRUMENT = "BTC-USD"
HYPOTHESIS_GENERATION_SMA_WINDOWS = (3, 5, 7, 10)
HYPOTHESIS_GENERATION_MOMENTUM_LOOKBACKS = (3, 5, 7, 10)


def hypothesis_generation_search_space():
    """Return this batch's Strategy objects, in a fixed, declared order.

    Deterministic and side-effect free: calling this twice must produce
    structurally identical Strategies (verified by the corresponding test),
    since the batch registry (M2.7-T3) will hash this declaration.
    """
    strategies = [sma_crossover_strategy(window)
                 for window in HYPOTHESIS_GENERATION_SMA_WINDOWS]
    strategies += [momentum_crossover_strategy(lookback)
                  for lookback in HYPOTHESIS_GENERATION_MOMENTUM_LOOKBACKS]
    return strategies


# M2.7-T3 (R-2.7-003): an aggregate registry auditing every Strategy
# GENERATED and TESTED in one batch -- not just the ones whose Statistical
# Validation came back VALIDATED. Independently consultable from each
# individual Knowledge Record, so a later audit can tell whether a "success"
# was real or an artifact of how many variants were tried.
HYPOTHESIS_GENERATION_BATCH_REGISTRY_SCHEMA_VERSION = "1"
HYPOTHESIS_GENERATION_BATCH_SCHEMA_VERSION = "1"
HYPOTHESIS_GENERATION_BATCH_STATUS = "SEALED"


def _hypothesis_generation_batch_id_is_valid(value):
    return (isinstance(value, str)
            and bool(re.fullmatch(r"HYPOTHESIS_GENERATION_BATCH\|[0-9a-f]{64}", value)))


def _hypothesis_generation_batch_member_is_valid(member):
    fields = {"strategy_id", "parameters", "hypothesis_id", "hypothesis_version",
             "validation_id", "dataset_directory"}
    return (
        isinstance(member, dict) and set(member) == fields
        and _hypothesis_text_is_valid(member.get("strategy_id"))
        and isinstance(member.get("parameters"), dict)
        and _hypothesis_id_is_valid(member.get("hypothesis_id"))
        and isinstance(member.get("hypothesis_version"), int)
        and not isinstance(member.get("hypothesis_version"), bool)
        and member["hypothesis_version"] >= 1
        and _statistical_validation_id_is_valid(member.get("validation_id"))
        and _hypothesis_text_is_valid(member.get("dataset_directory"))
    )


def _hypothesis_generation_batch_id(search_space_id, instrument, members):
    content = {
        "schema_version": HYPOTHESIS_GENERATION_BATCH_SCHEMA_VERSION,
        "search_space_id": search_space_id,
        "instrument": instrument,
        "members": members,
    }
    return "HYPOTHESIS_GENERATION_BATCH|" + digest(encoded(content))


def _hypothesis_generation_batch_materialization(created_at, batch_code_revision):
    if (not _explicit_utc(created_at)
            or not _hypothesis_code_revision_is_valid(batch_code_revision)):
        raise ValueError("Batch creation time and code revision are required")
    source = _pipeline_source_bytes()
    return {
        "created_at": created_at, "batch_code_revision": batch_code_revision,
        "pipeline_sha256": digest(source),
    }


_HYPOTHESIS_GENERATION_STRATEGY_CONSTRUCTORS = {
    "SMA_CROSSOVER": lambda parameters: sma_crossover_strategy(parameters["window"]),
    "MOMENTUM_CROSSOVER": lambda parameters: momentum_crossover_strategy(parameters["lookback"]),
}


def _hypothesis_generation_batch_member_strategy(member):
    """Reconstruct the exact Strategy a batch member declares -- mirrors
    experiment.py's _experiment_conditions_strategy, since a member's
    Hypothesis Dataset/Partition must be reverified with this same Strategy
    object, not the SMA3 default."""
    constructor = _HYPOTHESIS_GENERATION_STRATEGY_CONSTRUCTORS.get(member["strategy_id"])
    if constructor is None:
        raise ValueError("Batch member Strategy is not recognized")
    return constructor(member["parameters"])


def _hypothesis_generation_batch_summary(outcomes):
    counts = {name: 0 for name in STATISTICAL_VALIDATION_OUTCOMES}
    for outcome in outcomes:
        counts[outcome] += 1
    return {
        "batch_size": len(outcomes),
        "validated_count": counts["VALIDATED"],
        "not_validated_count": counts["NOT_VALIDATED"],
        "insufficient_evidence_count": counts["INSUFFICIENT_EVIDENCE"],
    }


def _hypothesis_generation_batch_member_outcomes(members, *, validation_registry_path,
                                                 partition_registry_path,
                                                 fold_result_registry_path,
                                                 experiment_registry_path,
                                                 hypothesis_registry_path):
    """Independently reload and reverify every member's Statistical
    Validation -- never trust a passed-in outcome. Also proves each member
    was actually evaluated with THIS batch's own size (R-2.7-002): a member
    cannot silently reuse a validation corrected for a different N, or none
    at all.
    """
    outcomes = []
    for member in members:
        strategy = _hypothesis_generation_batch_member_strategy(member)
        validation = verified_statistical_validation(
            validation_registry_path, member["validation_id"],
            partition_registry_path=partition_registry_path,
            fold_result_registry_path=fold_result_registry_path,
            experiment_registry_path=experiment_registry_path,
            hypothesis_registry_path=hypothesis_registry_path,
            dataset_directory=member["dataset_directory"], strategy=strategy)
        batch = validation.get("batch")
        if batch is None or batch["batch_size"] != len(members):
            raise ValueError(
                "Batch member Statistical Validation is not corrected for this batch size")
        outcomes.append(validation["outcome"])
    return outcomes


def _hypothesis_generation_batch_content(batch_id, search_space_id, instrument, members,
                                         summary, materialization):
    return {
        "batch_id": batch_id,
        "schema_version": HYPOTHESIS_GENERATION_BATCH_SCHEMA_VERSION,
        "search_space_id": search_space_id,
        "instrument": instrument,
        "members": members,
        "summary": summary,
        "materialization": materialization,
        "status": HYPOTHESIS_GENERATION_BATCH_STATUS,
    }


def _hypothesis_generation_batch_record(batch_id, search_space_id, instrument, members,
                                        summary, materialization):
    content = _hypothesis_generation_batch_content(
        batch_id, search_space_id, instrument, members, summary, materialization)
    return {**content, "record_id": "HYPOTHESIS_GENERATION_BATCH_RECORD|" + batch_id + "|"
            + digest(encoded(content))}


def _hypothesis_generation_batch_record_is_valid(record):
    fields = {"batch_id", "schema_version", "search_space_id", "instrument", "members",
             "summary", "materialization", "status", "record_id"}
    if not isinstance(record, dict) or set(record) != fields:
        return False
    members = record.get("members")
    summary = record.get("summary")
    materialization = record.get("materialization")
    if (not _hypothesis_generation_batch_id_is_valid(record.get("batch_id"))
            or record.get("schema_version") != HYPOTHESIS_GENERATION_BATCH_SCHEMA_VERSION
            or not _hypothesis_text_is_valid(record.get("search_space_id"))
            or not _hypothesis_text_is_valid(record.get("instrument"))
            or not isinstance(members, list) or not members
            or not all(_hypothesis_generation_batch_member_is_valid(item) for item in members)
            or len({(item["hypothesis_id"], item["validation_id"]) for item in members})
            != len(members)
            or record["batch_id"] != _hypothesis_generation_batch_id(
                record["search_space_id"], record["instrument"], members)
            or not isinstance(summary, dict) or set(summary) != {
                "batch_size", "validated_count", "not_validated_count",
                "insufficient_evidence_count"}
            or summary["batch_size"] != len(members)
            or (summary["validated_count"] + summary["not_validated_count"]
                + summary["insufficient_evidence_count"]) != len(members)
            or not isinstance(materialization, dict) or set(materialization) != {
                "created_at", "batch_code_revision", "pipeline_sha256"}
            or not _explicit_utc(materialization.get("created_at"))
            or not _hypothesis_code_revision_is_valid(materialization.get("batch_code_revision"))
            or not re.fullmatch(r"[0-9a-f]{64}", materialization.get("pipeline_sha256", ""))
            or record.get("status") != HYPOTHESIS_GENERATION_BATCH_STATUS):
        return False
    expected = _hypothesis_generation_batch_record(
        record["batch_id"], record["search_space_id"], record["instrument"], members,
        summary, materialization)
    return record == expected


def _hypothesis_generation_batch_registry_is_valid(registry):
    if (not isinstance(registry, dict)
            or set(registry) != {"schema_version", "batches"}
            or registry.get("schema_version") != HYPOTHESIS_GENERATION_BATCH_REGISTRY_SCHEMA_VERSION
            or not isinstance(registry.get("batches"), list)
            or not all(_hypothesis_generation_batch_record_is_valid(item)
                      for item in registry["batches"])):
        return False
    identifiers = [item["batch_id"] for item in registry["batches"]]
    return len(identifiers) == len(set(identifiers))


def _load_hypothesis_generation_batch_registry(registry_path):
    try:
        registry = json.loads(Path(registry_path).read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Persisted Hypothesis Generation Batch registry cannot be read") from error
    if not _hypothesis_generation_batch_registry_is_valid(registry):
        raise ValueError("Persisted Hypothesis Generation Batch registry is invalid")
    return registry


def _persist_hypothesis_generation_batch(registry_path, record):
    if not _hypothesis_generation_batch_record_is_valid(record):
        raise ValueError("Hypothesis Generation Batch record is invalid")
    path = Path(registry_path)
    registry = (_load_hypothesis_generation_batch_registry(path) if path.exists() else {
        "schema_version": HYPOTHESIS_GENERATION_BATCH_REGISTRY_SCHEMA_VERSION, "batches": []})
    matches = [item for item in registry["batches"] if item["batch_id"] == record["batch_id"]]
    if matches:
        if len(matches) == 1 and matches[0] == record:
            return matches[0]
        raise ValueError("Hypothesis Generation Batch identity already exists with different content")
    registry["batches"].append(record)
    if not _hypothesis_generation_batch_registry_is_valid(registry):
        raise ValueError("Constructed Hypothesis Generation Batch registry is invalid")
    _atomic_write(path, encoded(registry))
    return record


def constitute_hypothesis_generation_batch(registry_path, *, search_space_id, instrument, members,
                                           validation_registry_path, partition_registry_path,
                                           fold_result_registry_path, experiment_registry_path,
                                           hypothesis_registry_path, created_at,
                                           batch_code_revision):
    """M2.7-T3: seal one batch's full audit trail -- every Strategy tested,
    not just the ones that validated -- plus an independently-reverified
    aggregate outcome summary. Idempotent: constituting the same batch
    content twice returns the already-sealed record."""
    if not _hypothesis_text_is_valid(search_space_id) or not _hypothesis_text_is_valid(instrument):
        raise ValueError("Batch search space identity and instrument are required")
    if not isinstance(members, list) or not members or not all(
            _hypothesis_generation_batch_member_is_valid(item) for item in members):
        raise ValueError("Batch members are invalid")
    pairs = [(item["hypothesis_id"], item["validation_id"]) for item in members]
    if len(pairs) != len(set(pairs)):
        raise ValueError("Batch members must be distinct")

    outcomes = _hypothesis_generation_batch_member_outcomes(
        members, validation_registry_path=validation_registry_path,
        partition_registry_path=partition_registry_path,
        fold_result_registry_path=fold_result_registry_path,
        experiment_registry_path=experiment_registry_path,
        hypothesis_registry_path=hypothesis_registry_path)
    summary = _hypothesis_generation_batch_summary(outcomes)

    batch_id = _hypothesis_generation_batch_id(search_space_id, instrument, members)
    materialization = _hypothesis_generation_batch_materialization(created_at, batch_code_revision)
    record = _hypothesis_generation_batch_record(
        batch_id, search_space_id, instrument, members, summary, materialization)
    return _persist_hypothesis_generation_batch(registry_path, record)


def load_hypothesis_generation_batch(registry_path, batch_id):
    if not _hypothesis_generation_batch_id_is_valid(batch_id):
        raise ValueError("A valid Hypothesis Generation Batch identity is required")
    registry = _load_hypothesis_generation_batch_registry(registry_path)
    matches = [item for item in registry["batches"] if item["batch_id"] == batch_id]
    if len(matches) != 1:
        raise ValueError("Hypothesis Generation Batch is not registered unambiguously")
    return matches[0]


def verified_hypothesis_generation_batch(registry_path, batch_id, *, validation_registry_path,
                                         partition_registry_path, fold_result_registry_path,
                                         experiment_registry_path, hypothesis_registry_path):
    """Reload a batch and reproduce its aggregate summary from every
    member's independently re-verified Statistical Validation -- never
    trust the persisted summary alone."""
    record = load_hypothesis_generation_batch(registry_path, batch_id)
    outcomes = _hypothesis_generation_batch_member_outcomes(
        record["members"], validation_registry_path=validation_registry_path,
        partition_registry_path=partition_registry_path,
        fold_result_registry_path=fold_result_registry_path,
        experiment_registry_path=experiment_registry_path,
        hypothesis_registry_path=hypothesis_registry_path)
    summary = _hypothesis_generation_batch_summary(outcomes)
    expected = _hypothesis_generation_batch_record(
        record["batch_id"], record["search_space_id"], record["instrument"], record["members"],
        summary, record["materialization"])
    if record != expected:
        raise ValueError("Hypothesis Generation Batch summary or membership is invalid")
    return record
