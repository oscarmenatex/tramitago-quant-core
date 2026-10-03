"""Declare the candidate space this project is actually choosing from.

EVERY EFFECT RANGE BELOW IS RECALLED FROM LITERATURE, NOT MEASURED HERE. It is
an input to a feasibility test and never evidence. The conservative end is what
the test uses, for the same reason section 8.1 uses adverse bounds.

The available-years figures ARE measured: Alpaca's SIP feed serves from
2016-01-04, FRED's ICE series only three years, BAA10Y from 1990, and SVXY's
-0.5x structure from 2018-02-28. Where an instrument is younger than the premium
it expresses, the instrument is what counts.

THIS IS RECORD SCHEMA 2. Every candidate now says whether it requires a short and
what is known about its monitor, because the first two registers said neither and
that omission cost a wrong recommendation on 2026-10-03: VIXY held short was
offered as "the one route to a sustainable position" by a platform whose
executor cannot open a short, and whose monitor link had already failed a
measurement on the mirror instrument.

APPEND-ONLY. An earlier version of this script deleted the registry file and
re-declared, which erased every earlier register from the working copy. It now
only appends, and the two earlier registers were restored from git.

    python3.11 -B scripts/research/declare_candidate_register.py
"""

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
    STATUS_UNTRIED, STATUS_MEASURED_DEAD, MONITOR_NONE_POSSIBLE, MONITOR_LINK_MET,
    MONITOR_LINK_REFUTED, MONITOR_IDENTITY_ONLY, MONITOR_UNEXAMINED, CERTAINTY_MEASURED,
    CERTAINTY_INFERRED, EXECUTOR_CAN_OPEN_SHORTS)
from tramitago_quant_core.research.pre_declaration import CLAIM_PREMIUM, CLAIM_MISPRICING

REGISTER = REPO / "artifacts" / "research" / "candidate-registers.json"

JUSTIFICATION = (
    "The set of harvestable premia this project can express with the data sources it "
    "actually reaches, declared BEFORE the next one is picked, now carrying for every "
    "candidate whether it requires a short and what is known about its monitor. The "
    "first two registers carried neither, which let VIXY held short be recommended on "
    "its effect size alone by a platform whose executor cannot open a short and whose "
    "monitor link had already failed a measurement on the mirror instrument.")

VIX_SLOPE = "VIX3M minus VIX, the term structure slope, published daily by CBOE"
VIX_SLOPE_FAILED = ("inverted days degraded the return in 2 of 5 usable folds, 0.40 "
                    "against a 0.70 threshold, with five usable folds meeting the "
                    "declared minimum")

CANDIDATES = [
    candidate(
        name="variance risk premium, via VIXY held SHORT",
        claim_class=CLAIM_PREMIUM,
        payer="buyers of VIX futures and index options purchasing protection against "
              "equity drawdowns they are mandated or unwilling to bear",
        effect_low="0.50", effect_high="1.00",
        effect_source="the same literature as the SVXY entry; this is the same premium "
                      "without the inverse ETP's daily rebalancing drag and without its "
                      "structure change, so the window is the feed's rather than the "
                      "wrapper's",
        available_years="10.75",
        data_source="Alpaca SIP from 2016-01-04; MEASURED shortable, easy to borrow and "
                    "fractionable, so a $109 position is exactly expressible",
        status=STATUS_UNTRIED, requires_short=True,
        monitor=monitor_status(
            variable=VIX_SLOPE, status=MONITOR_LINK_REFUTED, certainty=CERTAINTY_INFERRED,
            measured_on="SVXY, the inverse of the same VIX futures index at half leverage",
            evidence=VIX_SLOPE_FAILED)),
    candidate(
        name="variance risk premium, via SVXY",
        claim_class=CLAIM_PREMIUM,
        payer="buyers of VIX futures and index options, purchasing protection against "
              "equity drawdowns they are mandated or unwilling to bear",
        effect_low="0.50", effect_high="1.00",
        effect_source="published estimates of the roll-yield harvest on short VIX futures "
                      "run roughly 0.5 to 1.0 before catastrophic tail events; MEASURED "
                      "at a point of 0.5021 with a lower bound of -0.0445",
        available_years="8.57",
        data_source="Alpaca SIP; SVXY's -0.5x structure from 2018-02-28",
        status=STATUS_MEASURED_DEAD, requires_short=False,
        monitor=monitor_status(
            variable=VIX_SLOPE, status=MONITOR_LINK_REFUTED, certainty=CERTAINTY_MEASURED,
            evidence=VIX_SLOPE_FAILED)),
    candidate(
        name="US equity risk premium, via SPY",
        claim_class=CLAIM_PREMIUM,
        payer="every investor reducing equity exposure for reasons unrelated to expected "
              "return: mandates, liability matching, liquidity needs and horizon",
        effect_low="0.40", effect_high="0.80",
        effect_source="long-run equity Sharpe is conventionally quoted near 0.4, and "
                      "measured 0.81 over this project's own 2018-2026 window",
        available_years="10.75", data_source="Alpaca SIP from 2016-01-04",
        status=STATUS_MEASURED_DEAD, requires_short=False,
        monitor=monitor_status(
            variable="none exists that is not the position's own return",
            status=MONITOR_NONE_POSSIBLE,
            evidence="an unconditional premium's only observable is the premium not "
                     "paying, which is P&L, and R3 returned NOT_EVALUABLE because no "
                     "monitor could be declared at all")),
    candidate(
        name="crypto perpetual funding carry",
        claim_class=CLAIM_PREMIUM,
        payer="levered long holders of the perpetual, paying funding to maintain exposure",
        effect_low="0.30", effect_high="0.80",
        effect_source="no published long-run estimate; the range is a guess from the "
                      "gross figures this project sealed, and should be read as weaker "
                      "than the others",
        available_years="10.00", data_source="BitMEX and Hyperliquid public history",
        status=STATUS_MEASURED_DEAD, requires_short=True,
        monitor=monitor_status(
            variable="the funding rate, published every eight hours",
            status=MONITOR_LINK_MET, certainty=CERTAINTY_MEASURED,
            evidence="the exit rule validated at 8 of 8 folds on a pre-declared holdout "
                     "and R3 passed in its admission record")),
    candidate(
        name="investment-grade credit premium, via LQD against IEF",
        claim_class=CLAIM_PREMIUM,
        payer="investors constrained to Treasuries or the highest grades by mandate, "
              "capital treatment or collateral eligibility",
        effect_low="0.20", effect_high="0.50",
        effect_source="published excess-return Sharpes for investment-grade credit run "
                      "roughly 0.2 to 0.5; measured 0.19 here",
        available_years="10.75", data_source="Alpaca SIP from 2016-01-04",
        status=STATUS_MEASURED_DEAD, requires_short=True,
        monitor=monitor_status(
            variable="BAA10Y, Moody's Baa yield relative to the 10-year Treasury",
            status=MONITOR_IDENTITY_ONLY,
            evidence="the trigger state occurred on 0 of 2184 days so no walk-forward "
                     "could establish the link, and R3 passed only through the identity "
                     "form")),
    candidate(
        name="variance risk premium, via VIXM held SHORT",
        claim_class=CLAIM_PREMIUM,
        payer="the same buyers of protection, further out the curve where they hedge "
              "horizon rather than event risk",
        effect_low="0.30", effect_high="0.70",
        effect_source="mid-term VIX futures carry a flatter curve, so the roll harvest is "
                      "smaller than the short-term one by roughly a third in published "
                      "comparisons; the range is recalled and wide because the comparison "
                      "is less studied than the front",
        available_years="10.75",
        data_source="Alpaca SIP from 2016-01-04; MEASURED shortable, easy to borrow and "
                    "fractionable",
        status=STATUS_UNTRIED, requires_short=True,
        monitor=monitor_status(
            variable=VIX_SLOPE, status=MONITOR_UNEXAMINED,
            evidence="the only measurement is on a front-month product and was refuted, "
                     "but VIXM tracks months four to seven and the slope compares "
                     "months one and three, so that result is not carried over and "
                     "nobody has examined this one")),
    candidate(
        name="currency carry, via developed-market FX ETFs",
        claim_class=CLAIM_PREMIUM,
        payer="borrowers in low-rate currencies and hedgers paying forward points",
        effect_low="0.30", effect_high="0.50",
        effect_source="published FX carry Sharpes run roughly 0.3 to 0.5 on diversified "
                      "baskets, with pronounced negative skew",
        available_years="10.75",
        data_source="Alpaca SIP; single-currency ETFs express the differential poorly",
        status=STATUS_UNTRIED, requires_short=True,
        monitor=monitor_status(
            variable="the interest rate differential between the two currencies",
            status=MONITOR_UNEXAMINED,
            evidence="policy and money-market rates are quoted daily, but nobody has "
                     "measured whether the differential collapsing degrades the return")),
    candidate(
        name="crypto relative value, via pair ratio reversion",
        claim_class=CLAIM_MISPRICING,
        payer="nobody -- a convergence bet has no counterparty paying to shed a risk, "
              "which is why it sits outside the declared destination",
        effect_low="0.00", effect_high="0.50",
        effect_source="no basis for a range; two Hypotheses measured 5 of 10 folds and "
                      "the stopping rule cannot judge the population below five",
        available_years="10.00", data_source="Coinbase public history",
        status=STATUS_UNTRIED, requires_short=True,
        monitor=monitor_status(
            variable="none exists that is independent of the signal or the return",
            status=MONITOR_NONE_POSSIBLE,
            evidence="the ratio against its own average IS the signal and the spread "
                     "return IS the P&L, so no variable is independent of either")),
    candidate(
        name="term premium, via TLT against SHY",
        claim_class=CLAIM_PREMIUM,
        payer="investors who need duration for liability matching regardless of its "
              "expected return, and regulators who require it",
        effect_low="0.20", effect_high="0.40",
        effect_source="published term-premium Sharpes run roughly 0.2 to 0.4 over long "
                      "samples, and 2022 was catastrophic for duration inside this window",
        available_years="10.75", data_source="Alpaca SIP from 2016-01-04",
        status=STATUS_UNTRIED, requires_short=True,
        monitor=monitor_status(
            variable="the 10-year minus 2-year Treasury slope, quoted daily",
            status=MONITOR_UNEXAMINED,
            evidence="the curve is quoted daily, but nobody has examined whether its "
                     "flattening degrades the return")),
    candidate(
        name="commodity roll premium, via broad commodity ETFs",
        claim_class=CLAIM_PREMIUM,
        payer="producers and consumers hedging price risk, who pay to transfer it",
        effect_low="0.10", effect_high="0.40",
        effect_source="published backwardation-harvest Sharpes are low and unstable, and "
                      "the sign has flipped for long stretches since 2008",
        available_years="10.75", data_source="Alpaca SIP from 2016-01-04",
        status=STATUS_UNTRIED, requires_short=False,
        monitor=monitor_status(
            variable="the commodity futures curve shape, backwardation against contango",
            status=MONITOR_UNEXAMINED,
            evidence="the curve is observable, but nobody has examined whether a flip "
                     "in its shape degrades the return")),
    candidate(
        name="variance risk premium, via CBOE VIX futures directly",
        claim_class=CLAIM_PREMIUM,
        payer="the same buyers of protection, without an ETP wrapper between the premium "
              "and the position",
        effect_low="0.50", effect_high="1.00",
        effect_source="the same literature as the ETP entries; what differs is the window",
        # CORRECTED 2026-10-03: an earlier entry called this UNREACHABLE. It is not.
        # CBOE's own public page links a settlement archive that returns 200 without
        # authentication, one file per expiry, from 2013-01-02. Guessing a CDN path had
        # returned 403 and the guess had been recorded as a fact. What stops it is the
        # LOT, not the data.
        available_years="13.75",
        data_source="CBOE settlement archive, free, from 2013-01-02",
        status=STATUS_UNTRIED, requires_short=True, minimum_position_usd="1800",
        monitor=monitor_status(
            variable=VIX_SLOPE, status=MONITOR_LINK_REFUTED, certainty=CERTAINTY_INFERRED,
            measured_on="SVXY, whose return is the inverse of the same VIX futures index",
            evidence=VIX_SLOPE_FAILED)),
]


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
    record = constitute_candidate_register(
        REGISTER, justification=JUSTIFICATION, candidates=CANDIDATES,
        declared_by=f"Oscar Carmenate Rodriguez, at {revision[:12]}", declared_at=now)

    ranked = rank_candidates(record)
    print("=" * 100)
    print(f"CANDIDATE REGISTER (schema {record['schema_version']}) -- {len(CANDIDATES)} "
          f"candidates; executor can open shorts: {EXECUTOR_CAN_OPEN_SHORTS}")
    print("=" * 100)
    print(f"{'':2} {'candidate':<42} {'range':>9} {'bar':>6} {'yrs':>6}  "
          f"{'verdict':<22} blockers")
    for item in ranked:
        mark = {"MEASURED_DEAD": "x", "DECLARED": ">", "UNTRIED": " "}[item["status"]]
        span = f"{item['effect']['low']}-{item['effect']['high']}"
        print(f"{mark:2} {item['name'][:42]:<42} {span:>9} {item['required']:>6} "
              f"{item['available_years']:>6}  {item['verdict']:<22} {_blockers(item)}")
    print("\n  x measured and dead     SHORT executor cannot open one")
    print("  R3! monitor refuted or impossible, MEASURED     r3? refuted on a SIBLING only")

    scored = [{**entry, **candidate_verdict(entry)} for entry in record["candidates"]]
    admissible = [item["name"] for item in scored if admissible_in_principle(item)]
    print(f"\n  ADMISSIBLE IN PRINCIPLE: {len(admissible)} of {len(scored)}"
          + ("" if admissible else "  -- none"))
    for name in admissible:
        print(f"    {name}")

    print("\n  WHAT EACH SINGLE CHANGE WOULD UNLOCK (candidates whose ONLY hard blocker it is):")
    for kind, names in what_would_unlock(record).items():
        print(f"    remove {kind}:")
        if not names:
            print("      nobody")
        for entry in names:
            weak = "  [still probably blocked at R3]" if entry["still_probably_blocked"] else ""
            print(f"      {entry['name']} ({entry['verdict']}){weak}")
    print(f"\n  {record['register_id'][:58]}")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())
