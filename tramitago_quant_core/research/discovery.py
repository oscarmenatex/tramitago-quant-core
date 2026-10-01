"""Bounded Discovery: look cheaply and widely, so the expensive machinery is
spent on candidates that survived a look.

THE PROBLEM THIS EXISTS FOR. Thirty-eight Hypotheses, zero validated. Every one
of them went straight into the full sealed apparatus -- dataset capture,
Experiment, Walk-Forward, Bonferroni, Knowledge Record -- so the search has been
limited by how many ideas can be invented by hand, and each wrong guess costs a
complete validation cycle. Etapa 2.7's "autonomous generation" did not change
this: it hard-coded eight variants and ran all eight through the whole
apparatus. The bottleneck is not validation throughput. It is that nothing ever
LOOKS before committing.

A Finding already exists (research/finding.py) as the record of an observation.
What never existed is the thing that PRODUCES Findings. This is that mechanism.

WHAT MAKES IT BOUNDED, which is the whole design. Searching widely is exactly
how a project convinces itself of noise, so each of these is load-bearing:

  1. THE SPACE IS SEALED BEFORE ANY DATA IS READ. `constitute_discovery_space`
     hashes the complete enumeration of candidates, both windows, and a written
     justification. A scan refuses a space it cannot reload and rehash. This is
     R-2.7-001 promoted from a module constant to an object that can be audited.

  2. THE MULTIPLICITY TRAVELS WITH THE FINDING. Every candidate's observation is
     sealed, not just the winner's, and the Finding records how many were
     examined. Without this, Discovery becomes a laundering machine: look at
     five hundred, report one, and let the Hypothesis be corrected for N=1. The
     count exists so the correction downstream is the honest one.

  3. A HOLDOUT THE SCAN CANNOT READ. The space declares a discovery window and a
     disjoint holdout, and `scan_discovery_space` REFUSES any row outside the
     discovery window -- it is not a convention, it raises. The Hypothesis a
     Finding motivates is then constituted over the holdout, which no scan ever
     saw. This is the protection that actually binds; "the promotion step is
     manual" was procedural and depended on nobody making a mistake.

  4. THE REPRESENTATIVENESS GATE, APPLIED BEFORE THE MONEY IS SPENT. The rule
     adopted 2026-09-30 -- a monitor must be able to observe the state it is
     meant to detect -- has until now lived only in a governance document, and
     it killed two Hypotheses AFTER each had consumed a full validation cycle
     (the SPY regime filters and the carry exit rule, both INSUFFICIENT_EVIDENCE
     for the same reason). Here it runs first, for free. This module is the
     first place that rule is executable code.

WHAT A SCAN PRODUCES IS NOT EVIDENCE. The effect measured on the discovery
window is an OBSERVATION. It has been selected on, by construction, which is the
point of discovery and the reason it can never be a verdict. The Finding
contract says this already; this module is the thing that honours it.
"""

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from tramitago_quant_core.shared.util import (
    digest, encoded, epoch, _atomic_write, _explicit_utc, _hypothesis_text_is_valid,
    _hypothesis_code_revision_is_valid, _pipeline_source_bytes,
)
from tramitago_quant_core.research.experiment import _EXPERIMENT_STRATEGY_CONSTRUCTORS
from tramitago_quant_core.research.finding import constitute_finding, query_findings
from tramitago_quant_core.strategy_contract.strategy import _strategy_classify_rows
from tramitago_quant_core.strategy_contract.outcome import strategy_outcome

DISCOVERY_SPACE_REGISTRY_SCHEMA_VERSION = "1"
DISCOVERY_SPACE_SCHEMA_VERSION = "1"
DISCOVERY_SCAN_REGISTRY_SCHEMA_VERSION = "1"
DISCOVERY_SCAN_SCHEMA_VERSION = "1"

# How many decimals an observation is sealed with. Fixed so the same rows always
# hash to the same scan: a float repr that varies by platform would make a sealed
# scan irreproducible on another machine.
_OBSERVATION_PRECISION = 12

GROUP_UPPER = "UPPER"
GROUP_LOWER = "LOWER_OR_EQUAL"


def _fixed(value):
    return f"{value:.{_OBSERVATION_PRECISION}f}"


def _decimal(value, name):
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError) as error:
        raise ValueError(f"{name} is not a decimal") from error
    if not amount.is_finite():
        raise ValueError(f"{name} must be finite")
    return amount


def _window_is_valid(window):
    return (isinstance(window, dict)
            and set(window) == {"start_utc", "end_exclusive_utc"}
            and _explicit_utc(window.get("start_utc"))
            and _explicit_utc(window.get("end_exclusive_utc"))
            and epoch(window["start_utc"]) < epoch(window["end_exclusive_utc"]))


def _windows_are_disjoint(first, second):
    return (epoch(first["end_exclusive_utc"]) <= epoch(second["start_utc"])
            or epoch(second["end_exclusive_utc"]) <= epoch(first["start_utc"]))


def _candidate_is_valid(candidate):
    """A candidate names a Strategy the SEALED machinery already knows how to
    rebuild. That restriction is deliberate: Discovery must not be able to
    propose something the validation layer cannot later re-derive from its own
    record, or a Finding could be unpromotable by construction.

    `series` is OPTIONAL and names which price series the candidate is measured
    on. It exists because a relative-value Strategy carries its second leg as a
    column name ("pair_close"), not as the pair's identity -- so without it,
    PAIR_RATIO_REVERSION(5) on ETH/ETC and on SOL/AVAX are the SAME candidate
    and a space of eighteen pair-window combinations collapses to three. The
    alternative, one scan per pair, would hide the multiplicity across pairs,
    which is precisely the laundering this module exists to prevent.

    Optional rather than versioned, on purpose: field presence is the
    discriminator, the same additive pattern the Outcome contract uses, so every
    already-sealed space and scan reverifies byte for byte.
    """
    return (isinstance(candidate, dict)
            and set(candidate) in ({"strategy_id", "parameters"},
                                   {"strategy_id", "parameters", "series"})
            and candidate.get("strategy_id") in _EXPERIMENT_STRATEGY_CONSTRUCTORS
            and isinstance(candidate.get("parameters"), dict)
            and ("series" not in candidate
                 or _hypothesis_text_is_valid(candidate.get("series"))))


def candidate_strategy(candidate):
    """Rebuild the Strategy object a candidate names."""
    if not _candidate_is_valid(candidate):
        raise ValueError("Discovery candidate is not a recognized Strategy")
    return _EXPERIMENT_STRATEGY_CONSTRUCTORS[candidate["strategy_id"]](candidate["parameters"])


def _discovery_space_id(content):
    return "DISCOVERY_SPACE|" + digest(encoded(content))


def _discovery_space_id_is_valid(value):
    return isinstance(value, str) and bool(re.fullmatch(r"DISCOVERY_SPACE\|[0-9a-f]{64}", value))


def _discovery_space_content(*, justification, candidates, discovery_window, holdout_window,
                             minimum_support, minimum_minority_state_frequency, declared_by):
    return {
        "schema_version": DISCOVERY_SPACE_SCHEMA_VERSION,
        "justification": justification,
        "candidates": candidates,
        "discovery_window": discovery_window,
        "holdout_window": holdout_window,
        "minimum_support": minimum_support,
        "minimum_minority_state_frequency": minimum_minority_state_frequency,
        "declared_by": declared_by,
    }


def _discovery_space_record_is_valid(record):
    fields = {"space_id", "schema_version", "justification", "candidates", "discovery_window",
              "holdout_window", "minimum_support", "minimum_minority_state_frequency",
              "declared_by", "declared_at"}
    if not isinstance(record, dict) or set(record) != fields:
        return False
    candidates = record.get("candidates")
    if (not _discovery_space_id_is_valid(record.get("space_id"))
            or record.get("schema_version") != DISCOVERY_SPACE_SCHEMA_VERSION
            or not _hypothesis_text_is_valid(record.get("justification"))
            or not isinstance(candidates, list) or not candidates
            or not all(_candidate_is_valid(item) for item in candidates)
            or len({encoded(item) for item in candidates}) != len(candidates)
            # All or none. A space mixing series-bearing candidates with
            # series-less ones could not say what the series-less ones were
            # measured on, and its multiplicity would be unreadable.
            or len({"series" in item for item in candidates}) != 1
            or not _window_is_valid(record.get("discovery_window"))
            or not _window_is_valid(record.get("holdout_window"))
            or not _windows_are_disjoint(record["discovery_window"], record["holdout_window"])
            or not isinstance(record.get("minimum_support"), int)
            or isinstance(record.get("minimum_support"), bool)
            or record["minimum_support"] < 1
            or not _hypothesis_text_is_valid(record.get("minimum_minority_state_frequency"))
            or not _hypothesis_text_is_valid(record.get("declared_by"))
            or not _explicit_utc(record.get("declared_at"))):
        return False
    content = _discovery_space_content(
        justification=record["justification"], candidates=candidates,
        discovery_window=record["discovery_window"], holdout_window=record["holdout_window"],
        minimum_support=record["minimum_support"],
        minimum_minority_state_frequency=record["minimum_minority_state_frequency"],
        declared_by=record["declared_by"])
    return record["space_id"] == _discovery_space_id(content)


def _discovery_space_registry_is_valid(registry):
    if (not isinstance(registry, dict)
            or set(registry) != {"schema_version", "spaces"}
            or registry.get("schema_version") != DISCOVERY_SPACE_REGISTRY_SCHEMA_VERSION
            or not isinstance(registry.get("spaces"), list)
            or not all(_discovery_space_record_is_valid(item) for item in registry["spaces"])):
        return False
    identifiers = [item["space_id"] for item in registry["spaces"]]
    return len(identifiers) == len(set(identifiers))


def _load_discovery_space_registry(registry_path):
    try:
        registry = json.loads(Path(registry_path).read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Persisted Discovery Space registry cannot be read") from error
    if not _discovery_space_registry_is_valid(registry):
        raise ValueError("Persisted Discovery Space registry is invalid")
    return registry


def constitute_discovery_space(registry_path, *, justification, candidates, discovery_window,
                               holdout_window, minimum_support,
                               minimum_minority_state_frequency, declared_by, declared_at):
    """Seal the complete, bounded space BEFORE anything is scanned.

    The holdout is mandatory and must be disjoint from the discovery window.
    There is no flag to skip it: a space with nothing held back cannot produce a
    Finding whose Hypothesis is testable on data the search never saw, and that
    is the only reason a wide search is defensible at all.

    `minimum_support` and `minimum_minority_state_frequency` are sealed HERE, not
    passed to the scan, so neither can be relaxed after seeing which candidates
    they would have excluded.
    """
    if not _explicit_utc(declared_at):
        raise ValueError("Discovery Space declaration time must be canonical UTC")
    _decimal(minimum_minority_state_frequency, "Minimum minority state frequency")
    content = _discovery_space_content(
        justification=justification, candidates=candidates, discovery_window=discovery_window,
        holdout_window=holdout_window, minimum_support=minimum_support,
        minimum_minority_state_frequency=minimum_minority_state_frequency,
        declared_by=declared_by)
    record = {**content, "space_id": _discovery_space_id(content), "declared_at": declared_at}
    if not _discovery_space_record_is_valid(record):
        raise ValueError(
            "A Discovery Space must declare a written justification, a non-empty set of "
            "distinct recognized candidates, and a discovery window disjoint from its holdout")

    path = Path(registry_path)
    registry = (_load_discovery_space_registry(path) if path.exists()
                else {"schema_version": DISCOVERY_SPACE_REGISTRY_SCHEMA_VERSION, "spaces": []})
    matches = [item for item in registry["spaces"] if item["space_id"] == record["space_id"]]
    if matches:
        # Identity covers everything but the declaration time, so a re-declaration
        # of the same space is the same space -- returning the ORIGINAL keeps the
        # first declaration's timestamp, which is the one that precedes the data.
        return matches[0]
    registry["spaces"].append(record)
    if not _discovery_space_registry_is_valid(registry):
        raise ValueError("Constructed Discovery Space registry is invalid")
    _atomic_write(path, encoded(registry))
    return record


def load_discovery_space(registry_path, space_id):
    if not _discovery_space_id_is_valid(space_id):
        raise ValueError("A valid Discovery Space identity is required")
    registry = _load_discovery_space_registry(registry_path)
    matches = [item for item in registry["spaces"] if item["space_id"] == space_id]
    if len(matches) != 1:
        raise ValueError("Discovery Space is not registered unambiguously")
    return matches[0]


def minority_state_frequency(upper_count, lower_count):
    """Fraction of classified rows falling in the RARER of the two groups.

    A NECESSARY condition for the representativeness gate, deliberately not the
    gate itself. The full rule asks whether a monitor can observe the state it
    must detect, and which state is adverse depends on the Outcome, not on the
    Strategy alone. But whichever group turns out to be adverse, it cannot be
    observed more often than the rarer one -- so a candidate that fails here can
    never pass the full gate, and that is what makes this a sound cheap filter
    rather than a guess.
    """
    total = upper_count + lower_count
    if total == 0:
        return None
    return min(upper_count, lower_count) / total


def _candidate_observation(strategy, rows, horizon=1, series=None):
    """Measure one candidate on the discovery rows. Returns counts, the minority
    state frequency, and the difference of group means of the Strategy's own
    declared Outcome -- never a verdict, and never a p-value: a number that has
    been selected on cannot carry an error rate, and printing one would invite
    the Finding to be read as evidence."""
    if horizon < 1:
        raise ValueError("Discovery horizon must be a positive integer")
    outcome = strategy_outcome(strategy)
    classified = _strategy_classify_rows(strategy, rows)
    totals = {GROUP_UPPER: [], GROUP_LOWER: []}
    for index, row in enumerate(classified):
        if row["group"] is None or index + horizon >= len(classified):
            continue
        totals[row["group"]].append(outcome["compute"](row, classified[index + horizon]))

    upper, lower = totals[GROUP_UPPER], totals[GROUP_LOWER]
    minority = minority_state_frequency(len(upper), len(lower))
    effect = (sum(upper) / len(upper) - sum(lower) / len(lower)) if upper and lower else None
    return {
        "strategy_id": strategy["strategy_id"],
        "parameters": strategy["parameters"],
        # Present only when the candidate declared one, so a scan sealed before
        # series existed hashes to exactly the same identity it always did.
        **({"series": series} if series is not None else {}),
        "outcome_id": outcome["outcome_id"],
        "upper_count": len(upper),
        "lower_count": len(lower),
        "minority_state_frequency": None if minority is None else _fixed(minority),
        "effect": None if effect is None else _fixed(effect),
    }


def candidate_is_examinable(observation, space):
    """Whether a candidate may become a Finding at all.

    Two refusals, and both are cheap here and ruinous later:
      - support below the declared floor: a group mean over a handful of rows is
        not an observation of anything;
      - minority state below the declared floor: the representativeness gate. A
        candidate failing it is INADMISSIBLE OUTRIGHT, not merely weak -- no
        amount of validation can rescue a mechanism nothing can monitor.
    """
    if observation["effect"] is None:
        return False, "one of the two groups is empty"
    if min(observation["upper_count"], observation["lower_count"]) < space["minimum_support"]:
        return False, (f"support {min(observation['upper_count'], observation['lower_count'])} "
                       f"below the declared minimum {space['minimum_support']}")
    floor = _decimal(space["minimum_minority_state_frequency"], "Minimum minority state frequency")
    observed = _decimal(observation["minority_state_frequency"], "Minority state frequency")
    if observed < floor:
        return False, (f"minority state frequency {observation['minority_state_frequency']} "
                       f"below the declared minimum {space['minimum_minority_state_frequency']}: "
                       "no monitor could observe this state often enough to act on it")
    return True, "examinable"


def _rows_within_window(rows, window):
    start, end = epoch(window["start_utc"]), epoch(window["end_exclusive_utc"])
    return all(start <= epoch(row["timestamp"]) < end for row in rows)


def _candidate_rows(candidate, rows):
    """Resolve the rows a candidate is measured on.

    `rows` is a plain list when the space declares no series, and a mapping from
    series label to rows when it does. Mixing the two forms is refused rather
    than guessed at: silently measuring every candidate on the same series would
    produce a scan whose observations all look distinct and are not.
    """
    series = candidate.get("series")
    if series is None:
        if isinstance(rows, dict):
            raise ValueError("This Discovery Space declares no series, so rows must be a list")
        return None, rows
    if not isinstance(rows, dict):
        raise ValueError("This Discovery Space declares series, so rows must be keyed by series")
    if series not in rows:
        raise ValueError(f"No rows were supplied for declared series {series}")
    return series, rows[series]


def _discovery_scan_id(space_id, observations):
    content = {"schema_version": DISCOVERY_SCAN_SCHEMA_VERSION,
               "space_id": space_id, "observations": observations}
    return "DISCOVERY_SCAN|" + digest(encoded(content))


def _discovery_scan_id_is_valid(value):
    return isinstance(value, str) and bool(re.fullmatch(r"DISCOVERY_SCAN\|[0-9a-f]{64}", value))


def scan_discovery_space(space, rows, *, horizon=1):
    """Measure EVERY candidate in the space on the discovery rows.

    Refuses any row outside the declared discovery window. That refusal is the
    holdout: not a convention a later caller might forget, but the reason a
    Hypothesis built from a Finding can be tested on data this never touched.

    Every candidate's observation comes back, including the refused ones. A scan
    that reported only its survivors would hide its own multiplicity, which is
    the one number the correction downstream depends on.
    """
    if not _discovery_space_record_is_valid(space):
        raise ValueError("Discovery Space record is invalid")
    if not rows:
        raise ValueError("A Discovery scan needs rows")
    for one_series in (rows.values() if isinstance(rows, dict) else [rows]):
        if not one_series or not _rows_within_window(one_series, space["discovery_window"]):
            raise ValueError(
                "Discovery rows fall outside the declared discovery window; the holdout "
                "must never be read by a scan")

    observations = []
    for index, candidate in enumerate(space["candidates"]):
        series, candidate_rows = _candidate_rows(candidate, rows)
        observation = _candidate_observation(
            candidate_strategy(candidate), candidate_rows, horizon, series)
        examinable, reason = candidate_is_examinable(observation, space)
        observations.append({**observation, "declared_order": index,
                             "examinable": examinable, "reason": reason})
    return observations


def rank_observations(observations):
    """Order examinable candidates by observed effect, best first.

    Ranking by the effect measured on the discovery window IS selection, and
    saying so plainly matters more than hedging it: that is what Discovery is
    for. It is defensible only because the number never leaves this layer as
    evidence and the holdout is untouched. Ties break on declared order, so the
    ranking is reproducible and never depends on dictionary ordering.
    """
    examinable = [item for item in observations if item["examinable"]]
    return sorted(examinable, key=lambda item: (-Decimal(item["effect"]), item["declared_order"]))


def _discovery_scan_summary(observations):
    """The shape of the whole scan, not just its winner.

    Added after the first real scan (17 candidates on an axis already measured
    empty) came back with effects scattered almost symmetrically around zero:
    best +0.0029, worst -0.0029, ten positive and seven negative. Reported alone,
    "+0.0029 per day" reads as an edge of roughly 100% a year. Reported beside a
    mirror-image worst and a median at nothing, the same number reads as what it
    is -- the right tail of noise.

    Under a real mechanism the distribution is skewed; under none it is centred.
    That comparison costs nothing and is the first thing anyone reading a Finding
    needs, so it belongs in the record rather than in whoever remembers to ask.
    """
    examinable = [item for item in observations if item["examinable"]]
    ranked = rank_observations(observations)
    effects = sorted(Decimal(item["effect"]) for item in examinable)
    middle = len(effects) // 2
    median = (effects[middle] if len(effects) % 2
              else (effects[middle - 1] + effects[middle]) / 2) if effects else None
    return {
        "candidates_examined": len(observations),
        "candidates_examinable": len(examinable),
        "candidates_refused": len(observations) - len(examinable),
        "best_effect": ranked[0]["effect"] if ranked else None,
        "worst_effect": ranked[-1]["effect"] if ranked else None,
        "median_effect": None if median is None else _fixed(median),
        "positive_effects": sum(1 for value in effects if value > 0),
        "negative_effects": sum(1 for value in effects if value < 0),
    }


def _discovery_scan_materialization(scanned_at, scan_code_revision):
    if not _explicit_utc(scanned_at) or not _hypothesis_code_revision_is_valid(scan_code_revision):
        raise ValueError("Discovery scan time and code revision are required")
    return {"scanned_at": scanned_at, "scan_code_revision": scan_code_revision,
            "pipeline_sha256": digest(_pipeline_source_bytes())}


def _discovery_scan_record(space_id, observations, summary, materialization):
    content = {
        "scan_id": _discovery_scan_id(space_id, observations),
        "schema_version": DISCOVERY_SCAN_SCHEMA_VERSION,
        "space_id": space_id,
        "observations": observations,
        "summary": summary,
        "materialization": materialization,
    }
    return {**content, "record_id": "DISCOVERY_SCAN_RECORD|" + digest(encoded(content))}


def _discovery_scan_record_is_valid(record):
    fields = {"scan_id", "schema_version", "space_id", "observations", "summary",
              "materialization", "record_id"}
    if not isinstance(record, dict) or set(record) != fields:
        return False
    observations = record.get("observations")
    materialization = record.get("materialization")
    if (not _discovery_scan_id_is_valid(record.get("scan_id"))
            or record.get("schema_version") != DISCOVERY_SCAN_SCHEMA_VERSION
            or not _discovery_space_id_is_valid(record.get("space_id"))
            or not isinstance(observations, list) or not observations
            or record["scan_id"] != _discovery_scan_id(record["space_id"], observations)
            or not isinstance(materialization, dict)
            or set(materialization) != {"scanned_at", "scan_code_revision", "pipeline_sha256"}
            or not _explicit_utc(materialization.get("scanned_at"))
            or not _hypothesis_code_revision_is_valid(materialization.get("scan_code_revision"))
            or not re.fullmatch(r"[0-9a-f]{64}", materialization.get("pipeline_sha256", ""))):
        return False
    expected = _discovery_scan_record(
        record["space_id"], observations, _discovery_scan_summary(observations), materialization)
    return record == expected


def _discovery_scan_registry_is_valid(registry):
    if (not isinstance(registry, dict)
            or set(registry) != {"schema_version", "scans"}
            or registry.get("schema_version") != DISCOVERY_SCAN_REGISTRY_SCHEMA_VERSION
            or not isinstance(registry.get("scans"), list)
            or not all(_discovery_scan_record_is_valid(item) for item in registry["scans"])):
        return False
    identifiers = [item["scan_id"] for item in registry["scans"]]
    return len(identifiers) == len(set(identifiers))


def _load_discovery_scan_registry(registry_path):
    try:
        registry = json.loads(Path(registry_path).read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Persisted Discovery Scan registry cannot be read") from error
    if not _discovery_scan_registry_is_valid(registry):
        raise ValueError("Persisted Discovery Scan registry is invalid")
    return registry


def constitute_discovery_scan(registry_path, *, space, observations, scanned_at,
                              scan_code_revision):
    """Seal one scan: every candidate examined, with its own refusal reason where
    it was refused. Idempotent on identical content, like every other sealed
    record here."""
    if not _discovery_space_record_is_valid(space):
        raise ValueError("Discovery Space record is invalid")
    record = _discovery_scan_record(
        space["space_id"], observations, _discovery_scan_summary(observations),
        _discovery_scan_materialization(scanned_at, scan_code_revision))
    if not _discovery_scan_record_is_valid(record):
        raise ValueError("Discovery Scan record is invalid")

    path = Path(registry_path)
    registry = (_load_discovery_scan_registry(path) if path.exists()
                else {"schema_version": DISCOVERY_SCAN_REGISTRY_SCHEMA_VERSION, "scans": []})
    matches = [item for item in registry["scans"] if item["scan_id"] == record["scan_id"]]
    if matches:
        if len(matches) != 1:
            raise ValueError("Discovery Scan is registered ambiguously")
        # The ORIGINAL comes back, materialization and all, and that is a
        # deliberate departure from the Hypothesis Generation Batch registry,
        # which refuses a re-seal whose content differs at all.
        #
        # A batch is a one-time act: it constitutes new Hypotheses, so running it
        # twice means two different things happened. A scan is a pure function of
        # (space, rows) over a window that is already in the past -- re-running it
        # must reproduce it, and the identity already covers the space and EVERY
        # observation. What can still differ is only when it was sealed, by which
        # revision, against which pipeline source. None of those change what was
        # measured; identical observations from a later revision is reassurance,
        # not a conflict. Raising on it would make a runner unrunnable twice,
        # which is how this was found.
        return matches[0]
    registry["scans"].append(record)
    if not _discovery_scan_registry_is_valid(registry):
        raise ValueError("Constructed Discovery Scan registry is invalid")
    _atomic_write(path, encoded(registry))
    return record


def load_discovery_scan(registry_path, scan_id):
    if not _discovery_scan_id_is_valid(scan_id):
        raise ValueError("A valid Discovery Scan identity is required")
    registry = _load_discovery_scan_registry(registry_path)
    matches = [item for item in registry["scans"] if item["scan_id"] == scan_id]
    if len(matches) != 1:
        raise ValueError("Discovery Scan is not registered unambiguously")
    return matches[0]


def _finding_already_derived(registry_path, scan_id, rank):
    """The Finding previously derived from this scan at this rank, if any.

    Matched on the evidence the Finding already carries rather than on a new
    stored key, so nothing has to be added to the Finding contract and an
    already-sealed Finding is recognised by its own record. A DISCARDED one
    counts: re-running a runner must not quietly resurrect an observation
    somebody judged not worth testing.
    """
    if not Path(registry_path).exists():
        return None
    marker = f"SELECTED_RANK|{rank}"
    for record in query_findings(registry_path):
        evidence = record["supporting_evidence"]
        if scan_id in evidence and marker in evidence:
            return record
    return None


def finding_from_scan(finding_registry_path, *, scan, space, rank=0, created_by, created_at):
    """Turn the rank-th examinable candidate of a sealed scan into a Finding.

    THE MULTIPLICITY IS NOT OPTIONAL. The Finding's supporting evidence states
    how many candidates were examined and where in the ranking this one came.
    A Hypothesis later constituted from this Finding must be corrected for that
    count, not for one -- selecting the best of forty and then testing it as
    though it were the only idea anyone had is precisely the data snooping the
    whole separation exists to prevent, and it is invisible unless the number
    travels with the observation.

    The holdout window is carried too, so whoever constitutes the Hypothesis can
    see, without going back to the registry, which period the search already
    consumed.

    Still deliberately not automatic: this produces an OBSERVATION, never a
    Hypothesis. Promotion stays the manual, two-step transition finding.py
    describes.
    """
    if scan["space_id"] != space["space_id"]:
        raise ValueError("Scan and Discovery Space do not correspond")
    if not _discovery_scan_record_is_valid(scan):
        raise ValueError("Discovery Scan record is invalid")
    ranked = rank_observations(scan["observations"])
    if not ranked:
        raise ValueError("No candidate in this scan is examinable")
    if not isinstance(rank, int) or isinstance(rank, bool) or not 0 <= rank < len(ranked):
        raise ValueError("Requested rank is outside this scan's examinable candidates")
    chosen = ranked[rank]

    # A scan is idempotent; without this the Finding was not, so re-running a
    # runner minted a SECOND Finding for the same observation. That is not a
    # cosmetic duplicate: the Finding registry is how anyone asks what is still
    # OPEN and awaiting a decision, and one observation appearing as two makes
    # the search look broader than it was -- the same misreading the sealed
    # multiplicity exists to prevent. Identity stays a UUID, because a Finding's
    # status is mutable; what is deduplicated is the DERIVATION, keyed on the
    # evidence already recorded.
    existing = _finding_already_derived(finding_registry_path, scan["scan_id"], rank)
    if existing is not None:
        return existing

    return constitute_finding(
        finding_registry_path,
        observation=(
            f"On the discovery window alone, {chosen['strategy_id']}{chosen['parameters']}"
            f"{' on ' + chosen['series'] if 'series' in chosen else ''} "
            f"shows a difference of mean {chosen['outcome_id']} between its two groups of "
            f"{chosen['effect']}. This is an observation selected out of "
            f"{scan['summary']['candidates_examined']} candidates, not evidence. The same scan's "
            f"worst candidate scored {scan['summary']['worst_effect']} and its median "
            f"{scan['summary']['median_effect']}, with "
            f"{scan['summary']['positive_effects']} positive and "
            f"{scan['summary']['negative_effects']} negative."),
        exploration_context=(
            f"Bounded Discovery scan {scan['scan_id']} over Discovery Space "
            f"{space['space_id']}, discovery window "
            f"{space['discovery_window']['start_utc']} to "
            f"{space['discovery_window']['end_exclusive_utc']}. Holdout reserved and unread: "
            f"{space['holdout_window']['start_utc']} to "
            f"{space['holdout_window']['end_exclusive_utc']}."),
        supporting_evidence=[
            scan["scan_id"],
            space["space_id"],
            f"CANDIDATES_EXAMINED|{scan['summary']['candidates_examined']}",
            f"SELECTED_RANK|{rank}",
            f"EFFECT_ON_DISCOVERY_WINDOW|{chosen['effect']}",
            f"SCAN_EFFECT_SPREAD|worst={scan['summary']['worst_effect']}"
            f"|median={scan['summary']['median_effect']}"
            f"|positive={scan['summary']['positive_effects']}"
            f"|negative={scan['summary']['negative_effects']}",
            f"MINORITY_STATE_FREQUENCY|{chosen['minority_state_frequency']}",
            f"SUPPORT|UPPER={chosen['upper_count']}|LOWER={chosen['lower_count']}",
            *([f"SERIES|{chosen['series']}"] if "series" in chosen else []),
            f"HOLDOUT_UNREAD|{space['holdout_window']['start_utc']}|"
            f"{space['holdout_window']['end_exclusive_utc']}",
        ],
        created_by=created_by, created_at=created_at)
