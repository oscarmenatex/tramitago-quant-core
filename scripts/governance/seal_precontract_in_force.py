"""Seal the ground for correcting when the pre-declaration contract came into force.

THE DEFECT, mine. PRE_DECLARATION_IN_FORCE_SINCE was set to 2026-10-03T00:00:00Z,
MIDNIGHT UTC of the day the contract was written, as a round number. The contract was
introduced by a commit at 05:32:10 UTC and merged at 05:36:32 UTC. So the rule reached
back five and a half hours over Hypotheses that were declared before it existed and
could not possibly have answered seven questions nobody had yet asked.

FOUND by migrating the credit premium to the spec engine, which refused to reload it.
Measuring what the instant touches showed it was not one Hypothesis but two: SPY's
three versions were created 01:15 to 01:41 UTC and the credit premium's 03:33 to
04:16, six versions in all, every one of them before the contract.

THIS LOOSENS A GATE, which is the direction that carries the heavier burden, so the
ground is sealed before the code changes and what it moves is MEASURED from the
registry rather than argued. What it does not do is rescue anything observed: both
Hypotheses it exempts were already measured and denied, and the two Hypotheses created
after the contract -- the volatility premium and XYLD -- stay in scope, each with the
seven answers it was required to give.

    python3.11 -B scripts/governance/seal_precontract_in_force.py
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

RECORD = REPO / "artifacts" / "governance" / "pre-declaration-in-force.json"
HYPOTHESES = REPO / "artifacts" / "research" / "hypotheses.json"
INTRODUCING_COMMIT = "99d930f"
INTRODUCING_PR = "118"
OLD_INSTANT = "2026-10-03T00:00:00Z"


def _git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True,
                          check=True).stdout.strip()


def _instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _facts():
    committed = datetime.fromisoformat(
        _git("show", "-s", "--format=%cI", INTRODUCING_COMMIT)).astimezone(timezone.utc)
    merged = subprocess.run(
        ["gh", "pr", "view", INTRODUCING_PR, "--json", "mergedAt", "-q", ".mergedAt"],
        cwd=REPO, capture_output=True, text=True).stdout.strip() or None
    return committed, merged


def _effect(new_instant):
    """Every Hypothesis version created on the day, against the old and new instant."""
    versions = sorted(
        (item["creation_timestamp"], item["hypothesis_id"], item["version"])
        for item in json.loads(HYPOTHESES.read_bytes())["hypotheses"]
        if item["creation_timestamp"].startswith("2026-10-03"))
    rows = []
    for created, hypothesis_id, version in versions:
        before = _instant(created) >= _instant(OLD_INSTANT)
        after = _instant(created) >= new_instant
        rows.append({"created": created, "hypothesis": hypothesis_id[:19], "version": version,
                     "in_scope_under_the_old_instant": before,
                     "in_scope_under_the_new_instant": after})
    moved = [row for row in rows if row["in_scope_under_the_old_instant"]
             != row["in_scope_under_the_new_instant"]]
    return rows, moved


def main():
    require_evidence_host(REPO)
    revision = _git("rev-parse", "HEAD")
    committed, merged = _facts()
    new_instant = committed
    rows, moved = _effect(new_instant)
    between = [row for row in rows if committed <= _instant(row["created"])
               < (_instant(merged) if merged else committed)]

    content = {
        "schema_version": "1", "kind": "pre-declaration-in-force-correction",
        "sealed_at": datetime.now(timezone.utc).isoformat(
            timespec="microseconds").replace("+00:00", "Z"),
        "code_revision": revision,
        "authorised_by": "Oscar Carmenate Rodriguez, Director del Proyecto",
        "corrects": "PRE_DECLARATION_IN_FORCE_SINCE in research/pre_declaration.py",
        "ground": {
            "name": "A RULE CANNOT BIND WHAT PREDATES IT",
            "statement": (
                "The contract was introduced by commit " + INTRODUCING_COMMIT + " at "
                + committed.isoformat().replace("+00:00", "Z") + " and merged at "
                + str(merged) + ". Its in-force instant was set to " + OLD_INSTANT
                + ", midnight UTC of that day, as a round number. The rule therefore "
                "reached back over "
                + str(round((committed - _instant(OLD_INSTANT)).total_seconds() / 3600, 2))
                + " hours, and demanded seven answers of Hypotheses declared before the "
                "question existed."),
            "why_it_is_independent": (
                "It is a fact about two timestamps, checkable against git and the "
                "registry by a reader who knows no result and no candidate. It would be "
                "the same defect whichever Hypotheses fell inside the window."),
            "provenance": (
                "FOUND while migrating the credit premium to the spec engine, which refused "
                "to reload it, and not while seeking an outcome for any live candidate. The "
                "weakness is that it is my own carelessness, a round number chosen without "
                "checking the commit time, discovered in the same session it was made. The "
                "first account of it named one Hypothesis; measuring the registry showed two."),
        },
        "revision": {
            "before": "in force from " + OLD_INSTANT,
            "after": "in force from " + committed.isoformat().replace("+00:00", "Z")
                     + ", the instant the introducing commit was made",
            "also_changed": (
                "The comparison parses both instants instead of comparing strings. A "
                "second-precision constant against microsecond timestamps misorders "
                "within the same second, because the character that separates the "
                "fraction sorts before the one that ends the string."),
            "what_is_untouched": [
                "The seven questions and the prose floor on each.",
                "The refusal at capture of a Hypothesis lacking them.",
                "Every Hypothesis created from the new instant on, which stays in scope.",
                "The bar derived from the window and the range rule aligned on 2026-10-03.",
            ],
        },
        "integrity_check": {
            "question": "section 11.0's own test: harder or easier, and what does it rescue?",
            "harder_or_easier": (
                "EASIER, the direction that carries the heavier burden: fewer Hypotheses "
                "fall under the rule. The burden is met by the ground being a fact about "
                "two timestamps, by its effect being measured below, and by the change "
                "touching only Hypotheses that predate the rule."),
            "measured": {
                "versions_created_on_the_day": len(rows),
                "moved_out_of_scope": [f"{row['hypothesis']} v{row['version']} "
                                       f"({row['created']})" for row in moved],
                "hypotheses_affected": sorted({row["hypothesis"] for row in moved}),
                "versions_still_in_scope": [f"{row['hypothesis']} v{row['version']}"
                                            for row in rows
                                            if row["in_scope_under_the_new_instant"]],
                "created_between_the_commit_and_the_merge": [
                    f"{row['hypothesis']} v{row['version']}" for row in between],
            },
            "does_it_rescue_what_was_observed": (
                "NO. Both Hypotheses it exempts were already measured and DENIED: SPY died "
                "on monitorability and the credit premium on a net Sharpe of 0.1875. Neither "
                "is a candidate and neither can be admitted by this change. The live "
                "candidate, XYLD, was created at 15:13 UTC, after both instants, and is "
                "unaffected."),
            "why_the_commit_and_not_the_merge": (
                "The Director authorised the commit instant. It is immaterial to the record: "
                "no Hypothesis was created between the commit and the merge, so the two "
                "instants exempt exactly the same versions."),
        },
    }
    record = {**content, "correction_id": "PRE_DECLARATION_IN_FORCE|" + digest(encoded(content))}
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(RECORD, encoded({"schema_version": "1", "corrections": [record]}))

    print("=" * 78)
    print("GROUND SEALED -- before the code was changed")
    print("=" * 78)
    print(f"  contract committed  {committed.isoformat().replace('+00:00', 'Z')}   merged {merged}")
    print(f"  old in-force instant {OLD_INSTANT}  (reached back "
          f"{(committed - _instant(OLD_INSTANT)).total_seconds() / 3600:.2f} h)")
    print(f"\n  moved OUT of scope: {len(moved)} versions of "
          f"{len({r['hypothesis'] for r in moved})} Hypotheses")
    for row in moved:
        print(f"    {row['hypothesis']} v{row['version']}  {row['created']}")
    print(f"  still in scope: {[f'{r['hypothesis']} v{r['version']}' for r in rows if r['in_scope_under_the_new_instant']]}")
    print(f"  created between the commit and the merge: {len(between)}")
    print(f"\n  {record['correction_id'][:62]}")
    print(f"  -> {RECORD.relative_to(REPO)}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
