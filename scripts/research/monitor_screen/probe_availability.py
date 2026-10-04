"""Availability probe for the monitor screen. RUNS ON THE VM, standard library only.

    python3.11 -B ~/probe_availability.py
    python3.11 -B ~/probe_availability.py --series T10Y2Y DFII10

WHAT IT READS. For each series, from FRED: its first and last observation, how many daily
observations fall inside the screen window, how many are missing, and the longest stretch with no
observation. It PRINTS NO VALUE, no level and no return, and it never touches an exposure. What it
answers is whether a variable could be evaluated at all, which is a fact about the series and not
about any outcome, so it can be asked before the space is sealed.

THE KEY. It is read from ~/.fredkey on the machine that runs this and goes only into the request.
It is never printed, never written, and an error prints the kind of failure and nothing else,
because an error message that echoes the request would echo the key.

THE DEFAULTS are held to a test against the space file, so the probe cannot be asked about a
different window or a different list than the one that will be sealed.
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta

API = "https://api.stlouisfed.org/fred/"
DEFAULT_SERIES = ("T10Y2Y", "T10Y3M", "DFII10", "DTWEXBGS")
WINDOW_START, WINDOW_END = "2018-03-01", "2026-10-01"
MINIMUM_COMPLETENESS = 0.90
MAXIMUM_GAP_DAYS = 10
START_TOLERANCE_DAYS = 7


def weekdays_between(start, end):
    """Monday to Friday days in [start, end): what a daily series could hold at most."""
    day, count = date.fromisoformat(start), 0
    stop = date.fromisoformat(end)
    while day < stop:
        count += day.weekday() < 5
        day += timedelta(days=1)
    return count


def probe(series_id, start, end, fetch):
    """The availability facts of one series. `fetch(path, params)` returns parsed JSON.

    Nothing about the values is kept: they are walked once to count and discarded.
    """
    try:
        meta = fetch("series", {"series_id": series_id})["seriess"][0]
        observations = fetch("series/observations", {
            "series_id": series_id, "observation_start": start,
            "observation_end": end})["observations"]
    except Exception as error:
        return {"series": series_id, "verdict": f"ERROR: {type(error).__name__}"}

    dated = [item["date"] for item in observations if item.get("value") not in (None, ".")]
    missing = len(observations) - len(dated)
    gaps = [(date.fromisoformat(b) - date.fromisoformat(a)).days
            for a, b in zip(dated, dated[1:])]
    expected = weekdays_between(start, end)
    result = {
        "series": series_id, "title": meta.get("title"),
        "frequency": meta.get("frequency_short"), "units": meta.get("units_short"),
        "observation_start": meta.get("observation_start"),
        "observation_end": meta.get("observation_end"),
        "observations_in_window": len(dated), "missing_in_window": missing,
        "weekdays_in_window": expected,
        "completeness": round(len(dated) / expected, 3) if expected else 0.0,
        "longest_gap_days": max(gaps) if gaps else None,
        "first_in_window": dated[0] if dated else None,
        "last_in_window": dated[-1] if dated else None,
    }
    reasons = []
    if not dated:
        reasons.append("no observation in the window")
    else:
        late = (date.fromisoformat(dated[0]) - date.fromisoformat(start)).days
        if late > START_TOLERANCE_DAYS:
            reasons.append(f"first observation in the window is {late} days after it opens")
        if result["completeness"] < MINIMUM_COMPLETENESS:
            reasons.append(f"completeness {result['completeness']} is below {MINIMUM_COMPLETENESS}")
        if result["longest_gap_days"] is not None and result["longest_gap_days"] > MAXIMUM_GAP_DAYS:
            reasons.append(f"longest gap is {result['longest_gap_days']} days")
    if meta.get("frequency_short") != "D":
        reasons.append(f"frequency is {meta.get('frequency_short')}, not daily")
    result["verdict"] = "AVAILABLE" if not reasons else "FLAG: " + "; ".join(reasons)
    return result


def _fetcher(key):
    def fetch(path, params):
        query = urllib.parse.urlencode({**params, "api_key": key, "file_type": "json"})
        with urllib.request.urlopen(API + path + "?" + query, timeout=60) as response:
            return json.loads(response.read())
    return fetch


def render(results):
    lines = ["=" * 78, f"{'series':10s} {'first obs':11s} {'in window':>9s} {'missing':>8s} "
             f"{'compl.':>7s} {'max gap':>8s}  verdict", "=" * 78]
    for r in results:
        if "observations_in_window" not in r:
            lines.append(f"{r['series']:10s} {r['verdict']}")
            continue
        lines.append(f"{r['series']:10s} {str(r['observation_start']):11s} "
                     f"{r['observations_in_window']:9d} {r['missing_in_window']:8d} "
                     f"{r['completeness']:7.3f} {str(r['longest_gap_days']):>8s}  {r['verdict']}")
    lines.append("=" * 78)
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--series", nargs="+", default=list(DEFAULT_SERIES))
    parser.add_argument("--start", default=WINDOW_START)
    parser.add_argument("--end", default=WINDOW_END)
    args = parser.parse_args(argv)
    key_file = os.path.join(os.path.expanduser("~"), ".fredkey")
    try:
        with open(key_file, encoding="utf-8") as handle:
            key = handle.read().strip()
    except OSError:
        raise SystemExit("No ~/.fredkey on this machine: run this on the VM, where the key lives.")
    if not key:
        raise SystemExit("~/.fredkey is empty.")
    results = [probe(series, args.start, args.end, _fetcher(key)) for series in args.series]
    print(render(results))
    print(json.dumps(results, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
