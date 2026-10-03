"""Judge ANY held-position Hypothesis from its spec. One script, no asset in it.

    python3.11 -B scripts/research/run_premium.py scripts/research/specs/xyld.json

RUN THIS YOURSELF, OSCAR -- it needs credentials, and credentials never reach me:

    $env:ALPACA_PAPER_API_KEY_ID = ...
    $env:ALPACA_PAPER_API_SECRET_KEY = ...

WHY THIS REPLACES THE PER-ASSET RUNNERS. Twenty-three scripts in this directory stamp
a code_revision into sealed records and none has a test; the three most recent share
52 to 61 percent of their executable code. This file is deliberately thin: it wires
the two things that need the outside world -- a venue's bars and a FRED response -- and
hands everything else to research/premium_runner.py and research/premium_engine.py,
which have tests and know no ticker.

The existing runners are NOT migrated or deleted here. The engine was proven against
two of them first: run on the sealed SPY and SVXY datasets it reproduces their weight,
Sharpe, lower bound, drawdown bound and level-claim verdict to the last decimal.

THE FRED KEY NEVER LEAVES THE VM. The response is fetched there over ssh for the
Hypothesis's own date range and arrives here as bytes; this process never reads the
key, and a response that echoes one is refused before it is sealed.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tramitago_quant_core.data.alpaca_equity_series import capture_alpaca_equity_bars
from tramitago_quant_core.governance.evidence_host import require_evidence_host
from tramitago_quant_core.research.premium_runner import run_spec

ARTIFACTS = REPO / "artifacts" / "research"
VM_HOST = "core-oracle"

PATHS = {
    "hypotheses": ARTIFACTS / "hypotheses.json",
    "pre_declarations": ARTIFACTS / "pre-declarations.json",
    "datasets": ARTIFACTS / "datasets",
    "checks": ARTIFACTS / "checks",
    "level_claims": ARTIFACTS,
    "admissions": ARTIFACTS / "admissions.json",
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _credential_injector():
    key_id = os.environ.get("ALPACA_PAPER_API_KEY_ID")
    secret = os.environ.get("ALPACA_PAPER_API_SECRET_KEY")
    if not key_id or not secret:
        raise SystemExit(
            "Set ALPACA_PAPER_API_KEY_ID and ALPACA_PAPER_API_SECRET_KEY as environment "
            "variables before running this script. Never put credentials in a file.")
    return lambda headers: {**headers, "APCA-API-KEY-ID": key_id,
                            "APCA-API-SECRET-KEY": secret}


class RealIO:
    def __init__(self):
        self._injector = _credential_injector()

    def capture_bars(self, *, symbol, start, end, warmup, horizon, acquired_at, adjustment,
                     feed):
        return capture_alpaca_equity_bars(
            symbol=symbol, evaluable_start_utc=start, evaluable_end_exclusive_utc=end,
            warmup_periods=warmup, horizon=horizon, acquired_at=acquired_at,
            credential_injector=self._injector, feed=feed, adjustment=adjustment)

    def relay_fred(self, series_id, start, end):
        import base64
        command = (
            f'curl -sS -G "https://api.stlouisfed.org/fred/series/observations" '
            f'-d series_id={series_id} -d observation_start={start} -d observation_end={end} '
            f'-d file_type=json --data-urlencode "api_key@$HOME/.fredkey" | base64 -w0')
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=20", "-o", "BatchMode=yes", VM_HOST, command],
            capture_output=True, text=True, timeout=240)
        if result.returncode != 0 or not result.stdout.strip():
            raise SystemExit(f"Could not fetch {series_id} on {VM_HOST}: "
                             f"{result.stderr.strip()[:200]}")
        return base64.b64decode(result.stdout.strip())


def _print(result):
    spec_slug = result["slug"]
    print(f"{'=' * 78}\n{spec_slug}   {result['days']} days ({result['years']:.2f} yr)"
          f"{'   [resumed from a sealed dataset]' if result.get('resumed') else ''}\n{'=' * 78}")
    declared = (result.get("pre_declaration") or {}).get("required_effect", {})
    if declared:
        print(f"  DECLARED  effect {declared.get('plausible_low', declared.get('plausible_point_sharpe'))}"
              f" to {declared.get('plausible_high', declared.get('plausible_point_sharpe'))}"
              f" against a bar of {declared['required_point_sharpe']}"
              f"  {declared.get('position_against_bar', '')}")
    if result["void"]:
        print(f"\n  MEASUREMENT VOID: {result['void']}")
        check = result.get("distribution_check")
        if check and check.get("implied_annual_yield") is not None:
            print(f"  distributions  implied annual yield {check['implied_annual_yield']} "
                  f"(declared {check['expected_annual_yield']}); ratio fell on "
                  f"{check['decreases']} of {check['days']} days beyond quote rounding")
        print("  Nothing was judged and no level claim or admission was sealed. The")
        print("  captured data stays, because it is what was observed.\n" + "=" * 78)
        return
    check = result.get("distribution_check")
    if check:
        print(f"  distributions  implied annual yield {check['implied_annual_yield']} "
              f"(declared {check['expected_annual_yield']}) -> {check['status']}")
    print(f"  GROSS {result['gross_total']:+.4f}   COST {result['cost_total']:.6f}   "
          f"NET {result['net_total']:+.4f}   weight {result['weight']:.1%}")
    print(f"  net Sharpe  point {result['sharpe_point']:+.4f}   LOWER bound "
          f"{result['sharpe_bound']:+.4f}   MEASURED against the declared range above")
    print(f"  drawdown    point {result['drawdown_point']:.2%}   UPPER bound "
          f"{result['drawdown_bound']:.2%}")
    monitor = result.get("monitor")
    if monitor:
        print(f"  monitor     trigger on {monitor['trigger_days']} of {monitor['observed_days']} "
              f"observed days ({monitor['trigger_frequency']:.1%}); {monitor['missing_days']} missing")
        ratio = monitor["link_ratio"]
        print(f"              link {monitor['link_met']} of {monitor['link_usable']} usable folds"
              + (f" = {ratio:.4f}" if ratio is not None else "")
              + f"  -> {monitor['link_outcome']}  ({monitor['link_form']})")
        for name, control in (result.get("controls") or {}).items():
            cr = control["ratio"]
            print(f"              CONTROL on {name}: {control['met']} of {control['usable']}"
                  + (f" = {cr:.4f}" if cr is not None else "")
                  + "   (if equal to the above, the monitor detects drawdowns, not the premium)")
    claim = result["level_claim"]
    print(f"\n  LEVEL CLAIM -> {claim['outcome']}   consistency {claim['consistency']}   "
          f"{claim['reason'] or ''}")
    sealed = result.get("sealed", {})
    if sealed.get("admission_gates"):
        print(f"\n  ADMISSION -> {sealed['admission_outcome']}")
        for gate in sealed["admission_gates"]:
            mark = {"PASSED": "  ok  ", "FAILED": " FAIL ", "NOT_EVALUABLE": " none "}[gate["state"]]
            print(f"   [{mark}] {gate['gate']}\n            {gate['detail'][:120]}")
    elif claim["outcome"] != "VALIDATED":
        print("  Admission not reached: it is open only to a VALIDATED claim.")
    print("=" * 78)


def main():
    require_evidence_host(REPO)
    if len(sys.argv) != 2:
        raise SystemExit("usage: run_premium.py <spec.json>")
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, check=True).stdout.strip()
    result = run_spec(spec, io=RealIO(), paths=PATHS, now=_now(), code_revision=revision)
    _print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
