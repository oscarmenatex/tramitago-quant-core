"""Seal the ground for aligning the pre-declaration contract with the register.

THE CONFLICT, found when declaring XYLD. The register uses three verdicts and
refuses a candidate only when even the OPTIMISTIC end of its recalled effect range
falls short of its bar. The pre-declaration contract refused whenever the single
declared plausible Sharpe -- the CONSERVATIVE end -- fell short. XYLD, recalled at
0.35 to 0.80 against a bar of 0.500, was UNCERTAIN in one and REFUSED in the
other, and could be declared only by choosing 0.575, a number picked to cross.

THIS LOOSENS A GATE, and for the very candidate being declared. That is the shape
section 11.5 of the admission proposal forbids: modifying the apparatus in view of
a result the modification would benefit. So the ground is stated before any code
changes, its provenance is recorded per ground including where it is weak, and
what the change admits is MEASURED from the register rather than argued.

THE COST IS STATED, NOT HIDDEN. The class this opens -- candidates whose range
straddles their bar -- has produced no admission in this project: every measured
member of it died. Aligning the contract makes that class declarable. It does not
make it likely to succeed, and the record says so.

    python3.11 -B scripts/governance/seal_precontract_alignment.py
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tramitago_quant_core.governance.evidence_host import require_evidence_host
from tramitago_quant_core.research.pre_declaration import required_point_sharpe_for
from tramitago_quant_core.shared.util import digest, encoded, _atomic_write

RECORD = REPO / "artifacts" / "governance" / "pre-declaration-alignment.json"
REGISTER = REPO / "artifacts" / "research" / "candidate-registers.json"


def _measure():
    """What each rule would let through, computed from the sealed register."""
    registers = json.loads(REGISTER.read_bytes())["registers"]
    latest = [item for item in registers if item["schema_version"] == "2"][-1]
    rows = []
    for entry in latest["candidates"]:
        bar = required_point_sharpe_for(entry["available_years"])
        low, high = Decimal(entry["effect"]["low"]), Decimal(entry["effect"]["high"])
        rows.append({
            "name": entry["name"], "status": entry["status"], "bar": str(bar),
            "low": str(low), "high": str(high),
            "reachable": entry["reachable_today"],
            "strict_allows": low >= bar, "aligned_allows": high >= bar})
    strict = [r for r in rows if r["strict_allows"]]
    aligned = [r for r in rows if r["aligned_allows"]]
    opened = [r for r in aligned if not r["strict_allows"]]
    dead_opened = [r for r in opened if r["status"] == "MEASURED_DEAD"]
    return {
        "register_id": latest["register_id"],
        "candidates": len(rows),
        "declarable_under_the_strict_rule": len(strict),
        "declarable_under_the_aligned_rule": len(aligned),
        "newly_declarable": [r["name"] for r in opened],
        "newly_declarable_that_are_already_measured": [r["name"] for r in dead_opened],
        "of_those_measured_how_many_were_admitted": 0,
        "still_refused_under_the_aligned_rule": [
            r["name"] for r in rows if not r["aligned_allows"]],
    }


GROUND = {
    "name": "THE REGISTER CORRECTED THIS FLAW BEFORE XYLD WAS A CANDIDATE, AND THE CONTRACT WAS NOT",
    "statement": (
        "A recalled effect range has two ends and neither is right alone. The conservative "
        "end refuses a premium that then clears its gate: the S&P 500 was recalled at 0.40 "
        "to 0.80 against a bar of 0.500 and MEASURED 0.81. The optimistic end admits one "
        "that then fails: the investment-grade credit premium was recalled at 0.20 to 0.50 "
        "and MEASURED 0.19. The candidate register therefore holds three verdicts and "
        "refuses only when even the optimistic end falls short. The pre-declaration "
        "contract was written the same day with a single declared value, the conservative "
        "end, and applied the rule the register had already shown to be wrong."),
    "why_it_is_independent": (
        "Both measurements -- SPY at 0.81 and credit at 0.19 -- were sealed before XYLD "
        "existed in this project's candidate list, and the three-verdict rule was sealed "
        "in the register in PR 123 while the list held no covered-call fund; XYLD entered "
        "in PR 125. A reader can check the ordering in the git record, and can check the "
        "ground against two sealed measurements without knowing any covered-call result."),
    "provenance": (
        "PARTLY WEAK, AND RECORDED AS SUCH. The ground predates the candidate it benefits. "
        "But the inconsistency BETWEEN the contract and the register was NOTICED while "
        "trying to declare XYLD, which is the order the proposal warns against: the "
        "apparatus is being changed at the moment a change would help. What stands in its "
        "favour is that the rule being aligned TO was already sealed, and the change "
        "removes an inconsistency rather than introducing a new constant."),
}

REVISION = {
    "clause": "the feasibility refusal in research/pre_declaration.py",
    "before": ("A single plausible point Sharpe is declared, the conservative end of the "
               "recalled range, and the declaration is refused when it falls below the bar "
               "derived from the window."),
    "after": ("The recalled range is declared as TWO ends. The declaration is refused only "
              "when even the OPTIMISTIC end falls below the bar, matching the register. A "
              "declaration whose conservative end falls below the bar but whose optimistic "
              "end clears it is ACCEPTED and RECORDED AS STRADDLING, so the position "
              "against the bar travels with the declaration and is never inferred later."),
    "what_is_untouched": [
        "The bar itself, derived from the window length, with no new constant.",
        "The floor of 0.50 below which no bar may fall.",
        "The refusal of a declared bar that disagrees with the derived one.",
        "The requirement to explain a window shorter than the source serves.",
        "The seven answers and the prose floor on each.",
        "Every record already sealed: schema 1 reproduces byte for byte.",
    ],
    "what_it_adds": (
        "Two fields, plausible_low and plausible_high, and a derived position against "
        "the bar (CLEARS or STRADDLES). Schema 2; schema 1 records are unchanged."),
}

INTEGRITY_CHECK = {
    "question": "section 11.0's own test: harder or easier, and what does it rescue?",
    "harder_or_easier": (
        "EASIER, the direction that carries the heavier burden. It makes a class of "
        "candidates declarable that the contract refused, which is why the ground is "
        "stated before the change, the provenance is recorded per ground, and the effect "
        "is measured from the register rather than argued."),
    "measured": _measure(),
    "the_cost_stated_plainly": (
        "The class this opens -- candidates whose range straddles their bar -- has produced "
        "NO admission in this project. Every measured member of it died: the S&P 500 on "
        "monitorability, the volatility premium on a Sharpe bound of -0.0445 and a refuted "
        "monitor link, the funding carry on returns, and the credit premium on size. "
        "Aligning the contract makes the class declarable and says nothing about how it "
        "will resolve. Four of four is a small sample and a poor omen, and both are true."),
}


def main():
    require_evidence_host(REPO)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, check=True).stdout.strip()
    content = {
        "schema_version": "1", "kind": "pre-declaration-alignment",
        "sealed_at": datetime.now(timezone.utc).isoformat(
            timespec="microseconds").replace("+00:00", "Z"),
        "code_revision": revision,
        "authorised_by": "Oscar Carmenate Rodriguez, Director del Proyecto",
        "aligns": "research/pre_declaration.py with the candidate register's three verdicts",
        "ground": GROUND, "revision": REVISION, "integrity_check": INTEGRITY_CHECK,
    }
    record = {**content, "alignment_id": "PRE_DECLARATION_ALIGNMENT|" + digest(encoded(content))}
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(RECORD, encoded({"schema_version": "1", "alignments": [record]}))

    measured = INTEGRITY_CHECK["measured"]
    print("=" * 78)
    print("GROUND SEALED -- before the contract changed")
    print("=" * 78)
    print(f"\n  {GROUND['name']}\n")
    print(f"  measured from register {measured['register_id'][19:31]}, "
          f"{measured['candidates']} candidates:")
    print(f"    declarable under the STRICT rule   {measured['declarable_under_the_strict_rule']}")
    print(f"    declarable under the ALIGNED rule  {measured['declarable_under_the_aligned_rule']}")
    print(f"    newly declarable: {len(measured['newly_declarable'])}")
    for name in measured["newly_declarable"]:
        print(f"      {name[:70]}")
    print(f"    of which already measured: {len(measured['newly_declarable_that_are_already_measured'])}"
          f", and ADMITTED: {measured['of_those_measured_how_many_were_admitted']}")
    print(f"\n  {record['alignment_id'][:62]}")
    print(f"  -> {RECORD.relative_to(REPO)}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
