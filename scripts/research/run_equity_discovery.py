"""Bounded Discovery over US equities -- the one axis with genuinely different data
that has never received a search with its multiplicity sealed.

WHY HERE. Seven equity Hypotheses exist and every one was invented by hand, one
per validation cycle, with no joint multiplicity ever recorded. One of them
validated on 2024 and was refuted out of sample within days. The retrospective
audit put the number on the whole comparative search: 37 searches, 2 validated
against 6.77 expected by chance. Inventing an eighth by hand would repeat exactly
what that audit condemned.

THE WINDOWS, AND WHAT EACH HAS ALREADY SEEN. The sealed SPY datasets cover three
years and they are NOT equally clean:

    2022   examined by MOMENTUM_CROSSOVER(10) only, as an out-of-sample test
    2023   examined by MOMENTUM_CROSSOVER(10) only, as an out-of-sample test
    2024   examined by five hand-made Hypotheses -- BURNED, used by neither

So discovery reads 2022 and the holdout is 2023, and MOMENTUM_CROSSOVER is
excluded ENTIRELY rather than merely its lookback of 10. Its behaviour on both
windows is already on record, and including any member of a family whose sign on
the holdout I already know would be searching a space I have partial knowledge
of -- which is the one contamination a sealed space cannot detect, because it
happens before the space is written.

THE HOLDOUT IS 360 DAYS AND THAT IS NOT TIDINESS. A search of fourteen needs nine
folds to be passable at all, the walk-forward needs the fold count to divide the
period exactly, and 360 = 9 x 40 satisfies both. A calendar year would not: 365 =
5 x 73 admits only five folds, and five folds support a corrected search of size
ONE. Declaring the period from the arithmetic, before the scan, is the discipline
the previous attempt lacked when it sealed a verdict no outcome could have
changed.

NO NETWORK AND NO CREDENTIALS. The rows come from datasets already sealed by
earlier equity work, and the holdout year is simply never loaded.

    python3.11 -B scripts/research/run_equity_discovery.py
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
from tramitago_quant_core.research.walk_forward import minimum_folds_for_batch

DISCOVERY_WINDOW = {"start_utc": "2022-01-01T00:00:00Z",
                    "end_exclusive_utc": "2023-01-01T00:00:00Z"}
HOLDOUT_WINDOW = {"start_utc": "2023-01-02T00:00:00Z",
                  "end_exclusive_utc": "2023-12-28T00:00:00Z"}     # 360 days = 9 x 40
DATASET = REPO / "artifacts" / "research" / "datasets" / "spy_mom10_oos_2022"
SYMBOL = "SPY"

# Four families, every one needing nothing but OHLCV, and none of them examined on
# either window. Windows are the classic trend lengths rather than a grid tuned to
# anything: Brock, Lakonishok & LeBaron (1992) tested 1, 2, 5, 50, 150 and 200.
SMA_WINDOWS = (5, 10, 20, 50, 100, 200)
VOLUME_WINDOWS = (10, 20, 50)
RANGE_WINDOWS = (10, 20, 50)
CONFIRMATION_PAIRS = ((10, 20), (20, 50))

MINIMUM_SUPPORT = 30
MINIMUM_MINORITY_STATE_FREQUENCY = "0.20"

JUSTIFICATION = (
    "US equities, the one axis with genuinely different data that has never received a "
    "search with its multiplicity sealed: all seven existing equity Hypotheses were invented "
    "by hand, one per validation cycle, and one validated on 2024 then was refuted out of "
    "sample. Four families needing only OHLCV -- SMA_CROSSOVER, VOLUME_SURGE, INTRADAY_RANGE "
    "and SMA_VOLUME_CONFIRMATION -- over the classic trend lengths rather than a tuned grid. "
    "MOMENTUM_CROSSOVER is excluded ENTIRELY, not merely its lookback of 10: its behaviour on "
    "both the discovery window and the holdout is already on record, and searching a family "
    "whose sign on the holdout is already known is contamination a sealed space cannot "
    "detect. Discovery reads 2022; the holdout is 2023 and is not loaded. 2024 is excluded "
    "from both, having been examined by five hand-made Hypotheses. The holdout is 360 days "
    "because a search of fourteen needs nine folds to be passable and nine must divide the "
    "period; a calendar year admits only five. N=14, declared complete."
)

ARTIFACTS = REPO / "artifacts" / "discovery"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _code_revision():
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                          text=True, check=True).stdout.strip()


def _rows():
    """Sealed SPY rows, filtered to the declared discovery window.

    Equities trade on a calendar, so a year is roughly 250 rows rather than 365.
    A long trailing window therefore buys its warmup out of the window's own first
    sessions, which shows up honestly in the support counts.
    """
    start = p.epoch(DISCOVERY_WINDOW["start_utc"])
    end = p.epoch(DISCOVERY_WINDOW["end_exclusive_utc"])
    rows = []
    for row in csv.DictReader((DATASET / "dataset.csv").open(encoding="utf-8")):
        if not start <= p.epoch(row["timestamp"]) < end:
            continue
        rows.append({"timestamp": row["timestamp"], "close": float(row["close"]),
                     "open": float(row["open"]), "high": float(row["high"]),
                     "low": float(row["low"]), "volume": float(row["volume"])})
    return rows


def _name(observation):
    values = tuple(observation["parameters"].values())
    return f"{observation['strategy_id']}{values}"


def main():
    require_evidence_host(REPO)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    candidates = (
        [{"strategy_id": "SMA_CROSSOVER", "parameters": {"window": w}} for w in SMA_WINDOWS]
        + [{"strategy_id": "VOLUME_SURGE", "parameters": {"window": w}} for w in VOLUME_WINDOWS]
        + [{"strategy_id": "INTRADAY_RANGE", "parameters": {"window": w}} for w in RANGE_WINDOWS]
        + [{"strategy_id": "SMA_VOLUME_CONFIRMATION",
            "parameters": {"sma_window": sma, "volume_window": vol}}
           for sma, vol in CONFIRMATION_PAIRS]
    )
    folds = minimum_folds_for_batch(len(candidates))
    span = ((p.epoch(HOLDOUT_WINDOW["end_exclusive_utc"])
             - p.epoch(HOLDOUT_WINDOW["start_utc"])) // 86400)
    if span % folds:
        raise SystemExit(f"Holdout of {span} days does not divide into {folds} folds")

    space = constitute_discovery_space(
        ARTIFACTS / "discovery-spaces.json", justification=JUSTIFICATION,
        candidates=candidates, discovery_window=DISCOVERY_WINDOW,
        holdout_window=HOLDOUT_WINDOW, minimum_support=MINIMUM_SUPPORT,
        minimum_minority_state_frequency=MINIMUM_MINORITY_STATE_FREQUENCY,
        declared_by="equity-discovery-runner", declared_at=_now())
    print(f"space        {space['space_id']}")
    print(f"candidates   {len(candidates)}  ->  {folds} folds, alpha "
          f"{0.05 / len(candidates):.5f}, smallest attainable p {0.5 ** folds:.5f}")
    print(f"holdout      {HOLDOUT_WINDOW['start_utc'][:10]} .. "
          f"{HOLDOUT_WINDOW['end_exclusive_utc'][:10]}  ({span} days = {folds} x "
          f"{span // folds}, not loaded)")

    rows = _rows()
    print(f"rows         {len(rows)} sessions of {SYMBOL} from the sealed {DATASET.name} dataset")

    observations = scan_discovery_space(space, rows)
    scan = constitute_discovery_scan(
        ARTIFACTS / "discovery-scans.json", space=space, observations=observations,
        rows_digest=discovery_rows_digest(rows), scanned_at=_now(),
        scan_code_revision=_code_revision())
    print(f"scan         {scan['scan_id']}")
    print(f"summary      {json.dumps(scan['summary'], sort_keys=True)}")

    print("\n  candidate                           minority  support       effect  examinable")
    for item in observations:
        minority = ("     n/a" if item["minority_state_frequency"] is None
                    else f"{float(item['minority_state_frequency']):8.3f}")
        effect = "         n/a" if item["effect"] is None else f"{float(item['effect']):+.9f}"
        print(f"  {_name(item):<33}  {minority}  {item['upper_count']:>3}/{item['lower_count']:<4} "
              f"{effect}  {'yes' if item['examinable'] else 'NO'}")
    for item in observations:
        if not item["examinable"]:
            print(f"\n  REFUSED {_name(item)}\n          {item['reason']}")

    if not rank_observations(observations):
        print("\nNo candidate is examinable, and that IS the answer: nothing in this space has "
              "a state frequent enough to compare on this window. No cycle was spent.")
        return 0

    finding = finding_from_scan(ARTIFACTS / "findings.json", scan=scan, space=space,
                                created_by="equity-discovery-runner", created_at=_now())
    print(f"\nfinding      {finding['finding_id']}")
    print(f"observation  {finding['observation']}")
    print(f"\nThe holdout was not loaded. Promoting this needs a fresh sealed dataset over "
          f"{HOLDOUT_WINDOW['start_utc'][:10]}..{HOLDOUT_WINDOW['end_exclusive_utc'][:10]}, "
          f"which needs Alpaca credentials, and a validation corrected for "
          f"{len(candidates)} over {folds} folds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
