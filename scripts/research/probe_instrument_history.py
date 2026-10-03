"""Which bars Alpaca will serve for a symbol, and from when. Seals nothing.

AVAILABILITY ONLY. It reports the first and last bar and the count, and never
a return, a mean or a ratio. Looking at what a candidate instrument PAID before
declaring a Hypothesis about it is selection; looking at whether the data
exists is not, and the difference is why this prints what it prints.

    python3.11 -B scripts/research/probe_instrument_history.py SVXY VXX

Run it before declaring the volatility premium. The instrument matters more
than usual there: SVXY's structure changed from -1x to -0.5x on 2018-02-28, so
a window spanning that date mixes two different instruments under one ticker,
and VXX was reissued in 2019. A Hypothesis cannot be declared over a window
whose instrument is not one instrument.
"""

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tramitago_quant_core.data.alpaca_equity_series import (
    _alpaca_bars_url, _alpaca_bars_parse, _alpaca_get, ALPACA_FEED_SIP)

PROBE_FROM, PROBE_TO = "2010-01-01", "2026-10-01"


def _injector():
    key_id = os.environ.get("ALPACA_PAPER_API_KEY_ID")
    secret = os.environ.get("ALPACA_PAPER_API_SECRET_KEY")
    if not key_id or not secret:
        raise SystemExit(
            "Set ALPACA_PAPER_API_KEY_ID and ALPACA_PAPER_API_SECRET_KEY as environment "
            "variables before running this script. Never put credentials in a file.")
    return lambda headers: {**headers, "APCA-API-KEY-ID": key_id,
                            "APCA-API-SECRET-KEY": secret}


def main():
    symbols = sys.argv[1:] or ["SVXY", "VXX"]
    injector = _injector()
    print(f"Alpaca {ALPACA_FEED_SIP.upper()} feed, {PROBE_FROM} to {PROBE_TO}\n")
    for symbol in symbols:
        days, token, pages = set(), None, 0
        try:
            while True:
                raw, _ = _alpaca_get(
                    _alpaca_bars_url(symbol, PROBE_FROM, PROBE_TO, token, ALPACA_FEED_SIP),
                    injector, None)
                rows, token = _alpaca_bars_parse(raw, symbol)
                days |= {row["timestamp"][:10] for row in rows}
                pages += 1
                if not token:
                    break
        except Exception as error:
            print(f"  {symbol:6} ERROR {type(error).__name__}: {str(error)[:90]}")
            continue
        if not days:
            print(f"  {symbol:6} no bars served")
            continue
        after = sum(1 for day in days if day >= "2018-03-01")
        print(f"  {symbol:6} {len(days):>5} bars  {min(days)} -> {max(days)}  "
              f"({pages} page(s))")
        print(f"  {'':6} {after:>5} of them on or after 2018-03-01, the date SVXY became -0.5x")
    return 0


if __name__ == "__main__":
    sys.exit(main())
