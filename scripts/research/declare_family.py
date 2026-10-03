"""Declare a mechanism family: every member answers the seven questions, then the
definition and the joint rule are sealed. No market data is read.

    python3.11 -B scripts/research/declare_family.py scripts/research/families/covered-call-equity.json

WHAT IT DOES, IN THIS ORDER, and the order is the point:
  1. validates the family and every undeclared member against the pre-declaration
     contract with a placeholder identity, BEFORE anything is written;
  2. constitutes a Hypothesis and a pre-declaration for each member that has none,
     writing the identity back into the family file;
  3. seals the definition (monitor, rule, members) so that run_family.py refuses any
     family that no longer matches it.

Members already declared (XYLD) are left as they are. Adding a member later is a new
sealed definition: it can only make the joint rule harder.
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tramitago_quant_core.governance.evidence_host import require_evidence_host
from tramitago_quant_core.research.hypothesis import constitute_hypothesis
from tramitago_quant_core.research.mechanism_family import (
    validate_family, pre_declaration_answers, seal_family, definition_digest)
from tramitago_quant_core.research.pre_declaration import (
    pre_declaration, constitute_pre_declaration)

ARTIFACTS = REPO / "artifacts" / "research"
HYPOTHESES = ARTIFACTS / "hypotheses.json"
PRE_DECLARATIONS = ARTIFACTS / "pre-declarations.json"
FAMILIES = ARTIFACTS / "families.json"

GROUND = {
    "name": "THE VARIABLE MUST WORK WHEREVER ITS MECHANISM DOES, OR IT DOES NOT WORK",
    "statement": (
        "A monitor derived from a mechanism has no reason to pass in one market and fail in "
        "another that shares the mechanism. Testing it in a second market with the monitor "
        "FIXED, and requiring it to hold in every member, makes the test harder with each "
        "member added. Passing in one member after failing in another is what chance "
        "produces, and the rule refuses to read it as evidence."),
    "why_it_is_independent": (
        "It is a statement about how evidence accumulates, true whichever member passes. It "
        "was reached after XYLD failed R3, but it binds QYLD no more kindly than XYLD: a pass "
        "in QYLD alone does not clear either."),
    "provenance": (
        "Proposed by Claude as a replication, corrected by the Director, who observed that "
        "changing the stock until the monitor passes fits the asset to the function, and that "
        "tuning the function to the asset is the same fault from the other side. The weakness "
        "is that XYLD was measured first and its result is known, so the rule was written "
        "knowing one member failed; it is sealed before any other member is measured."),
}


def _git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True,
                          check=True).stdout.strip()


def _description(family, member, draw):
    mechanism = family["mechanism"]
    period = member["period"]
    caveats = mechanism["caveats"] + member.get("caveats", [])
    return (
        f"LEVEL CLAIM, one leg, zero turnover by construction.\n\n"
        f"{member['symbol']} ({member['name']}) held CONTINUOUSLY from {period['start_utc'][:10]} "
        f"to {period['end_exclusive_utc'][:10]}: one entry, one exit, no signal, no "
        f"rebalancing and no shorting. The net return of that position, after execution "
        f"costs, is POSITIVE and its figures clear the admission thresholds of DOC-011 "
        f"section 8.\n\n"
        f"MECHANISM: {mechanism['name']}. It is the second or later member of the family "
        f"'{family['slug']}', whose monitor was fixed before this member was declared and "
        f"is judged jointly under the rule: {family['joint_rule']['statement']}\n\n"
        f"{member['notes']}\n\nPre-declared caveats:\n- "
        + "\n- ".join(c.format(draw=draw) for c in caveats))


def main(path):
    require_evidence_host(REPO)
    path = Path(path)
    family = validate_family(json.loads(path.read_text(encoding="utf-8")))
    revision = _git("rev-parse", "HEAD")
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")

    pending = [m for m in family["members"] if not m.get("hypothesis_id")]
    # VALIDATE EVERYTHING FIRST, with a placeholder identity, before any write.
    for member in pending:
        pre_declaration(hypothesis_id="HYPOTHESIS|validation-only-nothing-is-sealed",
                        **pre_declaration_answers(family, member))

    registered = {item["hypothesis_id"] for item in
                  json.loads(HYPOTHESES.read_bytes())["hypotheses"]}
    declared = []
    for member in pending:
        draw = len(registered) + 1
        answers = pre_declaration_answers(family, member)
        acceptance = {
            "metric": f"net_sharpe({member['symbol']} held continuously, net of execution cost)",
            "comparison": "GE", "threshold": "0.50", "expected_direction": "INCREASE"}
        record = constitute_hypothesis(
            HYPOTHESES, description=_description(family, member, draw),
            target_metric=acceptance["metric"], expected_direction="INCREASE",
            constraints={"period": member["period"], "universe": [member["symbol"]],
                         "variables": ["close", "forward_return_1d", "sma_close_3"]},
            acceptance_criterion=acceptance, creation_timestamp=now, status="PROPOSED",
            created_by="Oscar Carmenate Rodriguez",
            provenance=family["mechanism"]["provenance"],
            code_revision=revision, system_version="0.1.0")
        registered.add(record["hypothesis_id"])
        sealed = constitute_pre_declaration(
            PRE_DECLARATIONS, pre_declaration(hypothesis_id=record["hypothesis_id"], **answers),
            declared_at=now, code_revision=revision)
        member["hypothesis_id"] = record["hypothesis_id"]
        declared.append((member["symbol"], record["hypothesis_id"], sealed))
        # Written back at once, so a failure on the next member loses nothing.
        path.write_text(json.dumps(family, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")

    seal, is_new = seal_family(
        FAMILIES, family, sealed_at=now, code_revision=revision,
        authorised_by="Oscar Carmenate Rodriguez, Director del Proyecto", ground=GROUND)

    print("=" * 78)
    print(f"FAMILY {family['slug']} -- no market data has been read")
    print("=" * 78)
    for symbol, hypothesis_id, sealed in declared:
        effect = sealed["required_effect"]
        print(f"  declared {symbol}  {hypothesis_id[:30]}  effect {effect['plausible_low']} to "
              f"{effect['plausible_high']} vs bar {effect['required_point_sharpe']} "
              f"{effect['position_against_bar']}")
    for member in family["members"]:
        if all(member["symbol"] != d[0] for d in declared):
            print(f"  already declared {member['symbol']}  {member['hypothesis_id'][:30]}")
    print(f"\n  joint rule   {family['joint_rule']['name']}")
    print(f"  definition   {definition_digest(family)[:40]}")
    print(f"  {'sealed' if is_new else 'already sealed'}  {seal['family_id'][:56]}")
    print(f"  -> {FAMILIES.relative_to(REPO)}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
