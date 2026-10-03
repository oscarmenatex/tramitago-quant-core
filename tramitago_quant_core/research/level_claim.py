"""The judge for a LEVEL claim: does holding this position pay, net of costs?

WHY THIS EXISTS, stated as the contradiction it resolves. The destination was
redefined to HARVESTING RISK PREMIA. The hypothesis catalogue says of the carry,
the one premium this project has actually measured, that it is "fuera del alcance
metodológico actual -- NO ES UNA HYPOTHESIS en el sentido de M2.x: no hay grupos
que comparar". Both sentences cannot stand. The existing apparatus computes
`upper_mean - lower_mean` (experiment.py) and returns INCONCLUSIVE the moment one
group is empty, so it can only ever judge a COMPARATIVE claim: "returns differ
between these two groups". A risk premium is a LEVEL claim about one position:
"holding this earns more than zero after what it costs to hold it". Thirty-eight
Hypotheses have been judged by the comparative apparatus and none validated,
while the measured 10.63%-24.14% annualised funding has never faced a verdict at
all, because nothing could express the question.

This judges that question. It knows nothing about groups, signals or directions.

THREE THINGS IT REFUSES TO DO, and each is the point rather than a precaution:

  NET ONLY. There is no gross path and no default contract. Everything this
  project has sealed to date is gross -- thirty-three hypotheses accepted or
  rejected on a quantity nobody could have traded. A premium is exactly the kind
  of claim that dies on costs, since it is harvested by holding something that
  must be entered, financed and eventually closed, on both legs.

  THE ADVERSE BOUND, NEVER THE MEAN. And not a t-bound: a premium's return
  distribution is "small positive, small positive, ..., occasionally
  catastrophic", so a normal approximation prices the tail it is most important
  to get right as though it did not exist. The bound here is the lower percentile
  of a MOVING-BLOCK bootstrap, which resamples runs rather than days so that
  persistence -- funding regimes last weeks -- survives the resampling. The seed
  and the block length are sealed into the claim, so the number is reproducible
  and cannot be re-rolled until it looks better.

  AND IT REFUSES TO CERTIFY A PREMIUM WHOSE TAIL IT NEVER SAW. This is the
  decisive one. A carry loses when the basis moves against it or the funding
  inverts, which is rare; a fold containing no such episode shows a beautiful
  positive mean, and a verdict built on consistency across such folds would be
  measuring THAT NOTHING WENT WRONG, not that the premium is real. Hypothesis #37
  died of exactly this, and the representativeness rule adopted 2026-09-30 exists
  because of it. So a window must CONTAIN the episode or the outcome is
  INSUFFICIENT_EVIDENCE -- never VALIDATED, and never NOT_VALIDATED either, since
  "we did not observe the thing that kills this" is not a refutation.

TAIL COVERAGE AND MONITORABILITY ARE TWO DIFFERENT THINGS, and schema version 1
of this module conflated them. It asked for a FREQUENCY of adverse periods, which
is the monitorability question -- can a monitor observe the state often enough to
act in time. Applied to the measurement question it is unanswerable: a tail is
rare BY DEFINITION, so no window can ever show 10% of days in it. The consequence
was measured rather than argued. Judged over 2019-2021, which contains the March
2020 collapse, the carry loses 10.48% in a single day and one unlevered fold
breaches the 15% drawdown limit -- and the gate returned INSUFFICIENT_EVIDENCE
for the SAME REASON as the placid 2024-2025 window: 2 adverse days in 730 is
0.27%. A window holding the tail and a window missing it came back
indistinguishable.

Version 2 asks for COVERAGE instead: how many distinct adverse episodes the
window contains, which is what a measurement can actually answer and what makes
those two windows different. The frequency question is not abandoned, it moves to
where it belongs -- the monitoring contract, as a condition of OPERATING a
premium rather than of measuring one. Version 1 claims keep being judged by the
version 1 rule, so every verdict already issued stays reproducible as what it
was.

The verdict vocabulary is deliberately the same VALIDATED / NOT_VALIDATED /
INSUFFICIENT_EVIDENCE the comparative apparatus issues, so governance needs no
new concept to consume it.
"""

import json
import math
import random
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from tramitago_quant_core.shared.util import (
    digest, encoded, _atomic_write, _explicit_utc, _hypothesis_text_is_valid,
    _hypothesis_code_revision_is_valid, _pipeline_source_bytes,
)
from tramitago_quant_core.risk.cost_model import verified_cost_contract, net_returns, cost_summary
from tramitago_quant_core.research.walk_forward import (
    _risk_analytics_max_drawdown, _risk_analytics_sharpe_ratio,
    STATISTICAL_VALIDATION_OUTCOMES,
)

LEVEL_CLAIM_SCHEMA_VERSION = "3"
LEVEL_CLAIM_SCHEMA_VERSIONS = ("1", "2", "3")
LEVEL_CLAIM_VALIDATION_SCHEMA_VERSION = "1"
LEVEL_CLAIM_VALIDATION_REGISTRY_SCHEMA_VERSION = "1"
LEVEL_CLAIM_VALIDATION_STATUS = "ISSUED"

FOLD_MET = "MET"
FOLD_NOT_MET = "NOT_MET"
FOLD_INCONCLUSIVE = "INCONCLUSIVE"

OUTCOME_VALIDATED = "VALIDATED"
OUTCOME_NOT_VALIDATED = "NOT_VALIDATED"
OUTCOME_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"

# Reasons, kept explicit so a verdict never arrives without saying why. The
# distinction between the last two is the one that matters: a premium nobody
# watched lose is UNJUDGED, not refuted.
REASON_FOLDS_BELOW_MINIMUM = "USABLE_FOLDS_BELOW_MINIMUM"
REASON_ADVERSE_PERIODS_TOO_RARE = "ADVERSE_PERIODS_BELOW_MINIMUM_FREQUENCY"   # schema 1 only
REASON_TAIL_NOT_COVERED = "ADVERSE_EPISODES_BELOW_MINIMUM"
REASON_CONSISTENCY_BELOW = "CONSISTENCY_BELOW_THRESHOLD"
REASON_DRAWDOWN_EXCEEDED = "DRAWDOWN_LIMIT_EXCEEDED"

_PRECISION = 12


def _fixed(value):
    return None if value is None else f"{value:.{_PRECISION}f}"


def _decimal(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be declared as a string")
    try:
        amount = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{name} is not a decimal") from error
    if not amount.is_finite():
        raise ValueError(f"{name} must be finite")
    return amount


def _positive_int(value, name):
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def level_claim(*, position_description, cost_contract, minimum_folds_required,
                consistency_threshold, minimum_adverse_episodes,
                adverse_period_threshold, maximum_drawdown,
                confidence_level="0.95", bootstrap_resamples=2000, bootstrap_block_periods=5,
                bootstrap_seed=0, source):
    """Seal one declared level claim BEFORE it is evaluated.

    Everything that could be tuned after seeing a number lives here and is hashed
    into the claim's identity: the confidence level, the bootstrap's seed, block
    length and resample count, the drawdown limit, the consistency threshold and
    the representativeness floor. A claim re-declared with a friendlier seed is a
    DIFFERENT claim with a different identity, which is the only way to make
    re-rolling visible.

    `cost_contract` is required and has no default. A level claim evaluated gross
    would be the thirty-fourth measurement of a quantity nobody could trade.

    `minimum_adverse_episodes` is the TAIL COVERAGE requirement: how many distinct
    adverse episodes the window must contain for the measurement to have seen what
    ends this position. One is already a strong requirement for a premium, and
    zero is not accepted -- a window with no episode cannot judge a premium at
    all, which is the whole reason this field replaced a frequency floor.

    `adverse_period_threshold` is required and has no default either, and the
    reason is a hole found by using this module on the real carry. Counting any
    losing period as "adverse" let two years of small daily noise satisfy
    representativeness -- 27.8% of days lost money -- while the window contained
    NO episode of the kind that actually ends a carry: the worst single day lost
    0.198%, against a +2.74% adverse basis excursion measured on BitMEX during a
    squeeze. The gate passed on a technicality. Declaring the MAGNITUDE that
    counts as adverse is what makes the gate ask the question it was adopted to
    ask. Zero is permitted and means "any loss counts", which is the weakest
    declaration available and should be chosen deliberately, never by default.
    """
    verified_cost_contract(cost_contract)
    if not _hypothesis_text_is_valid(position_description):
        raise ValueError("A level claim must describe the position it is about")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("A level claim must declare where its terms come from")

    _positive_int(minimum_folds_required, "Minimum folds required")
    _positive_int(bootstrap_resamples, "Bootstrap resamples")
    _positive_int(bootstrap_block_periods, "Bootstrap block periods")
    if not isinstance(bootstrap_seed, int) or isinstance(bootstrap_seed, bool) or bootstrap_seed < 0:
        raise ValueError("Bootstrap seed must be a non-negative integer")

    consistency = _decimal(consistency_threshold, "Consistency threshold")
    _positive_int(minimum_adverse_episodes, "Minimum adverse episodes")
    adverse_threshold = _decimal(adverse_period_threshold, "Adverse period threshold")
    if adverse_threshold > 0:
        raise ValueError("Adverse period threshold must be zero or a loss")
    drawdown = _decimal(maximum_drawdown, "Maximum drawdown")
    confidence = _decimal(confidence_level, "Confidence level")
    if not 0 < consistency <= 1:
        raise ValueError("Consistency threshold must be in (0, 1]")
    if not 0 < drawdown < 1:
        raise ValueError("Maximum drawdown must be a fraction in (0, 1)")
    if not Decimal("0.5") < confidence < 1:
        raise ValueError("Confidence level must be in (0.5, 1)")

    content = {
        "schema_version": LEVEL_CLAIM_SCHEMA_VERSION,
        "position_description": position_description,
        "cost_contract_id": cost_contract["contract_id"],
        "minimum_folds_required": minimum_folds_required,
        "consistency_threshold": consistency_threshold,
        "minimum_adverse_episodes": minimum_adverse_episodes,
        "adverse_period_threshold": adverse_period_threshold,
        "maximum_drawdown": maximum_drawdown,
        "confidence_level": confidence_level,
        "bootstrap_resamples": bootstrap_resamples,
        "bootstrap_block_periods": bootstrap_block_periods,
        "bootstrap_seed": bootstrap_seed,
        "source": source.strip(),
    }
    return {**content, "claim_id": "LEVEL_CLAIM|" + digest(encoded(content))}


def verified_level_claim(claim):
    """Re-derive the identity, failing closed on any alteration of the terms."""
    if not isinstance(claim, dict) or "claim_id" not in claim:
        raise ValueError("Level claim is invalid")
    content = {key: value for key, value in claim.items() if key != "claim_id"}
    if claim["claim_id"] != "LEVEL_CLAIM|" + digest(encoded(content)):
        raise ValueError("Level claim identity does not match its terms")
    return claim


def adverse_period_frequency(returns, threshold=0.0):
    """Fraction of periods in which the position lost MORE than `threshold`.

    The operational meaning of representativeness for a level claim. A premium
    harvested over a stretch where it never really lost has not been measured
    against the thing that ends it, and no amount of consistency across such
    stretches changes that -- it compounds it.

    The magnitude matters as much as the sign, which is what using this on the
    carry made obvious: at threshold zero, two placid years register 27.8%
    "adverse" periods out of ordinary daily noise, and the gate reports that the
    tail was observed when the worst day of the window lost 0.198% against a
    squeeze measured at +2.74%.
    """
    if not returns:
        return None
    limit = float(threshold)
    return sum(1 for value in returns if value < limit) / len(returns)


def adverse_episodes(returns, threshold=0.0):
    """How many distinct adverse EPISODES the series contains.

    Consecutive periods below the threshold are ONE episode, not several: the
    March 2020 collapse cost 10.48% on the 12th and 2.21% on the 13th, and
    counting that as two independent observations of the tail would be the same
    overstatement in miniature that counting daily noise as "adverse" was.

    Counted per fold and summed, so an episode straddling a fold boundary counts
    twice. That errs toward saying the tail WAS covered, which is the generous
    direction, and with folds of several months against episodes of days it is a
    rare case rather than a systematic bias.
    """
    if not returns:
        return 0
    limit = float(threshold)
    episodes, inside = 0, False
    for value in returns:
        if value < limit:
            if not inside:
                episodes += 1
            inside = True
        else:
            inside = False
    return episodes


def adverse_mean_bound(returns, *, confidence_level, resamples, block_periods, seed):
    """Lower bound of the mean, by MOVING-BLOCK bootstrap.

    Blocks rather than individual periods because a premium's returns are
    serially dependent -- funding regimes persist for weeks -- and resampling
    single days would shatter that dependence and manufacture a tighter bound
    than the data supports. Blocks wrap around the end of the series so every
    period keeps the same chance of being drawn, which an unwrapped version
    quietly denies to the last few.

    Deterministic given the seed, which the claim seals. Returns None for a
    series too short to form one block, rather than a bound computed from
    something that is not a resample.
    """
    if not returns or len(returns) < block_periods:
        return None
    rng = random.Random(seed)
    count = len(returns)
    blocks_needed = math.ceil(count / block_periods)
    means = []
    for _ in range(resamples):
        sample = []
        for _ in range(blocks_needed):
            start = rng.randrange(count)
            sample.extend(returns[(start + offset) % count] for offset in range(block_periods))
        del sample[count:]
        means.append(math.fsum(sample) / count)
    means.sort()
    index = int((1.0 - float(confidence_level)) * len(means))
    return means[min(index, len(means) - 1)]


def evaluate_fold(claim, cost_contract, *, fold_index, period, positions, gross_returns):
    """One fold's level result: net returns first, then the bound, then whether
    the losses were even observable.

    The fold is INCONCLUSIVE rather than NOT_MET when the position was never held
    or the series is too short to bootstrap. A fold that could not be measured is
    not evidence against the claim, and counting it as one would let a sparse
    position be refuted by its own sparseness.
    """
    verified_level_claim(claim)
    _checked_cost_contract(claim, cost_contract)
    net = net_returns(cost_contract, positions, gross_returns)
    held = [value for value, position in zip(net, positions) if position == 1]

    bound = adverse_mean_bound(
        held, confidence_level=claim["confidence_level"],
        resamples=claim["bootstrap_resamples"],
        block_periods=claim["bootstrap_block_periods"], seed=claim["bootstrap_seed"])
    threshold = float(claim["adverse_period_threshold"])
    adverse = adverse_period_frequency(held, threshold)
    episodes = adverse_episodes(held, threshold)
    mean_net = math.fsum(held) / len(held) if held else None
    drawdown = _risk_analytics_max_drawdown(net)

    # WHICH QUESTION A FOLD ANSWERS, and schema 3 exists because schemas 1 and 2
    # asked the wrong one. They marked a fold MET only when its own 95% bound was
    # above zero -- that is, when the position was STATISTICALLY SIGNIFICANT
    # inside that fold alone. The consistency threshold those claims are judged
    # against was calibrated for SIGN consistency: how often the thing paid. The
    # two are not the same test, and the gap is sample size, not quality.
    #
    # MEASURED on SPY over 2018-2026, seven folds of about fifteen months: 6 of 7
    # folds have a positive MEAN (0.857, clearing the 0.70 threshold) and 3 of 7
    # have a positive BOUND (0.4286, failing it). The full sample's bound IS
    # positive. The aggregate is significant; no fifteen-month slice of it can
    # be, and demanding that of every slice refuses everything rather than
    # discriminating between things.
    #
    # So schema 3 splits the two questions that one number was conflating. The
    # fold answers "did it pay in this period" by its SIGN, and significance is
    # established once, on all the data, where a bound has the power to mean
    # something -- which the admission Sharpe gate already does. Nothing new is
    # invented and no new threshold is introduced; 0.70 simply goes back to
    # measuring what it was set for.
    #
    # The bound is still computed and still recorded on every fold. It stopped
    # deciding the fold; it did not stop being evidence.
    if not held or bound is None:
        result = FOLD_INCONCLUSIVE
    elif (mean_net > 0 if claim.get("schema_version") == "3" else bound > 0):
        result = FOLD_MET
    else:
        result = FOLD_NOT_MET

    return {
        "fold_index": fold_index,
        "period": period,
        "result": result,
        "periods": len(net),
        "periods_held": len(held),
        "mean_net_return": _fixed(mean_net),
        "adverse_mean_bound": _fixed(bound),
        "adverse_period_frequency": _fixed(adverse),
        "adverse_episodes": episodes,
        "max_drawdown": _fixed(drawdown),
        "sharpe_ratio": _fixed(_risk_analytics_sharpe_ratio(held) if held else None),
        "costs": _fold_cost_summary(cost_contract, positions, gross_returns),
    }


def _checked_cost_contract(claim, contract):
    """The contract travels alongside the claim rather than inside it: the claim
    seals only its IDENTITY, so there is never a second copy of a sealed thing
    that could disagree with the first. Passing a different contract than the one
    the claim was sealed with is refused, not silently honoured."""
    verified_cost_contract(contract)
    if contract["contract_id"] != claim["cost_contract_id"]:
        raise ValueError("Cost contract is not the one this level claim was sealed with")
    return contract


def _fold_cost_summary(contract, positions, gross_returns):
    summary = cost_summary(contract, positions, gross_returns)
    return {
        "contract_id": summary["contract_id"],
        "gross_total": _fixed(summary["gross_total"]),
        "net_total": _fixed(summary["net_total"]),
        "cost_total": _fixed(summary["cost_total"]),
        "transitions": summary["transitions"],
    }


def _aggregate_adverse_frequency(folds):
    """Pooled over every held period, not averaged over folds. Averaging would
    let a handful of folds with many losses hide a majority with none, and it is
    the pooled sample that any monitor would actually have to learn from."""
    held = sum(item["periods_held"] for item in folds)
    if not held:
        return None
    losses = sum(float(item["adverse_period_frequency"]) * item["periods_held"]
                 for item in folds if item["adverse_period_frequency"] is not None)
    return losses / held


def level_claim_outcome(claim, folds):
    """The verdict, and the order of the checks is the argument.

    TAIL COVERAGE IS CHECKED BEFORE CONSISTENCY, and that ordering is the whole
    module. Consistency across folds measures how often the premium paid; if the
    tail was never observed, a high consistency means the sample contained no
    information about failure, and reporting NOT_VALIDATED or VALIDATED would both
    be claims the evidence cannot support. Checking consistency first would let
    such a sample pass.

    Schema 1 claims are judged by the frequency rule they were sealed under;
    schema 2 and 3 by tail coverage. Schema 3 additionally marks a fold MET on
    the SIGN of its mean rather than on its own bound -- see evaluate_fold. A
    verdict is never re-adjudicated under a rule that did not exist when it was
    issued, which is why all three rules remain here rather than one.
    """
    verified_level_claim(claim)
    usable = [item for item in folds if item["result"] != FOLD_INCONCLUSIVE]
    if len(usable) < claim["minimum_folds_required"]:
        return OUTCOME_INSUFFICIENT, REASON_FOLDS_BELOW_MINIMUM, None, None

    adverse = _aggregate_adverse_frequency(usable)
    if claim.get("schema_version") == "1":
        # The rule those claims were sealed under. Kept so every verdict already
        # issued reproduces as what it was, never re-adjudicated under a rule that
        # did not exist when it was issued.
        floor = float(claim["minimum_adverse_period_frequency"])
        if adverse is None or adverse < floor:
            return OUTCOME_INSUFFICIENT, REASON_ADVERSE_PERIODS_TOO_RARE, None, _fixed(adverse)
    else:
        episodes = sum(item.get("adverse_episodes", 0) for item in usable)
        if episodes < claim["minimum_adverse_episodes"]:
            return OUTCOME_INSUFFICIENT, REASON_TAIL_NOT_COVERED, None, _fixed(adverse)

    passing = sum(1 for item in usable if item["result"] == FOLD_MET)
    consistency = passing / len(usable)
    if consistency < float(claim["consistency_threshold"]):
        return (OUTCOME_NOT_VALIDATED, REASON_CONSISTENCY_BELOW,
                _fixed(consistency), _fixed(adverse))

    worst = max(float(item["max_drawdown"]) for item in usable)
    if worst > float(claim["maximum_drawdown"]):
        return (OUTCOME_NOT_VALIDATED, REASON_DRAWDOWN_EXCEEDED,
                _fixed(consistency), _fixed(adverse))

    return OUTCOME_VALIDATED, None, _fixed(consistency), _fixed(adverse)


def level_claim_gate_report(claim, folds):
    """Every declared gate's standing, whatever the verdict stopped at.

    THE VERDICT NAMES ONE GATE AND THE RECORD USED TO LOSE THE REST. Gates are
    checked in order and the first refusal ends it, so a gate further down is
    never evaluated -- and the audit of 2026-10-02 found that the 15% drawdown
    limit, the number this project's DESTINATION rests on, had NEVER participated
    in a verdict. It sits last; folds, tail coverage or consistency always fired
    first. Had it been reached it would have refused three of six level claims.

    The consequence was never a wrong verdict. It was a mis-stated one: a record
    saying CONSISTENCY_BELOW_THRESHOLD when it could equally have said "and it
    breaches your risk limit", which for a destination defined by drawdown
    control is the more decision-relevant sentence by a wide margin.

    So every gate reports its own standing independently of the order: whether it
    was REACHED, and whether it WOULD refuse. Ordering still decides the single
    outcome -- re-ordering would change what sealed verdicts recompute to, and
    gate order is a governance semantic -- but the record no longer forgets what
    the gates it never reached would have said.
    """
    verified_level_claim(claim)
    usable = [item for item in folds if item["result"] != FOLD_INCONCLUSIVE]
    outcome, reason, _, _ = level_claim_outcome(claim, folds)

    enough = len(usable) >= claim["minimum_folds_required"]
    adverse = _aggregate_adverse_frequency(usable)
    if claim.get("schema_version") == "1":
        floor = float(claim["minimum_adverse_period_frequency"])
        covered = adverse is not None and adverse >= floor
        coverage_detail = (f"frequency {_fixed(adverse)} against {claim['minimum_adverse_period_frequency']}")
    else:
        episodes = sum(item.get("adverse_episodes", 0) for item in usable)
        covered = episodes >= claim["minimum_adverse_episodes"]
        coverage_detail = f"{episodes} episode(s) against {claim['minimum_adverse_episodes']}"
    consistency = (sum(1 for item in usable if item["result"] == FOLD_MET) / len(usable)
                   if usable else None)
    consistent = consistency is not None and consistency >= float(claim["consistency_threshold"])
    worst = max((float(item["max_drawdown"]) for item in usable), default=None)
    within = worst is not None and worst <= float(claim["maximum_drawdown"])

    checks = [
        ("minimum_folds_required", REASON_FOLDS_BELOW_MINIMUM, enough,
         f"{len(usable)} usable against {claim['minimum_folds_required']}"),
        ("tail coverage",
         REASON_TAIL_NOT_COVERED if claim.get("schema_version") != "1"
         else REASON_ADVERSE_PERIODS_TOO_RARE, covered, coverage_detail),
        ("consistency_threshold", REASON_CONSISTENCY_BELOW, consistent,
         f"{_fixed(consistency)} against {claim['consistency_threshold']}"),
        ("maximum_drawdown", REASON_DRAWDOWN_EXCEEDED, within,
         f"worst fold {_fixed(worst)} against {claim['maximum_drawdown']}"),
    ]
    report, stopped = [], False
    for order, (name, refusal, passes, detail) in enumerate(checks, 1):
        report.append({
            "gate": name, "order": order, "refusal_reason": refusal,
            "reached": not stopped,
            "would_refuse": not passes,
            "decided_the_verdict": (not stopped) and (not passes) and reason == refusal,
            "detail": detail,
        })
        if not passes:
            stopped = True
    return report


def _validation_id(claim_id, folds):
    content = {"schema_version": LEVEL_CLAIM_VALIDATION_SCHEMA_VERSION,
               "claim_id": claim_id, "folds": folds}
    return "LEVEL_CLAIM_VALIDATION|" + digest(encoded(content))


def _validation_id_is_valid(value):
    return (isinstance(value, str)
            and bool(re.fullmatch(r"LEVEL_CLAIM_VALIDATION\|[0-9a-f]{64}", value)))


def _materialization(validated_at, validation_code_revision):
    if (not _explicit_utc(validated_at)
            or not _hypothesis_code_revision_is_valid(validation_code_revision)):
        raise ValueError("Validation time and code revision are required")
    return {"validated_at": validated_at,
            "validation_code_revision": validation_code_revision,
            "pipeline_sha256": digest(_pipeline_source_bytes())}


def _validation_record(claim, folds, outcome, reason, consistency, adverse, materialization,
                       gate_report=None):
    content = {
        "validation_id": _validation_id(claim["claim_id"], folds),
        "schema_version": LEVEL_CLAIM_VALIDATION_SCHEMA_VERSION,
        "claim": claim,
        "folds": folds,
        "outcome": outcome,
        "outcome_reason": reason,
        "consistency_ratio": consistency,
        "adverse_period_frequency": adverse,
        "materialization": materialization,
        "status": LEVEL_CLAIM_VALIDATION_STATUS,
        # Present only on records sealed since the gate audit, so every verdict
        # issued before it keeps reproducing exactly as it was written.
        **({} if gate_report is None else {"gate_report": gate_report}),
    }
    return {**content, "record_id": "LEVEL_CLAIM_VALIDATION_RECORD|" + digest(encoded(content))}


def _validation_record_is_valid(record):
    fields = {"validation_id", "schema_version", "claim", "folds", "outcome", "outcome_reason",
              "consistency_ratio", "adverse_period_frequency", "materialization", "status",
              "record_id"}
    if not isinstance(record, dict) or set(record) not in (fields, fields | {"gate_report"}):
        return False
    claim, folds = record.get("claim"), record.get("folds")
    if (not _validation_id_is_valid(record.get("validation_id"))
            or record.get("schema_version") != LEVEL_CLAIM_VALIDATION_SCHEMA_VERSION
            or claim.get("schema_version") not in LEVEL_CLAIM_SCHEMA_VERSIONS
            or not isinstance(claim, dict) or "claim_id" not in claim
            or not isinstance(folds, list) or not folds
            or record["validation_id"] != _validation_id(claim["claim_id"], folds)
            or record.get("outcome") not in STATISTICAL_VALIDATION_OUTCOMES
            or record.get("status") != LEVEL_CLAIM_VALIDATION_STATUS):
        return False
    try:
        verified_level_claim(claim)
    except ValueError:
        return False
    # Re-derive the verdict from the folds. A record whose outcome was edited no
    # longer reproduces, which is the point of recomputing rather than trusting.
    outcome, reason, consistency, adverse = level_claim_outcome(claim, folds)
    expected = _validation_record(
        claim, folds, outcome, reason, consistency, adverse, record["materialization"],
        level_claim_gate_report(claim, folds) if "gate_report" in record else None)
    return record == expected


def _registry_is_valid(registry):
    if (not isinstance(registry, dict)
            or set(registry) != {"schema_version", "validations"}
            or registry.get("schema_version") != LEVEL_CLAIM_VALIDATION_REGISTRY_SCHEMA_VERSION
            or not isinstance(registry.get("validations"), list)
            or not all(_validation_record_is_valid(item) for item in registry["validations"])):
        return False
    identifiers = [item["validation_id"] for item in registry["validations"]]
    return len(identifiers) == len(set(identifiers))


def _load_registry(registry_path):
    try:
        registry = json.loads(Path(registry_path).read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Persisted Level Claim Validation registry cannot be read") from error
    if not _registry_is_valid(registry):
        raise ValueError("Persisted Level Claim Validation registry is invalid")
    return registry


def constitute_level_claim_validation(registry_path, *, claim, folds, validated_at,
                                      validation_code_revision):
    """Seal one verdict on one level claim, with every fold it was computed from."""
    verified_level_claim(claim)
    if not isinstance(folds, list) or not folds:
        raise ValueError("A level claim validation needs folds")
    outcome, reason, consistency, adverse = level_claim_outcome(claim, folds)
    record = _validation_record(
        claim, folds, outcome, reason, consistency, adverse,
        _materialization(validated_at, validation_code_revision),
        level_claim_gate_report(claim, folds))
    if not _validation_record_is_valid(record):
        raise ValueError("Level Claim Validation record is invalid")

    path = Path(registry_path)
    registry = (_load_registry(path) if path.exists() else {
        "schema_version": LEVEL_CLAIM_VALIDATION_REGISTRY_SCHEMA_VERSION, "validations": []})
    matches = [item for item in registry["validations"]
               if item["validation_id"] == record["validation_id"]]
    if matches:
        # Same claim, same folds -- same verdict. Only the materialization can
        # differ, and none of it changes what was measured.
        return matches[0]
    registry["validations"].append(record)
    if not _registry_is_valid(registry):
        raise ValueError("Constructed Level Claim Validation registry is invalid")
    _atomic_write(path, encoded(registry))
    return record


def load_level_claim_validation(registry_path, validation_id):
    if not _validation_id_is_valid(validation_id):
        raise ValueError("A valid Level Claim Validation identity is required")
    registry = _load_registry(registry_path)
    matches = [item for item in registry["validations"]
               if item["validation_id"] == validation_id]
    if len(matches) != 1:
        raise ValueError("Level Claim Validation is not registered unambiguously")
    return matches[0]


def verified_level_claim_validation(registry_path, validation_id):
    """Reload and recompute the verdict from the sealed folds -- never trust the
    stored outcome."""
    record = load_level_claim_validation(registry_path, validation_id)
    if not _validation_record_is_valid(record):
        raise ValueError("Level Claim Validation does not reproduce")
    return record
