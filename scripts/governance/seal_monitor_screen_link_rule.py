"""Seal the Director's decision on what a link confirmed by the monitor screen may do for R3.

THE DECISION, 2026-10-03: option C. A link confirmed by the screen is an ADDITIONAL requirement
for a premium that relies on it. The premium must pass R3 under M1 on its own measurement window
AND the screen link. The screen link never replaces M1.

WHY IT IS SEALED NOW. The scan has not been run. Deciding what a passing candidate may be used
for after seeing who passes is the pattern section 11.5 forbids, and the decision touches R3, so
the ground is written and the effect measured before anything is scanned.

THIS CHANGES NO GATE. The rule only ever adds a requirement, so it cannot admit anything that R3
refuses today. It is recorded as HARDER, and what it rescues is measured below rather than
asserted: the admissions already sealed are scanned for any citation of a screen link.

ENFORCEMENT IS NOT IN THE CODE YET, and the record says so. The point where it would bind is the
monitorability gate reading a monitor that declares a screen link, and that needs a new admission
schema: schemas 1 to 4 are sealed and changing their text invalidates every record sealed under
them, which has already happened three times. It is wired when the scan exists, as a new schema
and never as an edit of an old one.

    python3.11 -B scripts/governance/seal_monitor_screen_link_rule.py
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tramitago_quant_core.governance.evidence_host import require_evidence_host
from tramitago_quant_core.shared.util import digest, encoded, _atomic_write

RECORD = REPO / "artifacts" / "governance" / "monitor-screen-link-rule.json"
ADMISSIONS = REPO / "artifacts" / "research" / "admissions.json"
SPACE = REPO / "scripts" / "research" / "monitor_screen" / "space.json"
CITATION_MARKERS = ("monitor_screen", "screen_link", "monitor-screen")


def _git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True,
                          check=True).stdout.strip()


def _admissions_citing_the_screen():
    records = json.loads(ADMISSIONS.read_bytes())["admissions"]
    cited = [r["admission_id"] for r in records
             if any(marker in json.dumps(r) for marker in CITATION_MARKERS)]
    return len(records), cited


def main():
    require_evidence_host(REPO)
    total, cited = _admissions_citing_the_screen()
    space = json.loads(SPACE.read_text(encoding="utf-8"))
    content = {
        "schema_version": "1", "kind": "monitor-screen-link-rule",
        "sealed_at": datetime.now(timezone.utc).isoformat(
            timespec="microseconds").replace("+00:00", "Z"),
        "code_revision": _git("rev-parse", "HEAD"),
        "authorised_by": "Oscar Carmenate Rodriguez, Director del Proyecto",
        "decision": "OPTION C",
        "applies_to": "R3 monitorability, for a premium whose monitor was chosen through the "
                      "monitor screen " + space["slug"],
        "ground": {
            "name": "A LINK CONFIRMED ELSEWHERE CANNOT STAND IN FOR THE LINK MEASURED ON THE "
                    "PREMIUM'S OWN WINDOW",
            "statement": (
                "R3 asks that a monitor rest on a validated inferential link. The screen can "
                "confirm that a variable led adverse returns of an exposure on a holdout it was "
                "not selected on. That is evidence about the variable and the exposure, in one "
                "regime, and it does not say the premium being measured has the same behaviour on "
                "its own window, with its own costs and its own wrapper. So it adds to the "
                "requirement and cannot replace the test the premium has to pass."),
            "why_it_is_independent": (
                "It does not depend on which candidate passes or on which hypothesis it would "
                "help. It would read the same if the scan found nothing, and it binds a "
                "candidate that passes exactly as it binds one that fails."),
            "provenance": (
                "Chosen by the Director from three options drafted by Claude (A no, B alternative "
                "route, C additional requirement). The weakness is that the options were drafted "
                "knowing XYLD, QYLD and SPY were denied on R3 alone, so the question was framed "
                "by a result; option C was chosen because it is the only one that cannot benefit "
                "any of them, which is the property that makes the choice credible."),
        },
        "rule": {
            "requirement": "a premium that relies on a screen link must pass R3 under M1 on its "
                           "own measurement window AND the screen link",
            "never": "the screen link never replaces M1, and never opens the identity form M2, "
                     "which stays closed after a failed M1 (M2_AVAILABILITY|04a35abd)",
            "conditions_that_protect_the_screen": [
                "the holdout is used once, for the single claim declared in the space",
                "if k candidates pass discovery, each must clear the holdout at confidence "
                "1 minus 0.05 divided by k (Bonferroni), declared before the scan",
                "a link is valid only for the exposure it was confirmed on, exactly",
                "a confirmed link is reconfirmed annually, as a calendar trigger",
            ],
            "enforcement": "NOT YET IN CODE. It binds by this record and by the pre-declaration "
                           "of any premium that cites the screen. It is wired with the scan, as a "
                           "NEW admission schema; admission schemas 1 to 4 are never edited.",
        },
        "what_is_untouched": [
            "M1 and its constants (5 usable folds, 0.70, 10 trigger days).",
            "M2 and its availability rule.",
            "DOC-011 section 12 and the Sharpe, R1, R2, R4 and R5 gates.",
            "Every sealed admission, level claim and family verdict.",
        ],
        "integrity_check": {
            "question": "section 11.0's own test: harder or easier, and what does it rescue?",
            "harder_or_easier": "HARDER. It adds a requirement and removes none, so it cannot "
                                "admit anything R3 refuses today.",
            "measured": {
                "admissions_sealed": total,
                "admissions_citing_a_screen_link": len(cited),
                "ids": cited,
            },
            "does_it_rescue_what_was_observed": (
                "NO. SPY, XYLD and QYLD, denied on R3 alone, are not helped: under this rule a "
                "screen link would still leave them needing M1 on their own window, which they "
                "failed. Option B would have been the one that could."),
            "what_it_costs": "It unblocks nothing on its own. The screen serves to choose better "
                             "monitors to declare, and a premium still has to pass M1.",
        },
        "decisions_recorded_in_the_space": [d["n"] for d in space["decisions"]],
    }
    record = {**content, "rule_id": "MONITOR_SCREEN_LINK_RULE|" + digest(encoded(content))}
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(RECORD, encoded({"schema_version": "1", "rules": [record]}))

    print("=" * 78)
    print("RULE SEALED -- before any scan, and before any candidate was read")
    print("=" * 78)
    print(f"  decision    OPTION C: the screen link is an ADDITIONAL requirement, never a substitute")
    print(f"  harder      yes; admissions sealed {total}, citing a screen link {len(cited)}")
    print(f"  rescues     nobody: SPY, XYLD and QYLD still need M1 on their own window")
    print(f"  enforcement NOT in code yet; wired with the scan as a new admission schema")
    print(f"  {record['rule_id'][:62]}")
    print(f"  -> {RECORD.relative_to(REPO)}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
