"""Seal the arithmetic ground for correcting two acceptance thresholds.

THE CORRECTION IS NOT "THE BAR WAS TOO HIGH". It is that two thresholds were
calibrated for one quantity and applied to a different, stricter one. Both
mismatches are arithmetic: a reader can verify each without knowing which
Hypotheses pass under either rule, which is what makes them admissible grounds
rather than a response to a disappointing result.

NEITHER CORRECTION INTRODUCES A NEW CONSTANT. That is the strongest property of
both and the reason this shape was chosen: 0.50 and 0.70 are unchanged, and
each goes back to measuring the quantity it was set for. A correction that had
to invent a replacement number would be indistinguishable from tuning.

THE PROVENANCE IS NOT UNIFORM AND THIS RECORD SAYS SO, per defect, because the
order matters and the project's own rule is that choosing a threshold by
looking at what it admits is fitting the gate to the data.

    python3.11 -B scripts/governance/seal_threshold_correction.py
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

RECORD = REPO / "artifacts" / "governance" / "threshold-corrections.json"

DEFECTS = [
    {
        "gate": "DOC-011 §8.2, net Sharpe",
        "threshold": "0.50",
        "calibrated_for": "the Sharpe RATIO -- an effect size",
        "applied_to": "its LOWER 95% BOUND -- an effect size minus its sampling error",
        "why_that_is_a_mismatch": (
            "A lower bound is the point estimate minus a quantity that depends on SAMPLE "
            "SIZE, not on quality. Requiring the bound to exceed a threshold set for the "
            "point estimate therefore demands a point estimate larger than that threshold "
            "by an amount nobody declared and which nobody can state without knowing the "
            "sample length."),
        "measured_gap": {
            "series": "SPY total return, held continuously, 2198 sessions (8.72 years)",
            "point_estimate": "0.8125",
            "lower_95_bound": "0.2695",
            "gap": "0.5430",
            "consequence": (
                "a threshold of 0.50 ON THE BOUND behaves as a threshold of approximately "
                "1.04 on the point estimate at this sample length, and worse at shorter "
                "ones. The gate reads 'net Sharpe >= 0.50' and operated as 'Sharpe >= 1.04 "
                "over a decade'."),
        },
        "correction": (
            "Separate the two questions the single number conflated. EFFECT SIZE is judged "
            "on the point estimate against the unchanged 0.50, where that threshold was "
            "calibrated. WHETHER THE EFFECT IS REAL is judged by the bound against ZERO, "
            "which is what §8.1 asks a bound to establish."),
        "what_the_correction_does_not_do": (
            "It does not weaken or remove the bound requirement, which does real work: "
            "dropping it would readmit spy-mom10-2024 on a point Sharpe of 1.5198, a "
            "Hypothesis whose sign broke in BOTH of its out-of-sample periods. Its bound "
            "is -0.1798 and the corrected rule still refuses it."),
        "provenance": (
            "STATED BEFORE the consequences were computed. The 0.543 gap and the 'behaves "
            "like 1.04' reading are in commit 2f3f0ab, which sealed the SPY measurement; "
            "the table of which Hypotheses each candidate rule would admit was computed "
            "afterwards. The ground precedes the outcome in the git record."),
    },
    {
        "gate": "level claim, fold consistency",
        "threshold": "0.70",
        "calibrated_for": (
            "SIGN consistency -- how often the position paid, declared as what a finished "
            "system should clear"),
        "applied_to": (
            "per-fold STATISTICAL SIGNIFICANCE: a fold counted only when its own lower 95% "
            "bound was above zero"),
        "why_that_is_a_mismatch": (
            "Significance inside a fold is a statement about that fold's length. A bound "
            "computed on a fifteen-month slice discards the power that makes a bound "
            "meaningful, and demanding it of every slice refuses everything rather than "
            "discriminating between things -- which carries no more information than a "
            "gate that refuses nothing."),
        "measured_gap": {
            "series": "SPY total return, 7 folds of about 15 months",
            "folds_with_a_positive_mean": "6/7 = 0.857, which CLEARS 0.70",
            "folds_with_a_positive_bound": "3/7 = 0.4286, which FAILS 0.70",
            "full_sample_bound": "positive",
            "consequence": (
                "the aggregate IS significant and no fifteen-month slice of it can be. The "
                "same position passes or fails on which quantity the 0.70 is read against."),
        },
        "correction": (
            "A fold is MET on the SIGN of its mean net return, which is what 0.70 was set "
            "for. Significance is established ONCE, on the whole sample, where a bound has "
            "the power to mean something -- which the §8.2 gate already does. The bound is "
            "still computed and still recorded on every fold; it stopped deciding the fold, "
            "it did not stop being evidence."),
        "what_the_correction_does_not_do": (
            "It does not re-adjudicate anything. Sealed claims carry schema 1 or 2 and are "
            "judged by the rule they were issued under; only claims sealed as schema 3 use "
            "this one."),
        "provenance": (
            "WEAKER THAN THE FIRST, AND DELIBERATELY RECORDED AS SUCH. This mismatch was "
            "found in the SAME execution that computed which Hypotheses each candidate "
            "rule would admit, so the ground and the outcome are not separated in time. "
            "The arithmetic stands on its own and any reader can check it without knowing "
            "any verdict -- but the order in which this project arrived at it is not the "
            "order it should have arrived at it, and a later reader is entitled to weigh "
            "that."),
    },
]

CONTEXT = (
    "Both defects were invisible until 2026-10-02 because this project had no bound on a "
    "Sharpe until that day -- §8.1 required adverse bounds and the only bound implemented "
    "was on a MEAN. The first thing that could expose the mismatch was the first Sharpe "
    "bound ever computed here. What exposed it was the equity risk premium: SPY held "
    "continuously for 8.72 years, netting +15.21% a year at an execution cost of 0.009% of "
    "gross, refused by both gates. That Hypothesis was declared in writing, before any data "
    "was read, as a test OF THE GATES, with the inference to be drawn from a refusal stated "
    "in advance precisely so it could not be invented afterwards."
)


def main():
    require_evidence_host(REPO)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, check=True).stdout.strip()
    content = {
        "schema_version": "1",
        "kind": "threshold-correction",
        "sealed_at": datetime.now(timezone.utc).isoformat(
            timespec="microseconds").replace("+00:00", "Z"),
        "code_revision": revision,
        "authorised_by": "Oscar Carmenate Rodriguez, Director del Proyecto",
        "context": CONTEXT,
        "introduces_no_new_constant": True,
        "defects": DEFECTS,
    }
    record = {**content,
              "correction_id": "THRESHOLD_CORRECTION|" + digest(encoded(content))}
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(RECORD, encoded({"schema_version": "1", "corrections": [record]}))

    print("=" * 78)
    print("ARITHMETIC GROUND SEALED")
    print("=" * 78)
    for defect in DEFECTS:
        print(f"\n  {defect['gate']}   threshold {defect['threshold']} UNCHANGED")
        print(f"    calibrated for  {defect['calibrated_for']}")
        print(f"    applied to      {defect['applied_to']}")
    print(f"\n  introduces no new constant: {record['introduces_no_new_constant']}")
    print(f"  {record['correction_id'][:54]}")
    print(f"  -> {RECORD.relative_to(REPO)}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
