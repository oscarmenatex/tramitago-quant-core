"""Read the machinery-verification record and say whether the loop is alive.

The first thing the platform watches by itself. Until now `evaluate_monitor`
existed only as an import -- a verdict nobody consumed -- while a loop ticked
every five minutes with nothing reading its output.

It supervises THE LOOP, not a strategy. Nothing is admitted to operate, so
anything that sized or traded would be anticipatory; watching the one thing
actually running is not.

Run it on its own timer, deliberately NOT inside the tick it supervises: a
supervisor that only runs when the loop runs cannot report that the loop
stopped.

    python3.11 -B scripts/operations/supervise_machinery_verification.py \
        --data-dir /var/lib/tramitago-quant-core/forward-paper
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pipeline as p

RECORD_DIRNAME = "machinery-verification"
DEFAULT_CADENCE_SECONDS = 300          # the installed timer


def supervision_contract(cadence_seconds):
    """Degraded when the tick success rate falls below a floor and stays there.

    Thresholds are declared here rather than discovered: 0.80 to degrade, 0.95 to
    recover. The gap is hysteresis -- without it a rate sitting on the boundary
    would flap between states on every tick.
    """
    return p.monitoring_contract(
        variable="tick_success_rate",
        observation_period_seconds=cadence_seconds,
        degradation_threshold="0.80", degrades_when=p.DEGRADES_BELOW,
        confirmation_periods=3, action=p.ACTION_SUSPEND,
        re_entry_threshold="0.95",
        source="machinery-verification observations.jsonl, appended by each tick")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--cadence-seconds", type=int, default=DEFAULT_CADENCE_SECONDS)
    parser.add_argument("--missed-ticks-tolerated", type=int, default=3)
    args = parser.parse_args()

    record_dir = Path(args.data_dir) / RECORD_DIRNAME
    contract = supervision_contract(args.cadence_seconds)
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")

    previous_path = record_dir / "supervision.json"
    previous = (json.loads(previous_path.read_bytes()).get("monitor_state", p.STATE_HEALTHY)
                if previous_path.exists() else p.STATE_HEALTHY)

    observations = p.read_observations(record_dir / "observations.jsonl")
    verdict = p.supervise(
        contract=contract, observations=observations, now_utc=now,
        expected_cadence_seconds=args.cadence_seconds,
        missed_ticks_tolerated=args.missed_ticks_tolerated, state=previous)

    record = {"supervised_at": now, "contract_id": contract["contract_id"], **verdict}
    record_dir.mkdir(parents=True, exist_ok=True)
    previous_path.write_bytes(p.encoded(record))
    print(json.dumps(record, sort_keys=True))

    # Exit 0 whatever the verdict. A non-zero exit would make the scheduler treat
    # a correctly reported DEGRADED as a crash of the supervisor itself, which is
    # the opposite of what a supervisor is for.
    return 0


if __name__ == "__main__":
    sys.exit(main())
