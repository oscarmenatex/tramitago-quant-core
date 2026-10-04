"""Run the monitor screen: seal the space, scan it once, confirm on the holdout once.

    python3.11 -B scripts/research/run_monitor_screen.py seal
    python3.11 -B scripts/research/run_monitor_screen.py scan
    python3.11 -B scripts/research/run_monitor_screen.py confirm --spend-the-holdout

THE ORDER IS THE CONTRACT.
  seal     validates the space and seals it. Nothing is read. Run it, and read the printed identity,
           before anything else: a scan refuses a space that is not sealed exactly as it stands.
  scan     RUN THIS YOURSELF, OSCAR. It fetches six FRED series through the VM (the key never
           leaves it) and reads the returns of the three sealed exposure datasets on the DISCOVERY
           window only. It observes all 14 candidates, seals every one with the multiplicity, and
           prints them. A pass here is not evidence.
  confirm  Opens the holdout. It can be done ONCE: a marker is sealed before the returns are read,
           and a second attempt is refused whatever happened to the first. It does nothing when no
           candidate passed, and then the holdout stays unopened. The flag exists so it cannot be
           run by accident.

No Alpaca credentials are needed: the exposures are already sealed datasets.
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_premium import relay_fred_over_ssh
from tramitago_quant_core.governance.evidence_host import require_evidence_host
from tramitago_quant_core.research.monitor_screen import (
    seal_space, load_sealed_space, scan, seal_scan, confirm_on_holdout, holdout_was_opened,
    LINK_MET)
from tramitago_quant_core.research.monitor_screen_io import load_exposure_returns, load_series

ARTIFACTS = REPO / "artifacts" / "research"
SPACE_FILE = REPO / "scripts" / "research" / "monitor_screen" / "space.json"
RULE_FILE = REPO / "artifacts" / "governance" / "monitor-screen-link-rule.json"
SPACES = ARTIFACTS / "monitor-screen-spaces.json"
SCANS = ARTIFACTS / "monitor-screen-scans.json"
HOLDOUT = ARTIFACTS / "monitor-screen-holdout.json"
CHECKS = ARTIFACTS / "checks" / "monitor_screen"
AUTHORISED_BY = "Oscar Carmenate Rodriguez, Director del Proyecto"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True,
                          check=True).stdout.strip()


def _no_network(series_id, start, end):
    raise SystemExit(f"{series_id} has no sealed capture and this step must not fetch it. "
                     f"Run the scan first.")


def _series(space, relay):
    start = space["fixed_parameters"]["series_history_start"]
    end = space["windows"]["holdout"]["end_exclusive_utc"][:10]
    now = _now()
    return {v["series"]: load_series(CHECKS, v["series"], relay, start, end, now)
            for v in space["variables"]}


def _exposure_returns(space, window_name):
    window = space["windows"][window_name]
    return {e["id"]: load_exposure_returns(ARTIFACTS / "datasets" / e["sealed_dataset"],
                                           e["return_column"], window)
            for e in space["exposures"]}


def _seal():
    space = json.loads(SPACE_FILE.read_text(encoding="utf-8"))
    rule = json.loads(RULE_FILE.read_bytes())["rules"][0]["rule_id"]
    record, is_new = seal_space(SPACES, space, sealed_at=_now(), code_revision=_git("rev-parse", "HEAD"),
                                authorised_by=AUTHORISED_BY, link_rule_id=rule)
    print("=" * 78)
    print(f"SPACE {'SEALED' if is_new else 'ALREADY SEALED'} -- no series and no return was read")
    print("=" * 78)
    print(f"  {record['space_id'][:66]}")
    print(f"  candidates {len(record['space']['candidates'])}   windows "
          f"{record['space']['windows']['discovery']['start_utc'][:10]} to "
          f"{record['space']['windows']['holdout']['end_exclusive_utc'][:10]}")
    print(f"  link rule  {rule[:62]}")
    print("=" * 78)


def _print_scan(record):
    summary = record["summary"]
    print("=" * 78)
    print(f"SCAN  {record['scan_id'][:60]}")
    print("=" * 78)
    print(f"  {'':4s} {'exposure':8s} {'variable':9s} {'form':27s} {'on':>5s} {'unk':>4s} {'folds':>6s}  outcome")
    for o in record["observations"]:
        print(f"  {o['candidate']:4s} {o['exposure']:8s} {o['variable']:9s} {o['form']:27s} "
              f"{o['on_days']:5d} {o['unknown_days']:4d} {o['met']:>2d}/{o['usable']:<3d}  {o['outcome']}")
    print(f"\n  examined {summary['examined']}   met {summary['by_outcome'][LINK_MET]}   "
          f"failed {summary['by_outcome']['REACHABLE_AND_FAILED']}   "
          f"unreachable {summary['by_outcome']['UNREACHABLE']}")
    print(f"  expected by chance alone: {summary['expected_false_passes_by_chance']} false passes "
          f"of {summary['examined']}  (tests on this question: {summary['total_tested_on_this_question']})")
    print(f"  {summary['reading']}")
    print("=" * 78)


def _scan():
    space = json.loads(SPACE_FILE.read_text(encoding="utf-8"))
    record = load_sealed_space(SPACES, space)                 # refuses an unsealed or changed space
    series = _series(record["space"], relay_fred_over_ssh)
    returns = _exposure_returns(record["space"], "discovery")
    observations, summary = scan(record["space"], series=series, returns_by_exposure=returns)
    sealed, _ = seal_scan(SCANS, record, observations, summary, series=series,
                          returns_by_exposure=returns, scanned_at=_now(),
                          code_revision=_git("rev-parse", "HEAD"))
    _print_scan(sealed)


def _confirm():
    space = json.loads(SPACE_FILE.read_text(encoding="utf-8"))
    record = load_sealed_space(SPACES, space)
    scans = [s for s in json.loads(SCANS.read_bytes())["scans"] if s["space_id"] == record["space_id"]]
    if not scans:
        raise SystemExit("There is no scan of this space to confirm. Run the scan first.")
    scan_record = scans[-1]
    series = _series(record["space"], _no_network)            # sealed captures only, no fetch
    result = confirm_on_holdout(
        record, scan_record, series=series,
        load_holdout_returns=lambda: _exposure_returns(record["space"], "holdout"),
        path=HOLDOUT, opened_at=_now(), code_revision=_git("rev-parse", "HEAD"))
    print("=" * 78)
    if not result["holdout_opened"]:
        print("NOTHING MET THE DISCOVERY RULE -- the holdout stays unopened")
        print("=" * 78)
        return
    outcome = result["result"]
    print(f"HOLDOUT OPENED, ONCE  {outcome['record_id'][:52]}")
    print("=" * 78)
    print(f"  claim: {outcome['claim']}")
    print(f"  confidence {outcome['confidence']}  (1 minus 0.05 over the {outcome['passed_discovery']} that passed)")
    for r in outcome["results"]:
        bound = r.get("difference_upper_bound")
        print(f"  {r['candidate']:4s} {r['status']:10s} on {r['on_days']:4d} off {r['off_days']:4d}  "
              f"upper bound {('%+.5f' % bound) if bound is not None else '-':>10s}  "
              f"{'CONFIRMED' if r['confirmed'] else 'not confirmed'}")
    print(f"\n  confirmed: {outcome['confirmed'] or 'none'}")
    print("  Under the sealed rule a confirmed link is an ADDITIONAL requirement for a premium,")
    print("  never a substitute for M1 on the premium's own window.")
    print("=" * 78)


def main():
    require_evidence_host(REPO)
    parser = argparse.ArgumentParser()
    parser.add_argument("step", choices=("seal", "scan", "confirm"))
    parser.add_argument("--spend-the-holdout", action="store_true",
                        help="required for confirm: the holdout can be opened once")
    args = parser.parse_args()
    try:
        if args.step == "seal":
            _seal()
        elif args.step == "scan":
            _scan()
        else:
            if not args.spend_the_holdout:
                raise SystemExit("confirm opens the holdout, which can be done ONCE. Pass "
                                 "--spend-the-holdout to do it.")
            if holdout_was_opened(HOLDOUT, load_sealed_space(
                    SPACES, json.loads(SPACE_FILE.read_text(encoding="utf-8")))["space_id"]):
                raise SystemExit("The holdout of this space was already opened. It is spent.")
            _confirm()
    except ValueError as refusal:
        # A refusal is an answer, not a crash: print it as one.
        raise SystemExit(str(refusal)) from None
    return 0


if __name__ == "__main__":
    sys.exit(main())
