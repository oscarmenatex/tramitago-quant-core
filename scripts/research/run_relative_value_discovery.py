"""Bounded Discovery over VÍA 7.C -- relative value, the one cheap axis the
catalogue does not record as measured dead.

WHY THIS AXIS. "Catalogo de Hipotesis - Ruta hacia Fase 1" lists 7.C as
MACHINERY BUILT (pluggable Outcome plus a real second leg) with exactly ONE data
point: the ETH/BTC pair, REJECTED, "el eje tiene UN solo dato: probar otro par
cuesta poco <- reabrible". The aggregate measurement that closed the
single-signal axis covered single-instrument DIRECTIONAL prediction only, so it
says nothing about a spread. Every other open route -- Nivel 4 + 7.B intraday,
Nivel 5 another market -- is a real construction investment.

WHY A SCAN RATHER THAN ONE MORE PAIR. Testing a second pair by hand would add a
second data point at the cost of another full validation cycle, and then a third.
Eighteen pair-window combinations cost minutes here, and the count is sealed so
whatever survives is corrected for having been chosen out of eighteen.

THE PAIRS ARE CHOSEN STRUCTURALLY, NEVER BY MEASURED CORRELATION. Picking pairs
because their prices moved together in the data would snoop the space itself --
the one failure a sealed space cannot detect, because it happens before the
space is written. Each pair below shares a protocol lineage or a sector, stated
in advance, and the justification would read identically had the data never been
fetched.

ETH/BTC IS DELIBERATELY ABSENT although it is the pair already tested. Its
Hypothesis was evaluated on 2025, which is this space's holdout; including it
would hand the search a period it has already seen for that pair. The existing
data point stands on its own record.

Public endpoint, no credentials.

    python3.11 -B scripts/research/run_relative_value_discovery.py
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
from tramitago_quant_core.data.acquisition import normalize, _coinbase_public_request
from tramitago_quant_core.research.discovery import (
    constitute_discovery_space, scan_discovery_space, rank_observations,
    constitute_discovery_scan, finding_from_scan,
)

DISCOVERY_WINDOW = {"start_utc": "2024-01-01T00:00:00Z",
                    "end_exclusive_utc": "2025-01-01T00:00:00Z"}
HOLDOUT_WINDOW = {"start_utc": "2025-01-01T00:00:00Z",
                  "end_exclusive_utc": "2026-01-01T00:00:00Z"}

# (primary leg, second leg, the structural relationship -- declared, not measured)
PAIRS = [
    ("ETH-USD", "ETC-USD", "ETC is the pre-fork Ethereum chain: same protocol lineage"),
    ("BTC-USD", "BCH-USD", "BCH is a fork of Bitcoin: same chain until 2017"),
    ("BTC-USD", "LTC-USD", "LTC is a near-clone of Bitcoin's design (PoW, UTXO)"),
    ("ETH-USD", "SOL-USD", "the two largest smart-contract settlement platforms"),
    ("SOL-USD", "AVAX-USD", "competing layer-1s launched in the same cycle"),
    ("ADA-USD", "DOT-USD", "competing layer-1s launched in the same cycle"),
]
WINDOWS = (5, 10, 20)

MINIMUM_SUPPORT = 30
MINIMUM_MINORITY_STATE_FREQUENCY = "0.20"

JUSTIFICATION = (
    "Vía 7.C (relative value), the only axis the hypothesis catalogue leaves open at low cost "
    "and does not record as measured dead -- the aggregate signal measurement covered "
    "single-instrument directional prediction only, never a spread. Six pairs x three "
    "PAIR_RATIO_REVERSION windows, N=18, declared complete. The pairs are chosen by STRUCTURAL "
    "relationship stated in advance -- protocol lineage (ETH/ETC, BTC/BCH, BTC/LTC) or competing "
    "platforms of the same generation (ETH/SOL, SOL/AVAX, ADA/DOT) -- never by correlation "
    "measured in the data, which would snoop the space itself. ETH/BTC is excluded although it "
    "is the pair already tested: its Hypothesis was evaluated on 2025, which is this space's "
    "holdout. Windows 5/10/20 are short, medium and long relative to a daily ratio and are the "
    "same three for every pair, so no pair is favoured by a wider grid. No pair, window or "
    "instrument may be added to THIS space afterwards."
)

ARTIFACTS = REPO / "artifacts" / "discovery"
MAX_CANDLES_PER_REQUEST = 300


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _code_revision():
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                          text=True, check=True).stdout.strip()


def _daily_closes(product):
    """Closes for one product over the discovery window, by timestamp.

    Deliberately NOT routed through the sealed capture layer: this is unsealed
    exploration, which the Finding contract permits precisely because a Finding
    is not a verdict. Keeping an unsealed fetch out of the data layer stops it
    being mistaken for one that is.
    """
    start = p.epoch(DISCOVERY_WINDOW["start_utc"])
    end = p.epoch(DISCOVERY_WINDOW["end_exclusive_utc"])
    closes, digests = {}, []
    cursor = start
    while cursor < end:
        stop = min(cursor + MAX_CANDLES_PER_REQUEST * 86400, end)
        url = (f"https://api.exchange.coinbase.com/products/{product}/candles"
               f"?granularity=86400&start={p.iso(cursor)}&end={p.iso(stop)}")
        with urlopen(_coinbase_public_request(url), timeout=30) as response:
            raw = response.read()
        digests.append(p.digest(raw))
        rows, report = normalize(raw, cursor, stop, product)
        if report["errors"]:
            raise SystemExit(f"{product} {p.iso(cursor)}: {report['errors'][:2]}")
        for row in rows:
            closes[row["timestamp"]] = row["close"]
        cursor = stop
    return closes, digests


def _pair_rows(primary_closes, second_closes):
    """One row per day BOTH legs printed. A day missing from either leg is
    dropped rather than filled: a spread needs two real prices, and inventing one
    would put a number the market never set into the signal."""
    shared = sorted(set(primary_closes) & set(second_closes))
    return [{"timestamp": stamp, "close": primary_closes[stamp],
             "pair_close": second_closes[stamp]} for stamp in shared]


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    candidates = [
        {"strategy_id": "PAIR_RATIO_REVERSION",
         "parameters": {"window": window, "pair_variable": "pair_close"},
         "series": f"{primary}/{second}"}
        for primary, second, _ in PAIRS for window in WINDOWS
    ]

    # SEALED FIRST, FETCHED SECOND.
    space = constitute_discovery_space(
        ARTIFACTS / "discovery-spaces.json", justification=JUSTIFICATION,
        candidates=candidates, discovery_window=DISCOVERY_WINDOW,
        holdout_window=HOLDOUT_WINDOW, minimum_support=MINIMUM_SUPPORT,
        minimum_minority_state_frequency=MINIMUM_MINORITY_STATE_FREQUENCY,
        declared_by="relative-value-discovery-runner", declared_at=_now())
    print(f"space        {space['space_id']}")
    print(f"candidates   {len(candidates)}  ({len(PAIRS)} pairs x {len(WINDOWS)} windows)")
    print(f"holdout      {HOLDOUT_WINDOW['start_utc']} .. "
          f"{HOLDOUT_WINDOW['end_exclusive_utc']}  (unread)")

    closes, digests = {}, {}
    for product in sorted({leg for pair in PAIRS for leg in pair[:2]}):
        closes[product], digests[product] = _daily_closes(product)
        print(f"  fetched    {product:<10} {len(closes[product])} days")

    rows = {f"{primary}/{second}": _pair_rows(closes[primary], closes[second])
            for primary, second, _ in PAIRS}
    for label, series in rows.items():
        print(f"  aligned    {label:<20} {len(series)} days both legs printed")

    observations = scan_discovery_space(space, rows)
    scan = constitute_discovery_scan(
        ARTIFACTS / "discovery-scans.json", space=space, observations=observations,
        scanned_at=_now(), scan_code_revision=_code_revision())
    print(f"scan         {scan['scan_id']}")
    print(f"summary      {json.dumps(scan['summary'], sort_keys=True)}")

    print("\n  rank  pair                  window    effect        minority  support")
    for rank, item in enumerate(rank_observations(observations)):
        print(f"  {rank:>4}  {item['series']:<20}  {item['parameters']['window']:>6}  "
              f"{float(item['effect']):+.6f}  "
              f"{float(item['minority_state_frequency']):>8.3f}  "
              f"{item['upper_count']}/{item['lower_count']}")
    for item in observations:
        if not item["examinable"]:
            print(f"  REFUSED {item['series']} w={item['parameters']['window']}: {item['reason']}")

    finding = finding_from_scan(ARTIFACTS / "findings.json", scan=scan, space=space,
                               created_by="relative-value-discovery-runner", created_at=_now())
    print(f"\nfinding      {finding['finding_id']}")
    print(f"observation  {finding['observation']}")
    print(f"raw_sha256   {json.dumps(digests, sort_keys=True)}")
    print("\nThe holdout was not read. A Hypothesis built from this Finding must be "
          f"corrected for {scan['summary']['candidates_examined']} candidates, not one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
