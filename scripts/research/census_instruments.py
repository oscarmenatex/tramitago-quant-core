"""Census of tradable instruments by mechanism class. METADATA ONLY: no return is read or computed.

    python3.11 -B scripts/research/census_instruments.py --limit-symbols 30     # a trial first
    python3.11 -B scripts/research/census_instruments.py                        # the full census

RUN THIS YOURSELF, OSCAR -- it needs your Alpaca PAPER credentials as environment variables, and
credentials never reach me:

    $env:ALPACA_PAPER_API_KEY_ID = Read-Host "ALPACA_PAPER_API_KEY_ID"
    $secret = Read-Host "ALPACA_PAPER_API_SECRET_KEY" -AsSecureString
    $env:ALPACA_PAPER_API_SECRET_KEY = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret))

Any way of setting the two variables without writing them to a file or to the shell history is
fine. Never put them in a file. Run it on the evidence host (your local machine), never on the VM.

WHAT IT DOES. One request lists the active assets. The ones that are fund-like and fall in a
mechanism class are probed with two requests each, to learn the DATE of the first monthly bar and
the recent monthly dollar volume. About a thousand instruments make about two thousand requests at
the throttled rate, which is some ten minutes. It resumes from a cache, so an interruption costs
nothing, and a symbol that fails is recorded as an error and retried next time.

WHAT IT NEVER DOES. It reads no daily bars, computes no return and keeps no price. The census
definition (classes, keywords, tiers, liquidity floor) is fixed in
tramitago_quant_core/research/instrument_census.py and its digest is sealed with the result, so it
cannot be adjusted after the counts are seen.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tramitago_quant_core.data.alpaca_equity_series import _alpaca_get
from tramitago_quant_core.governance.evidence_host import require_evidence_host
from tramitago_quant_core.research.instrument_census import (
    run_census, summarise, census_record, candidates, definition_digest, is_fund_like)
from tramitago_quant_core.shared.util import encoded, _atomic_write

ARTIFACTS = REPO / "artifacts" / "research"
CACHE = ARTIFACTS / "census" / "instrument-census-cache.json"
REGISTRY = ARTIFACTS / "instrument-census.json"
TRADING_HOST, DATA_HOST = "paper-api.alpaca.markets", "data.alpaca.markets"
THROTTLE_SECONDS = 0.35
MAX_RETRIES = 4


def _injector():
    key_id = os.environ.get("ALPACA_PAPER_API_KEY_ID")
    secret = os.environ.get("ALPACA_PAPER_API_SECRET_KEY")
    if not key_id or not secret:
        raise SystemExit("Set ALPACA_PAPER_API_KEY_ID and ALPACA_PAPER_API_SECRET_KEY as environment "
                         "variables before running this. Never put credentials in a file.")
    return lambda headers: {**headers, "APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}


def _get_json(url, injector):
    """One GET with a throttle and a backoff on 429. A failure raises its kind and nothing else, so
    an error never carries the request, and the request carries the credential."""
    for attempt in range(MAX_RETRIES):
        try:
            raw, _ = _alpaca_get(url, injector, None)
            time.sleep(THROTTLE_SECONDS)
            return json.loads(raw)
        except HTTPError as error:
            if error.code == 429 and attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt * 2)
                continue
            raise RuntimeError(f"HTTP{error.code}") from None
    raise RuntimeError("retries exhausted")


def _assets(injector):
    url = f"https://{TRADING_HOST}/v2/assets?" + urlencode({"status": "active", "asset_class": "us_equity"})
    return _get_json(url, injector)


def _fetcher(injector):
    def fetch_bars(symbol, start, limit):
        query = urlencode({"timeframe": "1Month", "start": start, "limit": limit,
                           "adjustment": "raw", "feed": "sip"})
        payload = _get_json(f"https://{DATA_HOST}/v2/stocks/{symbol}/bars?{query}", injector)
        return payload.get("bars") or []
    return fetch_bars


def _git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()


def _print(record):
    print("=" * 100)
    print(f"INSTRUMENT CENSUS  as of {record['asof']}   {record['census_id'][:48]}")
    print(f"  {record['assets_active_tradable_total']} active US equities; {record['fund_like_total']} "
          f"fund-like; {record['probed']} named in a mechanism class and probed")
    print("=" * 100)
    print(f"  {'class':24s} {'named':>6s} {'plain':>6s} {'A+liquid':>9s} {'A/B+liq':>8s} {'+shortable':>11s}  examples (A, liquid, by volume)")
    for label, row in record["summary"].items():
        print(f"  {label:24s} {row['named']:6d} {row['plain']:6d} {row['plain_tier_a_liquid']:9d} "
              f"{row['plain_tier_a_or_b_liquid']:8d} {row['plain_tier_a_liquid_shortable']:11d}  "
              f"{', '.join(row['examples'])}")
    print("-" * 100)
    print("  plain = without leveraged or inverse products. A = first bar on or before 2018-03-01.")
    print("  Counts are an UPPER BOUND on independent premia and the classes are read from names.")
    print("=" * 100)


def main():
    require_evidence_host(REPO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-symbols", type=int, default=None,
                        help="probe only the first N candidates (a trial run)")
    parser.add_argument("--asof", default=date.today().isoformat())
    args = parser.parse_args()
    injector = _injector()
    assets = _assets(injector)
    tradable = [a for a in assets if a.get("status") == "active" and a.get("tradable") is True]
    fund_like = [a for a in tradable if is_fund_like(a.get("name"))]
    print(f"{len(assets)} assets listed; {len(tradable)} tradable; {len(fund_like)} fund-like; "
          f"{len(candidates(assets))} named in a class (definition {definition_digest()[:12]})")

    cache = json.loads(CACHE.read_bytes()) if CACHE.exists() else {}
    started = time.time()

    def progress(done, total, symbol):
        if done % 25 == 0 or done == total:
            print(f"  probed {done}/{total}  ({time.time() - started:5.0f}s)  last {symbol}", flush=True)
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(CACHE, encoded(cache))

    rows, facts = run_census(assets, _fetcher(injector), asof=args.asof, cache=cache,
                             limit=args.limit_symbols, on_progress=progress)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(CACHE, encoded(facts))
    if args.limit_symbols is not None:
        record = census_record(rows, summarise(rows), asof=args.asof,
                               assets_total=len(tradable), fund_like_total=len(fund_like))
        print("\n  TRIAL RUN: shown but NOT sealed")
        _print(record)
        return 0
    record = census_record(rows, summarise(rows), asof=args.asof, assets_total=len(tradable),
                           fund_like_total=len(fund_like))
    registry = json.loads(REGISTRY.read_bytes()) if REGISTRY.exists() else {"schema_version": "1", "censuses": []}
    if not any(r["census_id"] == record["census_id"] for r in registry["censuses"]):
        registry["censuses"].append({**record, "code_revision": _git("rev-parse", "HEAD")})
        _atomic_write(REGISTRY, encoded(registry))
    _print(record)
    print(f"  sealed -> {REGISTRY.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
