"""Measure where Alpaca's bar coverage for a symbol actually begins. Seals nothing.

The equity risk premium capture refused with a coverage mismatch whose message
truncates the missing list to five dates, so it reported that SOMETHING was
absent without saying how much. The first probe answered: on the IEX feed,
644 of 2198 sessions in the declared window have no bar, the earliest bar is
2018-11-01, and the first fully covered window starts 2020-07-24 -- four months
AFTER the March 2020 crash, which would quietly remove the worst tail and keep
the recovery.

So this now asks a second question before anybody redesigns a window around that
limit: the module hardcodes feed=iex, the free tier's, which carries a few
percent of consolidated volume. SIP is the consolidated tape. Whether this
account can read it is one request away.

    python3.11 -B scripts/research/probe_alpaca_coverage.py

Read-only: no Hypothesis, no dataset, no capture, nothing written anywhere. It
is deliberately NOT guarded by the evidence host rule for the same reason --
there is no evidence here to seal, and none to destroy.
"""

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tramitago_quant_core.data.alpaca_equity_series import (
    _alpaca_bars_url, _alpaca_bars_parse, _alpaca_calendar_url, _alpaca_calendar_parse,
    _alpaca_get)

SYMBOL = "SPY"
DECLARED_START, DECLARED_END = "2018-01-02", "2026-10-01"
PROBE_FROM = "2014-01-01"
FEEDS = ("iex", "sip")


def _injector():
    key_id = os.environ.get("ALPACA_PAPER_API_KEY_ID")
    secret = os.environ.get("ALPACA_PAPER_API_SECRET_KEY")
    if not key_id or not secret:
        raise SystemExit(
            "Set ALPACA_PAPER_API_KEY_ID and ALPACA_PAPER_API_SECRET_KEY as environment "
            "variables before running this script. Never put credentials in a file.")
    return lambda headers: {**headers, "APCA-API-KEY-ID": key_id,
                            "APCA-API-SECRET-KEY": secret}


def _with_feed(url, feed):
    return url.replace("feed=iex", f"feed={feed}")


def _bar_days(injector, feed):
    """Every distinct day the feed will serve, walking all pages."""
    raw, _ = _alpaca_get(
        _with_feed(_alpaca_bars_url(SYMBOL, PROBE_FROM, DECLARED_END), feed), injector, None)
    rows, token = _alpaca_bars_parse(raw, SYMBOL)
    days = {row["timestamp"][:10] for row in rows}
    while token:
        raw, _ = _alpaca_get(
            _with_feed(_alpaca_bars_url(SYMBOL, PROBE_FROM, DECLARED_END, token), feed),
            injector, None)
        more, token = _alpaca_bars_parse(raw, SYMBOL)
        days |= {row["timestamp"][:10] for row in more}
    return days


def main():
    injector = _injector()

    raw, _ = _alpaca_get(_alpaca_calendar_url(PROBE_FROM, DECLARED_END), injector, None)
    sessions = _alpaca_calendar_parse(raw)
    in_window = [day for day in sessions if DECLARED_START <= day < DECLARED_END]
    print(f"declared window {DECLARED_START} -> {DECLARED_END}: "
          f"{len(in_window)} NYSE sessions")
    print("")

    for feed in FEEDS:
        try:
            days = _bar_days(injector, feed)
        except Exception as error:
            print(f"  feed={feed:4} UNAVAILABLE   {type(error).__name__}: "
                  f"{str(error)[:110]}")
            continue
        if not days:
            print(f"  feed={feed:4} returned no bars at all")
            continue
        missing = sorted(set(in_window) - days)
        print(f"  feed={feed:4} earliest {min(days)}   "
              f"{len(missing)} of {len(in_window)} sessions missing")
        if missing:
            print(f"             first gap {missing[0]}, last gap {max(missing)}")
            print(f"             fully covered only from {max(missing)} on, which "
                  f"EXCLUDES everything before it")
        else:
            print(f"             FULLY COVERS the declared window")
    return 0


if __name__ == "__main__":
    sys.exit(main())
