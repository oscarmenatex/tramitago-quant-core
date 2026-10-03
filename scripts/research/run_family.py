"""Measure every member of a mechanism family and judge the family jointly.

    python3.11 -B scripts/research/run_family.py scripts/research/families/covered-call-equity.json

RUN THIS YOURSELF, OSCAR -- it needs the same environment variables as run_premium.py
and the same VM for FRED. Credentials never reach me.

It does nothing run_premium.py does not already do for one spec. It derives each
member's spec from the family, runs it through the same engine, reads the monitor link
out of each result and applies the joint rule. A member that was already measured and
sealed (XYLD) is reproduced from its sealed artifacts without sealing it again.

THE DEFINITION MUST BE SEALED FIRST. A family whose monitor, rule or members no longer
match a sealed record is refused, so nothing can be adjusted after a result is seen.
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import subprocess

from run_premium import RealIO, PATHS, _now, _print
from tramitago_quant_core.governance.evidence_host import require_evidence_host
from tramitago_quant_core.research.mechanism_family import (
    validate_family, member_spec, judge_family, sealed_family, seal_verdict)
from tramitago_quant_core.research.premium_engine import validate_spec
from tramitago_quant_core.research.premium_runner import run_spec

ARTIFACTS = REPO / "artifacts" / "research"
FAMILIES = ARTIFACTS / "families.json"
VERDICTS = ARTIFACTS / "family-verdicts.json"


def _outcome(result, underlying):
    monitor = result.get("monitor")
    if result.get("void") or not monitor:
        return None
    return {"link_outcome": monitor["link_outcome"], "link_met": monitor["link_met"],
            "link_usable": monitor["link_usable"], "link_ratio": monitor["link_ratio"],
            "control": (result.get("controls") or {}).get(underlying)}


def main():
    require_evidence_host(REPO)
    path = sys.argv[1]
    family = validate_family(json.loads(Path(path).read_text(encoding="utf-8")))
    record = sealed_family(FAMILIES, family)
    if any(not member.get("hypothesis_id") for member in family["members"]):
        raise SystemExit("A member has no Hypothesis yet; run declare_family.py first.")
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, check=True).stdout.strip()
    now, io = _now(), RealIO()

    outcomes = {}
    for member in family["members"]:
        spec = member_spec(family, member)
        validate_spec(spec)
        # A member already sealed is reproduced, never sealed a second time.
        claim_file = ARTIFACTS / f"level-claim-validations-{spec['slug']}.json"
        result = run_spec(spec, io=io, paths=PATHS, now=now, code_revision=revision,
                          seal=not claim_file.exists())
        _print(result)
        outcomes[member["symbol"]] = _outcome(result, member["underlying"])

    verdict = judge_family(family["members"], outcomes)
    sealed, is_new = seal_verdict(VERDICTS, record, verdict, decided_at=now,
                                  code_revision=revision)
    print("=" * 78)
    print(f"FAMILY VERDICT  {verdict['verdict']}")
    print(f"  {verdict['reason']}")
    for row in verdict["members"]:
        ratio = "-" if row["ratio"] is None else f"{row['ratio']:.4f}"
        print(f"  {row['symbol']:6s} link {row['met']} of {row['usable']} = {ratio}  "
              f"{row['link_outcome']}")
    print(f"  {'sealed' if is_new else 'already sealed'}  {sealed['verdict_id'][:56]}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
