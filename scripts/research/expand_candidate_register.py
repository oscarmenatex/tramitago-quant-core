"""Expand the candidate space with premia whose short sits INSIDE the wrapper.

WHY THIS EXPANSION. The register of 2026-10-03 found 0 of 11 candidates
admissible in principle, and the only single change that unlocked anything was
building shorts in the executor, which would unlock three candidates none of
which has been measured. This looks for the other route: the same premium in a
wrapper the executor CAN hold, where the fund does the shorting.

A covered-call or put-write ETF sells options inside the fund, so the position is
bought with BUY and held, in fractional shares, and the executor never opens a
short. It is the variance risk premium again, which the register already carries
three times, so this is a new EXPRESSION of a known premium and not a new payer.
Genuinely new payers are scarce and the document says so rather than inflating
the list.

WHAT WAS MEASURED, NOT RECALLED. Alpaca's SIP feed was probed for every symbol
on 2026-10-03 and the years below are bars divided by 252. The probe also found
that PUTW no longer exists: bars end 2025-04-03 and the asset endpoint returns
404. A launch date recalled from memory would have missed that.

WHAT IS RECALLED, AND SAYS SO. Every effect range is recalled from literature and
is an input to a feasibility test, never evidence.

APPEND-ONLY. This reads the latest schema-2 register and adds to it.

    python3.11 -B scripts/research/expand_candidate_register.py
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tramitago_quant_core.governance.evidence_host import require_evidence_host
from tramitago_quant_core.research.candidate_register import (
    candidate, monitor_status, constitute_candidate_register, rank_candidates,
    what_would_unlock, admissible_in_principle, candidate_verdict,
    STATUS_UNTRIED, MONITOR_LINK_TESTABLE, MONITOR_UNEXAMINED, EXECUTOR_CAN_OPEN_SHORTS)
from tramitago_quant_core.research.pre_declaration import CLAIM_PREMIUM

REGISTER = REPO / "artifacts" / "research" / "candidate-registers.json"

JUSTIFICATION = (
    "The set of harvestable premia this project can express, expanded on 2026-10-03 with "
    "wrappers that sell options INSIDE the fund, so the executor holds them with BUY and "
    "never opens a short. This is a new expression of the variance risk premium already "
    "in the register and not a new payer. Considered and passed over without an entry: "
    "RYLD (7.43 years, and its volatility index was not verified on FRED), JEPI (6.35 "
    "years), DBMF (7.38 years) and MNA (no monitor variable identified, which is a gap in "
    "the search and not a finding about the premium), and USMV and SPLV, whose low-volatility "
    "anomaly is a mispricing and sits outside the declared destination of risk premia. "
    "Years come from bars probed on the SIP feed, never from launch dates recalled.")

# MEASURED 2026-10-03 on the sealed SPY dataset and FRED's VIXCLS, reading no
# candidate return: a fact about the MONITOR, which is what makes it admissible
# to know before declaring.
VRP_SPREAD = ("VIX minus the 21-day realised volatility of the S&P 500; VIX is published "
              "daily by CBOE and distributed through FRED, realised volatility is "
              "computed from index prices")
VRP_SPREAD_EVIDENCE = (
    "MEASURED on 2026-10-03: the spread was at or below zero on 330 of 2159 days, "
    "15.3 percent, and on at least ten days in 6 of 7 folds, so the empirical link is "
    "testable; whether those days degrade the fund return has NOT been tested")

DISTRIBUTIONS = ("tradable and fractionable, MEASURED; its distributions are large and "
                 "partly return of capital, and whether the adjusted bars treat them as "
                 "total return is UNCHECKED")

NEW = [
    candidate(
        name="variance risk premium, via XYLD held long (S&P 500 covered calls)",
        claim_class=CLAIM_PREMIUM,
        payer="buyers of S&P 500 call options, who pay for upside exposure that the fund "
              "sells them inside the wrapper",
        effect_low="0.35", effect_high="0.80",
        effect_source="published studies of the buy-write index find a Sharpe roughly "
                      "equal to or modestly above the underlying index over long samples; "
                      "the range sits below the S&P 500 own 0.40 to 0.80 at the low end "
                      "for the fund fee and the capped upside",
        available_years="10.72",
        data_source=f"Alpaca SIP 2016-01-04 to 2026-10-01, 2702 bars, MEASURED; {DISTRIBUTIONS}",
        status=STATUS_UNTRIED, requires_short=False,
        monitor=monitor_status(variable=VRP_SPREAD, status=MONITOR_LINK_TESTABLE,
                               evidence=VRP_SPREAD_EVIDENCE)),
    candidate(
        name="variance risk premium, via DIVO held long (active covered calls)",
        claim_class=CLAIM_PREMIUM,
        payer="buyers of call options on the single stocks the fund holds, who pay for "
              "upside exposure the fund sells them",
        effect_low="0.35", effect_high="0.80",
        effect_source="an actively managed covered-call fund on dividend growers, so the "
                      "same literature as the index version with less published evidence; "
                      "the range is recalled and wide",
        available_years="9.77",
        data_source=f"Alpaca SIP 2016-12-14 to 2026-10-01, 2462 bars, MEASURED; {DISTRIBUTIONS}",
        status=STATUS_UNTRIED, requires_short=False,
        monitor=monitor_status(
            variable=VRP_SPREAD, status=MONITOR_LINK_TESTABLE,
            evidence=VRP_SPREAD_EVIDENCE + "; the S&P 500 spread is only a PROXY for the "
                     "single-stock options this fund writes")),
    candidate(
        name="variance risk premium, via QYLD held long (Nasdaq-100 covered calls)",
        claim_class=CLAIM_PREMIUM,
        payer="buyers of Nasdaq-100 call options, who pay for upside exposure the fund "
              "sells them",
        effect_low="0.30", effect_high="0.70",
        effect_source="the same literature as the S&P 500 version; the fund is known to "
                      "have lagged its own index badly in strong markets because the "
                      "capped upside costs more where the underlying trends, so the "
                      "range sits lower",
        available_years="10.72",
        data_source=f"Alpaca SIP 2016-01-04 to 2026-10-01, 2702 bars, MEASURED; {DISTRIBUTIONS}",
        status=STATUS_UNTRIED, requires_short=False,
        monitor=monitor_status(
            variable="VXN minus the realised volatility of the Nasdaq-100",
            status=MONITOR_UNEXAMINED,
            evidence="VXNCLS exists on FRED from 2001 with 6696 observations, but its "
                     "trigger frequency over this window has not been measured and "
                     "realised Nasdaq-100 volatility needs prices not yet captured")),
    candidate(
        name="variance risk premium, via PUTW (S&P 500 put-write)",
        claim_class=CLAIM_PREMIUM,
        payer="buyers of S&P 500 put options, who pay for protection the fund sells them",
        effect_low="0.40", effect_high="0.80",
        effect_source="published studies of the put-write index find a Sharpe above the "
                      "underlying over long samples, before the fund fee",
        available_years="9.10",
        data_source="Alpaca SIP 2016-02-24 to 2025-04-03, 2292 bars, MEASURED; the asset "
                    "endpoint now returns 404",
        status=STATUS_UNTRIED, requires_short=False,
        reachable_today=False,
        unreachable_reason=("the fund no longer exists: the feed serves bars to 2025-04-03 "
                            "and the asset endpoint returns 404, so the DATA is reachable "
                            "and the INSTRUMENT is not. It also shows that this family can "
                            "close, which is a fund-survival risk the others carry"),
        monitor=monitor_status(variable=VRP_SPREAD, status=MONITOR_LINK_TESTABLE,
                               evidence=VRP_SPREAD_EVIDENCE)),
]


def _latest_schema_2_candidates():
    registers = json.loads(REGISTER.read_bytes())["registers"]
    latest = [item for item in registers if item["schema_version"] == "2"]
    if not latest:
        raise SystemExit("There is no schema 2 register to expand; declare it first.")
    return latest[-1]["candidates"]


def _blockers(item):
    parts = []
    if any(b["kind"] == "REQUIRES_SHORT" for b in item["hard_blockers"]):
        parts.append("SHORT")
    if any(b["kind"] in ("MONITOR_REFUTED", "NO_MONITOR_POSSIBLE")
           for b in item["hard_blockers"]):
        parts.append("R3!")
    if item["probable_blockers"]:
        parts.append("r3?")
    return " ".join(parts) or "-"


def main():
    require_evidence_host(REPO)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, check=True).stdout.strip()
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    previous = _latest_schema_2_candidates()
    record = constitute_candidate_register(
        REGISTER, justification=JUSTIFICATION, candidates=previous + NEW,
        declared_by=f"Oscar Carmenate Rodriguez, at {revision[:12]}", declared_at=now)

    print("=" * 104)
    print(f"EXPANDED REGISTER -- {len(previous)} existing + {len(NEW)} new = "
          f"{len(record['candidates'])}; executor can open shorts: {EXECUTOR_CAN_OPEN_SHORTS}")
    print("=" * 104)
    print(f"{'':2} {'candidate':<46} {'range':>9} {'bar':>6} {'yrs':>6}  "
          f"{'verdict':<20} blockers")
    for item in rank_candidates(record):
        mark = {"MEASURED_DEAD": "x", "DECLARED": ">", "UNTRIED": " "}[item["status"]]
        new = "+" if item["name"] in {entry["name"] for entry in NEW} else " "
        span = f"{item['effect']['low']}-{item['effect']['high']}"
        print(f"{mark}{new} {item['name'][:46]:<46} {span:>9} {item['required']:>6} "
              f"{item['available_years']:>6}  {item['verdict']:<20} {_blockers(item)}")
    print("\n  x measured and dead    + added in this expansion")

    scored = [{**entry, **candidate_verdict(entry)} for entry in record["candidates"]]
    admissible = [item["name"] for item in scored if admissible_in_principle(item)]
    print(f"\n  ADMISSIBLE IN PRINCIPLE: {len(admissible)} of {len(scored)}")
    for name in admissible:
        print(f"    {name}")
    print("\n  WHAT EACH SINGLE CHANGE WOULD STILL UNLOCK:")
    for kind, names in what_would_unlock(record).items():
        print(f"    remove {kind}: " + (", ".join(e["name"][:34] for e in names) or "nobody"))
    print(f"\n  {record['register_id'][:58]}")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    sys.exit(main())
