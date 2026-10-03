"""The candidate space, declared and sealed BEFORE one of them is picked.

WHY THIS EXISTS. Discovery seals its space before scanning, so a Finding cannot
hide how many candidates it beat. Nothing did the same for the Hypotheses chosen
by hand, and on 2026-10-03 that cost something measurable: six premia were
sourced from recollection and ranked by MONITORABILITY, because the equity
premium had died of it. The record says that was the wrong axis -- of six
Hypotheses measured to a point Sharpe, ONE died of monitorability and FOUR died
of size or returns. The rarest failure mode was optimised against.

A register fixes the part of that which is fixable. It does not propose
candidates; it makes the set visible, states what each would need, and records
what was passed over so the ranking can be argued with later instead of
reconstructed from whichever one was tried.

THE BAR IS NOT THE SAME FOR EVERY CANDIDATE, and that is the finding this makes
operational. §8.2 needs a point Sharpe at or above 0.50 AND a lower 95% bound
above zero, and the gap between the two scales as one over the root of the
sample -- measured at 0.543 on SPY over 2198 days and 0.547 on SVXY over 2158,
the same number on unrelated instruments. So a candidate with eight years of
history must plausibly reach 0.55 while one with eleven need only reach 0.50.
RANKING BY EFFECT SIZE ALONE IGNORES HALF OF THAT, and ranking by monitorability
ignores all of it.

EVERY EFFECT RANGE HERE IS RECALLED FROM LITERATURE, NOT MEASURED. It is an
input to a feasibility test, never evidence, and a register entry that reads
like a result is a register entry that is wrong. The conservative end is what
the test uses, for the same reason §8.1 uses adverse bounds.
"""

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from tramitago_quant_core.research.pre_declaration import (
    required_point_sharpe_for, CLAIM_CLASSES)
from tramitago_quant_core.shared.util import (
    digest, encoded, _atomic_write, _explicit_utc, _hypothesis_text_is_valid)

CANDIDATE_REGISTER_SCHEMA_VERSION = "1"

STATUS_UNTRIED = "UNTRIED"
STATUS_DECLARED = "DECLARED"
STATUS_MEASURED_DEAD = "MEASURED_DEAD"
STATUSES = (STATUS_UNTRIED, STATUS_DECLARED, STATUS_MEASURED_DEAD)

# THREE VERDICTS, BECAUSE NEITHER END OF A RANGE IS RIGHT ALONE, and the first
# version of this module got it wrong by using only the conservative one.
# MEASURED: SPY's recalled range was 0.40 to 0.80 and it came in at 0.81 -- the
# conservative end would have refused a premium that then cleared its gate. The
# credit premium's range was 0.20 to 0.50 and it came in at 0.19 -- the
# optimistic end would have spent two days on it.
#
#   FEASIBLE   the CONSERVATIVE end clears: very likely to clear in fact
#   UNCERTAIN  the range straddles the bar: worth spending on, outcome unknown
#   BELOW_BAR  the OPTIMISTIC end fails: cannot clear, and spending is a waste
#
# Only the third is a refusal. The middle is where honest work lives, and a
# screen that collapses it into either neighbour is lying in one direction.
VERDICT_FEASIBLE = "FEASIBLE"
VERDICT_UNCERTAIN = "UNCERTAIN"
VERDICT_BELOW_BAR = "BELOW_BAR"
VERDICT_UNREACHABLE_DATA = "UNREACHABLE_DATA"
# A FIFTH ANSWER, ADDED AFTER THE ARITHMETIC OF 2026-10-03. R4 asks whether the
# capacity CEILING clears the tranche and never whether the LOT fits inside it.
# Measured: at a $1,000 tranche and the 10.9% weight the drawdown limit derived,
# the position is $109 of notional, while one mini VIX future at VIX 18 is
# $1,800 -- sixteen times too large, and one FULL contract is a hundred and
# sixty-five times. Such a Hypothesis would pass R4 and be unoperable anyway,
# and no premium is large enough to fix an indivisible wrapper.
VERDICT_UNOPERABLE = "UNOPERABLE_AT_TRANCHE"

# DOC-001 Fase 1: microcapital $200-$1000. The ceiling of that range is what a
# candidate's smallest expressible position is measured against.
FASE_1_TRANCHE_USD = Decimal("1000")


def _decimal(value, name):
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a decimal string") from error
    if not number.is_finite():
        raise ValueError(f"{name} must be finite")
    return number


def _prose(value, name, minimum_words=6):
    if not _hypothesis_text_is_valid(value) or len(value.split()) < minimum_words:
        raise ValueError(f"{name} must be answered in writing, in a sentence")
    return value.strip()


def candidate(*, name, claim_class, payer, effect_low, effect_high, effect_source,
              available_years, data_source, status, reachable_today=True,
              minimum_position_usd=None):
    """One entry. The conservative end of the effect range is what will be tested.

    `available_years` is how much history the SOURCE serves for THIS instrument,
    which is a different question from how long the premium has existed. SVXY's
    structure dates from 2018 while the variance risk premium does not, and that
    gap is the whole reason this field exists.
    """
    if claim_class not in CLAIM_CLASSES:
        raise ValueError(f"Claim class must be one of {', '.join(CLAIM_CLASSES)}")
    if status not in STATUSES:
        raise ValueError(f"Status must be one of {', '.join(STATUSES)}")
    if not isinstance(reachable_today, bool):
        raise ValueError("Whether the data is reachable today must be stated explicitly")
    low, high = _decimal(effect_low, "Effect low"), _decimal(effect_high, "Effect high")
    if low > high:
        raise ValueError("The conservative end of an effect range cannot exceed the other")
    years = _decimal(available_years, "Available years")
    if years <= 0:
        raise ValueError("Available years must be positive")
    return {
        # A NAME IS NOT AN ARGUMENT, so the prose floor that applies to the
        # payer and the effect source does not apply here: "SPY" is a perfectly
        # good name and the first version of this refused it.
        "name": name.strip() if _hypothesis_text_is_valid(name) else _prose(name, "Name"),
        "claim_class": claim_class,
        "payer": _prose(payer, "Who pays"),
        "effect": {"low": effect_low, "high": effect_high,
                   "source": _prose(effect_source, "Where the range comes from")},
        "available_years": available_years,
        "data_source": (data_source.strip() if _hypothesis_text_is_valid(data_source)
                        else _prose(data_source, "The data source")),
        "status": status,
        "reachable_today": reachable_today,
        # Present only when the wrapper has a lot size worth stating. An ETF
        # share is not one; a futures contract is.
        **({"minimum_position_usd": minimum_position_usd}
           if minimum_position_usd is not None else {}),
    }


def candidate_verdict(entry):
    """What this candidate would need, and whether its own range reaches it.

    Never a prediction. It compares a RECALLED range against a DERIVED bar, so a
    BELOW_BAR verdict says the candidate cannot clear its gate on the history
    available -- which is a statement about the window as much as the premium,
    and reads differently when more history appears.
    """
    bar = required_point_sharpe_for(entry["available_years"])
    if not entry["reachable_today"]:
        return {"verdict": VERDICT_UNREACHABLE_DATA, "required": str(bar),
                "detail": "no source this project can reach serves it"}
    lot = entry.get("minimum_position_usd")
    if lot is not None:
        smallest = _decimal(lot, "Minimum position")
        # Checked against the WHOLE tranche, not against the weight the drawdown
        # limit would derive: a lot that does not fit the whole account cannot
        # fit a tenth of it, and the weight is not known before measuring.
        if smallest > FASE_1_TRANCHE_USD:
            return {"verdict": VERDICT_UNOPERABLE, "required": str(bar),
                    "detail": (f"the smallest expressible position is ${smallest:,.0f} "
                               f"against a ${FASE_1_TRANCHE_USD:,.0f} tranche: it cannot "
                               f"be held whatever the premium measures")}
    low = _decimal(entry["effect"]["low"], "Effect low")
    high = _decimal(entry["effect"]["high"], "Effect high")
    if low >= bar:
        verdict, detail = VERDICT_FEASIBLE, "even the conservative end clears it"
    elif high >= bar:
        verdict, detail = VERDICT_UNCERTAIN, "the range straddles it; the outcome is open"
    else:
        verdict, detail = VERDICT_BELOW_BAR, "even the optimistic end falls short"
    return {
        "verdict": verdict, "required": str(bar),
        "detail": (f"{entry['effect']['low']}-{entry['effect']['high']} against a bar of "
                   f"{bar} implied by {entry['available_years']} years: {detail}"),
    }


def rank_candidates(register):
    """Feasible first, then by how far the conservative end clears its own bar.

    Not by effect size and not by monitorability. A large effect on four years of
    history is behind a smaller one on twelve, because the bar the first must
    clear is higher -- which is the mistake this module exists to stop repeating.
    """
    scored = []
    for entry in register["candidates"]:
        verdict = candidate_verdict(entry)
        # Ranked on the OPTIMISTIC end within a verdict: among candidates whose
        # outcome is genuinely open, the one with the most room is the one worth
        # spending on first.
        margin = (_decimal(entry["effect"]["high"], "Effect high")
                  - _decimal(verdict["required"], "Required"))
        scored.append({**entry, **verdict, "margin": str(margin)})
    order = {VERDICT_FEASIBLE: 0, VERDICT_UNCERTAIN: 1, VERDICT_BELOW_BAR: 2,
             VERDICT_UNOPERABLE: 3, VERDICT_UNREACHABLE_DATA: 4}
    return sorted(scored, key=lambda item: (order[item["verdict"]],
                                            -_decimal(item["margin"], "Margin")))


def constitute_candidate_register(registry_path, *, justification, candidates,
                                  declared_by, declared_at):
    """Seal the space. Re-declaring an identical one returns what is there."""
    if not isinstance(candidates, list) or len(candidates) < 2:
        raise ValueError("A register of one candidate is a choice already made")
    names = [item["name"] for item in candidates]
    if len(names) != len(set(names)):
        raise ValueError("A register cannot name the same candidate twice")
    if not _explicit_utc(declared_at):
        raise ValueError("A register must say when it was declared")
    content = {
        "schema_version": CANDIDATE_REGISTER_SCHEMA_VERSION,
        "justification": _prose(justification, "Why this set and not another", 10),
        "candidates": candidates,
        "declared_by": (declared_by.strip() if _hypothesis_text_is_valid(declared_by)
                        else _prose(declared_by, "Who declared it")),
        "declared_at": declared_at,
    }
    record = {**content, "register_id": "CANDIDATE_REGISTER|" + digest(encoded(content))}
    path = Path(registry_path)
    registry = (json.loads(path.read_bytes()) if path.exists()
                else {"schema_version": CANDIDATE_REGISTER_SCHEMA_VERSION, "registers": []})
    for item in registry["registers"]:
        if item["register_id"] == record["register_id"]:
            return item
    registry["registers"].append(record)
    _atomic_write(path, encoded(registry))
    return record
