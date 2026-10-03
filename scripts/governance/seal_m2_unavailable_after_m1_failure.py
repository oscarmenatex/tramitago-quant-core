"""Seal the ground for closing M2 when M1 was reachable and failed.

THE DEFECT, found by the first Hypothesis to exercise both forms. The volatility
premium's monitor had an EMPIRICAL link available -- 5 usable folds, meeting the
declared minimum -- and it FAILED: inverted days degrade the return in 2 of 5
folds, 0.40 against a 0.70 threshold. The runner fell back to M2, the identity
form, and R3 PASSED.

The identity is true and does not control the outcome. An inverted VIX curve
does roll against the position by subtraction; the position's daily return is
dominated by the mark-to-market on the futures rather than by the roll, so a
true mechanism fails to produce the degradation it implies. M2 certified a
monitor that the available evidence had just refuted.

Only the net Sharpe stopped it being admitted. Had the bound been 0.045 better,
this would have been ADMITIDA with a monitor measured and rejected, which is the
single failure R3 exists to prevent.

THE GROUND IS INTERNAL AND NEEDS NO NEW MEASUREMENT. It is checkable against two
documents already sealed, by a reader who knows no result:

  MONITOR_FORM_REVISION|5bc199a4 justified M2 on the finding that §11.1 "is not
  strict -- it is unsatisfiable" for a premium whose adverse state is rare. Its
  entire warrant is ABSENT EVIDENCE. It says nothing about evidence that was
  gathered and came back against.

  §11.1 already contains the principle in its other half: INSUFFICIENT_EVIDENCE
  IS NOT A PASS. A rule that refuses to treat missing evidence as support, while
  allowing an identity to override adverse evidence, is inconsistent with
  itself.

So M2 was built as a FALLBACK FOR ABSENT EVIDENCE and was behaving as an
OVERRIDE FOR ADVERSE EVIDENCE. That inverts the strictness ordering the revision
was declared to preserve, eight hours after it was declared.

    python3.11 -B scripts/governance/seal_m2_unavailable_after_m1_failure.py
"""

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tramitago_quant_core.governance.evidence_host import require_evidence_host
from tramitago_quant_core.shared.util import digest, encoded, _atomic_write

RECORD = REPO / "artifacts" / "governance" / "m2-unavailable-after-m1-failure.json"

GROUND = {
    "name": "M2'S OWN SEALED WARRANT IS ABSENT EVIDENCE, NOT ADVERSE EVIDENCE",
    "statement": (
        "MONITOR_FORM_REVISION|5bc199a4 admitted the identity form on the measured finding "
        "that §11.1's two requirements are satisfied by disjoint parameter ranges -- that "
        "for a rare-state premium the empirical form is UNSATISFIABLE. Nothing in that "
        "record contemplates a link that CAN be tested and is. Using M2 after a reachable "
        "M1 has failed extends the form past the only justification ever given for it."),
    "second_ground": (
        "§11.1 already holds that INSUFFICIENT_EVIDENCE IS NOT A PASS. A rule refusing to "
        "read missing evidence as support, while letting an identity override evidence that "
        "exists and points the other way, is inconsistent with itself. The correction makes "
        "the gate say the same thing twice instead of two different things."),
    "why_it_is_independent": (
        "Both are internal: a reader checks them against two sealed documents without "
        "knowing any Hypothesis, instrument or outcome. No new measurement is needed and "
        "none is cited as the reason."),
    "provenance": (
        "The DEFECT was found by a measurement -- the volatility premium's link came back "
        "2 of 5 folds and M2 passed anyway -- and that is recorded plainly. The GROUND is "
        "not that measurement: it is the mismatch between M2's sealed warrant and its "
        "behaviour, which was there to be read eight hours earlier and was not."),
}

REVISION = {
    "clause": "§11.1 as revised by MONITOR_FORM_REVISION|5bc199a4, the availability of M2",
    "before": "A monitor is admissible if its link is established in EITHER form, M1 or M2.",
    "after": (
        "M2 is available only where M1 is NOT. Where the empirical form was reachable -- "
        "the trigger state occurred in at least the declared minimum of usable folds -- and "
        "the link FAILED to clear its consistency threshold, R3 FAILS. Not NOT_EVALUABLE: "
        "the evidence was gathered and it came back against, which is a verdict about the "
        "monitor and not an absence of one."),
    "what_is_untouched": [
        "M1 itself, including that INSUFFICIENT_EVIDENCE is not a pass for it.",
        "M2 where the empirical form is genuinely unreachable -- the case the revision was "
        "sealed for, and the credit premium's actual situation at zero trigger days.",
        "A P&L-only monitor never qualifies, whatever its latency.",
        "Monitoring never converts into size.",
        "The latency arithmetic, and every other gate and threshold.",
    ],
}

INTEGRITY_CHECK = {
    "question": "§11.0's test: harder or easier, and does it rescue what was observed?",
    "harder_or_easier": (
        "HARDER, unambiguously. It removes a path and adds none, so unlike the revision it "
        "corrects it carries the light burden rather than the heavy one."),
    "does_it_rescue_what_was_observed": (
        "It cannot rescue anything -- it only refuses. It does not change a single sealed "
        "verdict either: the volatility premium was DENIED on the net Sharpe, measured and "
        "bad at a point of 0.5021 and a bound of -0.0445, and remains denied. What changes "
        "is that its R3 would now FAIL rather than pass, so the record would state that its "
        "monitor was refuted rather than certified -- a difference that mattered only "
        "because the Sharpe bound happened to miss by 0.045."),
    "what_it_would_have_prevented": (
        "An ADMITIDA carrying a monitor whose link to degradation had been measured and "
        "rejected. R3 exists to prevent exactly that and, for eight hours, did not."),
}


def main():
    require_evidence_host(REPO)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, check=True).stdout.strip()
    content = {
        "schema_version": "1",
        "kind": "m2-availability-correction",
        "sealed_at": datetime.now(timezone.utc).isoformat(
            timespec="microseconds").replace("+00:00", "Z"),
        "code_revision": revision,
        "authorised_by": "Oscar Carmenate Rodriguez, Director del Proyecto",
        "corrects": "MONITOR_FORM_REVISION|5bc199a41119ff5c445bd605b83e1fe6213b",
        "ground": GROUND,
        "revision": REVISION,
        "integrity_check": INTEGRITY_CHECK,
    }
    record = {**content, "correction_id": "M2_AVAILABILITY|" + digest(encoded(content))}
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(RECORD, encoded({"schema_version": "1", "corrections": [record]}))

    print("=" * 78)
    print("GROUND SEALED -- before the code was changed")
    print("=" * 78)
    print(f"\n  {GROUND['name']}")
    print(f"\n  BEFORE  {REVISION['before']}")
    print(f"  AFTER   M2 is available only where M1 is NOT. A reachable M1 that FAILED")
    print(f"          makes R3 FAIL -- not NOT_EVALUABLE, because the evidence exists")
    print(f"          and points against.")
    print(f"\n  Integrity: HARDER, removes a path and adds none; rescues nothing and")
    print(f"             changes no sealed verdict.")
    print(f"\n  {record['correction_id'][:56]}")
    print(f"  -> {RECORD.relative_to(REPO)}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
