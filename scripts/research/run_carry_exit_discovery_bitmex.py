"""Bounded Discovery over carry exit rules, on BitMEX, with a passable test
declared in advance.

WHY THIS EXISTS. CARRY_FUNDING_SURGE(30) passed 5 of 5 folds on a 2025 holdout
and still came back NOT_VALIDATED, because a search of eight raises the bar to
0.05/8 = 0.00625 while five folds can never produce a p-value below 0.03125. The
test was unpassable by construction. Re-running it with more folds on the SAME
holdout would be choosing the test after watching it fail, so this declares a
different venue, different windows and the fold count the correction requires --
all before any of it is read.

WINDOWS, AND WHY THERE IS A GAP BETWEEN THEM:

  discovery   2017-01-01 .. 2019-01-01   BitMEX XBTUSD, never used by anything
  (skipped)   2019-01-01 .. 2021-07-01   already read by the level claim
  holdout     2021-07-01 .. 2024-01-07   920 days = 8 folds x 115, untouched

2019-2021 is deliberately in neither: the level-claim verdict on the continuously
held carry measured it, and reusing it would quietly reintroduce data this
project has already looked at.

The holdout length is not rounded for tidiness. A search of eight needs eight
folds to be passable, the walk-forward needs the fold count to divide the period
exactly, and 920 is the nearest length satisfying both.

THE CORRECTION IS DECIDED BY THE SPACE, NOT BY THE RESULT. Eight candidates,
eight folds, alpha 0.00625. Those three numbers follow from each other and are
fixed here, before the scan runs.

    python3.11 -B scripts/research/run_carry_exit_discovery_bitmex.py
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
from tramitago_quant_core.governance.evidence_host import require_evidence_host
from tramitago_quant_core.data.acquisition import normalize, _coinbase_public_request
from tramitago_quant_core.data.bitmex_perpetual_price import (
    capture_bitmex_perpetual_price, verified_bitmex_perpetual_price_capture,
)
from tramitago_quant_core.data.bitmex_funding_rate import (
    capture_bitmex_funding_rate, verified_bitmex_funding_rate_capture,
    BITMEX_FUNDING_PAYMENTS_PER_DAY,
)
from tramitago_quant_core.research.discovery import (
    constitute_discovery_space, scan_discovery_space, rank_observations,
    constitute_discovery_scan, finding_from_scan, discovery_rows_digest,
)
from tramitago_quant_core.research.walk_forward import minimum_folds_for_batch

SYMBOL, SPOT = "XBTUSD", "BTC-USD"
DISCOVERY_WINDOW = {"start_utc": "2017-01-01T00:00:00Z",
                    "end_exclusive_utc": "2019-01-01T00:00:00Z"}
HOLDOUT_WINDOW = {"start_utc": "2021-07-01T00:00:00Z",
                  "end_exclusive_utc": "2024-01-07T00:00:00Z"}
WINDOWS = (3, 5, 7, 10, 14, 20, 30)
MINIMUM_SUPPORT = 30
MINIMUM_MINORITY_STATE_FREQUENCY = "0.20"

JUSTIFICATION = (
    "Carry exit rules on BitMEX XBTUSD, re-declared after the Hyperliquid test proved "
    "unpassable by construction: five folds corrected for eight candidates can never produce "
    "a p-value below the 0.00625 bar. Same eight candidates as that search, for comparability "
    "-- CARRY_FUNDING_THRESHOLD at zero, which is #37's design and is expected to be refused "
    "on representativeness, plus CARRY_FUNDING_SURGE over seven trailing windows. Discovery "
    "reads 2017-2019, which nothing has read. The holdout is 2021-07 to 2024-01, also "
    "untouched, and 920 days long so that eight equal folds divide it -- eight being the "
    "fewest a search of eight can be corrected for. 2019 to 2021-07 is excluded from BOTH "
    "because the level-claim verdict on the continuously held carry already measured it. "
    "Declared complete: no window, venue or period may be added to THIS space afterwards."
)

ARTIFACTS = REPO / "artifacts" / "discovery"
MAX_CANDLES_PER_REQUEST = 300


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _code_revision():
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                          text=True, check=True).stdout.strip()


def _spot_closes(window):
    start, end = p.epoch(window["start_utc"]), p.epoch(window["end_exclusive_utc"])
    closes, digests = {}, []
    cursor = start
    while cursor < end:
        stop = min(cursor + MAX_CANDLES_PER_REQUEST * 86400, end)
        url = (f"https://api.exchange.coinbase.com/products/{SPOT}/candles"
               f"?granularity=86400&start={p.iso(cursor)}&end={p.iso(stop)}")
        with urlopen(_coinbase_public_request(url), timeout=30) as response:
            raw = response.read()
        digests.append(p.digest(raw))
        rows, report = normalize(raw, cursor, stop, SPOT)
        if report["errors"]:
            raise SystemExit(f"{SPOT} {p.iso(cursor)}: {report['errors'][:2]}")
        for row in rows:
            closes[row["timestamp"]] = row["close"]
        cursor = stop
    return closes, digests


def _name(observation):
    family = observation["strategy_id"].replace("CARRY_FUNDING_", "")
    return f"{family}({observation['parameters'].get('window', 0)})"


def main():
    require_evidence_host(REPO)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    candidates = [
        {"strategy_id": "CARRY_FUNDING_THRESHOLD",
         "parameters": {"funding_variable": "funding_rate", "perpetual_variable": "perp_close",
                        "threshold": "0",
                        "payments_per_period": BITMEX_FUNDING_PAYMENTS_PER_DAY}},
    ] + [
        {"strategy_id": "CARRY_FUNDING_SURGE",
         "parameters": {"window": window, "funding_variable": "funding_rate",
                        "perpetual_variable": "perp_close",
                        "payments_per_period": BITMEX_FUNDING_PAYMENTS_PER_DAY}}
        for window in WINDOWS
    ]
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
        declared_by="carry-exit-discovery-bitmex-runner", declared_at=_now())
    print(f"space        {space['space_id']}")
    print(f"candidates   {len(candidates)}  ->  {folds} folds, alpha {0.05 / len(candidates):.5f}")
    print(f"holdout      {HOLDOUT_WINDOW['start_utc'][:10]} .. "
          f"{HOLDOUT_WINDOW['end_exclusive_utc'][:10]}  ({span} days = {folds} x {span // folds},"
          f" not loaded)")

    perp, perp_capture, perp_raw = capture_bitmex_perpetual_price(
        SYMBOL, DISCOVERY_WINDOW["start_utc"], DISCOVERY_WINDOW["end_exclusive_utc"],
        _now(), bin_size="1d")
    if verified_bitmex_perpetual_price_capture(perp_raw, perp_capture) != perp:
        raise SystemExit("Perpetual capture does not re-verify")
    funding, funding_capture, funding_raw = capture_bitmex_funding_rate(
        SYMBOL, DISCOVERY_WINDOW["start_utc"], DISCOVERY_WINDOW["end_exclusive_utc"], _now())
    if verified_bitmex_funding_rate_capture(funding_raw, funding_capture) != funding:
        raise SystemExit("Funding capture does not re-verify")
    spot, spot_digests = _spot_closes(DISCOVERY_WINDOW)
    print(f"captured     perp {len(perp)} | funding {len(funding)} | spot {len(spot)} days, "
          f"both BitMEX captures re-verified from stored bytes")

    stamps = sorted(set(spot) & set(perp) & set(funding))
    rows = [{"timestamp": stamp, "close": spot[stamp], "perp_close": perp[stamp],
             "funding_rate": funding[stamp]} for stamp in stamps]
    print(f"rows         {len(rows)} days all three legs printed")

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
            print(f"\n  REFUSED {_name(item)}\n          {item['reason']}")

    if not rank_observations(observations):
        print("\nNo candidate is examinable. Nothing is promoted and no cycle was spent.")
        return 0

    finding = finding_from_scan(ARTIFACTS / "findings.json", scan=scan, space=space,
                                created_by="carry-exit-discovery-bitmex-runner",
                                created_at=_now())
    print(f"\nfinding      {finding['finding_id']}")
    print(f"observation  {finding['observation']}")
    print(f"spot sha256  {json.dumps(spot_digests)}")
    print(f"\nNEXT, and only this: judge it on the holdout with {folds} folds, corrected for "
          f"{len(candidates)}.\n  python3.11 -B scripts/research/run_carry_surge_holdout_bitmex.py "
          f"--finding {finding['finding_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
