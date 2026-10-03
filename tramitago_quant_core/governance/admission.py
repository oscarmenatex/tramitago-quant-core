"""ADMISIÓN — the state between a validated Hypothesis and an operating one.

THE GAP THIS CLOSES. DOC-011 §5 was amended on 2026-09-30: the criteria for
crossing Fase 0 -> Fase 1 are no longer the old progression thresholds but the
seven gates of §9, and the lifecycle that governs them runs
Validation -> VALIDADA -> ADMITIDA -> Operation. The platform could produce
VALIDATED, NOT_VALIDATED and INSUFFICIENT_EVIDENCE and could not produce
ADMITIDA or ADMISIÓN DENEGADA at all, so the deliverable Fase 1 actually
requires did not exist.

WHY THE DENIAL MATTERS AS MUCH AS THE ADMISSION, in the normative document's own
words: "sin este estado el sistema no puede registrar POR QUÉ algo que pasó
nunca llegó a operar, y esa es información que se pierde para siempre."
spy-mom10-2024 was VALIDATED and would have been denied on R2 and R3. That is
not the same as failing validation, and not the same as degrading while
operating, and nothing here could say so.

EVERY GATE IS EVALUATED, ALWAYS -- never short-circuited at the first failure.
This is the opposite of how the level claim judge orders its checks, and the
difference is deliberate: a verdict that stops at the first refusal hides what
the others would have said, and the gate audit of 2026-10-02 measured what that
costs -- the 15% drawdown limit had never once been evaluated because something
always fired before it. A denial exists to record reasons, so it records all of
them.

EVERY THRESHOLD APPLIES TO THE ADVERSE 95% BOUND, NEVER A POINT ESTIMATE (§8.1).
That is enforced structurally rather than trusted: a figure arrives labelled with
whether it is an adverse bound, and a gate refuses one that is not. The rule
exists because spy-mom10-2024 cleared every threshold then in force on point
estimates, with four hypotheses running at once and a 37% chance that at least
one reached 5/6 folds by chance alone.

A GATE WITH NO EVIDENCE IS NOT A GATE THAT PASSED. Missing evidence yields
NOT_EVALUABLE, which denies admission exactly as a failure does but is recorded
distinctly -- "we measured this and it falls short" and "nobody has measured
this" are different facts about a project, and collapsing them would hide which
one is true.
"""

import json
import math
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from tramitago_quant_core.shared.util import (
    digest, encoded, _atomic_write, _explicit_utc, _hypothesis_text_is_valid,
    _hypothesis_code_revision_is_valid, _pipeline_source_bytes,
)

ADMISSION_SCHEMA_VERSION = "1"
ADMISSION_REGISTRY_SCHEMA_VERSION = "1"
ADMISSION_STATUS = "ISSUED"

ADMITTED = "ADMITIDA"
ADMISSION_DENIED = "ADMISION_DENEGADA"

GATE_PASSED = "PASSED"
GATE_FAILED = "FAILED"
GATE_NOT_EVALUABLE = "NOT_EVALUABLE"

# §8.8, declared a CLOSED SET on 2026-09-30: no number is left to declare.
GATE_SHARPE = "maximando: net Sharpe >= 0.50 AND its lower 95% bound > 0"
GATE_DRAWDOWN = "R1 drawdown: worst fold, upper 95% bound <= 0.15"
GATE_SURVIVAL = "R2 survival: P1 (>=2 out-of-sample periods) or P2 (declared mechanism)"
GATE_MONITORABILITY = "R3 monitorability: latency < drawdown / daily loss if dead"
GATE_CAPACITY = "R4 capacity: declared ceiling C >= the current tranche"
GATE_DECISION_COST = "R5 decision cost: < 25% of expected annual net return"

SURVIVAL_P1 = "P1_OUT_OF_SAMPLE"
SURVIVAL_P2 = "P2_DECLARED_MECHANISM"

# §8.2 / §8.3, confirmed by the Director 2026-09-30.
MINIMUM_NET_SHARPE = "0.50"
MAXIMUM_DRAWDOWN = "0.15"
MINIMUM_OUT_OF_SAMPLE_PERIODS = 2
MAXIMUM_DECISION_COST_FRACTION = "0.25"


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


def adverse_bound(value, *, is_adverse_bound, source, point_estimate=None):
    """One figure, carrying whether it is an adverse 95% bound and where it came from.

    The label is required rather than assumed because §8.1 is the project's most
    expensive lesson: a point estimate passed every threshold then in force and
    was refuted out of sample. A gate here refuses a figure that is not an
    adverse bound, so the rule cannot be forgotten by a caller in a hurry.
    """
    _decimal(value, "Figure")
    if not isinstance(is_adverse_bound, bool):
        raise ValueError("Whether a figure is an adverse bound must be stated explicitly")
    if not _hypothesis_text_is_valid(source):
        raise ValueError("A figure must say where it came from")
    figure = {"value": value, "is_adverse_bound": is_adverse_bound, "source": source.strip()}
    # ADDITIVE, and absent when not supplied, so every figure already sealed
    # without one reproduces byte for byte. §8.2 needs BOTH numbers after the
    # 2026-10-02 correction -- see _sharpe_gate -- and the gate reports
    # NOT_EVALUABLE rather than FAILED when the point estimate was never
    # recorded, because nobody measuring it then was asked for it.
    if point_estimate is not None:
        _decimal(point_estimate, "Point estimate")
        figure["point_estimate"] = point_estimate
    return figure


def _gate(name, state, detail):
    return {"gate": name, "state": state, "detail": detail}


def _requires_adverse_bound(name, figure, comparison, threshold):
    """A gate over a numeric threshold. NOT_EVALUABLE when the figure is absent,
    FAILED when it is a point estimate, because an unlabelled number is not
    weaker evidence -- it is the specific kind this rule exists to refuse."""
    if figure is None:
        return _gate(name, GATE_NOT_EVALUABLE, "no figure has been measured")
    if not figure.get("is_adverse_bound"):
        return _gate(name, GATE_FAILED,
                     f"{figure['value']} is a point estimate, and §8.1 requires the adverse "
                     f"95% bound; spy-mom10-2024 cleared every threshold on point estimates "
                     f"and was refuted out of sample")
    value, bar = _decimal(figure["value"], name), _decimal(threshold, name)
    passes = value >= bar if comparison == "GE" else value <= bar
    sign = ">=" if comparison == "GE" else "<="
    return _gate(name, GATE_PASSED if passes else GATE_FAILED,
                 f"{figure['value']} {sign} {threshold} is {str(passes).lower()} "
                 f"(source: {figure['source']})")


def _sharpe_gate(figure):
    """Section 8.2, CORRECTED 2026-10-02 on arithmetic grounds sealed beforehand.

    THE DEFECT. The threshold 0.50 was set for a Sharpe RATIO and was being
    applied to its LOWER 95% BOUND, which is a different and much larger demand.
    MEASURED on SPY over 8.72 years: point +0.8125, bound +0.2695 -- the bound
    sits 0.543 BELOW the point estimate, so "bound >= 0.50" behaved like
    "Sharpe >= 1.04 over a decade". The gap is a function of sample size, so no
    replacement constant can fix it; at 8.72 years it is 0.543 and at three
    years it is far wider. The equity risk premium, the best documented premium
    in finance, failed a gate that reads "net Sharpe >= 0.50".

    THE CORRECTION SEPARATES THE TWO QUESTIONS THE ONE NUMBER CONFLATED, and
    introduces no new constant: 0.50 goes back to measuring EFFECT SIZE on the
    point estimate, where it was calibrated, and the bound keeps doing what
    section 8.1 requires of it -- establishing the effect is real at all -- by
    having to clear ZERO.

    THE BOUND REQUIREMENT IS NOT WEAKENED AWAY, and that matters, because it
    does real work. Measured across this project's four candidates, dropping it
    entirely would readmit spy-mom10-2024 on a point Sharpe of 1.52 -- a
    Hypothesis whose sign broke in BOTH out-of-sample periods. Its bound is
    -0.1798, so this rule still refuses it. What changed is the calibration of
    the threshold, not the existence of the evidentiary test.
    """
    if figure is None:
        return _gate(GATE_SHARPE, GATE_NOT_EVALUABLE, "no figure has been measured")
    if not figure.get("is_adverse_bound"):
        return _gate(GATE_SHARPE, GATE_FAILED,
                     f"{figure['value']} is a point estimate, and §8.1 requires the adverse "
                     f"95% bound alongside it")
    if figure.get("point_estimate") is None:
        return _gate(GATE_SHARPE, GATE_NOT_EVALUABLE,
                     "the bound is recorded but the point estimate is not, and §8.2 now "
                     "needs both; a bound alone cannot say whether the effect is large")
    bound = _decimal(figure["value"], GATE_SHARPE)
    point = _decimal(figure["point_estimate"], GATE_SHARPE)
    large = point >= _decimal(MINIMUM_NET_SHARPE, GATE_SHARPE)
    real = bound > 0
    return _gate(GATE_SHARPE, GATE_PASSED if (large and real) else GATE_FAILED,
                 f"point {figure['point_estimate']} >= {MINIMUM_NET_SHARPE} is "
                 f"{str(large).lower()}, and bound {figure['value']} > 0 is "
                 f"{str(real).lower()} (source: {figure['source']})")


def _survival_gate(evidence):
    """R2. P1 needs at least two independent out-of-sample periods with the sign
    holding in ALL of them -- one contrary sign refutes and no further period is
    tried. P2 needs three things in writing: who is on the other side, why they
    accept losing, and what would have to change."""
    survival = evidence.get("survival")
    if not isinstance(survival, dict):
        return _gate(GATE_SURVIVAL, GATE_NOT_EVALUABLE, "no survival evidence declared")
    form = survival.get("form")
    if form == SURVIVAL_P2:
        missing = [key for key in ("counterparty", "why_they_accept_losing", "what_would_end_it")
                   if not _hypothesis_text_is_valid(survival.get(key))]
        if missing:
            return _gate(GATE_SURVIVAL, GATE_NOT_EVALUABLE,
                         f"P2 declared but missing in writing: {', '.join(missing)}")
        return _gate(GATE_SURVIVAL, GATE_PASSED, "P2: mechanism declared in writing")
    if form == SURVIVAL_P1:
        periods = survival.get("out_of_sample_periods")
        if not isinstance(periods, list) or not periods:
            return _gate(GATE_SURVIVAL, GATE_NOT_EVALUABLE, "P1 declared with no periods")
        if any(not isinstance(item, dict) or "sign_held" not in item
               or not item.get("criterion_declared_before") for item in periods):
            return _gate(GATE_SURVIVAL, GATE_NOT_EVALUABLE,
                         "every P1 period must state its sign and that its criterion was "
                         "declared before it ran")
        broken = [item for item in periods if item["sign_held"] is not True]
        if broken:
            return _gate(GATE_SURVIVAL, GATE_FAILED,
                         f"the sign broke in {len(broken)} of {len(periods)} periods; one "
                         f"contrary sign refutes")
        if len(periods) < MINIMUM_OUT_OF_SAMPLE_PERIODS:
            return _gate(GATE_SURVIVAL, GATE_NOT_EVALUABLE,
                         f"{len(periods)} out-of-sample period(s), "
                         f"{MINIMUM_OUT_OF_SAMPLE_PERIODS} required")
        return _gate(GATE_SURVIVAL, GATE_PASSED,
                     f"P1: the sign held in all {len(periods)} periods")
    return _gate(GATE_SURVIVAL, GATE_NOT_EVALUABLE,
                 f"survival form must be {SURVIVAL_P1} or {SURVIVAL_P2}")


def _monitorability_gate(evidence):
    """R3, which ties monitoring to R1 rather than leaving it a good intention:
    if the death is not detected before it costs the drawdown limit, nobody is
    watching, they are looking. Surveillance by P&L alone NEVER qualifies, in any
    case, whatever its latency."""
    monitor = evidence.get("monitor")
    if not isinstance(monitor, dict):
        return _gate(GATE_MONITORABILITY, GATE_NOT_EVALUABLE, "no monitor declared")
    missing = [key for key in ("variable", "frequency_seconds", "degradation_threshold",
                               "action", "detection_latency_days",
                               "expected_daily_loss_if_dead")
               if monitor.get(key) in (None, "")]
    if missing:
        return _gate(GATE_MONITORABILITY, GATE_NOT_EVALUABLE,
                     f"§3 requires all four elements plus the latency arithmetic; "
                     f"missing: {', '.join(missing)}")
    if monitor.get("is_pnl_only") is True:
        return _gate(GATE_MONITORABILITY, GATE_FAILED,
                     "surveillance by P&L alone never qualifies, whatever its latency: a "
                     "P&L move cannot distinguish variance from the mechanism ending")
    if monitor.get("observes_adverse_state_representatively") is not True:
        return _gate(GATE_MONITORABILITY, GATE_FAILED,
                     "DOC-011 §12: a monitor must observe the state it is meant to detect "
                     "often enough to act on it")
    loss = _decimal(monitor["expected_daily_loss_if_dead"], "Expected daily loss if dead")
    if loss <= 0:
        return _gate(GATE_MONITORABILITY, GATE_NOT_EVALUABLE,
                     "the expected daily loss if the edge is dead must be positive, or the "
                     "latency arithmetic has no meaning")
    latency = _decimal(monitor["detection_latency_days"], "Detection latency")
    budget = _decimal(MAXIMUM_DRAWDOWN, "Drawdown limit") / loss
    passes = latency < budget
    return _gate(GATE_MONITORABILITY, GATE_PASSED if passes else GATE_FAILED,
                 f"latency {latency} days against a budget of {budget:.2f} days "
                 f"({MAXIMUM_DRAWDOWN} / {monitor['expected_daily_loss_if_dead']})")


def _capacity_gate(evidence):
    """R4. The question is inverted on purpose: not "does it work at target X",
    which has no X because the capital is open-ended, but "above what capital
    does it stop working". A ceiling declared BEFORE admitting, and the current
    tranche must fit under it."""
    capacity = evidence.get("capacity")
    if not isinstance(capacity, dict):
        return _gate(GATE_CAPACITY, GATE_NOT_EVALUABLE, "no capacity ceiling declared")
    if capacity.get("ceiling") in (None, ""):
        return _gate(GATE_CAPACITY, GATE_NOT_EVALUABLE,
                     "the ceiling C must be declared before admitting, not discovered after")
    if capacity.get("current_tranche") in (None, ""):
        return _gate(GATE_CAPACITY, GATE_NOT_EVALUABLE, "no current tranche declared")
    ceiling = _decimal(capacity["ceiling"], "Capacity ceiling")
    tranche = _decimal(capacity["current_tranche"], "Current tranche")
    passes = ceiling >= tranche
    return _gate(GATE_CAPACITY, GATE_PASSED if passes else GATE_FAILED,
                 f"ceiling {capacity['ceiling']} against a tranche of "
                 f"{capacity['current_tranche']}")


def _decision_cost_gate(evidence):
    """R5. Above a quarter of the return, the opportunity finances its own
    supervision instead of the operator. And surveillance must be automatable:
    daily human judgement fails this for a one-person operation whatever the
    number says."""
    cost = evidence.get("decision_cost")
    if not isinstance(cost, dict):
        return _gate(GATE_DECISION_COST, GATE_NOT_EVALUABLE, "no decision cost declared")
    missing = [key for key in ("build_cost", "minimum_declared_life_years",
                               "recurring_cost_per_year", "expected_annual_net_return")
               if cost.get(key) in (None, "")]
    if missing:
        return _gate(GATE_DECISION_COST, GATE_NOT_EVALUABLE,
                     f"missing: {', '.join(missing)}")
    if cost.get("surveillance_is_automatable") is not True:
        return _gate(GATE_DECISION_COST, GATE_FAILED,
                     "surveillance requiring daily human judgement fails R5 in a one-person "
                     "operation, whatever the number")
    life = _decimal(cost["minimum_declared_life_years"], "Minimum declared life")
    expected = _decimal(cost["expected_annual_net_return"], "Expected annual net return")
    if life <= 0 or expected <= 0:
        return _gate(GATE_DECISION_COST, GATE_NOT_EVALUABLE,
                     "the declared life and the expected annual net return must be positive")
    annual = (_decimal(cost["build_cost"], "Build cost") / life
              + _decimal(cost["recurring_cost_per_year"], "Recurring cost"))
    fraction = annual / expected
    bar = _decimal(MAXIMUM_DECISION_COST_FRACTION, "Decision cost fraction")
    return _gate(GATE_DECISION_COST, GATE_PASSED if fraction < bar else GATE_FAILED,
                 f"{fraction:.4f} of the expected annual net return, against {bar}")


def evaluate_admission_gates(evidence):
    """Every gate of §8.8's closed set, all of them, never short-circuited.

    A denial exists to record reasons, so it records all of them. Stopping at the
    first refusal is what left the drawdown limit unevaluated for six level
    claims, and that mistake is not repeated here.
    """
    if not isinstance(evidence, dict):
        raise ValueError("Admission evidence must be a mapping")
    return [
        _sharpe_gate(evidence.get("net_sharpe")),
        _requires_adverse_bound(GATE_DRAWDOWN, evidence.get("worst_fold_drawdown"), "LE",
                                MAXIMUM_DRAWDOWN),
        _survival_gate(evidence),
        _monitorability_gate(evidence),
        _capacity_gate(evidence),
        _decision_cost_gate(evidence),
    ]


def admission_outcome(gates):
    """ADMITIDA only if every gate passed. Anything else is ADMISIÓN DENEGADA,
    and the record keeps which gates failed apart from which could not be
    evaluated -- "we measured this and it falls short" and "nobody has measured
    this" are different facts about a project."""
    failed = [item["gate"] for item in gates if item["state"] == GATE_FAILED]
    unevaluable = [item["gate"] for item in gates if item["state"] == GATE_NOT_EVALUABLE]
    return ((ADMITTED if not failed and not unevaluable else ADMISSION_DENIED),
            failed, unevaluable)


def _materialization(decided_at, code_revision):
    if not _explicit_utc(decided_at) or not _hypothesis_code_revision_is_valid(code_revision):
        raise ValueError("Admission time and code revision are required")
    return {"decided_at": decided_at, "code_revision": code_revision,
            "pipeline_sha256": digest(_pipeline_source_bytes())}


def _admission_record(hypothesis_id, validation_id, evidence, gates, outcome,
                      failed, unevaluable, materialization):
    content = {
        "admission_id": "ADMISSION|" + digest(encoded(
            {"schema_version": ADMISSION_SCHEMA_VERSION, "hypothesis_id": hypothesis_id,
             "validation_id": validation_id, "evidence": evidence})),
        "schema_version": ADMISSION_SCHEMA_VERSION,
        "hypothesis_id": hypothesis_id,
        "validation_id": validation_id,
        "evidence": evidence,
        "gates": gates,
        "outcome": outcome,
        "gates_failed": failed,
        "gates_not_evaluable": unevaluable,
        "materialization": materialization,
        "status": ADMISSION_STATUS,
    }
    return {**content, "record_id": "ADMISSION_RECORD|" + digest(encoded(content))}


def _admission_record_is_valid(record):
    fields = {"admission_id", "schema_version", "hypothesis_id", "validation_id", "evidence",
              "gates", "outcome", "gates_failed", "gates_not_evaluable", "materialization",
              "status", "record_id"}
    if not isinstance(record, dict) or set(record) != fields:
        return False
    if (not re.fullmatch(r"ADMISSION\|[0-9a-f]{64}", record.get("admission_id", ""))
            or record.get("schema_version") != ADMISSION_SCHEMA_VERSION
            or record.get("outcome") not in (ADMITTED, ADMISSION_DENIED)
            or record.get("status") != ADMISSION_STATUS):
        return False
    # Re-derive the verdict from the evidence rather than trusting what is stored.
    gates = evaluate_admission_gates(record["evidence"])
    outcome, failed, unevaluable = admission_outcome(gates)
    expected = _admission_record(
        record["hypothesis_id"], record["validation_id"], record["evidence"], gates,
        outcome, failed, unevaluable, record["materialization"])
    return record == expected


def _registry_is_valid(registry):
    if (not isinstance(registry, dict) or set(registry) != {"schema_version", "admissions"}
            or registry.get("schema_version") != ADMISSION_REGISTRY_SCHEMA_VERSION
            or not isinstance(registry.get("admissions"), list)
            or not all(_admission_record_is_valid(item) for item in registry["admissions"])):
        return False
    identifiers = [item["admission_id"] for item in registry["admissions"]]
    return len(identifiers) == len(set(identifiers))


def _load_registry(registry_path):
    try:
        registry = json.loads(Path(registry_path).read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Persisted Admission registry cannot be read") from error
    if not _registry_is_valid(registry):
        raise ValueError("Persisted Admission registry is invalid")
    return registry


def constitute_admission(registry_path, *, hypothesis_id, validation_id, validation_outcome,
                         evidence, decided_at, code_revision):
    """Seal one admission decision on one VALIDATED Hypothesis.

    ONLY A VALIDATED HYPOTHESIS CAN BE ADMITTED OR DENIED. Something the evidence
    did not support is REJECTED, which is a different state reached earlier in
    the lifecycle; running it through the admission gates would imply the
    question of operating it had ever arisen.
    """
    if validation_outcome != "VALIDATED":
        raise ValueError(
            f"Only a VALIDATED Hypothesis reaches admission; this one is {validation_outcome}. "
            "Rejection and denial are different states and the lifecycle keeps them apart.")
    if not _hypothesis_text_is_valid(hypothesis_id) \
            or not _hypothesis_text_is_valid(validation_id):
        raise ValueError("Admission must name the Hypothesis and the Validation it rests on")

    gates = evaluate_admission_gates(evidence)
    outcome, failed, unevaluable = admission_outcome(gates)
    record = _admission_record(hypothesis_id, validation_id, evidence, gates, outcome,
                               failed, unevaluable,
                               _materialization(decided_at, code_revision))
    if not _admission_record_is_valid(record):
        raise ValueError("Admission record is invalid")

    path = Path(registry_path)
    registry = (_load_registry(path) if path.exists()
                else {"schema_version": ADMISSION_REGISTRY_SCHEMA_VERSION, "admissions": []})
    matches = [item for item in registry["admissions"]
               if item["admission_id"] == record["admission_id"]]
    if matches:
        # Same Hypothesis, same evidence -- same decision. Only the moment of
        # deciding can differ, and that changes nothing about what was decided.
        return matches[0]
    registry["admissions"].append(record)
    if not _registry_is_valid(registry):
        raise ValueError("Constructed Admission registry is invalid")
    _atomic_write(path, encoded(registry))
    return record


def load_admission(registry_path, admission_id):
    registry = _load_registry(registry_path)
    matches = [item for item in registry["admissions"] if item["admission_id"] == admission_id]
    if len(matches) != 1:
        raise ValueError("Admission is not registered unambiguously")
    return matches[0]


def query_admissions(registry_path, outcome=None):
    """Every admission decision, optionally by outcome. The everyday question a
    denial exists to answer: what passed validation and still never operated, and
    on which gate."""
    path = Path(registry_path)
    if not path.exists():
        return []
    return [item for item in _load_registry(path)["admissions"]
            if outcome is None or item["outcome"] == outcome]
