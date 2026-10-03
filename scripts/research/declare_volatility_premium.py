"""Declare the variance risk premium, and answer the seven questions first.

THE FIRST HYPOTHESIS DECLARED UNDER THE PRE-DECLARATION CONTRACT. Of the 47
before it, two named who pays; the multiplicity audit measured what the other
forty-five bought -- the pre-declared effect came back positive on 78 of 189
folds, 0.413 against a coin's 0.500, and the best p-value in the project is
short of its own correction by a factor of forty-six.

WHY THIS PREMIUM, AND WHY NOW. The equity premium cleared five of six gates and
died on monitorability: an unconditional premium has no degradation variable
that is not its own P&L. The credit premium had a quoted one and died on size,
0.19 against a bar near 0.54 -- and its monitor could not be tested either,
because the trigger state occurred on ZERO of 2184 days. This is the first
candidate whose death state ACTUALLY OCCURS:

  VIX term structure inverted on 173 of 2159 days (8.0%), in 7 of 8 years,
  reaching -18.23 at its worst, and present in 5 of 7 folds at 10 days or more.

That is what makes M1 -- the empirical form of the inferential link -- available
here for the first time, rather than M2's declared identity.

THE INSTRUMENT IS WHERE THIS COULD HAVE GONE WRONG QUIETLY. SVXY changed from
-1x to -0.5x on 2018-02-28, so any window spanning that date mixes two different
instruments under one ticker. The window starts 2018-03-01 and the structure is
one structure throughout. Alpaca serves 2159 sessions there, which is exactly
the number of days on which both VIX and VIX3M exist -- the position and its
monitor cover the same ground, day for day.

    python3.11 -B scripts/research/declare_volatility_premium.py
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tramitago_quant_core.governance.evidence_host import require_evidence_host
from tramitago_quant_core.research.hypothesis import constitute_hypothesis, revise_hypothesis
from tramitago_quant_core.research.pre_declaration import (
    pre_declaration, constitute_pre_declaration, CLAIM_PREMIUM)

ARTIFACTS = REPO / "artifacts" / "research"
HYPOTHESES = ARTIFACTS / "hypotheses.json"
PRE_DECLARATIONS = ARTIFACTS / "pre-declarations.json"
EXISTING_ID = None

INSTRUMENT = "SVXY"
PERIOD_START, PERIOD_END = "2018-03-01T00:00:00Z", "2026-10-01T00:00:00Z"

DESCRIPTION = """LEVEL CLAIM, one leg, zero turnover by construction.

SVXY (-0.5x daily short VIX futures) held CONTINUOUSLY from 2018-03-01 to
2026-10-01: one entry, one exit, no signal, no rebalancing and no shorting. The
net return of that position, after execution costs, is POSITIVE and its figures
clear the admission thresholds of DOC-011 section 8.

THIS IS THE VARIANCE RISK PREMIUM: the compensation received for bearing
equity-index volatility risk that someone else is paying to shed. It is not a
signal, not a prediction and not a convergence trade.

WHY IT IS DECLARED. Two premia have now been refused for reasons that had
nothing to do with how much they paid. The equity premium has no degradation
variable that is not its own P&L. The credit premium had a quoted one whose
trigger state occurred on ZERO of 2184 days, so no walk-forward could establish
the link and only the declared-identity form was available. This candidate's
death state OCCURS -- 173 days, 8.0%, 7 of 8 years, 5 of 7 folds -- which makes
the empirical form of R3 reachable here for the first time.

THE DECLARED MONITOR. Variable: the VIX term structure, VIX3M minus VIX, both
published daily by CBOE and distributed through FRED. Degradation: the slope
falling to or below zero, meaning the futures curve has inverted and the roll
yield that constitutes this premium has reversed. Action: suspend. It is the
price of a DIFFERENT instrument, forward-looking rather than realised, and it
would keep being published if this position were never opened."""

CAVEATS = [
    "THE MONITOR IS CLOSER TO THE POSITION THAN THE CREDIT ONE WAS, and that is "
    "declared rather than discovered. SVXY's return is driven by the roll "
    "yield, and the roll yield is the term structure, so the monitored variable "
    "and the position are related in a way BAA10Y and an LQD/IEF spread were "
    "not. They remain distinct quantities: the slope is the compensation being "
    "OFFERED, forward-looking, while the position's return is what was "
    "REALISED, and the two come apart -- spot volatility can spike while the "
    "curve stays in contango, and the curve can inverted on a day the position "
    "is flat. It qualifies, and it is nearer the line than the last one.",

    "THE DRAWDOWN GATE IS WHERE THIS IS EXPECTED TO BITE HARDEST. Short "
    "volatility is the archetype of a premium paid in small regular amounts and "
    "repaid in one catastrophic instalment: SVXY fell roughly 55% peak to "
    "trough in February and March 2020, INSIDE this window. Expect a derived "
    "weight well below the 28% SPY needed. DECLARED IN ADVANCE as the expected "
    "failure, with CAP-006 sizing as the declared response.",

    "THE WINDOW EXCLUDES FEBRUARY 2018 AND THAT IS NOT A CHOICE. SVXY was -1x "
    "until 2018-02-28 and lost about 90% on 2018-02-05 under that structure. A "
    "window reaching back past the change would measure two different "
    "instruments under one ticker, which is worse than excluding an episode. "
    "What the window does contain is March 2020, a larger volatility shock by "
    "VIX level, so the tail is present even though the worst day for the old "
    "structure is not.",

    "ONE GATE STILL CARRIES THE RISK, as it did for the credit premium: the "
    "net Sharpe. The pre-declaration answers it with 0.60 as the CONSERVATIVE "
    "end of a sourced range, not its midpoint, and 0.60 clears the 0.54 bar by "
    "six hundredths. If the measurement lands near the bottom of that range "
    "the Hypothesis dies and the declaration was close to right; that is a "
    "narrow margin and it is stated here rather than left to be noticed.",

    "A LEVERAGED PRODUCT HAS A DRAG THE PREMIUM DOES NOT. SVXY rebalances to "
    "-0.5x daily, and daily rebalancing of a leveraged exposure costs something "
    "in a volatile market regardless of direction. Part of whatever is measured "
    "is that drag rather than the premium, and no part of this declaration "
    "separates them.",

    "POPULATION: 'US volatility ETPs, daily, risk premium', in which ZERO "
    "Hypotheses have been measured. Named for the same reason as the previous "
    "two: it is neither population sealed EXHAUSTED, and declaring into a fresh "
    "one is also how any stopping rule gets escaped.",

    "IT COUNTS IN THE MULTIPLICITY LEDGER. It is the forty-eighth draw from "
    "the same search, and the first to answer seven questions before spending. "
    "Answering them does not make it a free shot -- it makes it a cheaper one "
    "to refuse.",
]

ACCEPTANCE = {
    "metric": "net_sharpe(SVXY held continuously, net of execution cost)",
    "comparison": "GE", "threshold": "0.50", "expected_direction": "INCREASE",
}

ANSWERS = dict(
    payer="buyers of VIX futures and index options -- portfolio managers and dealers "
          "purchasing protection against equity drawdowns",
    why_they_keep_paying="they are buying insurance against losses they are mandated or "
                         "unwilling to bear, and insurance that is not priced above its "
                         "expected cost would not be written by anyone; the premium is "
                         "the price of that transfer rather than a mistake being made",
    crossings_per_year="0.23",
    net_level_claimed="the net return of holding SVXY continuously over the window, after "
                      "execution costs, rather than a difference between two groups of days",
    monitor_variable="VIX3M minus VIX, the term structure slope",
    monitor_publisher="CBOE, published daily and distributed through FRED as VXVCLS "
                      "and VIXCLS",
    required_point_sharpe="0.54",
    plausible_point_sharpe="0.60",
    plausibility_source="published estimates of the roll-yield harvest on short VIX "
                        "futures run roughly 0.5 to 1.0 before catastrophic tail events; "
                        "0.60 is the CONSERVATIVE end of that range and not its midpoint, "
                        "and the -0.5x structure dilutes leverage without changing a "
                        "ratio, though its daily rebalancing drag is not in the estimate",
    claim_class=CLAIM_PREMIUM,
    window_years="8.57",
    adverse_episode_in_window="March 2020, when VIX reached 82 and the term structure "
                              "inverted on 53 days of that year, plus the 2025 spike with "
                              "29 inverted days",
    source="answered in writing before any bar of SVXY was captured; the monitor's "
           "availability and inversion frequency were measured first, which is a fact "
           "about the monitor and never about the position's returns",
)


def main():
    require_evidence_host(REPO)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, check=True).stdout.strip()
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")

    existing = [item for item in json.loads(HYPOTHESES.read_bytes())["hypotheses"]
                if EXISTING_ID and item["hypothesis_id"] == EXISTING_ID]
    seal = (partial(revise_hypothesis, HYPOTHESES, EXISTING_ID) if existing
            else partial(constitute_hypothesis, HYPOTHESES))

    record = seal(
        description=DESCRIPTION + "\n\nPre-declared caveats:\n- " + "\n- ".join(CAVEATS),
        target_metric=ACCEPTANCE["metric"], expected_direction="INCREASE",
        constraints={"period": {"start_utc": PERIOD_START, "end_exclusive_utc": PERIOD_END},
                     "universe": [INSTRUMENT],
                     # sma_close_3 declared and never used: the dataset contract
                     # imposes the default Strategy's column on every Hypothesis
                     # and a position that is merely HELD has no signal.
                     "variables": ["close", "forward_return_1d", "sma_close_3"]},
        acceptance_criterion=ACCEPTANCE, creation_timestamp=now, status="PROPOSED",
        created_by="Oscar Carmenate Rodriguez",
        provenance=[
            "PRE_DECLARATION_CONTRACT|the first Hypothesis to answer seven questions"
            " before any data was captured",
            "ADMISSION|68f53870628d8a6c50572604e077f1be5f41",
            "MEASURED|the VIX term structure inverted on 173 of 2159 days, 7 of 8 years,"
            " 5 of 7 folds -- so the empirical form of R3 is reachable here",
            "MEASURED|SVXY serves 2159 sessions from 2018-03-01, the date its structure"
            " became -0.5x, matching the days on which VIX and VIX3M both exist",
            "CRITERION|a premium is monitorable when its compensation is QUOTED,"
            " separately from the position's own P&L",
        ],
        code_revision=revision, system_version="0.1.0")

    answers = pre_declaration(hypothesis_id=record["hypothesis_id"], **ANSWERS)
    sealed = constitute_pre_declaration(PRE_DECLARATIONS, answers, declared_at=now,
                                        code_revision=revision)

    print(f"{'=' * 78}\nHYPOTHESIS DECLARED -- no market data has been read\n{'=' * 78}")
    print(f"  id        {record['hypothesis_id'][:52]}  v{record['version']}")
    print(f"  position  {INSTRUMENT} held continuously, one leg, no shorting")
    print(f"  period    {PERIOD_START[:10]} -> {PERIOD_END[:10]}  ({ANSWERS['window_years']} yr)")
    print(f"\n  SEVEN QUESTIONS, answered before spending:")
    print(f"    1 who pays       {ANSWERS['payer'][:58]}")
    print(f"    2 crossings/yr   {ANSWERS['crossings_per_year']}")
    print(f"    3 net level      claimed, not a difference of means")
    print(f"    4 monitor        {ANSWERS['monitor_variable']} -- {ANSWERS['monitor_publisher'][:34]}")
    print(f"    5 effect needs   {ANSWERS['required_point_sharpe']}, plausible "
          f"{ANSWERS['plausible_point_sharpe']} (conservative end)")
    print(f"    6 claim class    {ANSWERS['claim_class']}")
    print(f"    7 window         {ANSWERS['window_years']} yr, March 2020 inside it")
    print(f"\n  pre-declaration  {sealed['pre_declaration_id'][:50]}")
    print(f"  caveats          {len(CAVEATS)} sealed")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
