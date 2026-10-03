"""Where every Hypothesis stands, DERIVED from what is sealed and never stored.

WHY NOTHING IS STORED. A status field is a claim anyone can change without evidence,
and it drifts: the platform has already had records that said one thing while the
evidence under them said another. Here the state of a Hypothesis is a QUERY over the
admission, hypothesis, family and verdict registries, so it can only be what the
evidence supports and it cannot be out of date.

READ ONLY. This module writes nothing and changes no sealed record.

STATES THAT CAN BE DERIVED TODAY
  DENEGADO     the latest admission is ADMISION_DENEGADA. Carries which gates failed,
               which could not be evaluated, and the evidence that would reopen it.
  MEDIDO       admitted by the gates but not cleared by its family's joint verdict.
  AUTORIZADO   admitted AND cleared: the only state from which an operation may be
               proposed. Nothing holds it at the time of writing.

STATES THAT CANNOT BE DERIVED YET, and are reported as such rather than guessed:
  EN_OPERACION, SUSPENDIDO, RETIRADO. No record type says a tranche was assigned, a
  suspension confirmed or a position retired, and inventing one here would be the
  stored status this module exists to avoid.

A Hypothesis with no admission record is counted, not listed: it is declared, and
whether it was ever measured is another question.
"""

import calendar
import json
from datetime import date, datetime
from pathlib import Path

from tramitago_quant_core.governance.admission import ADMITTED
from tramitago_quant_core.research.mechanism_family import family_clearance

DENEGADO, MEDIDO, AUTORIZADO = "DENEGADO", "MEDIDO", "AUTORIZADO"
NOT_DERIVABLE = ("EN_OPERACION", "SUSPENDIDO", "RETIRADO")

DEFAULT_REMEASURE_MONTHS = 12

# What would reopen a denial, by the gate that refused. Written once so a denied
# Hypothesis never reads as a closed file: without a condition for reopening it,
# DENEGADO is just an archive.
REOPENS = (
    ("maximando", "a longer window or a new, pre-declared measurement that clears the "
                  "Sharpe and its lower bound; the same data cannot reopen it"),
    ("R1 drawdown", "a smaller derived weight or a lower upper bound of drawdown on new data"),
    ("R2 survival", "a written mechanism: who pays, why they keep paying, what ends it"),
    ("R3 monitorability", "a monitor whose empirical link is validated in every member of "
                          "its family, declared before it is measured"),
    ("R4 capacity", "a declared capacity at or above the tranche"),
    ("R5 decision cost", "a decision cost below a quarter of the expected net return"),
)


def reopening_condition(gate_name):
    for prefix, condition in REOPENS:
        if gate_name.startswith(prefix):
            return condition
    return "evidence that the gate itself can evaluate"


def load_config(path):
    """The re-measurement cadence, read from a file the Director can edit."""
    path = Path(path)
    if not path.exists():
        return {"remeasure_every_months": DEFAULT_REMEASURE_MONTHS, "source": "default"}
    config = json.loads(path.read_text(encoding="utf-8"))
    months = config.get("remeasure_every_months")
    if not isinstance(months, int) or isinstance(months, bool) or months < 1:
        raise ValueError("remeasure_every_months must be a positive whole number of months")
    return {"remeasure_every_months": months, "source": str(path)}


def add_months(day, months):
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _registry(path, key):
    path = Path(path)
    return json.loads(path.read_bytes())[key] if path.exists() else []


def _labels(hypotheses_path):
    """A readable name for each Hypothesis: the metric it was declared to move."""
    labels = {}
    for item in _registry(hypotheses_path, "hypotheses"):
        previous = labels.get(item["hypothesis_id"])
        if previous is None or item["version"] >= previous[0]:
            labels[item["hypothesis_id"]] = (item["version"], item["target_metric"])
    return {key: value[1] for key, value in labels.items()}


def _decided(admission):
    return datetime.fromisoformat(
        admission["materialization"]["decided_at"].replace("Z", "+00:00"))


def derive_status(*, admissions, hypotheses, families, verdicts, as_of, remeasure_months):
    """Every Hypothesis with an admission record, with its derived state.

    `as_of` is a date passed in, never read from the clock, so the same registries give
    the same report and it can be tested.
    """
    latest = {}
    for admission in _registry(admissions, "admissions"):
        key = admission["hypothesis_id"]
        if key not in latest or _decided(admission) > _decided(latest[key]):
            latest[key] = admission
    labels = _labels(hypotheses)
    declared = {item["hypothesis_id"] for item in _registry(hypotheses, "hypotheses")}

    members = []
    for hypothesis_id, admission in sorted(latest.items(),
                                           key=lambda pair: _decided(pair[1])):
        failed, unevaluable = admission["gates_failed"], admission["gates_not_evaluable"]
        cleared, why = family_clearance(hypothesis_id, families, verdicts)
        if admission["outcome"] == ADMITTED and cleared:
            state = AUTORIZADO
        elif admission["outcome"] == ADMITTED:
            state = MEDIDO
        else:
            state = DENEGADO
        decided = _decided(admission).date()
        due = add_months(decided, remeasure_months)
        members.append({
            "hypothesis_id": hypothesis_id,
            "label": labels.get(hypothesis_id, hypothesis_id),
            "state": state,
            "decided_on": decided.isoformat(),
            "gates_failed": failed,
            "gates_not_evaluable": unevaluable,
            "reopens_with": {gate: reopening_condition(gate)
                             for gate in failed + unevaluable},
            "family_clearance": None if why.startswith("not a member") else
                                {"cleared": cleared, "why": why},
            "remeasure_due": due.isoformat(),
            "remeasure_overdue": due < as_of,
        })

    counts = {state: sum(1 for m in members if m["state"] == state)
              for state in (AUTORIZADO, MEDIDO, DENEGADO)}
    return {
        "as_of": as_of.isoformat(),
        "remeasure_every_months": remeasure_months,
        "members": members,
        "counts": counts,
        "declared_without_admission": len(declared - set(latest)),
        "not_derivable": {state: "no record type exists for it" for state in NOT_DERIVABLE},
    }


def render(report):
    lines = ["=" * 78,
             f"LIFECYCLE STATUS as of {report['as_of']}   (derived from sealed records; "
             f"re-measure every {report['remeasure_every_months']} months)",
             "=" * 78]
    counts = report["counts"]
    lines.append(f"  AUTORIZADO {counts[AUTORIZADO]}   MEDIDO {counts[MEDIDO]}   "
                 f"DENEGADO {counts[DENEGADO]}   "
                 f"({report['declared_without_admission']} more declared, never admitted)")
    for member in report["members"]:
        lines.append("")
        lines.append(f"  {member['state']:10s} {member['label'][:62]}")
        lines.append(f"             decided {member['decided_on']}   re-measure due "
                     f"{member['remeasure_due']}"
                     + ("   OVERDUE" if member["remeasure_overdue"] else ""))
        for gate in member["gates_failed"]:
            lines.append(f"             FAILED {gate[:50]}")
        for gate in member["gates_not_evaluable"]:
            lines.append(f"             NOT EVALUABLE {gate[:44]}")
        for gate, condition in member["reopens_with"].items():
            lines.append(f"               {gate.split(':')[0][:18]} reopens with: "
                         f"{condition[:70]}")
        if member["family_clearance"]:
            clearance = member["family_clearance"]
            lines.append(f"             family: {'cleared' if clearance['cleared'] else 'NOT cleared'}"
                         f" -- {clearance['why'][:80]}")
    lines += ["", "  Not derivable, because no record says it: "
              + ", ".join(report["not_derivable"]), "=" * 78]
    return "\n".join(lines)
