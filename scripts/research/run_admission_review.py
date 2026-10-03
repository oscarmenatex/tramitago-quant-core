"""Put every VALIDATED Hypothesis through the admission gates, and record why.

DOC-011 §5 was amended 2026-09-30: crossing Fase 0 -> Fase 1 runs through the
seven gates of §9, and the lifecycle is Validation -> VALIDADA -> ADMITIDA ->
Operation. Three Hypotheses have validated. None operates. Until now the project
could state that only as an absence; this states it as a sealed decision naming
the gate.

WHAT THIS IS NOT. It is not an attempt to get something admitted, and it will
almost certainly admit nothing -- the carry exit rule governs a position already
refuted on drawdown, spy-mom10-2024 was refuted out of sample within days, and
the third used a consistency threshold of 0.60 rather than 0.70. The deliverable
is the RECORD, which the normative document asks for in as many words: "sin este
estado el sistema no puede registrar POR QUÉ algo que pasó nunca llegó a operar,
y esa es información que se pierde para siempre."

EVIDENCE IS ASSEMBLED FROM SEALED RECORDS WHERE IT EXISTS AND LEFT ABSENT WHERE
IT DOES NOT. Absent evidence yields NOT_EVALUABLE, never a quiet pass, and the
resulting map of what is missing is the second thing this produces: most of
these gates ask for quantities nobody has ever measured.

    python3.11 -B scripts/research/run_admission_review.py
"""

import glob
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pipeline as p
from tramitago_quant_core.governance.evidence_host import require_evidence_host
from tramitago_quant_core.governance.admission import (
    adverse_bound, constitute_admission, query_admissions,
    ADMITTED, ADMISSION_DENIED, GATE_FAILED, GATE_NOT_EVALUABLE, SURVIVAL_P1,
)

ARTIFACTS = REPO / "artifacts" / "research"
REGISTRY = ARTIFACTS / "admissions.json"

# What each validated Hypothesis has by way of out-of-sample evidence, read from
# the project's own record. Declared here rather than inferred, because "did this
# period participate in the discovery" is a fact about history, not about a file.
SURVIVAL_BY_SLUG = {
    "spy-mom10-2024": {
        "form": SURVIVAL_P1,
        "out_of_sample_periods": [
            {"period": "2022", "sign_held": False, "criterion_declared_before": True},
            {"period": "2023", "sign_held": False, "criterion_declared_before": True},
        ],
    },
    "carry-exit-bitmex-holdout": {
        "form": SURVIVAL_P1,
        "out_of_sample_periods": [
            {"period": "2021-07..2024-01", "sign_held": True,
             "criterion_declared_before": True},
        ],
    },
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _code_revision():
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                          text=True, check=True).stdout.strip()


def _validated():
    """Every VALIDATED Statistical Validation, with its slug."""
    found = []
    for path in sorted(glob.glob(str(ARTIFACTS / "statistical-validations*.json"))):
        slug = (Path(path).stem.replace("statistical-validations-", "")
                .replace("statistical-validations", "(shared)"))
        for validation in json.loads(Path(path).read_bytes())["validations"]:
            if validation["outcome"] == "VALIDATED":
                found.append((slug, validation))
    return found


def _evidence(slug, validation):
    """Assemble what the sealed record actually holds, and nothing it does not.

    The drawdown is the only gate the walk-forward can feed directly, and even
    that is a POINT ESTIMATE -- the worst fold's realised drawdown, not an upper
    95% bound on it. Submitted honestly labelled, which §8.1 then refuses. That
    refusal is the correct outcome and the reason the label is required.
    """
    drawdowns = [summary["risk_analytics"]["max_drawdown"]
                 for summary in validation["fold_summaries"]
                 if summary["risk_analytics"].get("max_drawdown") is not None]
    evidence = {}
    if drawdowns:
        evidence["worst_fold_drawdown"] = adverse_bound(
            f"{max(drawdowns):.6f}", is_adverse_bound=False,
            source=f"worst of {len(drawdowns)} realised fold drawdowns, {slug}; a point "
                   f"estimate, not an upper bound")
    if slug in SURVIVAL_BY_SLUG:
        evidence["survival"] = SURVIVAL_BY_SLUG[slug]
    # net_sharpe, monitor, capacity and decision_cost are absent on purpose: no
    # sealed record in this project holds them for any Hypothesis.
    return evidence


def main():
    require_evidence_host(REPO)
    revision = _code_revision()
    validated = _validated()
    print(f"VALIDATED Hypotheses found: {len(validated)}\n")

    for slug, validation in validated:
        evidence = _evidence(slug, validation)
        record = constitute_admission(
            REGISTRY, hypothesis_id=f"VALIDATION_SLUG|{slug}",
            validation_id=validation["validation_id"],
            validation_outcome=validation["outcome"], evidence=evidence,
            decided_at=_now(), code_revision=revision)

        print("=" * 78)
        print(f"{slug}    ->  {record['outcome']}")
        print("=" * 78)
        for gate in record["gates"]:
            mark = {"PASSED": "  ok  ", GATE_FAILED: " FAIL ",
                    GATE_NOT_EVALUABLE: " none "}[gate["state"]]
            print(f" [{mark}] {gate['gate']}")
            print(f"          {gate['detail']}")
        print(f"  admission  {record['admission_id'][:46]}\n")

    print("=" * 78)
    denied = query_admissions(REGISTRY, ADMISSION_DENIED)
    admitted = query_admissions(REGISTRY, ADMITTED)
    print(f"  ADMITIDA {len(admitted)}   ADMISION_DENEGADA {len(denied)}")
    missing = {}
    for record in denied:
        for gate in record["gates_not_evaluable"]:
            missing[gate] = missing.get(gate, 0) + 1
    if missing:
        print(f"\n  Gates no evaluable for want of evidence nobody has produced:")
        for gate, count in sorted(missing.items(), key=lambda item: -item[1]):
            print(f"    {count}/{len(denied)}  {gate}")
    print("\n  Fase 1 requires one ADMITIDA. There is none, and now the record says on")
    print("  which gate for each -- which is what DOC-011 asks for before authorizing.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
