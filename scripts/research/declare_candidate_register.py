"""Declare the candidate space this project is actually choosing from.

EVERY EFFECT RANGE BELOW IS RECALLED FROM LITERATURE, NOT MEASURED HERE. It is
an input to a feasibility test and never evidence. The conservative end is what
the test uses, for the same reason §8.1 uses adverse bounds.

The available-years figures ARE measured: Alpaca's SIP feed serves from
2016-01-04, FRED's ICE series only three years, BAA10Y from 1990, and SVXY's
-0.5x structure from 2018-02-28. Where an instrument is younger than the premium
it expresses, the instrument is what counts, and that gap is why the variance
risk premium sits where it does.

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
    candidate, constitute_candidate_register, rank_candidates,
    STATUS_UNTRIED, STATUS_DECLARED, STATUS_MEASURED_DEAD)
from tramitago_quant_core.research.pre_declaration import CLAIM_PREMIUM, CLAIM_MISPRICING

REGISTER = REPO / "artifacts" / "research" / "candidate-registers.json"

JUSTIFICATION = (
    "The set of harvestable premia this project can express with the data sources it "
    "actually reaches, declared BEFORE the next one is picked. It exists because the "
    "previous six were sourced from recollection and ranked by monitorability -- the "
    "failure mode that killed one of six, while size or returns killed four. What is "
    "passed over is now visible, and the bar each candidate must clear is derived from "
    "the history available for it rather than assumed to be the same for all.")

CANDIDATES = [
    candidate(
        name="variance risk premium, via SVXY",
        claim_class=CLAIM_PREMIUM,
        payer="buyers of VIX futures and index options, purchasing protection against "
              "equity drawdowns they are mandated or unwilling to bear",
        effect_low="0.50", effect_high="1.00",
        effect_source="published estimates of the roll-yield harvest on short VIX futures "
                      "run roughly 0.5 to 1.0 before catastrophic tail events",
        available_years="8.57", data_source="Alpaca SIP; SVXY's -0.5x structure from "
                                            "2018-02-28",
        status=STATUS_DECLARED),
    candidate(
        name="US equity risk premium, via SPY",
        claim_class=CLAIM_PREMIUM,
        payer="every investor reducing equity exposure for reasons unrelated to expected "
              "return: mandates, liability matching, liquidity needs and horizon",
        effect_low="0.40", effect_high="0.80",
        effect_source="long-run equity Sharpe is conventionally quoted near 0.4, and "
                      "measured 0.81 over this project's own 2018-2026 window",
        available_years="10.75", data_source="Alpaca SIP from 2016-01-04",
        status=STATUS_MEASURED_DEAD),
    candidate(
        name="investment-grade credit premium, via LQD against IEF",
        claim_class=CLAIM_PREMIUM,
        payer="investors constrained to Treasuries or the highest grades by mandate, "
              "capital treatment or collateral eligibility",
        effect_low="0.20", effect_high="0.50",
        effect_source="published excess-return Sharpes for investment-grade credit run "
                      "roughly 0.2 to 0.5; measured 0.19 here",
        available_years="10.75", data_source="Alpaca SIP from 2016-01-04",
        status=STATUS_MEASURED_DEAD),
    candidate(
        name="crypto perpetual funding carry",
        claim_class=CLAIM_PREMIUM,
        payer="levered long holders of the perpetual, paying funding to maintain exposure",
        effect_low="0.30", effect_high="0.80",
        effect_source="no published long-run estimate; the range is a guess from the "
                      "gross figures this project sealed, and should be read as weaker "
                      "than the others",
        available_years="10.00", data_source="BitMEX and Hyperliquid public history",
        status=STATUS_MEASURED_DEAD),
    candidate(
        name="term premium, via TLT against SHY",
        claim_class=CLAIM_PREMIUM,
        payer="investors who need duration for liability matching regardless of its "
              "expected return, and regulators who require it",
        effect_low="0.20", effect_high="0.40",
        effect_source="published term-premium Sharpes run roughly 0.2 to 0.4 over long "
                      "samples, and 2022 was catastrophic for duration inside this window",
        available_years="10.75", data_source="Alpaca SIP from 2016-01-04",
        status=STATUS_UNTRIED),
    candidate(
        name="currency carry, via developed-market FX ETFs",
        claim_class=CLAIM_PREMIUM,
        payer="borrowers in low-rate currencies and hedgers paying forward points",
        effect_low="0.30", effect_high="0.50",
        effect_source="published FX carry Sharpes run roughly 0.3 to 0.5 on diversified "
                      "baskets, with pronounced negative skew",
        available_years="10.75", data_source="Alpaca SIP; single-currency ETFs express "
                                             "the differential poorly",
        status=STATUS_UNTRIED),
    candidate(
        name="commodity roll premium, via broad commodity ETFs",
        claim_class=CLAIM_PREMIUM,
        payer="producers and consumers hedging price risk, who pay to transfer it",
        effect_low="0.10", effect_high="0.40",
        effect_source="published backwardation-harvest Sharpes are low and unstable, and "
                      "the sign has flipped for long stretches since 2008",
        available_years="10.75", data_source="Alpaca SIP from 2016-01-04",
        status=STATUS_UNTRIED),
    candidate(
        name="variance risk premium, via VIX futures directly",
        claim_class=CLAIM_PREMIUM,
        payer="the same buyers of protection, without an ETP wrapper between the premium "
              "and the position",
        effect_low="0.50", effect_high="1.00",
        effect_source="the same literature as the SVXY entry; what differs is the window, "
                      "because CBOE's VIX futures date from 2004 and the ETP does not",
        available_years="21.00", data_source="CBOE; NO SOURCE THIS PROJECT REACHES SERVES "
                                             "IT, which is the only thing stopping it",
        status=STATUS_UNTRIED, reachable_today=False),
    candidate(
        name="crypto relative value, via pair ratio reversion",
        claim_class=CLAIM_MISPRICING,
        payer="nobody -- a convergence bet has no counterparty paying to shed a risk, "
              "which is why it sits outside the declared destination",
        effect_low="0.00", effect_high="0.50",
        effect_source="no basis for a range; two Hypotheses measured 5 of 10 folds and "
                      "the stopping rule cannot judge the population below five",
        available_years="10.00", data_source="Coinbase public history",
        status=STATUS_UNTRIED),
]


def main():
    require_evidence_host(REPO)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, check=True).stdout.strip()
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    record = constitute_candidate_register(
        REGISTER, justification=JUSTIFICATION, candidates=CANDIDATES,
        declared_by=f"Oscar Carmenate Rodriguez, at {revision[:12]}", declared_at=now)

    print("=" * 84)
    print(f"CANDIDATE REGISTER SEALED -- {len(CANDIDATES)} candidates, ranked by margin "
          f"over their OWN bar")
    print("=" * 84)
    print(f"{'':2} {'candidate':<44} {'range':>11} {'bar':>6} {'yrs':>6}  verdict")
    for item in rank_candidates(record):
        mark = {"MEASURED_DEAD": "x", "DECLARED": ">", "UNTRIED": " "}[item["status"]]
        span = f"{item['effect']['low']}-{item['effect']['high']}"
        print(f"{mark:2} {item['name'][:44]:<44} {span:>11} "
              f"{item['required']:>6} {item['available_years']:>6}  {item['verdict']}")
    print("\n  x measured and dead    > declared, not yet admitted    (blank) untried")
    print(f"\n  {record['register_id'][:58]}")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    sys.exit(main())
