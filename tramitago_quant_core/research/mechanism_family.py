"""A mechanism family: one monitor, fixed once, tested on every asset that shares it.

WHY THIS EXISTS. Declaring a premium one asset at a time makes every test a fresh
chance to find an asset where the monitor happens to pass, and every asset that stops
working a fresh manual cycle. Both are the same defect from two sides: the asset is
being fitted to the function, or the function to the asset.

A family inverts that. The MECHANISM (who pays, why, what would end it) and the
MONITOR that watches it are written once. Each member supplies only what is a fact
about its own asset: the symbol, the volatility series and underlying that stand in
for the mechanism in its market, a cost, an adverse threshold and a recalled effect
range. The platform derives the spec, declares the Hypothesis, measures it and judges
the family jointly.

THE JOINT RULE. The monitor is admissible only if its empirical link holds in EVERY
member. One member passing while another fails is not a monitor that works, it is a
monitor that works where it was lucky, which is what chance produces. The rule makes
the test harder as members are added, never easier.

  GENERALIZES            every member REACHABLE_AND_MET
  DOES_NOT_GENERALIZE    at least one member REACHABLE_AND_FAILED
  INSUFFICIENT_EVIDENCE  none failed but at least one could not be measured
  PENDING                a member has not been run yet

Failure outranks insufficiency: a monitor refuted in one market is refuted, whatever
the other markets could not show. INSUFFICIENT_EVIDENCE is not a pass.

THE DEFINITION IS SEALED BEFORE ANY MEMBER IS MEASURED. A run refuses a family whose
definition no longer matches a sealed record, so the monitor cannot be nudged after the
first result is seen. Hypothesis identities are filled in after declaration and are not
part of the definition.
"""

import copy
import json
from decimal import Decimal
from pathlib import Path

from tramitago_quant_core.shared.util import digest, encoded, _atomic_write

FAMILY_SCHEMA_VERSION = "1"
VERDICT_SCHEMA_VERSION = "1"

GENERALIZES = "GENERALIZES"
DOES_NOT_GENERALIZE = "DOES_NOT_GENERALIZE"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
PENDING = "PENDING"

LINK_MET, LINK_FAILED, LINK_UNREACHABLE = (
    "REACHABLE_AND_MET", "REACHABLE_AND_FAILED", "UNREACHABLE")

MEMBER_KEYS = {"symbol", "name", "hypothesis_id", "implied_series", "implied_label",
               "implied_publisher", "underlying", "underlying_label", "index_label",
               "period", "expected_annual_yield", "cost_source", "half_spread", "slippage",
               "adverse_threshold", "crossings_per_year", "pre_declaration", "notes",
               "caveats"}
REQUIRED_MEMBER_KEYS = MEMBER_KEYS - {"hypothesis_id", "caveats"}
PRE_DECLARATION_KEYS = {"plausible_low", "plausible_high", "plausibility_source",
                        "window_years", "available_window_years",
                        "why_shorter_than_available", "adverse_episode_in_window"}
MECHANISM_KEYS = {"name", "payer", "why_they_keep_paying", "what_would_end_it",
                  "claim_class", "net_level_template", "monitor", "sizing_drawdown_limit",
                  "level_claim", "decision", "caveats", "provenance"}


def validate_family(family):
    """Refuse a family that cannot be derived into specs, up front and loudly."""
    if not isinstance(family, dict):
        raise ValueError("A family must be an object")
    for key in ("slug", "mechanism", "joint_rule", "members"):
        if key not in family:
            raise ValueError(f"A family must declare {key}")
    missing = MECHANISM_KEYS - set(family["mechanism"])
    if missing:
        raise ValueError(f"mechanism is missing: {', '.join(sorted(missing))}")
    rule = family["joint_rule"]
    if rule.get("requires") != "every member":
        raise ValueError("joint_rule.requires must be exactly: every member. A family "
                         "that accepts fewer than all of its members is a way of "
                         "choosing the asset that fits")
    members = family["members"]
    if len(members) < 2:
        raise ValueError("A family needs at least two members: one asset cannot show "
                         "that a monitor generalizes")
    seen = set()
    for member in members:
        unknown = set(member) - MEMBER_KEYS
        if unknown:
            raise ValueError(f"Unknown member keys: {', '.join(sorted(unknown))}")
        absent = REQUIRED_MEMBER_KEYS - set(member)
        if absent:
            raise ValueError(f"{member.get('symbol', '?')} is missing: "
                             f"{', '.join(sorted(absent))}")
        if member["symbol"] in seen:
            raise ValueError(f"{member['symbol']} appears twice")
        seen.add(member["symbol"])
        gap = PRE_DECLARATION_KEYS - set(member["pre_declaration"])
        if gap:
            raise ValueError(f"{member['symbol']} pre_declaration is missing: "
                             f"{', '.join(sorted(gap))}")
        if Decimal(str(member["adverse_threshold"])) > 0:
            raise ValueError(f"{member['symbol']} adverse_threshold must be a loss")
    return family


def _monitor(mechanism, member):
    """The monitor of one member: the template, with only the series substituted."""
    template = mechanism["monitor"]
    window = template["window"]
    return {
        "kind": template["kind"],
        "variable": f"{member['implied_label']} minus the {window}-day realised volatility "
                    f"of the {member['underlying_label']}",
        "implied": member["implied_series"], "underlying": member["underlying"],
        "window": window, "floor": template["floor"],
        "confirmation_periods": template["confirmation_periods"],
        "re_entry_threshold": template["re_entry_threshold"],
        "source": f"{member['implied_publisher']}; realised volatility is computed from "
                  f"sealed {member['underlying_label']} prices",
    }


def member_spec(family, member):
    """The engine spec for one member, derived and never hand written."""
    mechanism = family["mechanism"]
    symbol = member["symbol"]
    index = member["index_label"]
    level_claim = dict(mechanism["level_claim"])
    level_claim["adverse_threshold"] = str(member["adverse_threshold"])
    return {
        "slug": f"variance-premium-{symbol.lower()}",
        "hypothesis_id": member.get("hypothesis_id", ""),
        "notes": member["notes"],
        "instrument": {"provider": "alpaca_equity", "symbol": symbol, "feed": "sip"},
        "cost": {"commission": "0", "half_spread": member["half_spread"],
                 "slippage": member["slippage"], "legs": 1, "source": member["cost_source"]},
        "sizing": {"drawdown_limit": mechanism["sizing_drawdown_limit"]},
        "level_claim": level_claim,
        "monitor": _monitor(mechanism, member),
        "controls": [{"kind": "underlying_forward_returns", "name": member["underlying"]}],
        "checks": [{"kind": "distribution_adjustment",
                    "expected_annual_yield": list(member["expected_annual_yield"])}],
        "survival": {
            "counterparty": mechanism["payer"].format(index=index),
            "why_they_accept_losing": mechanism["why_they_keep_paying"],
            "what_would_end_it": mechanism["what_would_end_it"],
        },
        "decision": dict(mechanism["decision"]),
    }


def definition(family):
    """What is sealed: everything except the Hypothesis identities filled in later."""
    stripped = copy.deepcopy(family)
    for member in stripped["members"]:
        member.pop("hypothesis_id", None)
    return stripped


def definition_digest(family):
    return digest(encoded(definition(family)))


def pre_declaration_answers(family, member):
    """The seven answers for one member: the family's, plus the member's own numbers."""
    mechanism, own = family["mechanism"], member["pre_declaration"]
    return dict(
        payer=mechanism["payer"].format(index=member["index_label"]),
        why_they_keep_paying=mechanism["why_they_keep_paying"],
        crossings_per_year=member["crossings_per_year"],
        net_level_claimed=mechanism["net_level_template"].format(symbol=member["symbol"]),
        monitor_variable=_monitor(mechanism, member)["variable"],
        monitor_publisher=member["implied_publisher"] + " as " + member["implied_series"]
                          + ", while realised volatility is computed from index prices",
        plausible_low=own["plausible_low"], plausible_high=own["plausible_high"],
        plausibility_source=own["plausibility_source"],
        claim_class=mechanism["claim_class"],
        window_years=own["window_years"], available_window_years=own["available_window_years"],
        why_shorter_than_available=own["why_shorter_than_available"],
        adverse_episode_in_window=own["adverse_episode_in_window"],
        source="answered in writing before any bar of " + member["symbol"] + " was captured, "
               "from a monitor fixed by the family and not by this fund",
    )


def judge_family(members, outcomes):
    """The joint verdict. `outcomes` maps a symbol to its monitor result or None."""
    rows = []
    for member in members:
        result = outcomes.get(member["symbol"])
        rows.append({"symbol": member["symbol"],
                     "link_outcome": None if result is None else result["link_outcome"],
                     "met": None if result is None else result["link_met"],
                     "usable": None if result is None else result["link_usable"],
                     "ratio": None if result is None else result["link_ratio"],
                     "control": None if result is None else result.get("control")})
    states = [row["link_outcome"] for row in rows]
    if any(state is None for state in states):
        verdict, reason = PENDING, "a member has not been measured yet"
    elif LINK_FAILED in states:
        failed = [row["symbol"] for row in rows if row["link_outcome"] == LINK_FAILED]
        verdict = DOES_NOT_GENERALIZE
        reason = (f"the empirical link was reachable and failed in {', '.join(failed)}; a "
                  f"monitor refuted in one market is refuted, whatever the others show")
    elif LINK_UNREACHABLE in states:
        verdict = INSUFFICIENT_EVIDENCE
        reason = "none failed, but at least one member could not be measured; not a pass"
    else:
        verdict, reason = GENERALIZES, "the link was reachable and met in every member"
    return {"verdict": verdict, "reason": reason, "members": rows}


# --- sealed records ---------------------------------------------------------------------

def _registry(path, key):
    path = Path(path)
    if not path.exists():
        return {"schema_version": "1", key: []}
    return json.loads(path.read_bytes())


def seal_family(path, family, *, sealed_at, code_revision, authorised_by, ground):
    """Seal the definition. Idempotent on content: the same definition seals once."""
    content = {"schema_version": FAMILY_SCHEMA_VERSION, "kind": "mechanism-family",
               "slug": family["slug"], "definition_digest": definition_digest(family),
               "joint_rule": family["joint_rule"],
               "members": [m["symbol"] for m in family["members"]],
               "member_hypotheses": {m["symbol"]: m.get("hypothesis_id")
                                     for m in family["members"]},
               "ground": ground}
    family_id = "MECHANISM_FAMILY|" + digest(encoded(content))
    registry = _registry(path, "families")
    for record in registry["families"]:
        if record["family_id"] == family_id:
            return record, False
    record = {**content, "family_id": family_id, "sealed_at": sealed_at,
              "code_revision": code_revision, "authorised_by": authorised_by}
    registry["families"].append(record)
    _atomic_write(Path(path), encoded(registry))
    return record, True


def sealed_family(path, family):
    """The sealed record whose definition equals this family, or a refusal."""
    wanted = definition_digest(family)
    for record in _registry(path, "families")["families"]:
        if record["definition_digest"] == wanted:
            return record
    raise ValueError(
        "REFUSED: this family definition is not sealed. Its monitor, rule and members "
        "must be sealed before any member is measured, so that nothing can be adjusted "
        "after a result is seen. Run declare_family.py first; a changed definition is a "
        "new family and needs its own seal.")


def seal_verdict(path, family_record, verdict, *, decided_at, code_revision):
    """Seal the joint verdict. Idempotent on content, never on the clock."""
    content = {"schema_version": VERDICT_SCHEMA_VERSION, "kind": "family-verdict",
               "family_id": family_record["family_id"], "verdict": verdict["verdict"],
               "reason": verdict["reason"], "members": verdict["members"]}
    verdict_id = "FAMILY_VERDICT|" + digest(encoded(content))
    registry = _registry(path, "verdicts")
    for record in registry["verdicts"]:
        if record["verdict_id"] == verdict_id:
            return record, False
    record = {**content, "verdict_id": verdict_id, "decided_at": decided_at,
              "code_revision": code_revision}
    registry["verdicts"].append(record)
    _atomic_write(Path(path), encoded(registry))
    return record, True


def family_clearance(hypothesis_id, families_path, verdicts_path):
    """Whether a Hypothesis may rely on its monitor link, under the joint rule.

    A Hypothesis outside every family is unaffected: the rule binds members only. A
    member is cleared only when the LATEST verdict of its family is GENERALIZES; a
    family with no verdict, or any other verdict, clears nobody.
    """
    families = [r for r in _registry(families_path, "families")["families"]
                if hypothesis_id in (r.get("member_hypotheses") or {}).values()]
    if not families:
        return True, "not a member of any mechanism family"
    ids = {r["family_id"] for r in families}
    verdicts = [v for v in _registry(verdicts_path, "verdicts")["verdicts"]
                if v["family_id"] in ids]
    if not verdicts:
        return False, "its family has no verdict yet"
    latest = max(verdicts, key=lambda v: v["decided_at"])
    if latest["verdict"] != GENERALIZES:
        return False, f"its family verdict is {latest['verdict']}: {latest['reason']}"
    return True, "its family verdict is GENERALIZES"
