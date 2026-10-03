"""Declare the credit risk premium, BEFORE any data is captured.

WHY THIS ONE. On 2026-10-02 the equity risk premium cleared FIVE of six gates
and was denied on R3 alone: an unconditional premium has no degradation variable
that is not its own P&L, and the monitor contract refuses P&L surveillance
whatever its latency. No amount of work fixes that -- it is a property of the
premium, not of the effort spent on it.

What fell out of that failure is a criterion: A PREMIUM IS MONITORABLE WHEN ITS
COMPENSATION IS QUOTED SOMEWHERE, SEPARATELY FROM THE POSITION'S P&L. The carry
had one and failed on returns; equity had returns and failed on monitoring. The
credit spread is quoted daily and published by a third party, so this is the
first Hypothesis here where every one of the six gates is either already solved
or measurable, and none is structurally out of reach.

WHAT IS CLAIMED. A LEVEL, not a comparison: long HYG, short IEF at equal
notional, held continuously 2018-01-02 to 2026-10-01 -- one entry, one exit, no
signal and no rebalancing. The quantity judged is the quantity that would be
held, which is the gap a validated comparison leaves open and three of this
project's Hypotheses fell into.

WHERE THE RISK IS, STATED BEFORE MEASURING. One gate. If the point Sharpe comes
in below 0.50 it is MEASURED AND BAD, which DOC-011 makes an absolute veto that
no size redeems. Claude deliberately did NOT compute it before declaring: the
data is already captured and paid for, and looking first would select the
Hypothesis on its own outcome, which is the single thing this apparatus exists
to prevent.

    python3.11 -B scripts/research/declare_credit_premium.py
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

HYPOTHESES = REPO / "artifacts" / "research" / "hypotheses.json"
# Set after the first sealing so a re-run REVISES rather than declaring a second
# Hypothesis -- an amendment is visible and dated, a duplicate is a free retry.
EXISTING_ID = "HYPOTHESIS|cf55e397-75ab-49d9-8f69-1e155e312a2c"

PRIMARY, SECOND_LEG = "LQD", "IEF"
MONITOR_SERIES = "BAA10Y"
PERIOD_START, PERIOD_END = "2018-01-02T00:00:00Z", "2026-10-01T00:00:00Z"

DESCRIPTION = """LEVEL CLAIM, two legs, zero turnover by construction.

Long LQD (investment-grade corporate bonds) and short IEF (7-10 year Treasuries)
at EQUAL NOTIONAL, both total return, held CONTINUOUSLY from 2018-01-02 to
2026-10-01: one entry, one exit, no signal, no rebalancing. The net return of
that position, after execution costs on both legs, is POSITIVE and its figures
clear the admission thresholds of DOC-011 section 8.

THIS IS THE CREDIT RISK PREMIUM: the compensation an investor receives for
bearing default and illiquidity risk that a Treasury holder does not.

WHY IT IS DECLARED NOW. The equity risk premium cleared five of six gates on
2026-10-02 and was denied on R3 alone, because the only observable that reports
an unconditional premium dying is it not paying -- which is P&L, which the
monitor contract refuses. That is structural and unfixable. The criterion that
followed is MONITORABILITY FIRST: a premium is monitorable when its
compensation is QUOTED somewhere, separately from the position's own P&L.

WHY VERSION 2 CHANGED BOTH LEGS' CREDIT QUALITY. Version 1 declared HYG, high
yield, monitored by the ICE BofA High Yield OAS. MEASURED 2026-10-02 with a key
Oscar obtained for the purpose: FRED holds only 794 observations of that series,
2023-10-03 onward -- ICE restricts historical redistribution, so the free CSV
and the keyed API return the same rolling three-year window and neither can be
paginated past it. A monitor whose history begins in late 2023 HAS NEVER
OBSERVED A CREDIT CRISIS, and the representativeness gate would have been right
to refuse it. The same probe found Moody's Baa spread, BAA10Y, with 9,589 daily
observations reaching back at least to 1990 and through every crisis since.

So the position moved to the credit quality the monitor can actually see. That
also repaired an impurity version 1 had to declare rather than fix: HYG's
duration is about 3.5 years against IEF's 7.5, leaving the spread NET SHORT
DURATION, so part of it was a rates bet that 2022 flattered. LQD's duration is
about 8.3 years, close enough to IEF's that what remains is credit.

THE DECLARED MONITOR. Variable: BAA10Y, Moody's Seasoned Baa Corporate Bond
Yield relative to the 10-Year Treasury, published daily by a third party.
Degradation: the spread falling to or below a declared floor, meaning the market
has stopped paying for default risk and the mechanism this position rests on has
stopped operating. Action: suspend. It is not the position's P&L, it is not
derived from LQD or IEF prices, and it would keep being published if this
position were never opened."""

CAVEATS = [
    "ONE GATE CARRIES ALL THE RISK: the net Sharpe. If the point estimate lands "
    "below 0.50 it is MEASURED AND BAD, which DOC-011 holds to be an ABSOLUTE "
    "VETO that no size converts -- unlike a drawdown, which size resolves. "
    "Claude did not compute it before this was sealed, in either version, "
    "although the SIP feed that serves both legs is already paid for and the "
    "measurement would have taken a minute. Looking first would have selected "
    "the Hypothesis on its own outcome.",

    "THE MONITOR IS NOT THE SAME INSTRUMENT AS THE POSITION, AND CANNOT BE. "
    "BAA10Y is Moody's Baa yield against a 10-year Treasury; the position is an "
    "ETF holding investment-grade corporates across A and Baa against a 7-10 "
    "year Treasury ETF. They are the same premium at close but not identical "
    "credit quality, so LQD's own spread runs tighter than pure Baa. The "
    "monitor detects the premium DYING -- compensation for default risk "
    "collapsing -- which is a move that cannot happen in Baa without having "
    "happened in LQD. It is a weaker instrument for detecting a DIVERGENCE "
    "between the two, and that limitation is declared rather than discovered.",

    "VERSION 2 CHANGED THE INSTRUMENT AFTER VERSION 1 WAS SEALED, and the "
    "reason must be weighed by whoever reads this. It was NOT because anything "
    "was measured about HYG's returns -- nothing was, in either version. It was "
    "because the monitor HYG required does not exist with history, which is a "
    "fact about a data vendor and not about the market. Still, revising an "
    "instrument after declaring it is the shape of a free retry, and the only "
    "thing distinguishing this from one is that no outcome was observed between "
    "the versions.",

    "SHORTING IEF IS ASSUMED TO COST ONLY THE DECLARED RATES. Borrow cost and "
    "borrow availability are real, vary over time, and are absent from daily "
    "bar data. A Treasury ETF is about as easy to borrow as anything gets, "
    "which makes the assumption reasonable and still an assumption.",

    "THE TAIL IS IN THE WINDOW. LQD fell about 22% peak to trough in March 2020 "
    "as investment-grade credit seized, which is GOOD for the tail-coverage "
    "gate and is what the drawdown gate will have to be sized against. Expect "
    "the derived weight to be well below one, as it was for SPY.",

    "POPULATION: 'US fixed income ETFs, daily, risk premium', in which ZERO "
    "Hypotheses have been measured. It is neither of the two sealed EXHAUSTED "
    "BY MEASUREMENT on 2026-10-02. That is honest and it is also how any "
    "stopping rule gets escaped, so the split is named exactly as it was for "
    "the equity premium, and one Hypothesis cannot resolve a population that "
    "needs five.",

    "IT COUNTS IN THE MULTIPLICITY LEDGER, once, as one Hypothesis across both "
    "versions. It is not a free shot because the equity premium was denied on a "
    "technicality; it is the next draw from the same search.",
]

ACCEPTANCE = {
    "metric": ("net_sharpe(long LQD, short IEF at equal notional, held continuously, "
               "net of execution cost on both legs)"),
    "comparison": "GE",
    "threshold": "0.50",
    "expected_direction": "INCREASE",
}


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
        target_metric=ACCEPTANCE["metric"],
        expected_direction="INCREASE",
        constraints={"period": {"start_utc": PERIOD_START, "end_exclusive_utc": PERIOD_END},
                     "universe": [PRIMARY],
                     # pair_close is the IEF leg, sealed as an auxiliary source;
                     # sma_close_3 is declared and NEVER USED, because the dataset
                     # contract imposes the default Strategy's column on every
                     # Hypothesis -- a position that is merely HELD has no signal
                     # and the data layer still cannot express that.
                     "variables": ["close", "pair_close", "forward_spread_return_1d",
                                   "sma_close_3"]},
        acceptance_criterion=ACCEPTANCE,
        creation_timestamp=now,
        status="PROPOSED",
        created_by="Oscar Carmenate Rodriguez",
        provenance=[
            "ADMISSION|6d61c78276ecf615a6ed77b13c559a395768",
            "ANALYSIS|equity-risk-premium denied on R3 alone, 5 of 6 gates cleared",
            "CRITERION|a premium is monitorable when its compensation is QUOTED,"
            " separately from the position's own P&L",
            "MEASURED|FRED holds 794 observations of BAMLH0A0HYM2 from 2023-10-03"
            " (ICE restricts history) against 9589 of BAA10Y from 1990",
            "THRESHOLD_CORRECTION|efdfe375c4b670b9cd5c041005fef5054",
            "STOPPING_RULE|7c65a5829295a01d100ddcd4c63e18dad7065d657fc468ffa1670fddfd131954",
        ],
        code_revision=revision, system_version="0.1.0")

    print(f"{'=' * 78}\nHYPOTHESIS DECLARED -- no market data has been read\n{'=' * 78}")
    print(f"  id        {record['hypothesis_id'][:52]}  v{record['version']}")
    print(f"  position  long {PRIMARY}, short {SECOND_LEG}, equal notional, held throughout")
    print(f"  period    {PERIOD_START[:10]} -> {PERIOD_END[:10]}")
    print(f"  accepts   point Sharpe GE 0.50 and its lower 95% bound > 0")
    print(f"  monitor   {MONITOR_SERIES} -- QUOTED daily since 1990, not this position's P&L")
    print(f"  caveats   {len(CAVEATS)} pre-declared")
    print(f"\n  Still needed to judge it: a sealed-capture provider for the spread.")
    print(f"  Without it R3 is NOT_EVALUABLE and this lands where the equity premium did.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
