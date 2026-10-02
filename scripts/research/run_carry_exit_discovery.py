"""Bounded Discovery over carry EXIT RULES -- and a worked example of the tool.

THE QUESTION, stated so it can be answered cheaply: is there any exit rule on
this funding whose "exit" state occurs often enough to be comparable at all?

That is the blockage, not the premium. Hypothesis #37 classifies by whether
funding is at or above ZERO, and the inverted group holds 11 of 366 days in 2024
with one walk-forward fold containing none, so the validation can only ever
answer INSUFFICIENT_EVIDENCE. Asking the question as a SEARCH costs minutes;
asking it one hypothesis at a time costs a sealed validation cycle each, which
is how four looks at this family got spent in a single day without their
multiplicity ever being recorded together.

THE KNOWN-BROKEN CANDIDATE IS IN THE SPACE ON PURPOSE. CARRY_FUNDING_THRESHOLD
is #37's own design, and watching the representativeness filter refuse it for
free -- before anything expensive runs -- is the point of having the filter.

NO NETWORK. Both legs come from the already-sealed carry dataset, and the
holdout year is simply never loaded.

    python3.11 -B scripts/research/run_carry_exit_discovery.py
"""

import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pipeline as p
from tramitago_quant_core.governance.evidence_host import require_evidence_host
from tramitago_quant_core.research.discovery import (
    constitute_discovery_space, scan_discovery_space, rank_observations,
    constitute_discovery_scan, finding_from_scan, discovery_rows_digest,
)

DISCOVERY_WINDOW = {"start_utc": "2024-01-01T00:00:00Z",
                    "end_exclusive_utc": "2025-01-01T00:00:00Z"}
HOLDOUT_WINDOW = {"start_utc": "2025-01-01T00:00:00Z",
                  "end_exclusive_utc": "2026-01-01T00:00:00Z"}
DATASET = REPO / "artifacts" / "research" / "datasets" / "carry_exit_btc_2024"
PAYMENTS_PER_DAY = 24
WINDOWS = (3, 5, 7, 10, 14, 20, 30)

MINIMUM_SUPPORT = 30
MINIMUM_MINORITY_STATE_FREQUENCY = "0.20"

JUSTIFICATION = (
    "Carry EXIT RULES, asked as the question that actually blocks them: does any rule's exit "
    "state occur often enough to be comparable. CARRY_FUNDING_THRESHOLD is #37's own absolute "
    "threshold at zero, included so the representativeness filter is seen refusing the design "
    "already on record as degenerate rather than being told about it. CARRY_FUNDING_SURGE over "
    "seven trailing windows is the relative-threshold fix funding_rate_surge_strategy already "
    "proved for the funding SIGNAL and that was never carried across to the exit rule. Windows "
    "3 to 30 span a few days to a month, declared complete: no window outside this set may be "
    "added to THIS space, and a wider search is a new space with its own justification. "
    "Discovery reads 2024 only; 2025 is the holdout and is not loaded at all."
)

ARTIFACTS = REPO / "artifacts" / "discovery"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _code_revision():
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                          text=True, check=True).stdout.strip()


def _rows():
    """Sealed rows, filtered to the declared discovery window.

    The warmup rows the dataset carries BEFORE 2024 are dropped rather than used:
    a scan reads its declared window and nothing else, so a long trailing window
    buys its warmup out of the window's own first days. That shows up honestly in
    the support counts instead of being hidden by reaching outside.
    """
    start = p.epoch(DISCOVERY_WINDOW["start_utc"])
    end = p.epoch(DISCOVERY_WINDOW["end_exclusive_utc"])
    rows = []
    for row in csv.DictReader((DATASET / "dataset.csv").open(encoding="utf-8")):
        if not start <= p.epoch(row["timestamp"]) < end:
            continue
        rows.append({"timestamp": row["timestamp"], "close": float(row["close"]),
                     "perp_close": float(row["perp_close"]),
                     "funding_rate": float(row["funding_rate"])})
    return rows


def _name(observation):
    family = observation["strategy_id"].replace("CARRY_FUNDING_", "")
    return f"{family}({observation['parameters'].get('window', 0)})"


def main():
    require_evidence_host(REPO)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    candidates = [
        {"strategy_id": "CARRY_FUNDING_THRESHOLD",
         "parameters": {"funding_variable": "funding_rate", "perpetual_variable": "perp_close",
                        "threshold": "0", "payments_per_period": PAYMENTS_PER_DAY}},
    ] + [
        {"strategy_id": "CARRY_FUNDING_SURGE",
         "parameters": {"window": window, "funding_variable": "funding_rate",
                        "perpetual_variable": "perp_close",
                        "payments_per_period": PAYMENTS_PER_DAY}}
        for window in WINDOWS
    ]

    space = constitute_discovery_space(
        ARTIFACTS / "discovery-spaces.json", justification=JUSTIFICATION,
        candidates=candidates, discovery_window=DISCOVERY_WINDOW,
        holdout_window=HOLDOUT_WINDOW, minimum_support=MINIMUM_SUPPORT,
        minimum_minority_state_frequency=MINIMUM_MINORITY_STATE_FREQUENCY,
        declared_by="carry-exit-discovery-runner", declared_at=_now())
    print(f"space        {space['space_id']}")
    print(f"candidates   {len(candidates)}  (1 absolute + {len(WINDOWS)} relative windows)")
    print(f"holdout      {HOLDOUT_WINDOW['start_utc'][:10]} .. "
          f"{HOLDOUT_WINDOW['end_exclusive_utc'][:10]}  (not loaded)")

    rows = _rows()
    print(f"rows         {len(rows)} from the sealed {DATASET.name} dataset")

    observations = scan_discovery_space(space, rows)
    scan = constitute_discovery_scan(
        ARTIFACTS / "discovery-scans.json", space=space, observations=observations,
        rows_digest=discovery_rows_digest(rows), scanned_at=_now(),
        scan_code_revision=_code_revision())
    print(f"scan         {scan['scan_id']}")
    print(f"summary      {json.dumps(scan['summary'], sort_keys=True)}")

    print("\n  candidate                      minority  support       effect  examinable")
    for item in observations:
        minority = ("     n/a" if item["minority_state_frequency"] is None
                    else f"{float(item['minority_state_frequency']):8.3f}")
        effect = "         n/a" if item["effect"] is None else f"{float(item['effect']):+.9f}"
        print(f"  {_name(item):<28}  {minority}  {item['upper_count']:>3}/{item['lower_count']:<4} "
              f"{effect}  {'yes' if item['examinable'] else 'NO'}")

    for item in observations:
        if not item["examinable"]:
            print(f"\n  REFUSED {_name(item)}")
            print(f"          {item['reason']}")

    if not rank_observations(observations):
        print("\nNo candidate is examinable, and that IS the answer: on this funding, no exit "
              "rule in this space has a state frequent enough to compare. Nothing is promoted, "
              "and no validation cycle was spent finding out.")
        return 0

    finding = finding_from_scan(ARTIFACTS / "findings.json", scan=scan, space=space,
                                created_by="carry-exit-discovery-runner", created_at=_now())
    print(f"\nfinding      {finding['finding_id']}")
    print(f"observation  {finding['observation']}")
    print(f"\nThe holdout was not read. A Hypothesis built from this Finding must be corrected "
          f"for {scan['summary']['candidates_examined']} candidates, not one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
