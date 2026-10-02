"""Gate audit: which declared limits have ever refused anything, and which only
appear to.

A rule nobody has ever been stopped by is not obviously a rule. It might be well
calibrated and simply never tested, or it might be dead weight giving false
assurance that something is being checked. The two look identical from outside,
and the difference matters: this project's declared 15% drawdown limit is the one
number its DESTINATION rests on.

THE DISTINCTION THIS AUDIT EXISTS TO MAKE. A gate that never fired is one of
three things and they are not interchangeable:

  NEVER REACHED   another gate is checked first and always fires, so this one is
                  never evaluated at all. Ordering hides it.
  NEVER TRIGGERED it was evaluated and always passed. Redundant, or calibrated.
  UNTESTED        too few cases to say.

So every gate is counted twice: how often it was EVALUATED, and how often it
REFUSED. And for gates that other gates shadow, the counterfactual is computed
too -- would it have refused, had it been reached? That last number is the only
way to tell "never reached" from "never triggered", and it is the one nobody has
ever looked at.

Precedent: the retrospective multiplicity audit found that the fold criterion's
`beats_baseline` half has never once disagreed with MET across 193 folds. That
was a gate discovered to be doing nothing, and nobody had asked. This asks about
all of them.

Reads only sealed evidence. Revisits no verdict and changes nothing.

    python3.11 -B scripts/research/run_gate_audit.py
"""

import glob
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pipeline as p
from tramitago_quant_core.governance.evidence_host import require_evidence_host

OUTPUT = REPO / "artifacts" / "research" / "gate-audit"
BASE_SIGNIFICANCE = 0.05


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _code_revision():
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                          text=True, check=True).stdout.strip()


def _gate(name, where, order, evaluated, refused, shadowed_by=None,
          would_have_refused=None, note=""):
    verdict = ("REFUSES" if refused else
               "NEVER REACHED" if evaluated == 0 else
               "NEVER TRIGGERED")
    if refused == 0 and would_have_refused:
        verdict = "SHADOWED -- would have refused " + str(would_have_refused)
    return {"gate": name, "where": where, "checked_in_order": order,
            "evaluated": evaluated, "refused": refused,
            "shadowed_by": shadowed_by, "would_have_refused": would_have_refused,
            "verdict": verdict, "note": note}


def _statistical_validations():
    rows = []
    for path in sorted(glob.glob(str(REPO / "artifacts/research/statistical-validations*.json"))):
        rows.extend(json.loads(Path(path).read_bytes())["validations"])
    return rows


def _audit_statistical(validations):
    gates, folds = [], [f for v in validations for f in v["fold_summaries"]]
    usable_of = {id(v): [f for f in v["fold_summaries"]
                         if f["criterion_result"] != "INCONCLUSIVE"] for v in validations}

    # 1. The compound fold criterion. Whether its second half has ever excluded
    #    anything is a question about the apparatus, not about any hypothesis.
    disagreements = sum(1 for f in folds
                        if (f["criterion_result"] == "MET") != (f["beats_baseline"] is True))
    gates.append(_gate(
        "fold criterion: beats_baseline", "walk_forward fold", 0,
        evaluated=len(folds), refused=disagreements,
        note="MET and beats_baseline are the same set of folds in every one. The second "
             "half has excluded nothing, ever -- not wrong, simply never binding."))

    # 2. minimum_folds_required -- first, so never shadowed.
    gates.append(_gate(
        "minimum_folds_required", "statistical validation", 1,
        evaluated=len(validations),
        refused=sum(1 for v in validations
                    if v["outcome_reason"] == "USABLE_FOLDS_BELOW_MINIMUM")))

    # 3. consistency_threshold.
    reached = [v for v in validations if v["outcome_reason"] != "USABLE_FOLDS_BELOW_MINIMUM"]
    gates.append(_gate(
        "consistency_threshold", "statistical validation", 2,
        evaluated=len(reached),
        refused=sum(1 for v in validations
                    if v["outcome_reason"] == "CONSISTENCY_BELOW_THRESHOLD")))

    # 4. Bonferroni -- checked AFTER consistency, so shadowed by it. The
    #    counterfactual asks what it would have done on every corrected
    #    validation, whether or not consistency let it be reached.
    corrected = [v for v in validations if v.get("batch") is not None]
    would = 0
    for v in corrected:
        usable = usable_of[id(v)]
        passing = sum(1 for f in usable
                      if f["criterion_result"] == "MET" and f["beats_baseline"] is True)
        value = sum(math.comb(len(usable), k)
                    for k in range(passing, len(usable) + 1)) / 2 ** len(usable)
        if value > BASE_SIGNIFICANCE / v["batch"]["batch_size"]:
            would += 1
    gates.append(_gate(
        "bonferroni correction", "statistical validation", 3,
        evaluated=sum(1 for v in validations
                      if v["outcome_reason"] in (None, "BONFERRONI_CORRECTION_NOT_MET")
                      and v.get("batch") is not None),
        refused=sum(1 for v in validations
                    if v["outcome_reason"] == "BONFERRONI_CORRECTION_NOT_MET"),
        shadowed_by="consistency_threshold", would_have_refused=would,
        note=f"Only {len(corrected)} of {len(validations)} validations carry a correction "
             f"at all."))
    return gates


def _audit_level_claims():
    path = REPO / "artifacts/research/level-claim-validations.json"
    if not path.exists():
        return []
    claims = json.loads(path.read_bytes())["validations"]
    gates = []
    usable_of = {id(v): [f for f in v["folds"] if f["result"] != "INCONCLUSIVE"] for v in claims}

    gates.append(_gate(
        "minimum_folds_required", "level claim", 1, evaluated=len(claims),
        refused=sum(1 for v in claims
                    if v["outcome_reason"] == "USABLE_FOLDS_BELOW_MINIMUM")))

    coverage = ("ADVERSE_EPISODES_BELOW_MINIMUM", "ADVERSE_PERIODS_BELOW_MINIMUM_FREQUENCY")
    gates.append(_gate(
        "tail coverage", "level claim", 2,
        evaluated=sum(1 for v in claims
                      if v["outcome_reason"] != "USABLE_FOLDS_BELOW_MINIMUM"),
        refused=sum(1 for v in claims if v["outcome_reason"] in coverage),
        note="Schema 1 asked for a FREQUENCY and schema 2 for distinct EPISODES; both are "
             "counted here as the same question."))

    gates.append(_gate(
        "consistency_threshold", "level claim", 3,
        evaluated=sum(1 for v in claims if v["outcome_reason"] not in
                      coverage + ("USABLE_FOLDS_BELOW_MINIMUM",)),
        refused=sum(1 for v in claims
                    if v["outcome_reason"] == "CONSISTENCY_BELOW_THRESHOLD")))

    # maximum_drawdown is checked LAST, so everything above shadows it. This is
    # the counterfactual that matters most: the drawdown limit is the number the
    # project's DESTINATION rests on.
    would = 0
    for v in claims:
        usable = usable_of[id(v)]
        if not usable:
            continue
        worst = max(float(f["max_drawdown"]) for f in usable)
        if worst > float(v["claim"]["maximum_drawdown"]):
            would += 1
    gates.append(_gate(
        "maximum_drawdown", "level claim", 4,
        evaluated=sum(1 for v in claims if v["outcome"] == "VALIDATED"
                      or v["outcome_reason"] == "DRAWDOWN_LIMIT_EXCEEDED"),
        refused=sum(1 for v in claims if v["outcome_reason"] == "DRAWDOWN_LIMIT_EXCEEDED"),
        shadowed_by="consistency_threshold and tail coverage", would_have_refused=would,
        note="The 15% limit is the number the destination rests on, and it is checked last."))
    return gates


def _audit_discovery():
    path = REPO / "artifacts/discovery/discovery-scans.json"
    if not path.exists():
        return []
    scans = json.loads(path.read_bytes())["scans"]
    spaces = {s["space_id"]: s for s in json.loads(
        (REPO / "artifacts/discovery/discovery-spaces.json").read_bytes())["spaces"]}
    observations = [(scan, item) for scan in scans for item in scan["observations"]]

    empty = sum(1 for _, o in observations if o["effect"] is None)
    gates = [_gate("one group empty", "discovery candidate", 1,
                   evaluated=len(observations), refused=empty)]

    reached = [(s, o) for s, o in observations if o["effect"] is not None]
    support_refused = sum(1 for _, o in reached
                          if not o["examinable"] and o["reason"].startswith("support"))
    gates.append(_gate("minimum_support", "discovery candidate", 2,
                       evaluated=len(reached), refused=support_refused))

    # minimum_minority_state_frequency is checked after support, and a rare group
    # is also a small one, so support tends to fire first. Whether it EVER would
    # have fired on its own is the question.
    would, triggered = 0, 0
    for scan, item in reached:
        floor = float(spaces[scan["space_id"]]["minimum_minority_state_frequency"])
        if float(item["minority_state_frequency"]) < floor:
            would += 1
            if item["examinable"] or not item["reason"].startswith("support"):
                triggered += 1
    gates.append(_gate(
        "minimum_minority_state_frequency", "discovery candidate", 3,
        evaluated=sum(1 for _, o in reached
                      if o["examinable"] or not o["reason"].startswith("support")),
        refused=sum(1 for _, o in reached
                    if not o["examinable"] and "minority" in o["reason"]),
        shadowed_by="minimum_support", would_have_refused=would,
        note="A rare minority group is also a small one, so the support floor tends to "
             "fire first. The representativeness rule adopted 2026-09-30 lives here."))
    return gates


def main():
    require_evidence_host(REPO)
    validations = _statistical_validations()
    gates = (_audit_statistical(validations) + _audit_level_claims() + _audit_discovery())

    never = [g for g in gates if g["refused"] == 0]
    measurement = {
        "kind": "gate-audit",
        "schema_version": "1",
        "measured_at": _now(),
        "code_revision": _code_revision(),
        "gates": gates,
        "gates_total": len(gates),
        "gates_that_have_never_refused": len(never),
        "gates_shadowed_but_would_have_refused": sum(
            1 for g in gates if g["refused"] == 0 and g["would_have_refused"]),
    }
    measurement["measurement_id"] = "GATE_AUDIT|" + p.digest(p.encoded(
        {k: v for k, v in measurement.items() if k not in ("measured_at", "code_revision")}))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "measurement.json").write_bytes(p.encoded(measurement))

    print(f"{'gate':<38} {'where':<22} {'eval':>5} {'refuses':>8}  verdict")
    print("-" * 110)
    for g in gates:
        print(f"{g['gate']:<38} {g['where']:<22} {g['evaluated']:>5} {g['refused']:>8}  "
              f"{g['verdict']}")
    print("-" * 110)
    print(f"{len(gates)} gates, {len(never)} have never refused anything.")
    for g in never:
        if g["would_have_refused"]:
            print(f"\n  {g['gate']} is SHADOWED by {g['shadowed_by']}: it would have refused "
                  f"{g['would_have_refused']} case(s) had it been reached.")
        elif g["evaluated"]:
            print(f"\n  {g['gate']} was evaluated {g['evaluated']} times and never triggered.")
    print(f"\nsealed  {measurement['measurement_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
