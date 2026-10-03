"""Seven questions a Hypothesis must answer BEFORE any data is captured.

WHY THIS EXISTS, measured rather than argued. Across 47 sealed Hypotheses:

  30 of 47   a price transform predicts price
  13 of 47   a real payment exists (funding / carry)
   2 of 47   a ratio reverts
   2 of 47   a level is claimed
   2 of 47   NAME WHO PAYS -- and both were written on 2026-10-02

Forty-five were declared with no answer to "who is on the other side". The
consequence is in the multiplicity audit: 193 folds, the pre-declared effect
came back positive on 78 of 189 usable ones -- 0.413 against the 0.500 a coin
would give, which is 2.40 sigma the WRONG WAY -- with a mean fold metric of
-0.0011. The best single result in the project has p = 0.0625 against a
Bonferroni alpha of 0.00135 for the search that produced it: short by a factor
of forty-six.

THE DIAGNOSIS IS NOT "NOTHING WORKS". It is that a Hypothesis here was declared
along ONE dimension -- does a relation exist -- and judged along FOUR: level,
cost, path and durability. Three died on a dimension that was never in their
declaration. The carry on turnover, 253 crossings eating 257% of its gross. The
equity premium on monitorability, with no non-P&L variable in existence. The
credit premium on effect size, at 0.19 against a bar of roughly 0.54.

Every one of those was knowable, cheaply, before a single bar was captured.

AND THE INVERSE IS FORBIDDEN GROUND. The obvious reading of 0.413 is "declare
the opposite and get 0.587". This project already measured that trap: the
funding near-miss looked like 5/5 confirmation on the data that suggested it,
and the honest pre-declared test inverted the sign the following year. Nothing
here licenses flipping a direction to fit an observed tilt.

WHY FEWER HYPOTHESES IS NOT A SLOWER SEARCH BUT A STRONGER ONE. The correction a
result must clear scales with how many were tried. At 47 the Bonferroni alpha is
0.00135; at 5 it is 0.01; at 1 it is 0.05. A single well-chosen Hypothesis needs
roughly a thirty-seventh of the evidence that the same result would need as the
forty-seventh draw from a wide search. Volume is not neutral -- it is a tax paid
by every candidate, including the good one.

THE CONTRACT. Seven answers, sealed in their own record referencing the
Hypothesis, so the Hypothesis schema is untouched and every record already
issued stays valid. `require_pre_declaration` refuses to let a dataset be built
for a Hypothesis created after this contract existed and lacking one -- derived
from the creation timestamp, never from a flag, so the forty-seven sealed before
it are unaffected by construction.
"""

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from tramitago_quant_core.shared.util import (
    digest, encoded, _atomic_write, _explicit_utc, _hypothesis_text_is_valid,
    _hypothesis_code_revision_is_valid,
)

PRE_DECLARATION_SCHEMA_VERSION = "1"
PRE_DECLARATION_REGISTRY_SCHEMA_VERSION = "1"

# Hypotheses created from this instant must carry one. Derived from the
# Hypothesis's own creation_timestamp rather than announced by a flag, for the
# same reason forward-dating is: a flag can be omitted, a timestamp cannot.
PRE_DECLARATION_IN_FORCE_SINCE = "2026-10-03T00:00:00Z"

CLAIM_PREMIUM = "PREMIUM"
CLAIM_MISPRICING = "MISPRICING"
CLAIM_CLASSES = (CLAIM_PREMIUM, CLAIM_MISPRICING)

QUESTIONS = ("payer", "turnover", "net_level", "monitor", "required_effect",
             "claim_class", "window")

# §8.2 as corrected 2026-10-03: the gate is point >= 0.50 AND the lower 95%
# bound > 0. MEASURED on SPY at 2198 days, the bound sits 0.543 below the point,
# so clearing both needs a point of roughly 0.54 at a decade of daily data. A
# shorter window needs more. This is the number a declaration must confront.
MINIMUM_PLAUSIBLE_POINT_SHARPE = "0.54"


def _decimal(value, name):
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a decimal string") from error
    if not number.is_finite():
        raise ValueError(f"{name} must be finite")
    return number


def _prose(value, name, minimum_words=8):
    """An answer, not a gesture. A one-word 'yes' to 'who pays' is the failure
    this contract exists to stop, so the floor is a sentence."""
    if not _hypothesis_text_is_valid(value):
        raise ValueError(f"{name} must be answered in writing")
    if len(value.split()) < minimum_words:
        raise ValueError(
            f"{name} is answered in {len(value.split())} words; this contract exists "
            f"because 45 of 47 Hypotheses answered it in none")
    return value.strip()


def pre_declaration(*, hypothesis_id, payer, why_they_keep_paying, crossings_per_year,
                    net_level_claimed, monitor_variable, monitor_publisher,
                    required_point_sharpe, plausible_point_sharpe, plausibility_source,
                    claim_class, window_years, adverse_episode_in_window, source):
    """Seal seven answers. Refuses rather than records an answer that defeats itself.

    THE ONE THAT REFUSES OUTRIGHT is the effect size. A Hypothesis whose
    plausible effect is below what the gate needs is dead before capture, and
    declaring the REQUIRED size is feasibility rather than selection -- it is
    computed from the gate and the window, never from the returns. The credit
    premium cost two days because nobody asked: its plausible Sharpe was known
    from the literature to run 0.3 to 0.5 against a bar near 0.54, and it came
    in at 0.19.
    """
    if not isinstance(hypothesis_id, str) or not hypothesis_id.startswith("HYPOTHESIS|"):
        raise ValueError("A pre-declaration must reference its Hypothesis")
    if claim_class not in CLAIM_CLASSES:
        raise ValueError(f"Claim class must be one of {', '.join(CLAIM_CLASSES)}")

    crossings = _decimal(crossings_per_year, "Crossings per year")
    if crossings < 0:
        raise ValueError("Crossings per year cannot be negative")
    years = _decimal(window_years, "Window years")
    if years <= 0:
        raise ValueError("Window years must be positive")

    required = _decimal(required_point_sharpe, "Required point Sharpe")
    plausible = _decimal(plausible_point_sharpe, "Plausible point Sharpe")
    if required < _decimal(MINIMUM_PLAUSIBLE_POINT_SHARPE, "Floor"):
        raise ValueError(
            f"The required point Sharpe cannot be below {MINIMUM_PLAUSIBLE_POINT_SHARPE}: "
            f"§8.2 needs point >= 0.50 AND a lower 95% bound above zero, and the bound "
            f"sits about 0.54 below the point at a decade of daily data")
    if plausible < required:
        # Not a warning. A Hypothesis whose own declared plausible effect is
        # below what its gate needs has been refuted by its author before any
        # data was read, and capturing it would be spending to confirm that.
        raise ValueError(
            f"Declared plausible effect {plausible} is below the {required} this "
            f"Hypothesis needs. It cannot clear its own gate and must not be captured; "
            f"say so and stop, rather than measuring to find out")

    content = {
        "schema_version": PRE_DECLARATION_SCHEMA_VERSION,
        "hypothesis_id": hypothesis_id,
        "payer": {
            "who": _prose(payer, "Who pays"),
            "why_they_keep_paying": _prose(why_they_keep_paying, "Why they keep paying"),
        },
        "turnover": {"crossings_per_year": crossings_per_year},
        "net_level": {"claimed": _prose(net_level_claimed, "The net level claimed")},
        "monitor": {
            "variable": _prose(monitor_variable, "The monitored variable", 3),
            "published_by": _prose(monitor_publisher, "Who publishes it", 3),
        },
        "required_effect": {
            "required_point_sharpe": required_point_sharpe,
            "plausible_point_sharpe": plausible_point_sharpe,
            "plausibility_source": _prose(plausibility_source, "Plausibility source"),
        },
        "claim_class": claim_class,
        "window": {
            "years": window_years,
            "adverse_episode": _prose(adverse_episode_in_window, "The adverse episode"),
        },
        "source": _prose(source, "Where these answers come from"),
    }
    return {**content, "pre_declaration_id": "PRE_DECLARATION|" + digest(encoded(content))}


def verified_pre_declaration(record):
    """Re-derive the identity, failing closed on any alteration of the answers."""
    if not isinstance(record, dict) or "pre_declaration_id" not in record:
        raise ValueError("Pre-declaration is invalid")
    content = {key: record[key] for key in record if key != "pre_declaration_id"}
    if record["pre_declaration_id"] != "PRE_DECLARATION|" + digest(encoded(content)):
        raise ValueError("Pre-declaration identity does not match its answers")
    if set(content) != {"schema_version", "hypothesis_id", "source", *QUESTIONS}:
        raise ValueError("Pre-declaration does not answer exactly the seven questions")
    return record


def constitute_pre_declaration(registry_path, record, *, declared_at, code_revision):
    """Seal one pre-declaration. Identical answers for the same Hypothesis return
    the existing record rather than appending a second: re-running the declaring
    script is not a second Hypothesis."""
    verified_pre_declaration(record)
    if not _explicit_utc(declared_at):
        raise ValueError("A pre-declaration must say when it was declared")
    if not _hypothesis_code_revision_is_valid(code_revision):
        raise ValueError("A pre-declaration must say which revision produced it")
    sealed = {**record, "materialization": {"declared_at": declared_at,
                                            "code_revision": code_revision}}
    path = Path(registry_path)
    registry = (json.loads(path.read_bytes()) if path.exists() else
                {"schema_version": PRE_DECLARATION_REGISTRY_SCHEMA_VERSION,
                 "pre_declarations": []})
    for item in registry["pre_declarations"]:
        if item["pre_declaration_id"] == record["pre_declaration_id"]:
            return item
    registry["pre_declarations"].append(sealed)
    _atomic_write(path, encoded(registry))
    return sealed


def load_pre_declaration(registry_path, hypothesis_id):
    path = Path(registry_path)
    if not path.exists():
        return None
    for item in json.loads(path.read_bytes())["pre_declarations"]:
        if item["hypothesis_id"] == hypothesis_id:
            return verified_pre_declaration(
                {k: v for k, v in item.items() if k != "materialization"})
    return None


def require_pre_declaration(hypothesis, registry_path):
    """Refuse to build a dataset for a Hypothesis that should have answered first.

    Derived from the Hypothesis's own creation timestamp, so the 47 sealed
    before this contract existed are unaffected and no exemption list is kept.
    Enforced where the money is spent -- at capture -- rather than offered as
    advice, because the moment it matters is the moment somebody is impatient.
    """
    created = hypothesis.get("creation_timestamp")
    if not _explicit_utc(created) or created < PRE_DECLARATION_IN_FORCE_SINCE:
        return None
    found = load_pre_declaration(registry_path, hypothesis["hypothesis_id"])
    if found is None:
        raise ValueError(
            f"{hypothesis['hypothesis_id'][:40]} was created after "
            f"{PRE_DECLARATION_IN_FORCE_SINCE} and has no pre-declaration. Seven "
            f"questions must be answered before any data is captured: "
            f"{', '.join(QUESTIONS)}")
    return found
