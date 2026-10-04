"""Declare the variance risk premium through VIXM held SHORT, in PAPER, and answer the seven questions first.

THE FIRST HYPOTHESIS WHOSE POSITION IS A SHORT. The engine measures a simple short (return is
the negative of the instrument, a declared borrow fee while held, no natural loss limit) and the
executor can open one in PAPER only, for VIXM, inside config/instrument_contracts.json. This
measurement answers whether the effect is there. It does not authorise operating it: real
capital shorts are off in config/execution_capabilities.json.

WHY VIXM. It is the one short-requiring candidate in the register whose monitor link nobody has
measured. VIXY (front month) inherits the refuted SVXY link by inference. VIXM tracks months
four to seven of the VIX futures curve, while the monitor compares months one and three, so the
SVXY result is NOT carried over: it is a different part of the curve.

    python3.11 -B scripts/research/declare_vixm_short.py
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

INSTRUMENT = "VIXM"
PERIOD_START, PERIOD_END = "2016-01-06T00:00:00Z", "2026-10-01T00:00:00Z"

DESCRIPTION = """LEVEL CLAIM, one leg, zero turnover by construction, held SHORT.

VIXM (a long position in months four to seven of the VIX futures curve) held SHORT
CONTINUOUSLY from 2016-01-06 to 2026-10-01: one entry, one exit, no signal and no rebalancing.
The net return of that short, after execution costs and a declared borrow fee, is POSITIVE
and its figures clear the admission thresholds of DOC-011 section 8.

MECHANISM: the variance risk premium. Buyers of volatility protection pay a roll cost that a
holder of VIXM bears every month the curve is in contango; the short collects it. It is the
same premium measured on SVXY, taken from the other side and further out the curve.

THE DECLARED MONITOR, FIXED BEFORE ANY VIXM BAR IS CAPTURED: VIX3M minus VIX, the same series
and the same rule as the SVXY Hypothesis (inversion at or below zero, confirmed over three
periods, re-entry at 1.0). It is NOT changed for this instrument. It was refuted on SVXY (2 of 5
folds) and nobody has examined it on a product that sits further out the curve; that is what
this measurement tests.

PAPER ONLY. Operating this short with real capital is not possible and nothing here changes
that."""

CAVEATS = [
    "A SHORT LOSES WITHOUT BOUND, AND THE HISTORY MAY HOLD FEW SQUEEZES. VIXM decays, so its "
    "sample contains few days when it rose sharply, and the short loses exactly there. The "
    "engine reports the worst day against and the days beyond the declared adverse move of 10 "
    "percent; those figures, not the Sharpe, are the ones to read first.",

    "THE BORROW FEE IS DECLARED, NOT MEASURED. It is set at 3 percent a year, above what an "
    "easy-to-borrow ETF usually costs, as the conservative side. A broker rate card was not "
    "read, and a recall or a hard-to-borrow spell is not in any return series.",

    "THE EFFECT RANGE STRADDLES ITS BAR. The recalled range is 0.30 to 0.70 against a bar near "
    "0.52. Of the straddling candidates measured so far, none has been admitted.",

    "THE MONITOR IS CARRIED OVER FROM A PRODUCT WHERE IT FAILED. The slope compares months one "
    "and three and VIXM tracks months four to seven, so the link may weaken or hold. The "
    "crossings per year, 0.23, is the value measured for the SVXY window and is carried over "
    "unmeasured. Under M2_AVAILABILITY|04a35abd no identity fallback is declared: if the empirical "
    "link is reachable and fails, R3 fails.",

    "THE WINDOW IS THE FEED, AND IT CONTAINS THE TAILS. 2016-01-06 to 2026-10-01 includes "
    "February 2018 and March 2020, when VIX products rose sharply. Expect the drawdown gate to "
    "bind and the derived weight to be small.",

    "THIS COUNTS AS A CORRELATED DRAW. It is the third measurement of one premium (SVXY, XYLD, "
    "and this), and the multiplicity ledger should count it as draw {draw}, correlated with "
    "them, not independent.",
]

ACCEPTANCE = {
    "metric": "net_sharpe(VIXM held short continuously, net of execution cost and borrow)",
    "comparison": "GE", "threshold": "0.50", "expected_direction": "INCREASE",
}

ANSWERS = dict(
    payer="buyers of VIX futures and index options purchasing protection against equity "
          "drawdowns they are mandated or unwilling to bear, further out the curve where "
          "they hedge horizon rather than event risk",
    why_they_keep_paying="they are buying insurance against losses they are mandated or "
                         "unwilling to bear, and insurance not priced above its expected cost "
                         "would be written by nobody; the roll cost of holding VIXM is the "
                         "price of that transfer",
    crossings_per_year="0.23",
    net_level_claimed="the net return of holding VIXM SHORT continuously over the window, "
                      "after execution costs and a declared borrow fee, rather than a "
                      "difference between two groups of days",
    monitor_variable="VIX3M minus VIX, the term structure slope",
    monitor_publisher="CBOE, published daily and distributed through FRED as VXVCLS and VIXCLS",
    plausible_low="0.30", plausible_high="0.70",
    plausibility_source="mid-term VIX futures carry a flatter curve, so the roll harvest is "
                        "smaller than the short-term one by roughly a third in published "
                        "comparisons; the range is recalled and wide because the comparison "
                        "is less studied than the front, and its conservative end is below "
                        "the bar",
    claim_class=CLAIM_PREMIUM,
    window_years="10.71", available_window_years="10.75",
    why_shorter_than_available="the dataset contract needs two warmup rows and one forward "
                               "row, and the window starts two sessions after the first bar",
    adverse_episode_in_window="February 2018 and March 2020, when VIX products rose sharply "
                              "in days and a short in them lost most",
    source="answered in writing before any bar of VIXM was captured; the monitor frequency "
           "was measured on SVXY, which is a fact about the monitor and never about VIXM",
)


def main():
    require_evidence_host(REPO)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, check=True).stdout.strip()
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    pre_declaration(hypothesis_id="HYPOTHESIS|validation-only-nothing-is-sealed", **ANSWERS)

    draw = len({i["hypothesis_id"] for i in json.loads(HYPOTHESES.read_bytes())["hypotheses"]}) + 1
    record = constitute_hypothesis(
        HYPOTHESES,
        description=DESCRIPTION + "\n\nPre-declared caveats:\n- "
                    + "\n- ".join(c.format(draw=draw) for c in CAVEATS),
        target_metric=ACCEPTANCE["metric"], expected_direction="INCREASE",
        constraints={"period": {"start_utc": PERIOD_START, "end_exclusive_utc": PERIOD_END},
                     "universe": [INSTRUMENT],
                     "variables": ["close", "forward_return_1d", "sma_close_3"]},
        acceptance_criterion=ACCEPTANCE, creation_timestamp=now, status="PROPOSED",
        created_by="Oscar Carmenate Rodriguez",
        provenance=[
            "CANDIDATE_REGISTER|e4efb06c -- VIXM held SHORT, the one short candidate whose "
            "monitor link was unexamined",
            "DIRECTOR_INSTRUCTION|2026-10-04 -- measure VIXM short in PAPER first",
            "MEASURED|VIXM shortable, easy to borrow and fractionable on Alpaca, served from "
            "2016-01-04",
        ],
        code_revision=revision, system_version="0.1.0")
    answers = pre_declaration(hypothesis_id=record["hypothesis_id"], **ANSWERS)
    sealed = constitute_pre_declaration(PRE_DECLARATIONS, answers, declared_at=now,
                                        code_revision=revision)
    effect = sealed["required_effect"]
    print(f"{'=' * 78}\nHYPOTHESIS DECLARED -- no market data has been read\n{'=' * 78}")
    print(f"  id        {record['hypothesis_id']}  (draw {draw})")
    print(f"  effect    {effect['plausible_low']} to {effect['plausible_high']} against a bar of "
          f"{effect['required_point_sharpe']}  -> {effect['position_against_bar']}")
    print(f"  pre-declaration  {sealed['pre_declaration_id'][:50]}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
