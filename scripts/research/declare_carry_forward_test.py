"""Declare the project's first forward-dated Hypothesis, and then stop.

FORTY-FIVE HYPOTHESES AND NOT ONE WAS TESTED FORWARD. Every verdict here is
retrospective and every holdout a slice of the past declared unread. That
discipline is real and it has caught real things, but a held-back slice of
history is finite -- and on 2026-10-02 the stopping rule declared two populations
EXHAUSTED BY MEASUREMENT, which is what running out looks like.

The future is the only out-of-sample that renews itself, and the only one nobody
can peek at.

WHAT IS BEING DECLARED. CARRY_FUNDING_THRESHOLD at zero -- long BTC spot, short
BitMEX XBTUSD perpetual, held while funding is at or above zero -- is the only
Hypothesis this project has ever validated while carrying a correction for its
own search: 8 of 8 folds on a holdout declared before the scan that selected it,
Bonferroni over the eight candidates it was chosen from. The strongest possible
next evidence about it is not another slice of history. It is whether it holds on
data that did not exist when this was written.

THE CORRECTION TRAVELS. The candidate was the best of eight and that selection
does not un-happen because time passed, so the forward test carries Bonferroni
over 8 as well: alpha 0.00625, which needs 8 folds to be passable at all. Eight
folds of forty-six days is 368 of them, so the answer arrives in about a year.
That is the honest price of the only renewable out-of-sample there is, and
shortening the window to get an answer sooner would buy it by making the test
unpassable, which is not a shorter answer but a different one.

NOTHING CAN BE LOOKED AT UNTIL IT IS OVER. create_hypothesis_dataset refuses a
forward test whose period has not fully elapsed -- not mostly, not nearly -- and
it refuses by raising, because the moment this matters is the moment somebody is
curious how it is going.

This script declares and exits. There is nothing else it could do.

    python3.11 -B scripts/research/declare_carry_forward_test.py
"""

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pipeline as p
from tramitago_quant_core.governance.evidence_host import require_evidence_host
from tramitago_quant_core.research.hypothesis import constitute_hypothesis
from tramitago_quant_core.research.forward_test import (
    is_forward_hypothesis, forward_test_status, pending_forward_tests,
)
from tramitago_quant_core.research.walk_forward import minimum_folds_for_batch
from tramitago_quant_core.data.bitmex_funding_rate import BITMEX_FUNDING_PAYMENTS_PER_DAY

HYPOTHESES = REPO / "artifacts" / "research" / "hypotheses.json"
INSTRUMENT, SYMBOL, HORIZON = "BTC-USD", "XBTUSD", 1
BATCH_SIZE = 8                      # the scan this candidate was selected from
FOLD_DAYS = 46                      # 8 x 46 = 368
START = "2026-10-03T00:00:00Z"
END = "2027-10-06T00:00:00Z"

# The sealed chain this is the forward continuation of.
SOURCE_SCAN = "DISCOVERY_SCAN|6d298f423a308ebca9ea8da0013b63a31668501bc98c77891d4524688baabd10"
SOURCE_SPACE = "DISCOVERY_SPACE|e17055eb37188fb7743c88196d5e6e3c1d03c5ca17084fe84de597f38c42e716"
SOURCE_VALIDATION = "STATISTICAL_VALIDATION|96545cd1aee924fd4c64ea1aeaf68"


def main():
    require_evidence_host(REPO)
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, check=True).stdout.strip()
    folds = minimum_folds_for_batch(BATCH_SIZE)
    span = (p.epoch(END) - p.epoch(START)) // 86400
    if span % folds:
        raise SystemExit(f"{span} days does not divide into {folds} folds")
    if p.epoch(START) < p.epoch(now):
        raise SystemExit(
            f"The declared period starts {START[:10]}, which is not in the future at {now[:10]}. "
            "A forward test declared over a period already begun is not a forward test.")

    strategy = p.carry_funding_threshold_strategy(
        payments_per_period=BITMEX_FUNDING_PAYMENTS_PER_DAY)
    forward = strategy["outcome"]["column"](HORIZON)
    metric = (f"mean_{forward}({strategy['upper_group_description']}) - "
              f"mean_{forward}({strategy['lower_or_equal_group_description']})")
    variables = sorted(set(strategy["required_inputs"]["variables"])
                       | set(strategy["outcome"]["required_inputs"]["variables"])
                       | {strategy["column_name"], forward})

    hypothesis = constitute_hypothesis(
        HYPOTHESES,
        description=(
            f"FORWARD-DATED. Declared {now[:10]} for a period that has not happened yet: "
            f"{START[:10]} to {END[:10]}.\n\n"
            f"For a MARKET-NEUTRAL CARRY in BTC -- long {INSTRUMENT} spot, short BitMEX "
            f"{SYMBOL} at equal notional -- the mean forward carry return on days when "
            f"funding stood AT OR ABOVE zero is HIGHER than on days when it stood below. "
            f"Direction INCREASE pre-declared. Funding at "
            f"{BITMEX_FUNDING_PAYMENTS_PER_DAY} payments a day, BitMEX's eight-hour "
            f"settlement.\n\n"
            f"WHY THIS ONE. It is the only Hypothesis this project has validated while "
            f"carrying a correction for its own search: 8 of 8 folds on a holdout declared "
            f"before the scan that selected it ({SOURCE_SCAN}), Bonferroni over the eight "
            f"candidates it was chosen from, sealed as {SOURCE_VALIDATION}. The strongest "
            f"next evidence about it is not another slice of history.\n\n"
            f"THE CORRECTION TRAVELS FORWARD. The candidate was the best of "
            f"{BATCH_SIZE} and that selection does not un-happen because time passed, so "
            f"this carries Bonferroni over {BATCH_SIZE} too: alpha "
            f"{0.05 / BATCH_SIZE:.5f}, needing {folds} folds to be passable at all. "
            f"{folds} x {FOLD_DAYS} = {span} days.\n\n"
            f"NOTHING MAY BE LOOKED AT UNTIL THE PERIOD IS OVER. Its dataset cannot be "
            f"built before {END[:10]} -- not mostly elapsed, fully -- because a partial "
            f"period evaluated under this declaration's name would carry an authority it "
            f"does not have.\n\n"
            f"WHAT WOULD REFUTE IT: a metric at or below zero over the declared period, or "
            f"a walk-forward that fails its corrected test. What would NOT establish is "
            f"that the carry is worth holding: the POSITION is already NOT_VALIDATED on a "
            f"drawdown breach with and without this rule, and vía 7.D is closed. This "
            f"tests the RULE, which discriminates, not the position, which does not pay "
            f"for its tail."),
        target_metric=metric, expected_direction="INCREASE",
        constraints={"period": {"start_utc": START, "end_exclusive_utc": END},
                     "universe": [INSTRUMENT], "variables": variables},
        acceptance_criterion={"comparison": "GT", "expected_direction": "INCREASE",
                              "metric": metric, "threshold": "0"},
        creation_timestamp=now, status="CONSTITUTED", created_by="Oscar Carmenate Rodriguez",
        provenance=[SOURCE_SCAN, SOURCE_SPACE, SOURCE_VALIDATION,
                    f"CANDIDATES_EXAMINED|{BATCH_SIZE}",
                    "ANALYSIS|first-forward-dated-hypothesis"],
        code_revision=revision, system_version="0.1.0")

    print("=" * 76)
    print("FORWARD-DATED HYPOTHESIS DECLARED")
    print("=" * 76)
    print(f"  {hypothesis['hypothesis_id']}")
    print(f"  declared      {now[:19]}Z")
    print(f"  period        {START[:10]} .. {END[:10]}   ({span} days = {folds} x {FOLD_DAYS})")
    print(f"  correction    Bonferroni over {BATCH_SIZE}, alpha {0.05 / BATCH_SIZE:.5f}, "
          f"smallest attainable p {0.5 ** folds:.5f}")
    print(f"  forward       {is_forward_hypothesis(hypothesis)}")
    print(f"  status        {forward_test_status(hypothesis, now)}")

    registry = p.query_hypotheses(HYPOTHESES) if hasattr(p, "query_hypotheses") else None
    import json
    records = json.loads(HYPOTHESES.read_bytes())["hypotheses"]
    pending = pending_forward_tests(records, now)
    print(f"\n  pending forward tests: {len(pending)}")
    for item in pending:
        print(f"    {item['hypothesis_id']}  {item['days_remaining']} days left")

    print(f"\n{'=' * 76}")
    print(f"  Nothing further can be done with this until {END[:10]}.")
    print("  create_hypothesis_dataset refuses it, and that refusal is the point.")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    sys.exit(main())
