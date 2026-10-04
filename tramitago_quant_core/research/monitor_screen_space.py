"""The candidate space of the monitor screen: validated, never executed.

WHAT THE SCREEN IS FOR. Every premium measured so far was declared together with its monitor,
and the monitor then failed R3. The screen inverts the order: a sealed list of (variable, state,
exposure) candidates is scanned once, every candidate is counted, and a variable is only built
around if it led adverse returns of its exposure on a window it was selected on AND on a holdout
the scan could not read.

THIS MODULE READS NO DATA. It checks that the space is a space: that it could be sealed.

  The pass rule is the ENGINE's own. A screen easier than the gate a premium later faces would
  promote variables that then fail it, and a harder one would discard variables the gate would
  accept. So the constants are compared with premium_engine and monitors, not restated.

  Every candidate carries a written mechanism and ONE direction. A direction searched both ways
  doubles the candidates and invites reading whichever sign worked as the mechanism.

  The multiplicity is counted including the tests already run on this question. A screen that
  forgot the four it had already run would present one candidate in nineteen as one in fourteen.

  Windows are ordered and disjoint, and the holdout comes after the discovery window.
"""

import math
from datetime import datetime
from decimal import Decimal

from tramitago_quant_core.research.monitors import MINIMUM_TRIGGER_DAYS_PER_FOLD
from tramitago_quant_core.research.premium_engine import (
    LINK_CONSISTENCY_THRESHOLD, LINK_MINIMUM_USABLE_FOLDS)

STATUS_DRAFT = "DRAFT_FOR_REVIEW_NOT_SEALED"
STATUS_SEALED = "SEALED"
FORMS = ("SIGN_AT_OR_BELOW_ZERO", "CHANGE_21D_ABOVE_ZERO", "ABOVE_TRAILING_MEDIAN_252")
AVAILABILITY = ("TO_PROBE", "CAPTURED_FOR_CREDIT_PREMIUM", "CAPTURED_FOR_VARIANCE_PREMIUM",
                "PROBED_AVAILABLE")
MINIMUM_MECHANISM_WORDS = 12
MINIMUM_FOLDS = 5
MINIMUM_COMPLETENESS = 0.90
MAXIMUM_GAP_DAYS = 10
MINIMUM_HISTORY_DAYS = 380

TOP_LEVEL = {"slug", "status", "purpose", "justification", "windows", "folds", "pass_rule",
             "holdout_rule", "return_definition", "state_forms", "fixed_parameters",
             "variables", "exposures", "candidates", "adverse_direction", "ledger_prior",
             "multiplicity", "decisions", "open_decisions"}


def _instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def chance_of_passing(folds, threshold=LINK_CONSISTENCY_THRESHOLD):
    """The probability that a variable with NO relation to the returns meets the fold rule.

    Each usable fold is a coin flip under the null, and the rule needs at least threshold of
    them. This is an UPPER bound: it assumes every fold is usable, and an unusable fold can
    only make passing harder.
    """
    needed = math.ceil(Decimal(folds) * threshold)
    return sum(math.comb(folds, k) for k in range(needed, folds + 1)) / 2 ** folds


def expected_false_passes(space):
    return len(space["candidates"]) * chance_of_passing(space["folds"])


def _words(text):
    return len(str(text).split())


def validate_space(space):
    """Refuse a space that could not be sealed. Returns the space on success."""
    if not isinstance(space, dict):
        raise ValueError("A space must be an object")
    unknown = set(space) - TOP_LEVEL
    missing = TOP_LEVEL - set(space)
    if unknown or missing:
        raise ValueError(f"space keys differ: unknown {sorted(unknown)}, missing {sorted(missing)}")
    if space["status"] not in (STATUS_DRAFT, STATUS_SEALED):
        raise ValueError("status must be a draft or sealed")

    windows = space["windows"]
    discovery, holdout = windows["discovery"], windows["holdout"]
    for window in (discovery, holdout):
        if _instant(window["start_utc"]) >= _instant(window["end_exclusive_utc"]):
            raise ValueError("a window must start before it ends")
    if _instant(holdout["start_utc"]) < _instant(discovery["end_exclusive_utc"]):
        raise ValueError("the holdout must begin where the discovery window ends, never "
                         "inside it: a holdout the scan could read is not a holdout")

    if not isinstance(space["folds"], int) or space["folds"] < MINIMUM_FOLDS:
        raise ValueError(f"folds must be at least {MINIMUM_FOLDS}, the minimum usable folds")
    rule = space["pass_rule"]
    if rule["minimum_usable_folds"] != LINK_MINIMUM_USABLE_FOLDS \
            or Decimal(rule["consistency_threshold"]) != LINK_CONSISTENCY_THRESHOLD \
            or rule["minimum_trigger_days_per_fold"] != MINIMUM_TRIGGER_DAYS_PER_FOLD:
        raise ValueError("the pass rule must be the engine's own M1 constants: a screen easier "
                         "or harder than the gate a premium faces is not a screen for it")

    if set(space["state_forms"]) != set(FORMS):
        raise ValueError("state_forms must be exactly the declared forms")
    for key in ("change_horizon_days", "median_window_days"):
        if not isinstance(space["fixed_parameters"].get(key), int) \
                or space["fixed_parameters"][key] < 1:
            raise ValueError(f"fixed_parameters.{key} must be a positive whole number")

    variables = {}
    for variable in space["variables"]:
        if variable["id"] in variables:
            raise ValueError(f"variable {variable['id']} appears twice")
        validity = variable.get("validity", {})
        if validity.get("frequency") != "daily" or validity.get("revisions") != "none" \
                or validity.get("quote_based") is not True \
                or not str(validity.get("known_at_decision_time", "")).strip():
            raise ValueError(
                f"{variable['id']}: a monitor variable must be daily, quote based, never "
                f"revised, and known at decision time, or it cannot be acted on")
        if variable.get("availability") not in AVAILABILITY:
            raise ValueError(f"{variable['id']}: availability must be one of {AVAILABILITY}")
        if variable["availability"] == "PROBED_AVAILABLE":
            facts = variable.get("probe") or {}
            if facts.get("completeness", 0) < MINIMUM_COMPLETENESS \
                    or facts.get("longest_gap_days") is None \
                    or facts["longest_gap_days"] > MAXIMUM_GAP_DAYS \
                    or "value" in " ".join(facts):
                raise ValueError(f"{variable['id']}: a probed variable must carry probe facts "
                                 f"that meet the probe bar and hold no value")
        for key in ("series", "measures", "mechanism_family"):
            if not str(variable.get(key, "")).strip():
                raise ValueError(f"{variable['id']}: {key} must be written")
        variables[variable["id"]] = variable

    for variable in space["variables"]:
        if variable.get("lag_days") not in (0, 1):
            raise ValueError(f"{variable['id']}: lag_days must be 0 or 1, the publication lag "
                             f"the state is read with")
    history = space["fixed_parameters"].get("series_history_start")
    if not history or (_instant(windows["discovery"]["start_utc"]) - _instant(
            history + "T00:00:00Z")).days < MINIMUM_HISTORY_DAYS:
        raise ValueError(f"series_history_start must lie at least {MINIMUM_HISTORY_DAYS} days "
                         f"before the discovery window: the 252 day median needs it")

    exposures = {}
    for exposure in space["exposures"]:
        if not str(exposure.get("return_column", "")).strip():
            raise ValueError(f"{exposure['id']}: return_column must name the sealed column")
        if exposure["id"] in exposures:
            raise ValueError(f"exposure {exposure['id']} appears twice")
        exposures[exposure["id"]] = exposure

    seen, ids = set(), set()
    for candidate in space["candidates"]:
        if candidate["id"] in ids:
            raise ValueError(f"candidate {candidate['id']} appears twice")
        ids.add(candidate["id"])
        if set(candidate) != {"id", "variable", "form", "exposure", "mechanism"}:
            raise ValueError(f"{candidate['id']}: a candidate carries no direction or "
                             f"threshold of its own; the direction is declared once")
        if candidate["variable"] not in variables or candidate["exposure"] not in exposures \
                or candidate["form"] not in FORMS:
            raise ValueError(f"{candidate['id']}: names a variable, exposure or form that "
                             f"is not declared")
        key = (candidate["variable"], candidate["form"], candidate["exposure"])
        if key in seen:
            raise ValueError(f"{candidate['id']}: the same candidate is declared twice")
        seen.add(key)
        if _words(candidate["mechanism"]) < MINIMUM_MECHANISM_WORDS:
            raise ValueError(f"{candidate['id']}: no mechanism, no candidate: the reason this "
                             f"variable should lead adverse returns needs at least "
                             f"{MINIMUM_MECHANISM_WORDS} words")
    if {c["variable"] for c in space["candidates"]} != set(variables) \
            or {c["exposure"] for c in space["candidates"]} != set(exposures):
        raise ValueError("every declared variable and exposure must be used by a candidate")
    if "LOWER" not in space["adverse_direction"] or "never searched both ways" not in \
            space["adverse_direction"]:
        raise ValueError("the adverse direction must be declared once and never searched both ways")

    for decision in space["decisions"]:
        if set(decision) != {"n", "date", "by", "decision", "consequence"}                 or not all(str(decision[k]).strip() for k in decision):
            raise ValueError("a recorded decision needs its number, date, author, text and "
                             "consequence")
    count = space["multiplicity"]
    if count["scanned"] != len(space["candidates"]) \
            or count["also_counted_from_the_ledger"] != len(space["ledger_prior"]) \
            or count["total_tested_on_this_question"] != count["scanned"] \
            + count["also_counted_from_the_ledger"]:
        raise ValueError("the multiplicity must count every candidate AND every test already "
                         "run on this question")
    return space
