"""Declare the equity risk premium as a Hypothesis, BEFORE any data is captured.

WHY THIS ONE, AND WHY NOW. On 2026-10-02 the net level of all three VALIDATED
Hypotheses was measured for the first time. Two lose money -- not because the
signal is wrong but because they ROTATE: the carry crosses the market 253 times
and pays 257% of its gross away, SMA3 on ETH 139 times and pays 138%. The one
that keeps its gross, spy-mom10-2024, turns over 34 times and keeps 98%.
Separately: 43 sealed datasets, median 368 rows, ZERO reaching three years --
and the net Sharpe gate needs about three years even for an effect as strong as
a point estimate of 1.83. No Hypothesis this platform ever captured could have
cleared that gate, however good it was.

So the next capture has to be long and the next position has to barely trade.
This one is both by construction: a single buy, a single sell, eight and
three-quarter years apart.

WHAT IS BEING CLAIMED. A LEVEL, not a comparison. Today's other finding was
that a validated comparison -- mean of the favoured group above the other --
says NOTHING about whether the favoured group's own return is positive, and
three times out of three nobody had checked. Here the thing validated IS the
thing that must be positive, so the gap cannot open.

WHAT THIS REALLY MEASURES: THE GATES, NOT THE MARKET. The equity risk premium
is the best documented, most harvested risk premium in finance. If §8's
thresholds refuse THIS, that is evidence about the thresholds' calibration and
not about whether the premium exists. Oscar asked on 2026-10-01 whether the
required return is simply set too high; this is the measurement that can answer
it, and the inference is DECLARED HERE IN ADVANCE so it cannot be invented
afterwards if the result disappoints.

Nothing in this file reads market data. The Hypothesis is sealed first and
captured second, by `run_premium.py (specs/spy.json)`, which needs credentials this
process does not have.

    python3.11 -B scripts/research/declare_equity_risk_premium.py
"""

import json
import subprocess
import sys
from functools import partial
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tramitago_quant_core.governance.evidence_host import require_evidence_host
from tramitago_quant_core.research.hypothesis import constitute_hypothesis, revise_hypothesis

HYPOTHESES = REPO / "artifacts" / "research" / "hypotheses.json"
# Version 1 was sealed on 2026-10-02 with variables ["close", "forward_return_1d"]
# and REFUSED by the dataset contract, which requires the default Strategy's
# column. Re-running this revises to version 2 rather than declaring a second
# Hypothesis: no market data had been read when v1 was sealed and none has been
# read now, so the amendment is visible, dated, and provably pre-data.
EXISTING_ID = "HYPOTHESIS|426cd26a-10ab-4c5e-ba5d-9e88510a4613"

SYMBOL = "SPY"
# DECLARED BEFORE CAPTURE. Chosen for data availability on Alpaca's free tier,
# not for what it contains -- though what it contains matters and is stated in
# the caveats: three distinct adverse states (2018Q4, 2020, 2022).
PERIOD_START = "2018-01-02T00:00:00Z"
PERIOD_END = "2026-10-01T00:00:00Z"

DESCRIPTION = """LEVEL CLAIM, zero turnover by construction.

For SPY total return (dividends reinvested, split adjusted), held CONTINUOUSLY
from 2018-01-02 to 2026-10-01 -- one entry, one exit, no rebalancing and no
signal -- the net daily return after execution costs is POSITIVE, and its
adverse 95% bound clears the admission thresholds of DOC-011 section 8.

This is the EQUITY RISK PREMIUM: the compensation an investor receives for
bearing undiversifiable market risk. It is not a signal, not a prediction and
not a mispricing. It is the one risk premium whose existence is not in dispute.

WHY IT IS DECLARED AS A LEVEL AND NOT A COMPARISON. On 2026-10-02 this project
measured the net level of all three Hypotheses it had validated and found that
two of them lose money despite being validated, because a validated difference
of means constrains neither group's own return. A level claim cannot open that
gap: what is judged is what would be held.

WHY IT TRADES ONCE. The same measurement found turnover to be the discriminator
that the validation criterion never looks at. Two transitions over eight and
three-quarter years is the floor, and it makes the execution cost a rounding
error against the gross rather than two and a half times it.

WHY THE WINDOW IS THIS LONG. The net Sharpe gate is a LOWER 95% bound, and a
bound is a statement about sample size as much as about effect. Projected on
spy-mom10-2024's own distribution, a point Sharpe of 1.83 needs about three
years before its lower bound clears +0.50. Every one of this project's 43
sealed datasets is shorter than that, median one year. This window is 8.75."""

CAVEATS = [
    "THIS IS BETA, NOT ALPHA, AND THAT IS THE POINT. If it is admitted, the "
    "platform's first admitted position is the market itself. That is on the "
    "declared destination -- risk premia, not mispricing -- but it is NOT "
    "evidence that this platform can find anything, and it must never be "
    "reported as though it were.",

    "THE DRAWDOWN GATE WILL ALMOST CERTAINLY REFUSE IT UNLEVERED. SPY fell "
    "about 34% peak to trough in March 2020 and about 25% in 2022, against a "
    "declared 15% limit. This is DECLARED IN ADVANCE as the expected failure, "
    "and the declared response is CAP-006 sizing: scaling multiplies every "
    "period by the weight, so a declared weight below 1.0 can rescue a "
    "drawdown and can NEVER rescue a sign or a consistency. If the drawdown "
    "gate is the only one that refuses, the position is to be re-judged at a "
    "declared weight -- and that re-judgement is a sizing decision, not a new "
    "Hypothesis.",

    "POPULATION GERRYMANDERING IS THE REAL RISK HERE AND IT IS NAMED, NOT "
    "HIDDEN. 'US equities, daily, directional' was sealed EXHAUSTED BY "
    "MEASUREMENT on 2026-10-02 (7 Hypotheses, rate 0.410, 95% bound 0.554 "
    "against a 0.70 threshold). This is declared into a different population, "
    "'US equities, daily, risk premium', on the ground that all 7 exhausted "
    "Hypotheses are signals conditioning next-day direction and none is an "
    "unconditional level claim on holding the asset. That distinction is "
    "principled, but splitting a population is also exactly how any stopping "
    "rule can be escaped, and whoever reads this later should weigh it as such "
    "rather than take the split for granted.",

    "2018-2026 IS AN EXCEPTIONAL DECADE FOR US EQUITIES and the measured "
    "premium will overstate its long-run value. The window was fixed by what "
    "Alpaca's free tier can serve, before any of it was read, but it cannot be "
    "called representative of the equity risk premium across history.",

    "ONE HYPOTHESIS DOES NOT SATISFY THE STOPPING RULE, which requires at least "
    "5 per population before its bound is applicable. This OPENS a population; "
    "it cannot resolve one, and no exhaustion verdict may be drawn from it.",

    "THE FEED IS SIP, THE CONSOLIDATED TAPE, AND THE FREE ONE WOULD HAVE "
    "QUIETLY CORRUPTED THIS. Version 2's capture refused, and the probe said "
    "why: over this window IEX is missing 644 of 2198 NYSE sessions and its "
    "earliest bar is 2018-11-01, so its first fully covered window starts "
    "2020-07-24 -- four months AFTER the March 2020 crash. Adopting it would "
    "have removed the worst tail of the equity risk premium and kept the "
    "entire recovery, inflating the very quantity being measured, and it "
    "would have passed the three-year floor while doing so. SIP covers all "
    "2198 sessions from 2016-01-04. The window is UNCHANGED; only the feed "
    "moved, and the request URL is sealed into the capture's own identity so "
    "which one was used is not a matter of anyone's memory.",

    "SURVIVORSHIP IS NOT AT ISSUE for a single index ETF, but the ETF's own "
    "expense ratio (0.0945%/yr) is already inside the total return, so the "
    "figure measured is net of it. That makes the measurement conservative "
    "rather than optimistic, and it is noted so nobody adds the fee twice.",
]

ACCEPTANCE = {
    "metric": ("net_sharpe_lower_bound_95(SPY total return held continuously, "
               "net of execution cost)"),
    "comparison": "GE",
    "threshold": "0.50",
    "expected_direction": "INCREASE",
}


def main():
    require_evidence_host(REPO)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO,
                              capture_output=True, text=True, check=True).stdout.strip()
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")

    existing = [item for item in json.loads(HYPOTHESES.read_bytes())["hypotheses"]
                if item["hypothesis_id"] == EXISTING_ID]
    seal = (partial(revise_hypothesis, HYPOTHESES, EXISTING_ID) if existing
            else partial(constitute_hypothesis, HYPOTHESES))
    record = seal(
        description=DESCRIPTION + "\n\nPre-declared caveats:\n- " + "\n- ".join(CAVEATS),
        target_metric=ACCEPTANCE["metric"],
        expected_direction="INCREASE",
        constraints={"period": {"start_utc": PERIOD_START, "end_exclusive_utc": PERIOD_END},
                     "universe": [SYMBOL],
                     # sma_close_3 is declared and NEVER USED. The sealed-dataset
                     # contract requires the default Strategy's column among a
                     # Hypothesis's variables, because every Hypothesis this
                     # platform has ever held was a signal. A position that is
                     # simply HELD has no signal and no column, and the data
                     # layer cannot express that today. This is the minimum
                     # that makes the capture legal, it touches neither the
                     # claim nor the period nor the criterion, and the limit is
                     # FLAGGED rather than redesigned around.
                     "variables": ["close", "forward_return_1d", "sma_close_3"]},
        acceptance_criterion=ACCEPTANCE,
        creation_timestamp=now,
        status="PROPOSED",
        created_by="Oscar Carmenate Rodriguez",
        provenance=[
            "ADMISSION|the first admission review, which denied all three VALIDATED",
            "ANALYSIS|net-level-of-all-validated-2026-10-02",
            "MEASURED|turnover is the discriminator: 253 transitions cost 257% of gross,"
            " 139 cost 138%, 34 cost 2%",
            "MEASURED|43 sealed datasets, median 368 rows, zero reaching three years",
            "STOPPING_RULE|7c65a5829295a01d100ddcd4c63e18dad7065d657fc468ffa1670fddfd131954",
        ],
        code_revision=revision, system_version="0.1.0")

    print(f"{'=' * 78}\nHYPOTHESIS DECLARED -- no market data has been read\n{'=' * 78}")
    print(f"  id        {record['hypothesis_id'][:52]}")
    print(f"  period    {PERIOD_START[:10]} -> {PERIOD_END[:10]}   ({SYMBOL}, daily)")
    print(f"  claim     LEVEL, held continuously, 2 transitions")
    print(f"  accepts   {ACCEPTANCE['metric']}")
    print(f"            {ACCEPTANCE['comparison']} {ACCEPTANCE['threshold']}")
    print(f"  version   {record['version']}")
    print(f"  caveats   {len(CAVEATS)} pre-declared")
    print(f"\n  Next: run_premium.py (specs/spy.json), which needs ALPACA_PAPER_API_KEY_ID")
    print(f"        and ALPACA_PAPER_API_SECRET_KEY in the environment.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
