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

# The registry FILE is schema 1 and stays so; individual registers are append-only
# inside it. A register written from 2026-10-03 on is RECORD schema 2: it carries
# `requires_short` and a monitor status per candidate, which the first two
# registers lacked and whose absence cost a wrong recommendation that day.
CANDIDATE_REGISTER_SCHEMA_VERSION = "1"
CANDIDATE_REGISTER_RECORD_SCHEMA_VERSION = "2"

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

# WHAT THE EXECUTOR CAN DO, stated once and guarded by a test. MEASURED
# 2026-10-03: pipeline.py accepts only (ENTER, BUY) and (EXIT, SELL), at two
# places, so an ENTER with side SELL is rejected as an invalid proposal -- the
# platform cannot open a short. DOC-011's catalogue says the same ("requiere
# vender en corto ... Etapa 4.5 completa, sin construir") and the capability
# memory says "no short, no perpetual". Nothing here may be trusted to stay true
# by being written down: tests/test_candidate_register.py reads pipeline.py and
# FAILS if an ENTER/SELL pair ever appears, forcing this line to be revisited in
# the same change that builds the capability. DOC-011 section 6 went stale for
# a week by being prose; this is the same fact held to a test.
EXECUTOR_CAN_OPEN_SHORTS = False
EXECUTOR_SHORT_SOURCE = ("pipeline.py accepts only (ENTER, BUY) and (EXIT, SELL); "
                         "ENTER with SELL is rejected as an invalid proposal")

# WHAT IS KNOWN ABOUT A CANDIDATE'S MONITOR -- the second thing the first two
# registers did not carry. Of six Hypotheses measured to a point Sharpe, one died
# of monitorability, and the register recommended a candidate whose monitor link
# had already FAILED a measurement on its mirror instrument.
MONITOR_NONE_POSSIBLE = "NO_NON_PNL_VARIABLE_EXISTS"     # structural: SPY, pair reversion
MONITOR_LINK_MET = "EMPIRICAL_LINK_MET"                  # M1 reachable and cleared
MONITOR_LINK_REFUTED = "EMPIRICAL_LINK_REFUTED"          # M1 reachable and FAILED
MONITOR_IDENTITY_ONLY = "IDENTITY_ONLY"                  # trigger never occurs; M2 only
MONITOR_UNEXAMINED = "UNEXAMINED"                        # nobody has looked
# BETWEEN UNEXAMINED AND TESTED: the trigger state was MEASURED to occur in enough
# folds for the empirical link to be testable (the VIX slope's was 5 of 7 and the
# implied-minus-realised spread's 6 of 7), but whether the link holds has not been
# tested. Without it a candidate whose monitor is reachable reads the same as one
# nobody has looked at, and the difference is exactly what separates a premium
# worth declaring from one that will fail R3 for want of a testable trigger.
MONITOR_LINK_TESTABLE = "EMPIRICAL_LINK_TESTABLE"
MONITOR_STATUSES = (MONITOR_NONE_POSSIBLE, MONITOR_LINK_MET, MONITOR_LINK_REFUTED,
                    MONITOR_IDENTITY_ONLY, MONITOR_LINK_TESTABLE, MONITOR_UNEXAMINED)

# A refuted or met link is either MEASURED on this very instrument or INFERRED
# from a sibling. The distinction decides whether it eliminates a candidate or
# only weighs against it, and conflating them is how a mirror's result gets read
# as the candidate's own.
CERTAINTY_MEASURED = "MEASURED"
CERTAINTY_INFERRED = "INFERRED"

BLOCKER_REQUIRES_SHORT = "REQUIRES_SHORT"
BLOCKER_MONITOR_REFUTED = "MONITOR_REFUTED"
BLOCKER_NO_MONITOR = "NO_MONITOR_POSSIBLE"


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


def monitor_status(*, variable, status, evidence, certainty=None, measured_on=None):
    """What is KNOWN about a candidate's monitor, and how it is known.

    `certainty` is required for a met or refuted link and meaningless for the
    rest. A refuted link INFERRED from a sibling weighs against a candidate; one
    MEASURED on it eliminates it.
    """
    if status not in MONITOR_STATUSES:
        raise ValueError(f"Monitor status must be one of {', '.join(MONITOR_STATUSES)}")
    needs_certainty = status in (MONITOR_LINK_MET, MONITOR_LINK_REFUTED)
    if needs_certainty and certainty not in (CERTAINTY_MEASURED, CERTAINTY_INFERRED):
        raise ValueError("A met or refuted link must say whether it was MEASURED here "
                         "or INFERRED from a sibling")
    if not needs_certainty and certainty is not None:
        raise ValueError("Certainty applies only to a met or refuted empirical link")
    if certainty == CERTAINTY_INFERRED and not _hypothesis_text_is_valid(measured_on):
        raise ValueError("An inferred link must name the sibling it was measured on")
    return {
        "variable": (variable.strip() if _hypothesis_text_is_valid(variable)
                     else _prose(variable, "Monitor variable")),
        "status": status,
        "evidence": _prose(evidence, "Evidence for the monitor status"),
        **({"certainty": certainty} if certainty is not None else {}),
        **({"measured_on": measured_on.strip()}
           if _hypothesis_text_is_valid(measured_on) else {}),
    }


def candidate(*, name, claim_class, payer, effect_low, effect_high, effect_source,
              available_years, data_source, status, requires_short, monitor,
              reachable_today=True, minimum_position_usd=None, unreachable_reason=None):
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
    # REQUIRED, with no default, and that is the point: a candidate that does not
    # say whether it needs a short is how VIXY held short was recommended as the
    # one route to a sustainable position by a platform that cannot open one.
    if not isinstance(requires_short, bool):
        raise ValueError("Whether the candidate requires a short must be stated explicitly")
    if (not isinstance(monitor, dict) or monitor.get("status") not in MONITOR_STATUSES
            or not _hypothesis_text_is_valid(monitor.get("variable"))):
        raise ValueError("A candidate must carry a monitor_status(...), even if only "
                         "to say it is UNEXAMINED")
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
        "requires_short": requires_short,
        "monitor": monitor,
        # Why it cannot be reached, when it cannot. Present only then, so every
        # entry sealed before this existed hashes as it always did. PUTW showed
        # why it matters: the feed served bars to 2025-04-03 and the asset
        # endpoint returned 404 -- the fund was liquidated, so the DATA is
        # reachable and the INSTRUMENT is not, which "no source serves it" would
        # have described wrongly.
        **({"unreachable_reason": unreachable_reason}
           if unreachable_reason is not None else {}),
        # Present only when the wrapper has a lot size worth stating. An ETF
        # share is not one; a futures contract is.
        **({"minimum_position_usd": minimum_position_usd}
           if minimum_position_usd is not None else {}),
    }


def candidate_blockers(entry):
    """Known reasons a candidate cannot reach admission, SEPARATE from its effect.

    The verdict answers whether the effect could clear its bar. This answers
    whether anything ELSE already known stands in the way, and keeping them apart
    is what lets a candidate be FEASIBLE on size and blocked on execution at the
    same time. The first two registers collapsed the two and recommended a
    FEASIBLE candidate the platform could not operate.

    HARD blockers eliminate: structural facts, or measurements taken on this very
    instrument. PROBABLE blockers weigh against: a measurement on a sibling that
    has not been repeated here. Nothing probable is promoted to hard by repetition
    of the claim -- only by measurement.
    """
    hard, probable = [], []
    if entry["requires_short"] and not EXECUTOR_CAN_OPEN_SHORTS:
        hard.append({"kind": BLOCKER_REQUIRES_SHORT, "gate": "EXECUTION",
                     "detail": f"requires a short and the executor cannot open one: "
                               f"{EXECUTOR_SHORT_SOURCE}"})
    monitor = entry["monitor"]
    if monitor["status"] == MONITOR_NONE_POSSIBLE:
        hard.append({"kind": BLOCKER_NO_MONITOR, "gate": "R3",
                     "detail": "no variable exists that is not the position's own P&L, "
                               "and a P&L-only monitor never qualifies"})
    elif monitor["status"] == MONITOR_LINK_REFUTED:
        reason = (f"the empirical link was reachable and FAILED ({monitor['evidence']}), so "
                  f"M2 is closed by M2_AVAILABILITY|04a35abd and R3 fails")
        if monitor.get("certainty") == CERTAINTY_MEASURED:
            hard.append({"kind": BLOCKER_MONITOR_REFUTED, "gate": "R3", "detail": reason})
        else:
            probable.append({
                "kind": BLOCKER_MONITOR_REFUTED, "gate": "R3",
                "detail": (reason + f"; MEASURED on {monitor['measured_on']}, expected to "
                           f"carry over, NOT measured on this instrument")})
    return {"hard_blockers": hard, "probable_blockers": probable}


def candidate_verdict(entry):
    """The effect verdict plus every known blocker, kept apart.

    A candidate can be FEASIBLE and still blocked; the two answers are different
    questions and the register reports both.
    """
    return {**_effect_verdict(entry), **candidate_blockers(entry)}


def admissible_in_principle(scored):
    """Could this candidate reach admission, as far as anything known says?

    Not a prediction of success -- a statement that nothing already measured or
    structural forbids it. Measured-dead candidates are excluded: they have been
    through the gates and a second pass over the same data is not a search.
    """
    return (scored["verdict"] in (VERDICT_FEASIBLE, VERDICT_UNCERTAIN)
            and not scored["hard_blockers"]
            and scored["status"] != STATUS_MEASURED_DEAD)


def _effect_verdict(entry):
    """What this candidate would need, and whether its own range reaches it.

    Never a prediction. It compares a RECALLED range against a DERIVED bar, so a
    BELOW_BAR verdict says the candidate cannot clear its gate on the history
    available -- which is a statement about the window as much as the premium,
    and reads differently when more history appears.
    """
    bar = required_point_sharpe_for(entry["available_years"])
    if not entry["reachable_today"]:
        return {"verdict": VERDICT_UNREACHABLE_DATA, "required": str(bar),
                "detail": entry.get("unreachable_reason",
                                    "no source this project can reach serves it")}
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
    # Measured-dead candidates and hard-blocked ones sort BELOW everything that
    # could still be admitted, whatever their effect. A large effect behind a
    # blocker nothing can remove is not a candidate, and ranking it first is
    # exactly the recommendation this module was extended to stop making.
    #
    # A REFUSED candidate also sinks below a merely blocked one. The first version
    # of this ordering ranked a BELOW_BAR candidate FIRST because it had no
    # blockers, which is a refusal sorted as a recommendation: absence of a
    # blocker means nothing for a candidate whose own effect cannot reach its bar.
    refused = (VERDICT_BELOW_BAR, VERDICT_UNOPERABLE, VERDICT_UNREACHABLE_DATA)
    return sorted(scored, key=lambda item: (
        item["status"] == STATUS_MEASURED_DEAD, item["verdict"] in refused,
        bool(item["hard_blockers"]), bool(item["probable_blockers"]),
        order[item["verdict"]], -_decimal(item["margin"], "Margin")))


def what_would_unlock(register):
    """For each KIND of hard blocker, who would become admissible if only it went.

    The decision a Director actually faces is not "what is blocked" but "which
    single change buys the most", and that is arithmetic over the register
    rather than judgement. A candidate counts only if the removed kind was its
    ONLY hard blocker; one still blocked by something else is not unlocked.
    """
    scored = [{**entry, **candidate_verdict(entry)} for entry in register["candidates"]]
    kinds = sorted({blocker["kind"] for item in scored for blocker in item["hard_blockers"]})
    unlocked = {}
    for kind in kinds:
        names = []
        for item in scored:
            held = {blocker["kind"] for blocker in item["hard_blockers"]}
            if (held == {kind} and item["verdict"] in (VERDICT_FEASIBLE, VERDICT_UNCERTAIN)
                    and item["status"] != STATUS_MEASURED_DEAD):
                names.append({"name": item["name"], "verdict": item["verdict"],
                              "still_probably_blocked": bool(item["probable_blockers"])})
        unlocked[kind] = names
    return unlocked


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
    for item in candidates:
        if "requires_short" not in item or "monitor" not in item:
            raise ValueError(f"{item.get('name', '?')} lacks requires_short or a monitor "
                             f"status: a register is schema "
                             f"{CANDIDATE_REGISTER_RECORD_SCHEMA_VERSION} and says both "
                             f"for every candidate")
    content = {
        "schema_version": CANDIDATE_REGISTER_RECORD_SCHEMA_VERSION,
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
    # IDEMPOTENT ON CONTENT, NOT ON THE CLOCK. declared_at is inside the sealed
    # content, so comparing identities made every run of the declaring script
    # append a fresh duplicate that differed only by seconds -- two appeared in
    # sixteen. What makes two registers the same declaration is the same
    # justification and the same candidates under the same schema.
    def same_declaration(other):
        return (other["schema_version"] == record["schema_version"]
                and other["justification"] == record["justification"]
                and encoded(other["candidates"]) == encoded(record["candidates"]))
    for item in registry["registers"]:
        if item["register_id"] == record["register_id"] or same_declaration(item):
            return item
    registry["registers"].append(record)
    _atomic_write(path, encoded(registry))
    return record
