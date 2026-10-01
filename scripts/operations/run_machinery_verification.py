"""Continuous PAPER run, declared as MACHINERY VERIFICATION.

WHAT THIS IS, and the declaration matters more than the code. The FORWARD_PAPER
loop operates SMA3, whose sealed Disposition is REJECTED. Running it is NOT a
bet and its output is NOT performance evidence. It is a verification that the
execution path survives real time, real data and real failures -- the thing T9
established over seven days, kept running indefinitely.

Zero of 38 Hypotheses have been validated, so there is no edge to operate. The
purpose split adopted 2026-09-30 exists precisely for this case: a
machinery-verification run may ride a rejected signal, and can never reach LIVE.

WHAT THE RECORD MUST NEVER BE READ AS. A P&L from a rejected signal means
nothing. Any apparent profit is the market moving, not the strategy working, and
treating it otherwise would be the exact error the platform exists to prevent.
The declaration is written into every observation so a later reader cannot
separate the number from the caveat.

WHAT IT IS GENUINELY EVIDENCE OF: whether the loop runs unattended; whether it
recovers from transport failures; whether its lease, ledger and receipts stay
consistent across days; whether anything drifts. That is operational evidence,
and it is the only kind available while no Hypothesis survives.

Usage -- one activation per invocation, so a scheduler owns the cadence:

    python3.11 -B scripts/operations/run_machinery_verification.py --data-dir <path>

Install it on a timer with deploy/forward-paper-activation.{service,timer} or the
crontab example, pointing at this script rather than the bare entrypoint.
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pipeline as p

DECLARATION_SCHEMA_VERSION = "1"
RECORD_DIRNAME = "machinery-verification"


def _declaration(code_revision):
    """Sealed statement of what this run is and is not. Identity re-derives."""
    content = {
        "schema_version": DECLARATION_SCHEMA_VERSION,
        "purpose": p.COMMITTEE_PURPOSE_MACHINERY,
        "signal": "SMA3_LONG_ONLY",
        "signal_disposition": "REJECTED",
        "validated_hypotheses_available": 0,
        "verifies": [
            "the activation loop runs unattended across days",
            "transport failures are recovered rather than silently swallowed",
            "lease, ledger and receipts stay mutually consistent",
            "nothing drifts between the configuration and what executes",
        ],
        "is_not_evidence_of": [
            "that the strategy works -- its Disposition is REJECTED",
            "profit or loss of any kind; a P&L from a rejected signal is the "
            "market moving, not the strategy working",
            "readiness to allocate capital, which requires the admission gates",
        ],
        "can_reach_live": False,
        "code_revision": code_revision,
    }
    return {**content,
            "declaration_id": "MACHINERY_VERIFICATION|" + p.digest(p.encoded(content))}


def verified_declaration(declaration):
    content = {k: v for k, v in declaration.items() if k != "declaration_id"}
    if declaration.get("declaration_id") != "MACHINERY_VERIFICATION|" + p.digest(
            p.encoded(content)):
        raise ValueError("Machinery verification declaration identity does not match")
    if declaration.get("can_reach_live") is not False:
        raise ValueError("A machinery verification declaration must not claim LIVE reach")
    return declaration


def _code_revision():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _observe(record_dir, declaration, result, error, now):
    """Append one observation. Failures are recorded, never swallowed: a loop
    that only logs its successes cannot demonstrate it survives failure."""
    observation = {
        "observed_at": now,
        "declaration_id": declaration["declaration_id"],
        "purpose": declaration["purpose"],
        "signal_disposition": declaration["signal_disposition"],
        "outcome": result.get("status") if result else "ERROR",
        "error": error,
        "detail": {k: result.get(k) for k in ("activation", "reason", "owner_id")}
        if result else None,
        "not_evidence_of_performance": True,
    }
    path = record_dir / "observations.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(observation, sort_keys=True) + "\n")
    return observation


def _summary(record_dir):
    """Longitudinal view, the T9-equivalent: distinct days and outcome counts."""
    path = record_dir / "observations.jsonl"
    if not path.exists():
        return {"observations": 0}
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]
    days = {row["observed_at"][:10] for row in rows}
    outcomes = {}
    for row in rows:
        outcomes[row["outcome"]] = outcomes.get(row["outcome"], 0) + 1
    errors = [r for r in rows if r.get("error")]
    return {
        "observations": len(rows),
        "distinct_days": len(days),
        "first": min(days) if days else None,
        "last": max(days) if days else None,
        "outcomes": outcomes,
        "errors": len(errors),
        "last_error": errors[-1]["error"] if errors else None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    record_dir = data_dir / RECORD_DIRNAME
    record_dir.mkdir(parents=True, exist_ok=True)

    declaration_path = record_dir / "declaration.json"
    if declaration_path.exists():
        declaration = verified_declaration(json.loads(declaration_path.read_bytes()))
    else:
        declaration = _declaration(_code_revision())
        declaration_path.write_bytes(p.encoded(declaration))

    if args.summary_only:
        print(json.dumps({"declaration_id": declaration["declaration_id"],
                          **_summary(record_dir)}, indent=1))
        return 0

    now = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    result, error = None, None
    try:
        result = p.forward_paper_activation_entrypoint(data_dir, now_utc=now)
    except Exception as failure:                       # recorded, never swallowed
        error = f"{type(failure).__name__}: {failure}"

    observation = _observe(record_dir, declaration, result, error, now)
    summary = _summary(record_dir)
    print(json.dumps({"observation": observation, "summary": summary}, sort_keys=True))
    # Exit 0 on a recorded outcome, including a recorded failure: the scheduler
    # should keep running. A non-zero exit would stop the very longitudinal
    # observation this exists to produce.
    return 0


if __name__ == "__main__":
    sys.exit(main())
