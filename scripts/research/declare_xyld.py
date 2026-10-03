"""Declare the variance risk premium through XYLD, and answer the seven questions first.

THE SECOND HYPOTHESIS DECLARED UNDER THE PRE-DECLARATION CONTRACT, and the first
under its aligned form. Its effect range STRADDLES its bar: the recalled
conservative end is 0.35 against a bar of 0.500, and the contract accepts it only
because PRE_DECLARATION_ALIGNMENT|0a659627 changed the refusal to the optimistic
end in the same session this was declared. That ordering is recorded as weak
provenance in the alignment and repeated in a sealed caveat below.

WHY THIS WRAPPER. The candidate register found 0 of 11 admissible in principle; the
only single change that unlocked anything was building shorts in the executor. A
covered-call fund sells options INSIDE the fund, so it is held with BUY in
fractional shares and the executor never opens a short. It is the variance risk
premium again, in a wrapper the platform can hold.

THE ORDER MATTERS. The answers are validated against the contract with a
placeholder identity BEFORE the Hypothesis is sealed. The earlier volatility
script sealed first and validated second, which would have left a Hypothesis with
no pre-declaration if the contract had refused it.

    python3.11 -B scripts/research/declare_xyld.py
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tramitago_quant_core.governance.evidence_host import require_evidence_host
from tramitago_quant_core.research.hypothesis import constitute_hypothesis
from tramitago_quant_core.research.pre_declaration import (
    pre_declaration, constitute_pre_declaration, CLAIM_PREMIUM)

ARTIFACTS = REPO / "artifacts" / "research"
HYPOTHESES = ARTIFACTS / "hypotheses.json"
PRE_DECLARATIONS = ARTIFACTS / "pre-declarations.json"

INSTRUMENT = "XYLD"
# The third trading day of the feed: bars begin 2016-01-04 and the dataset
# contract needs two warmup rows before the first evaluable one. The end matches
# the other equity Hypotheses; the last bar serves as the forward row.
PERIOD_START, PERIOD_END = "2016-01-06T00:00:00Z", "2026-10-01T00:00:00Z"

DESCRIPTION = """LEVEL CLAIM, one leg, zero turnover by construction.

XYLD (the Global X S&P 500 Covered Call ETF) held CONTINUOUSLY from 2016-01-06 to
2026-10-01: one entry, one exit, no signal, no rebalancing and no shorting. The net
return of that position, after execution costs, is POSITIVE and its figures clear
the admission thresholds of DOC-011 section 8.

THIS IS THE VARIANCE RISK PREMIUM in a wrapper the platform can hold. The fund
writes call options on the S&P 500 inside itself, so the position is bought with
BUY and held in fractional shares, and the executor, which cannot open a short,
never needs to. It is the same premium that SVXY expressed, measured at a point
Sharpe of 0.5021 with a lower bound of -0.0445 over 8.57 years, here over the
10.71 the feed serves for this instrument.

WHAT IS NEW IN HOW IT WAS CHOSEN. It is the second Hypothesis to answer seven
questions before spending, and its effect range STRADDLES its bar rather than
clearing it. That is declared, not hidden: the recalled conservative end is 0.35
against a bar of 0.500. The declaration is accepted only under the contract as
aligned with the candidate register on 2026-10-03.

THE DECLARED MONITOR AND WHAT IT CLAIMS. Variable: VIX minus the 21-day realised
volatility of the S&P 500. Degradation: that spread at or below zero, meaning
realised volatility has caught up with what the market charged for it. Action:
suspend. It is not the position's P&L. The link claimed is that such days degrade
the fund's return, to be tested fold by fold. IT DOES NOT CLAIM to separate the
premium dying from the market falling: realised volatility exceeds implied mostly
on down days for the underlying, so a link that holds may be detecting equity
drawdowns. That is a legitimate stop and a different claim, and the runner is
declared to test the same link on SPY as a control beside it."""

CAVEATS = [
    "THE EFFECT RANGE STRADDLES ITS BAR, AND THE CONTRACT ACCEPTED IT ONLY BECAUSE IT "
    "WAS CHANGED THE SAME DAY. The recalled range is 0.35 to 0.80 against a derived bar "
    "of 0.500. The earlier contract refused any declaration whose conservative end fell "
    "below the bar. PRE_DECLARATION_ALIGNMENT|0a659627 aligned it with the register, and "
    "records itself as partly weak provenance because the inconsistency was noticed while "
    "trying to declare this Hypothesis. Its own measurement is the warning: the class of "
    "straddling candidates has produced no admission, four of four measured members died.",

    "THIS IS ONE IDEA IN THREE WRAPPERS, AND ONLY ONE IS DECLARED. DIVO and QYLD sit in "
    "the register undeclared. They write options on overlapping indices, so their "
    "returns are highly correlated and the effective breadth of the three is close to "
    "one. Declaring a second would be a correlated draw that paid the multiplicity tax "
    "as though independent, and would have to say so.",

    "THE CHOICE OF THIS FAMILY FOLLOWED A MEASUREMENT OF THE SAME PREMIUM. The variance "
    "risk premium was measured on SVXY at 0.5021 with a bound of -0.0445, and the search "
    "for a wrapper over a longer window began there. That is selection on a neighbouring "
    "outcome. The multiplicity ledger should count this and the SVXY Hypothesis as "
    "correlated draws and not as independent ones.",

    "IT MIXES THE EQUITY PREMIUM WITH THE VOLATILITY PREMIUM. A covered-call fund "
    "carries an equity beta recalled at 0.6 to 0.8, so most of what is measured is the "
    "market. Admitting it would be beta with a decoration, which the equity premium own "
    "declaration marked as not evidence that this platform can find anything, and that "
    "applies here at least as strongly.",

    "THE MONITOR CAN PASS FOR THE WRONG REASON, AND THE RUNNER IS DECLARED TO CONTROL "
    "FOR IT. Realised volatility exceeds implied mostly on days the underlying falls, "
    "which are bad days for any equity position. The same link is to be reported on SPY "
    "beside the fund. If it holds equally on SPY, the monitor detects drawdowns and not "
    "this premium, and the claim stays the narrower one of a stop. Under "
    "M2_AVAILABILITY|04a35abd, if the empirical link was reachable and fails, R3 FAILS "
    "and the identity form is not available as a fallback.",

    "THE DISTRIBUTION ADJUSTMENT IS UNCHECKED AND IS THE LARGEST DATA RISK. The fund "
    "pays large monthly distributions, partly return of capital. If the adjusted bars "
    "omit them the measured Sharpe is understated, and if they double count it is "
    "overstated. Before judging anything the runner must compare adjusted against raw "
    "closes, infer the distributions the adjustment implies, and check their size is of "
    "the order the fund publishes. A failure of that check voids the measurement.",

    "A FUND IN THIS FAMILY HAS ALREADY CLOSED. PUTW, a put-write fund, returned 404 from "
    "the asset endpoint with its bars ending 2025-04-03. XYLD could close too, forcing "
    "an exit at a time not of the platform choosing. That risk is not in any return "
    "series and is not priced here.",

    "THE WINDOW IS THE FEED'S, AND IT CONTAINS THE TAILS. 2016-01-06 to 2026-10-01 is "
    "the whole span served for this instrument less three bars the dataset contract "
    "cannot evaluate. It contains February 2018, March 2020 and 2022. Expect the "
    "drawdown gate to bind and the derived weight to sit well below one, as it did for "
    "SPY and for SVXY.",

    "POPULATION AND MULTIPLICITY. It is declared into 'US equities, daily, risk premium', "
    "which holds one measured Hypothesis, and it counts in the multiplicity ledger as "
    "draw {draw} from the same search. One Hypothesis cannot resolve a population that "
    "needs five.",
]

ACCEPTANCE = {
    "metric": "net_sharpe(XYLD held continuously, net of execution cost)",
    "comparison": "GE", "threshold": "0.50", "expected_direction": "INCREASE",
}

ANSWERS = dict(
    payer="buyers of S&P 500 call options, who pay for upside exposure that the fund "
          "sells them inside the wrapper",
    why_they_keep_paying="they want convex upside at limited cost, and structured product "
                         "issuers hedge by buying calls, so demand for upside is not "
                         "sensitive to price; the premium is what the sellers earn for "
                         "taking the other side of that demand and for bearing the crash "
                         "risk it implies",
    crossings_per_year="0.19",
    net_level_claimed="the net return of holding XYLD continuously over the window, after "
                      "execution costs and with its expense ratio already inside its "
                      "price, rather than a difference between two groups of days",
    monitor_variable="VIX minus the 21-day realised volatility of the S&P 500",
    monitor_publisher="CBOE publishes VIX daily and FRED distributes it as VIXCLS, while "
                      "realised volatility is computed from index prices",
    plausible_low="0.35", plausible_high="0.80",
    plausibility_source="published studies of the buy-write index find a Sharpe roughly "
                        "equal to or modestly above the underlying over long samples; the "
                        "range is recalled, wide, and sits below the S&P 500 own at the low "
                        "end for the fund fee and the capped upside, and the conservative "
                        "end is below the bar",
    claim_class=CLAIM_PREMIUM,
    window_years="10.71", available_window_years="10.72",
    why_shorter_than_available="the dataset contract needs two warmup rows and one forward "
                               "row, so three of the 2702 bars the feed serves cannot be "
                               "evaluated",
    adverse_episode_in_window="February 2018, when VIX more than doubled in a day, March "
                              "2020, and the 2022 drawdown",
    source="answered in writing before any bar of XYLD was captured; the monitor trigger "
           "frequency was measured first, which is a fact about the monitor and never "
           "about the fund's returns",
)


def _distinct_hypotheses():
    return len({item["hypothesis_id"] for item in
                json.loads(HYPOTHESES.read_bytes())["hypotheses"]})


def main():
    require_evidence_host(REPO)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, check=True).stdout.strip()
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")

    # VALIDATE FIRST. With a placeholder identity, before anything is sealed: if
    # the contract refuses these answers, nothing has been written anywhere.
    pre_declaration(hypothesis_id="HYPOTHESIS|validation-only-nothing-is-sealed", **ANSWERS)

    draw = _distinct_hypotheses() + 1
    record = constitute_hypothesis(
        HYPOTHESES,
        description=DESCRIPTION + "\n\nPre-declared caveats:\n- "
                    + "\n- ".join(c.format(draw=draw) for c in CAVEATS),
        target_metric=ACCEPTANCE["metric"], expected_direction="INCREASE",
        constraints={"period": {"start_utc": PERIOD_START, "end_exclusive_utc": PERIOD_END},
                     "universe": [INSTRUMENT],
                     # sma_close_3 is declared and never used: the dataset contract
                     # imposes the default Strategy's column on every Hypothesis and
                     # a position that is merely HELD has no signal.
                     "variables": ["close", "forward_return_1d", "sma_close_3"]},
        acceptance_criterion=ACCEPTANCE, creation_timestamp=now, status="PROPOSED",
        created_by="Oscar Carmenate Rodriguez",
        provenance=[
            "CANDIDATE_REGISTER|c3d223593d26 -- 3 of 15 admissible in principle after the "
            "expansion",
            "PRE_DECLARATION_ALIGNMENT|0a659627 -- the contract aligned with the register, "
            "sealed before the code changed",
            "MEASURED|XYLD served 2702 bars from 2016-01-04, tradable and fractionable",
            "MEASURED|VIX minus 21-day realised volatility was at or below zero on 330 of "
            "2159 days and on at least ten days in 6 of 7 folds",
            "ADMISSION|b1fbbaf88a5a687bf63619d95bb1b394c2e3 -- the same premium on SVXY, "
            "denied on the Sharpe bound",
        ],
        code_revision=revision, system_version="0.1.0")

    answers = pre_declaration(hypothesis_id=record["hypothesis_id"], **ANSWERS)
    sealed = constitute_pre_declaration(PRE_DECLARATIONS, answers, declared_at=now,
                                        code_revision=revision)
    effect = sealed["required_effect"]

    print(f"{'=' * 78}\nHYPOTHESIS DECLARED -- no market data has been read\n{'=' * 78}")
    print(f"  id        {record['hypothesis_id'][:52]}  v{record['version']}  (draw {draw})")
    print(f"  position  {INSTRUMENT} held continuously, one leg, no shorting")
    print(f"  period    {PERIOD_START[:10]} -> {PERIOD_END[:10]}  ({ANSWERS['window_years']} yr)")
    print(f"\n  SEVEN QUESTIONS, answered before spending:")
    print(f"    1 who pays       {ANSWERS['payer'][:58]}")
    print(f"    2 crossings/yr   {ANSWERS['crossings_per_year']}")
    print(f"    3 net level      claimed, not a difference of means")
    print(f"    4 monitor        {ANSWERS['monitor_variable'][:56]}")
    print(f"    5 effect         {effect['plausible_low']} to {effect['plausible_high']} "
          f"against a bar of {effect['required_point_sharpe']}  "
          f"-> {effect['position_against_bar']}")
    print(f"    6 claim class    {ANSWERS['claim_class']}")
    print(f"    7 window         {ANSWERS['window_years']} yr of {ANSWERS['available_window_years']} served")
    print(f"\n  pre-declaration  {sealed['pre_declaration_id'][:50]}  (schema {sealed['schema_version']})")
    print(f"  caveats          {len(CAVEATS)} sealed")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
