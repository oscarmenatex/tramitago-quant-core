"""Seal the independent ground for revising §11, as §11.5 requires.

§11.5 registered a specification finding and REFUSED to act on it, on the
ground that modifying the apparatus in view of a result the modification would
benefit is what §0 forbids. It also said what would be required to ever touch
it: "Si alguna vez se toca, el fundamento deberá enunciarse con independencia
de #37."

This states two such grounds. Each is checkable without knowing the outcome of
#37 or of any other Hypothesis.

NOTHING HERE WEAKENS WHAT §11 GUARANTEES. The guarantee is that nothing is
operated whose stop has not been established. The revision changes HOW a stop
may be established, not WHETHER one must be. Three clauses are untouched and
restated so they cannot be read as quietly dropped:

  - A P&L-only monitor NEVER qualifies, whatever its latency (§3, R3).
  - Monitoring NEVER converts into size (§10.3): a premium that fails R3 is
    inadmissible outright, not admissible small.
  - INSUFFICIENT_EVIDENCE is still not a pass for the empirical form (§11.1).

    python3.11 -B scripts/governance/seal_monitor_form_revision.py
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

RECORD = REPO / "artifacts" / "governance" / "monitor-form-revision.json"

GROUNDS = [
    {
        "name": "R2 ALREADY ADMITS TWO FORMS OF EVIDENCE FOR THIS EXACT PROBLEM; R3 ADMITS ONE",
        "statement": (
            "R2, survival, accepts EITHER P1 -- at least two out-of-sample periods with "
            "the sign holding in all of them -- OR P2, a mechanism declared in writing. P2 "
            "exists precisely because the empirical form cannot be supplied when the "
            "observations do not exist. R3, monitorability, admits only the empirical "
            "form: §11.1 requires the inferential link to have reached VALIDATED and says "
            "INSUFFICIENT_EVIDENCE is not a pass. The same document therefore answers the "
            "same difficulty -- insufficient observations of a rare state -- in two "
            "different ways depending on which gate is asking."),
        "why_it_is_independent": (
            "The asymmetry is in the text of the gates themselves. A reader can check it "
            "against §8 and §9 without knowing any Hypothesis, any instrument, any "
            "outcome, or that #37 ever existed."),
        "provenance": (
            "WEAK, AND RECORDED AS SUCH. Claude articulated this asymmetry in the same "
            "exchange in which it computed which Hypotheses a revision would admit. The "
            "ground and the consequence are not separated in time, which is exactly the "
            "order §11.5 warns against, and the only thing standing in its favour is that "
            "the consequence came back ZERO -- see the second ground's integrity check."),
    },
    {
        "name": "THE TWO REQUIREMENTS OF §11.1 ARE SATISFIED BY DISJOINT PARAMETER RANGES",
        "statement": (
            "§11.1 requires one threshold to do two incompatible things. To VALIDATE the "
            "inferential link the trigger state must occur often enough to be measured "
            "across folds. For the trigger to MEAN the mechanism has ended it must occur "
            "rarely, because a condition that fires in ordinary markets is a trading "
            "signal and not a stop. The same number is pulled in both directions."),
        "measurement": {
            "series": "BAA10Y, Moody's Baa yield relative to the 10-year Treasury",
            "window": "2018-01-02 to 2026-10-01, 2184 observations, range 1.36% to 4.31%",
            "folds": 7,
            "bands": [
                "floor 0.50% (below Baa expected credit loss, so the premium no longer "
                "compensates -- the operational meaning of death): 0 trigger days, 0 of 7 "
                "folds. The link cannot be validated.",
                "floor 1.36% (the tightest reading ever observed): 1 day, 0 of 7 folds.",
                "floor 1.50%: 151 days, 6.9%, 2 of 7 folds. Still not validatable.",
                "floor 2.00%: 1340 days, 61.4%, 7 of 7 folds. Validatable -- and firing in "
                "calm markets, where it is not a stop.",
                "floor 2.50%: 2012 days, 92.1%. Above the median.",
            ],
            "conclusion": (
                "The validatable band begins around 2.00% and the meaningful band ends "
                "around 0.50%. They are separated by the ENTIRE OBSERVED RANGE of the "
                "variable. No threshold satisfies both requirements, so for this monitor "
                "§11.1 is not strict -- it is unsatisfiable."),
        },
        "why_it_is_independent": (
            "It is arithmetic on a published third-party series, computed without "
            "reference to any Hypothesis's returns. The same computation on any variable "
            "whose death state is an extreme will have the same shape, which is why §11.3 "
            "itself says the exclusion is SYSTEMATIC. What is new is the magnitude: when "
            "§11 was adopted on 2026-10-01 nobody had computed that satisfying it requires "
            "a trigger firing on 61% of days."),
        "provenance": (
            "STRONGER. The band measurement was computed and reported BEFORE the table of "
            "which Hypotheses a revision would admit, in a separate exchange, in answer to "
            "the question of whether §11 was practically satisfiable at all."),
    },
]

REVISION = {
    "clause": "§11.1, the form of evidence admissible for a monitor's inferential link",
    "before": (
        "A monitor is admissible only if the inferential link it rests on -- that the "
        "observed condition implies degradation -- reached VALIDATED. "
        "INSUFFICIENT_EVIDENCE is not a pass."),
    "after": (
        "A monitor is admissible if its inferential link is established in EITHER of two "
        "forms, mirroring R2's existing structure:\n"
        "  M1 EMPIRICAL -- the link reached VALIDATED. Unchanged, including that "
        "INSUFFICIENT_EVIDENCE is not a pass.\n"
        "  M2 IDENTITY -- the implication follows by ARITHMETIC from an accounting "
        "relation together with an externally sourced, sealed parameter, such that it "
        "cannot fail to hold for any value of that parameter within its declared range."),
    "why_M2_is_stricter_than_R2s_P2": (
        "R2's P2 accepts a NARRATIVE mechanism: who is on the other side, why they accept "
        "losing, what would end it. M2 accepts no narrative. R3 guards a STOP, and a story "
        "that turns out to be wrong means the position cannot be stopped when it matters, "
        "which is the one failure §11 exists to prevent. An identity cannot turn out to be "
        "wrong. This is deliberately NOT a copy of P2 into R3."),
    "what_is_untouched": [
        "A P&L-only monitor never qualifies, whatever its latency.",
        "Monitoring never converts into size: failing R3 is inadmissible outright.",
        "INSUFFICIENT_EVIDENCE remains not a pass for M1.",
        "The latency arithmetic, R3's other half, is unchanged.",
        "Every other gate, and every threshold in the closed set of §8.8.",
    ],
    "the_cost_this_accepts": (
        "An M2 monitor is a REASONED stop, not a DEMONSTRATED one. Its failure mode is "
        "real: a position held in the belief it can be stopped, where the trigger does not "
        "fire or does not mean what was declared. The identity requirement is what prices "
        "that risk down, and it is why M2 must be recorded on the admission so an M2 "
        "monitor is never later mistaken for a demonstrated one."),
}

INTEGRITY_CHECK = {
    "question": "§11.0's own test: does this make passing harder or easier, and does it "
                "rescue what was observed?",
    "harder_or_easier": (
        "EASIER, and that is the opposite direction from §11, which hardened. A revision "
        "that loosens must therefore carry a heavier burden, which is why both grounds are "
        "stated, why M2 is narrower than the P2 it is modelled on, and why the consequence "
        "was measured rather than argued."),
    "does_it_rescue_what_was_observed": (
        "NO -- measured against every sealed denial, and this is the strongest fact in the "
        "record. ZERO of the four denied Hypotheses would be admitted by it. "
        "carry-exit-bitmex-holdout PASSED R3 already and falls on net Sharpe, R1, R4 and "
        "R5. eth and spy-mom10-2024 each fail five or six gates including net Sharpe and "
        "R1. equity-risk-premium-spy is the only one denied on R3 ALONE, and a revision "
        "does not touch it either: its R3 is NOT_EVALUABLE because no monitor exists at "
        "all -- an unconditional premium has no non-P&L variable -- and changing which "
        "evidence establishes a link cannot create a variable to observe. The revision "
        "affects exactly one thing: the credit premium, which has never been measured and "
        "whose net Sharpe Claude has deliberately not computed."),
}


def main():
    require_evidence_host(REPO)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, check=True).stdout.strip()
    content = {
        "schema_version": "1",
        "kind": "monitor-form-revision",
        "sealed_at": datetime.now(timezone.utc).isoformat(
            timespec="microseconds").replace("+00:00", "Z"),
        "code_revision": revision,
        "authorised_by": "Oscar Carmenate Rodriguez, Director del Proyecto",
        "revises": "§11.1 of 'Propuesta - Funcion Objetivo y Criterios de Admision "
                   "(2026-09-30)', as amended by the governance act of 2026-10-01",
        "required_by": "§11.5: 'Si alguna vez se toca, el fundamento debera enunciarse "
                       "con independencia de #37.'",
        "grounds": GROUNDS,
        "revision": REVISION,
        "integrity_check": INTEGRITY_CHECK,
    }
    record = {**content, "revision_id": "MONITOR_FORM_REVISION|" + digest(encoded(content))}
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(RECORD, encoded({"schema_version": "1", "revisions": [record]}))

    print("=" * 78)
    print("INDEPENDENT GROUND SEALED -- before any code was changed")
    print("=" * 78)
    for ground in GROUNDS:
        print(f"\n  {ground['name']}")
        print(f"    provenance: {ground['provenance'].split('.')[0]}.")
    print(f"\n  REVISION: M1 empirical (unchanged) OR M2 identity (new, narrower than P2)")
    print(f"  Untouched: {len(REVISION['what_is_untouched'])} clauses, restated in the record")
    print(f"  Integrity: rescues ZERO of the four sealed denials")
    print(f"\n  {record['revision_id'][:58]}")
    print(f"  -> {RECORD.relative_to(REPO)}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
