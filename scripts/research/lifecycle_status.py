"""Where every Hypothesis stands, derived from the sealed records. Writes nothing.

    python3.11 -B scripts/research/lifecycle_status.py
    python3.11 -B scripts/research/lifecycle_status.py --as-of 2027-10-06
    python3.11 -B scripts/research/lifecycle_status.py --json

The re-measurement cadence is read from config/lifecycle.json, which the Director can
edit; nothing in the code fixes it.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tramitago_quant_core.research.lifecycle import derive_status, load_config, render

ARTIFACTS = REPO / "artifacts" / "research"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", help="report as of this date (default: today)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--config", default=str(REPO / "config" / "lifecycle.json"))
    args = parser.parse_args()
    config = load_config(args.config)
    report = derive_status(
        admissions=ARTIFACTS / "admissions.json", hypotheses=ARTIFACTS / "hypotheses.json",
        families=ARTIFACTS / "families.json", verdicts=ARTIFACTS / "family-verdicts.json",
        as_of=date.fromisoformat(args.as_of) if args.as_of else date.today(),
        remeasure_months=config["remeasure_every_months"])
    print(json.dumps(report, indent=2) if args.json else render(report))


if __name__ == "__main__":
    main()
