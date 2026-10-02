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
validated (R-2.7-003). M2.7-T4 (below, autonomous orchestration) runs a
batch end to end -- only after T1/T2/T3 exist, per the Etapa's own rule
that automating the orchestration without the safeguards would automate
the risk, not resolve it.
"""

import json
import re
from pathlib import Path

from tramitago_quant_core.shared.util import (
    digest, encoded, epoch, _atomic_write, _explicit_utc, _hypothesis_text_is_valid,
    _hypothesis_code_revision_is_valid, _pipeline_source_bytes,
)
from tramitago_quant_core.research.hypothesis import _hypothesis_id_is_valid, constitute_hypothesis
from tramitago_quant_core.research.historical_dataset import create_hypothesis_dataset
from tramitago_quant_core.research.experiment import (
    constitute_experiment_conditions, execute_experiment_result,
)
from tramitago_quant_core.research.walk_forward import (
    STATISTICAL_VALIDATION_OUTCOMES, minimum_folds_for_batch,
    _statistical_validation_id_is_valid,
    verified_statistical_validation, constitute_walk_forward_partition,
    constitute_walk_forward_fold_result, constitute_statistical_validation,
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
HYPOTHESIS_GENERATION_PERIOD = {
    "start_utc": "2025-01-01T00:00:00Z", "end_exclusive_utc": "2026-01-01T00:00:00Z"}
# Every Hypothesis in this batch is a systematic variation of the SAME
# originally-discovered SMA3 pattern (widening/narrowing its window,
# swapping the averaging for a lag) -- not a fresh, independent discovery --
# so it honestly traces back to the same discovery evidence snapshot every
# prior real Hypothesis (BTC-USD, ETH-USD) has used, rather than a new one
# invented to justify each variant after the fact.
HYPOTHESIS_GENERATION_DISCOVERY_SNAPSHOT_SHA256 = (
    "a1b6a0bbe47c247c7e5d4adc5ab9c8d578dd0c347d39c4bdb6f6dc2c089ec00f")


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


def _hypothesis_generation_dataset_slug(strategy):
    parameter_value = next(iter(strategy["parameters"].values()))
    return f"{strategy['strategy_id'].lower()}_{parameter_value}"


def _hypothesis_generation_target_metric(strategy):
    return (f"mean_forward_return_1d({strategy['upper_group_description']}) - "
            f"mean_forward_return_1d({strategy['lower_or_equal_group_description']})")


def run_hypothesis_generation_batch(*, hypothesis_registry_path, dataset_root,
                                    experiment_registry_path, result_registry_path,
                                    partition_registry_path, fold_result_registry_path,
                                    validation_registry_path, batch_registry_path, created_at,
                                    code_revision, created_by="autonomous-hypothesis-generation",
                                    fold_count=None, minimum_folds_required=None, period=None,
                                    consistency_threshold="0.7", transport=None,
                                    acquired_at=None):
    """M2.7-T4: the autonomous orchestration this Etapa exists to build.

    For every Strategy in the declared search space (M2.7-T1): constitutes a
    Hypothesis, captures its sealed Dataset, defines and executes its
    Experiment, partitions and evaluates its Walk-Forward, then seals its
    Statistical Validation Bonferroni-corrected for this batch's size
    (M2.7-T2). Finally seals one aggregate Batch record (M2.7-T3) auditing
    every member, not just the ones that validated.

    Deliberately runs only after M2.7-T1/T2/T3 exist: automating the
    orchestration itself without those safeguards would automate the data-
    snooping risk this Etapa exists to neutralize, not resolve it.

    NOT idempotent: constitute_hypothesis always registers a genuinely new
    Hypothesis identity (no dedup key), matching how every Hypothesis in
    this project has always been constituted -- so calling this twice with
    the same `dataset_root` fails on the second run's dataset directory
    collision rather than silently duplicating the batch. Each real batch
    is a one-time act, exactly like constituting any other single Hypothesis.
    """
    strategies = hypothesis_generation_search_space()
    batch_size = len(strategies)
    # DERIVED FROM THE BATCH, not defaulted. A batch of N is corrected for N, and
    # below minimum_folds_for_batch(N) no member could pass whatever it measured:
    # the original default of 5 made this very batch of 8 unpassable, which is
    # how the defect was found. A caller may still declare more folds, never
    # fewer.
    required = minimum_folds_for_batch(batch_size)
    fold_count = required if fold_count is None else fold_count
    minimum_folds_required = required if minimum_folds_required is None else minimum_folds_required
    period = dict(HYPOTHESIS_GENERATION_PERIOD if period is None else period)
    # The walk-forward also demands the fold count divide the period exactly, and
    # the two rules together are sharper than either alone: 365 days admits only
    # 5 folds (365 = 5 x 73), and 5 folds support a corrected search of size ONE.
    # A batch of 8 over a calendar year is unvalidatable whatever it measures,
    # which is why the period is a parameter rather than a constant.
    span = (epoch(period["end_exclusive_utc"]) - epoch(period["start_utc"])) // 86400
    if span % fold_count:
        raise ValueError(
            f"A {span}-day period cannot be split into {fold_count} equal folds, and "
            f"{fold_count} is the fewest a search of {batch_size} can be corrected for. "
            f"Declare a period divisible by {fold_count}.")
    dataset_root = Path(dataset_root)
    members = []
    for strategy in strategies:
        upper = strategy["upper_group_description"]
        lower = strategy["lower_or_equal_group_description"]
        target_metric = _hypothesis_generation_target_metric(strategy)
        constraints = {
            "variables": ["close", strategy["column_name"], "forward_return_1d"],
            "period": dict(period),
            "universe": [HYPOTHESIS_GENERATION_INSTRUMENT],
        }
        acceptance_criterion = {
            "metric": target_metric, "comparison": "GT", "threshold": "0",
            "expected_direction": "INCREASE",
        }
        hypothesis = constitute_hypothesis(
            hypothesis_registry_path,
            description=(
                f"For {HYPOTHESIS_GENERATION_INSTRUMENT}, the mean t+1 return when {upper} "
                f"exceeds the mean t+1 return when {lower} (Etapa 2.7 autonomous batch "
                f"{HYPOTHESIS_GENERATION_SEARCH_SPACE_ID})."),
            target_metric=target_metric, expected_direction="INCREASE", constraints=constraints,
            acceptance_criterion=acceptance_criterion, creation_timestamp=created_at,
            status="CONSTITUTED", created_by=created_by,
            provenance=["DISCOVERY|artifacts/live-run-1",
                       "SNAPSHOT_SHA256|" + HYPOTHESIS_GENERATION_DISCOVERY_SNAPSHOT_SHA256],
            system_version="0.1.0", code_revision=code_revision)

        output = dataset_root / _hypothesis_generation_dataset_slug(strategy)
        manifest = create_hypothesis_dataset(
            hypothesis_registry_path, hypothesis["hypothesis_id"], 1, output,
            transport=transport, acquired_at=acquired_at, strategy=strategy)

        exp_record = constitute_experiment_conditions(
            experiment_registry_path, hypothesis_registry_path=hypothesis_registry_path,
            dataset_directory=output, hypothesis_id=hypothesis["hypothesis_id"],
            hypothesis_version=1, dataset_id=manifest["dataset_id"], created_at=created_at,
            revision_reason=f"AUTONOMOUS_BATCH_{HYPOTHESIS_GENERATION_SEARCH_SPACE_ID}",
            strategy=strategy)

        execute_experiment_result(
            result_registry_path, experiment_registry_path=experiment_registry_path,
            hypothesis_registry_path=hypothesis_registry_path, dataset_directory=output,
            experiment_id=exp_record["experiment_id"], experiment_version=1,
            experiment_record_id=exp_record["record_id"], execution_code_revision=code_revision)

        partition = constitute_walk_forward_partition(
            partition_registry_path, dataset_directory=output,
            hypothesis_registry_path=hypothesis_registry_path, fold_count=fold_count,
            partitioned_at=created_at, partition_code_revision=code_revision, strategy=strategy)

        for index in range(fold_count):
            constitute_walk_forward_fold_result(
                fold_result_registry_path, partition_registry_path=partition_registry_path,
                partition_id=partition["partition_id"], partition_record_id=partition["record_id"],
                fold_index=index, experiment_registry_path=experiment_registry_path,
                experiment_id=exp_record["experiment_id"], experiment_version=1,
                experiment_record_id=exp_record["record_id"],
                hypothesis_registry_path=hypothesis_registry_path, dataset_directory=output,
                computed_at=created_at, computation_code_revision=code_revision, strategy=strategy)

        validation = constitute_statistical_validation(
            validation_registry_path, partition_registry_path=partition_registry_path,
            partition_id=partition["partition_id"], partition_record_id=partition["record_id"],
            fold_result_registry_path=fold_result_registry_path,
            experiment_registry_path=experiment_registry_path,
            hypothesis_registry_path=hypothesis_registry_path, dataset_directory=output,
            minimum_folds_required=minimum_folds_required,
            consistency_threshold=consistency_threshold, validated_at=created_at,
            validation_code_revision=code_revision, batch_size=batch_size, strategy=strategy)

        members.append({
            "strategy_id": strategy["strategy_id"], "parameters": strategy["parameters"],
            "hypothesis_id": hypothesis["hypothesis_id"], "hypothesis_version": 1,
            "validation_id": validation["validation_id"], "dataset_directory": str(output),
        })

    return constitute_hypothesis_generation_batch(
        batch_registry_path, search_space_id=HYPOTHESIS_GENERATION_SEARCH_SPACE_ID,
        instrument=HYPOTHESIS_GENERATION_INSTRUMENT, members=members,
        validation_registry_path=validation_registry_path,
        partition_registry_path=partition_registry_path,
        fold_result_registry_path=fold_result_registry_path,
        experiment_registry_path=experiment_registry_path,
        hypothesis_registry_path=hypothesis_registry_path,
        created_at=created_at, batch_code_revision=code_revision)
