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
    _alpaca_bars_url, _alpaca_bars_parse, _alpaca_get, ALPACA_FEED_SIP,
    ALPACA_CALENDAR_HOST)

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


def _tradability(symbol, injector):
    """Whether the account may SHORT it, and in what size.

    A position the drawdown limit sizes at $109 cannot be held in a futures
    contract worth $1,800, and it cannot be held at all in something the broker
    will not lend. Both are facts about the WRAPPER rather than the premium, and
    both decide whether a Hypothesis can ever be operated at Fase 1 capital --
    which R4 does not ask, because it checks the ceiling and never the lot.
    """
    import json as _json
    raw, _ = _alpaca_get(f"https://{ALPACA_CALENDAR_HOST}/v2/assets/{symbol}",
                         injector, None)
    asset = _json.loads(raw)
    return {key: asset.get(key) for key in
            ("status", "tradable", "shortable", "easy_to_borrow", "fractionable",
             "marginable")}


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
        # Years of history as the register reads them: the span the FEED serves
        # for this instrument, which is the window a Hypothesis could actually
        # be measured over. Measured from the bars, never from a launch date
        # recalled from memory -- the register records what was probed.
        years = (len(days) / 252.0)
        print(f"  {symbol:6} {len(days):>5} bars  {min(days)} -> {max(days)}  "
              f"= {years:5.2f} yr  ({pages} page(s))")
        try:
            flags = _tradability(symbol, injector)
            print(f"  {'':6} {'':>5} " + "  ".join(
                f"{key}={value}" for key, value in flags.items()))
        except Exception as error:
            print(f"  {'':6} {'':>5} tradability unavailable: "
                  f"{type(error).__name__}: {str(error)[:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
