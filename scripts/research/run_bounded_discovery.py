"""First real Bounded Discovery scan -- and it is deliberately aimed at an axis
this project has already measured as dead.

WHY SCAN SOMETHING ALREADY KNOWN TO BE EMPTY. Daily crypto OHLCV was measured
across every sealed artifact and found to carry no continuous predictive
information; twenty-eight Hypotheses on that axis produced zero validations.
Pointing a brand-new search mechanism at a space where the answer is already
known is the only honest way to find out what the mechanism does. A scan of
seventeen candidates WILL return a best one with a positive effect -- that is
what best-of-N does to noise -- and the question this run answers is not "is
there an edge" but "does the machinery present that number as a discovery or as
what it is".

WHAT THIS RUN DOES NOT DO: touch the holdout. The scan reads 2024 only. The
2025 holdout stays unread, which is the entire reason a wide search is
defensible, and spending it here -- outside the formal Hypothesis apparatus,
to satisfy curiosity about the best candidate -- would destroy the one thing
this mechanism exists to protect.

ORDER MATTERS AND THE CODE ENFORCES IT: the space is constituted and sealed
BEFORE a single candle is fetched. Declaring a search space after seeing the
data is the failure this whole layer is built against, so it cannot be left to
the reader's good intentions about where to put a function call.

Needs no credentials -- Coinbase's candle endpoint is public.

    python3.11 -B scripts/research/run_bounded_discovery.py
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pipeline as p
from tramitago_quant_core.data.acquisition import (
    normalize, validate, _coinbase_public_request,
)
from tramitago_quant_core.research.discovery import (
    constitute_discovery_space, scan_discovery_space, rank_observations,
    constitute_discovery_scan, finding_from_scan,
)

INSTRUMENT = "BTC-USD"
DISCOVERY_WINDOW = {"start_utc": "2024-01-01T00:00:00Z",
                    "end_exclusive_utc": "2025-01-01T00:00:00Z"}
HOLDOUT_WINDOW = {"start_utc": "2025-01-01T00:00:00Z",
                  "end_exclusive_utc": "2026-01-01T00:00:00Z"}

# A group mean over fewer than thirty observations is not an observation of
# anything, and 0.20 is the minority-state floor: below one day in five, no
# monitor running at a daily cadence could accumulate enough observations of the
# rarer state to confirm a degradation before a drawdown limit is reached. Both
# are declared here, sealed into the space's identity, and therefore cannot be
# relaxed after seeing which candidates they exclude.
MINIMUM_SUPPORT = 30
MINIMUM_MINORITY_STATE_FREQUENCY = "0.20"

JUSTIFICATION = (
    "The four single-instrument Strategy families already expressible in the sealed "
    "Strategy contract on daily OHLCV (SMA_CROSSOVER, MOMENTUM_CROSSOVER, VOLUME_SURGE, "
    "INTRADAY_RANGE), over the same short period lengths for every family so none is "
    "favoured by a wider grid. Windows are capped at 20 so no candidate loses a materially "
    "larger slice of the discovery window to warmup than another. N=17, declared complete: "
    "no family, window or instrument may be added to THIS space afterwards; a wider search "
    "is a new space with its own written justification. The axis is one this project has "
    "already measured as empty, which is why it was chosen first -- a mechanism that "
    "returns an impressive finding here is telling us about itself, not about the market."
)

CANDIDATES = (
    [{"strategy_id": "SMA_CROSSOVER", "parameters": {"window": w}} for w in (3, 5, 7, 10, 20)]
    + [{"strategy_id": "MOMENTUM_CROSSOVER", "parameters": {"lookback": k}}
       for k in (1, 2, 3, 5, 7, 10)]
    + [{"strategy_id": "VOLUME_SURGE", "parameters": {"window": w}} for w in (5, 10, 20)]
    + [{"strategy_id": "INTRADAY_RANGE", "parameters": {"window": w}} for w in (5, 10, 20)]
)

ARTIFACTS = REPO / "artifacts" / "discovery"
MAX_CANDLES_PER_REQUEST = 300


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _code_revision():
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                          text=True, check=True).stdout.strip()


def _fetch_discovery_rows():
    """Coinbase caps a candle response, so the window is fetched in slices and
    re-validated as one series -- the same chunking the sealed Dataset layer
    does, for the same reason."""
    start, end = p.epoch(DISCOVERY_WINDOW["start_utc"]), p.epoch(DISCOVERY_WINDOW["end_exclusive_utc"])
    rows, raw_digests = [], []
    cursor = start
    while cursor < end:
        stop = min(cursor + MAX_CANDLES_PER_REQUEST * 86400, end)
        url = (f"https://api.exchange.coinbase.com/products/{INSTRUMENT}/candles"
               f"?granularity=86400&start={p.iso(cursor)}&end={p.iso(stop)}")
        with urlopen(_coinbase_public_request(url), timeout=30) as response:
            raw = response.read()
        raw_digests.append(p.digest(raw))
        slice_rows, report = normalize(raw, cursor, stop, INSTRUMENT)
        if report["status"] != "PASS" and report["errors"]:
            raise SystemExit(f"Coinbase slice {p.iso(cursor)} failed: {report['errors'][:2]}")
        rows.extend(slice_rows)
        cursor = stop

    rows.sort(key=lambda row: row["timestamp"])
    expected = [p.iso(moment) for moment in range(start, end, 86400)]
    report = validate(rows, {"status": "PASS", "rejected": 0, "errors": [], "warnings": []},
                      expected_timestamps=expected)
    if report["errors"]:
        raise SystemExit(f"Discovery rows failed validation: {report['errors'][:3]}")
    return rows, raw_digests


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    spaces = ARTIFACTS / "discovery-spaces.json"
    scans = ARTIFACTS / "discovery-scans.json"
    findings = ARTIFACTS / "findings.json"

    # SEALED FIRST, FETCHED SECOND. Not a stylistic ordering.
    space = constitute_discovery_space(
        spaces, justification=JUSTIFICATION, candidates=CANDIDATES,
        discovery_window=DISCOVERY_WINDOW, holdout_window=HOLDOUT_WINDOW,
        minimum_support=MINIMUM_SUPPORT,
        minimum_minority_state_frequency=MINIMUM_MINORITY_STATE_FREQUENCY,
        declared_by="bounded-discovery-runner", declared_at=_now())
    print(f"space        {space['space_id']}")
    print(f"candidates   {len(space['candidates'])}")
    print(f"holdout      {HOLDOUT_WINDOW['start_utc']} .. "
          f"{HOLDOUT_WINDOW['end_exclusive_utc']}  (unread)")

    rows, raw_digests = _fetch_discovery_rows()
    print(f"rows         {len(rows)} over {DISCOVERY_WINDOW['start_utc']} .. "
          f"{DISCOVERY_WINDOW['end_exclusive_utc']}")

    observations = scan_discovery_space(space, rows)
    scan = constitute_discovery_scan(scans, space=space, observations=observations,
                                     scanned_at=_now(), scan_code_revision=_code_revision())
    print(f"scan         {scan['scan_id']}")
    print(f"summary      {json.dumps(scan['summary'], sort_keys=True)}")

    print("\n  rank  candidate                       effect        minority  support")
    for rank, item in enumerate(rank_observations(observations)):
        name = f"{item['strategy_id']}{tuple(item['parameters'].values())}"
        print(f"  {rank:>4}  {name:<30}  {float(item['effect']):+.6f}  "
              f"{float(item['minority_state_frequency']):>8.3f}  "
              f"{item['upper_count']}/{item['lower_count']}")
    for item in observations:
        if not item["examinable"]:
            name = f"{item['strategy_id']}{tuple(item['parameters'].values())}"
            print(f"  REFUSED {name:<28}  {item['reason']}")

    finding = finding_from_scan(findings, scan=scan, space=space,
                                created_by="bounded-discovery-runner", created_at=_now())
    print(f"\nfinding      {finding['finding_id']}")
    print(f"observation  {finding['observation']}")
    print(f"raw_sha256   {raw_digests}")
    print("\nThe holdout was not read. A Hypothesis built from this Finding must be "
          f"corrected for {scan['summary']['candidates_examined']} candidates, not one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
