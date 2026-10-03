"""Judge a held-position Hypothesis from a SPEC, never from a script written for it.

WHY THIS EXISTS. Twenty-three research runners stamp a code_revision into sealed
records and none has a test; the three most recent share 52 to 61 percent of their
executable code. Every defect found in the last two days had to be fixed by hand in
each of them and was missed at least once: the adverse threshold that must scale
with the weight, the capacity model returning no ceiling, the identity form
certifying a monitor the evidence had refuted. The judgement was tied to the
instrument because it lived inside a script written for it.

This is the judgement, once. It knows no ticker, no venue and no premium. What
varies between Hypotheses is DATA and is in the spec: the cost rates, the monitor,
the adverse threshold, the survival mechanism. What does not vary is code and is
here: net returns, sizing against the drawdown limit, adverse bounds, the level
claim, the empirical link, capacity, and the evidence the admission gates read.

PURE. Nothing here touches the network, a registry or the clock, so the whole of it
can be tested against the sealed datasets already in the repository -- which is how
it is proven to reproduce the sealed results for SPY and SVXY rather than merely
resemble them.

ONLY THE HELD POSITION. One entry, one exit, no signal: the only position form this
project has ever validated at a level. A strategy that trades is a different object
and does not belong in a spec.
"""

import math
from decimal import Decimal, InvalidOperation

from tramitago_quant_core.governance.admission import (
    adverse_bound, evaluate_admission_gates, SURVIVAL_P2, MONITOR_LINK_M1,
    MONITOR_LINK_M2, LINK_UNREACHABLE, LINK_REACHABLE_MET, LINK_REACHABLE_FAILED,
)
from tramitago_quant_core.research.level_claim import (
    level_claim, evaluate_fold, level_claim_outcome,
)
from tramitago_quant_core.research.monitors import (
    evaluate_monitor, fold_bounds, link_consistency, MONITOR_KINDS, KIND_NONE,
)
from tramitago_quant_core.risk.capacity_model import capacity_contract, capacity_ceiling
from tramitago_quant_core.risk.cost_model import (
    cost_contract, net_returns, cost_per_side, COST_REGIME_HOLDING,
)
from tramitago_quant_core.risk.degradation_monitor import (
    monitoring_contract, detection_latency_seconds, DEGRADES_BELOW, ACTION_SUSPEND,
)
from tramitago_quant_core.risk.statistic_bounds import (
    net_sharpe_lower_bound, drawdown_upper_bound, sharpe_ratio, max_drawdown,
    weight_within_drawdown_bound,
)

# THE ONLY PROVIDER THE ENGINE CAN FETCH FROM, and the feeds that provider serves.
# A spec naming anything else is REFUSED, because the first version only checked
# that a provider was NAMED: a spec saying bitmex would have silently fetched
# Alpaca bars for that symbol, which is the class of defect this engine exists to
# end. A field that is accepted and not honoured is worse than one that is absent.
SUPPORTED_PROVIDERS = ("alpaca_equity",)
SUPPORTED_FEEDS = ("sip", "iex")

# The dataset column a Hypothesis is judged on. A single instrument is judged on the
# close-to-close return; a PAIR held long one leg and short the other at equal
# notional on the SPREAD return, which is a difference of two real returns and not
# one instrument's.
FORWARD_COLUMN = "forward_return_1d"
FORWARD_COLUMN_PAIR = "forward_spread_return_1d"

TRADING_DAYS = 252
TRANSITIONS = 2                     # one entry, one exit: the only position form here
LINK_MINIMUM_USABLE_FOLDS = 5
LINK_CONSISTENCY_THRESHOLD = Decimal("0.70")
DEFAULT_BOOTSTRAP = {"confidence": "0.95", "resamples": 2000, "block_periods": 5, "seed": 0}
CAPACITY_REFERENCE, CAPACITY_SEARCH_MULTIPLE = 200.0, 10_000

TOP_LEVEL = {"slug", "hypothesis_id", "instrument", "cost", "sizing", "level_claim",
             "bootstrap", "monitor", "survival", "decision", "checks", "controls", "notes"}


def _decimal(value, name):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{name} must be a decimal, got {value!r}") from error
    if not number.is_finite():
        raise ValueError(f"{name} must be finite")
    return number


def forward_column(spec):
    return FORWARD_COLUMN_PAIR if spec["instrument"].get("second_leg") else FORWARD_COLUMN


def validate_spec(spec):
    """Refuse a spec that names something this engine cannot honour, up front.

    Strict on purpose: an unknown top-level key is a typo until proven otherwise,
    and a typo in a field that decides a gate is how a threshold ends up silently
    defaulted.
    """
    if not isinstance(spec, dict):
        raise ValueError("A spec must be an object")
    unknown = set(spec) - TOP_LEVEL
    if unknown:
        raise ValueError(f"Unknown spec keys: {', '.join(sorted(unknown))}")
    for key in ("slug", "hypothesis_id", "instrument", "cost", "sizing", "level_claim",
                "survival", "decision"):
        if key not in spec:
            raise ValueError(f"A spec must declare {key}")
    if not str(spec["hypothesis_id"]).startswith("HYPOTHESIS|"):
        raise ValueError("A spec must reference a Hypothesis identity")
    for key in ("symbol", "provider"):
        if not spec["instrument"].get(key):
            raise ValueError(f"instrument.{key} is required")
    if spec["instrument"]["provider"] not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"instrument.provider {spec['instrument']['provider']!r} is not supported; this "
            f"engine can fetch from {', '.join(SUPPORTED_PROVIDERS)} only, and fetching "
            f"anything else from it would be silent")
    if spec["instrument"].get("feed", "sip") not in SUPPORTED_FEEDS:
        raise ValueError(f"instrument.feed must be one of {', '.join(SUPPORTED_FEEDS)}")
    second = spec["instrument"].get("second_leg")
    if second is not None and not second.get("symbol"):
        raise ValueError("instrument.second_leg needs a symbol")
    # THE NUMBER OF LEGS IS NOT FREE. It decides how many sides of cost every
    # transition pays, and a pair priced as one leg would understate its cost by
    # half while reading as a perfectly ordinary spec.
    expected_legs = 2 if second is not None else 1
    if spec["cost"].get("legs") != expected_legs:
        raise ValueError(f"cost.legs must be {expected_legs} for "
                         f"{'a pair' if second else 'a single instrument'}")
    # A SIMPLE SHORT: one instrument held short, its daily return the NEGATIVE of the
    # instrument's. It must declare what holding a short costs and how far the instrument
    # may rise against it, because both are omissions that read exactly like a free,
    # bounded position: an undeclared borrow fee looks like zero, and a short has no
    # natural limit on what it loses.
    direction = spec["instrument"].get("direction", "LONG")
    if direction not in ("LONG", "SHORT"):
        raise ValueError("instrument.direction must be LONG or SHORT")
    if direction == "SHORT":
        if second is not None:
            raise ValueError("a pair already holds a short leg; instrument.direction SHORT "
                             "is for a single instrument")
        if spec["cost"].get("borrow_annual") is None:
            raise ValueError("a short must DECLARE cost.borrow_annual, even if it is 0: an "
                             "omitted borrow cost reads exactly like a free one")
        if not 0 <= _decimal(spec["cost"]["borrow_annual"], "cost.borrow_annual") < 1:
            raise ValueError("cost.borrow_annual must be a fraction in [0, 1)")
        move = spec["instrument"].get("max_adverse_move")
        if move is None or not 0 < _decimal(move, "instrument.max_adverse_move") < 1:
            raise ValueError("a short loses without bound, so instrument.max_adverse_move "
                             "must declare how far the instrument may rise against it, as a "
                             "fraction in (0, 1)")
    elif "borrow_annual" in spec["cost"] or "max_adverse_move" in spec["instrument"]:
        raise ValueError("borrow_annual and max_adverse_move belong to a SHORT")
    cost = spec["cost"]
    for key in ("commission", "half_spread", "slippage"):
        if _decimal(cost[key], f"cost.{key}") < 0:
            raise ValueError(f"cost.{key} cannot be negative")
    if not isinstance(cost.get("legs"), int) or cost["legs"] < 1:
        raise ValueError("cost.legs must be a positive integer")
    if not str(cost.get("source", "")).strip():
        raise ValueError("cost.source must say where the rates come from")
    limit = _decimal(spec["sizing"]["drawdown_limit"], "sizing.drawdown_limit")
    if not 0 < limit < 1:
        raise ValueError("sizing.drawdown_limit must be a fraction in (0, 1)")
    claim = spec["level_claim"]
    for key in ("folds", "consistency_threshold", "adverse_threshold",
                "minimum_adverse_episodes"):
        if key not in claim:
            raise ValueError(f"level_claim.{key} is required")
    if _decimal(claim["adverse_threshold"], "level_claim.adverse_threshold") > 0:
        raise ValueError("level_claim.adverse_threshold must be zero or a loss")
    monitor = spec.get("monitor", {"kind": KIND_NONE})
    if monitor.get("kind", KIND_NONE) not in MONITOR_KINDS:
        raise ValueError(f"monitor.kind must be one of {', '.join(MONITOR_KINDS)}")
    survival = spec["survival"]
    for key in ("counterparty", "why_they_accept_losing", "what_would_end_it"):
        if not str(survival.get(key, "")).strip():
            raise ValueError(f"survival.{key} must be written, not omitted")
    for key in ("build_cost", "life_years", "tranche"):
        _decimal(spec["decision"][key], f"decision.{key}")
    return spec


# US equity quotes are published to the cent, so a stored close is within half a cent of
# the price it rounds. A property of the quote, derived from nothing a candidate did.
QUOTE_HALF_TICK = 0.005


def _rounding_bound(adjusted_close, raw_close):
    """The most the ratio adjusted/raw can be off from rounding both quotes to the cent."""
    return QUOTE_HALF_TICK / adjusted_close + QUOTE_HALF_TICK / raw_close


def distribution_check(dates, adjusted, raw, expected_annual_yield):
    """Whether the adjusted bars treat distributions as a real total return.

    A fund that pays large monthly distributions, partly return of capital, can be
    mis-adjusted in either direction: omitted distributions understate the Sharpe,
    double counted ones overstate it. The adjustment can be INFERRED from the two
    series. Back-adjusting for distributions scales EARLIER prices down, so the
    ratio of adjusted to raw is below one early on and rises toward one at the end,
    jumping only at ex-dates. Its total rise over the window is the cumulative
    distribution factor, and compounding that over the years gives an implied annual
    yield to compare against what the fund is declared to pay.

    THE RATIO IS NOISY BY CONSTRUCTION. Both closes are rounded to the cent, so the
    ratio of two rounded numbers moves by up to the rounding of each, on each of the
    two days compared: about 1e-4 at a price near 40 and 4e-4 near 17. A fall is
    therefore only evidence of a defect when it EXCEEDS that bound. The first
    version demanded no fall beyond 1e-6 and voided XYLD on 1235 of 2699 days, every
    one of them inside the rounding: it tested idealised floats and so never met it.

    VOID, not merely flagged: a measurement whose adjustment cannot be trusted is not
    a weaker measurement, it is not a measurement of this Hypothesis.
    """
    ratios = [(day, float(adjusted[day]) / float(raw[day])) for day in dates
              if day in adjusted and day in raw and float(raw[day]) > 0]
    if len(ratios) < TRADING_DAYS:
        return {"status": "VOID", "reason": f"only {len(ratios)} days carry both an "
                                           f"adjusted and a raw close"}
    values = [ratio for _, ratio in ratios]
    bounds = [_rounding_bound(float(adjusted[day]), float(raw[day])) for day, _ in ratios]
    decreases = sum(1 for i in range(1, len(values))
                    if values[i] / values[i - 1] - 1 < -(bounds[i] + bounds[i - 1]))
    years = len(values) / TRADING_DAYS
    factor = values[-1] / values[0]
    implied = factor ** (1 / years) - 1
    low, high = (float(_decimal(x, "expected yield")) for x in expected_annual_yield)
    inside = low <= implied <= high
    reasons = []
    if not inside:
        reasons.append(f"the implied annual distribution yield {implied:.4f} is outside the "
                       f"declared range {low:.2f} to {high:.2f}")
    if decreases:
        reasons.append(f"the adjusted-to-raw ratio FELL by more than the rounding of "
                       f"the quotes on {decreases} days, which a distribution adjustment "
                       f"cannot do")
    return {"status": "VOID" if reasons else "OK", "implied_annual_yield": f"{implied:.6f}",
            "expected_annual_yield": [str(low), str(high)], "days": len(values),
            "decreases": decreases, "reason": "; ".join(reasons) or None}


def _bounded_scale(contract_spec, weight, source, recurring="0"):
    """The cost rates scaled with the weight. Return and cost both scale with
    notional, and charging a full position's cost against a fraction of its return
    taxes it 1/w times over -- a mistake this project made once and that read
    exactly like 'the weight made it worse'."""
    return cost_contract(
        regime=COST_REGIME_HOLDING,
        commission_rate=f"{float(_decimal(contract_spec['commission'], 'c')) * weight:.10f}",
        half_spread_rate=f"{float(_decimal(contract_spec['half_spread'], 'h')) * weight:.10f}",
        slippage_rate=f"{float(_decimal(contract_spec['slippage'], 's')) * weight:.10f}",
        # A borrow fee is charged on the notional held, so it scales with the weight like
        # every other rate. A long keeps the literal "0" so its contract is unchanged.
        recurring_rate_per_period=("0" if recurring == "0"
                                   else f"{float(recurring) * weight:.12f}"),
        legs=contract_spec["legs"], source=source)


def _monitor_evidence(spec, monitor, link, net_mean, bounds):
    """The monitor block the admission gate reads, built from what was MEASURED.

    The empirical link is tested here rather than assumed. If it was reachable and
    clears, the form is M1. If it was reachable and FAILED, the identity form is
    closed (M2_AVAILABILITY|04a35abd) whatever the spec offers. If it was
    unreachable, M2 applies only when the spec declares an identity -- and a spec
    that declares none leaves R3 FAILED, because section 11.1 holds that
    INSUFFICIENT_EVIDENCE IS NOT A PASS and a monitor whose link cannot be
    established is not admitted with reservations.
    """
    declared = spec.get("monitor", {})
    met, usable = link
    enough = usable >= LINK_MINIMUM_USABLE_FOLDS
    ratio = (met / usable) if usable else None
    clears = enough and ratio is not None and Decimal(str(ratio)) >= LINK_CONSISTENCY_THRESHOLD
    outcome = (LINK_UNREACHABLE if not enough
               else LINK_REACHABLE_MET if clears else LINK_REACHABLE_FAILED)

    contract = monitoring_contract(
        variable=declared["variable"], observation_period_seconds=86400,
        degradation_threshold=declared["floor"], degrades_when=DEGRADES_BELOW,
        confirmation_periods=int(declared.get("confirmation_periods", 3)),
        action=ACTION_SUSPEND, re_entry_threshold=declared.get("re_entry_threshold", "1.0"),
        source=declared.get("source", "declared in the spec"))
    latency = detection_latency_seconds(contract) / 86400
    evidence = {
        "variable": declared["variable"], "frequency_seconds": 86400,
        "degradation_threshold": declared["floor"], "action": ACTION_SUSPEND,
        "detection_latency_days": f"{latency:.6f}",
        "expected_daily_loss_if_dead": f"{max(net_mean, 0.0):.8f}",
        "is_pnl_only": False,
        "observes_adverse_state_representatively": clears,
        "link_empirical_outcome": outcome,
    }
    identity = declared.get("identity")
    if clears:
        evidence["link_form"] = MONITOR_LINK_M1
        evidence["link_consistency"] = {
            "met": met, "usable_folds": usable, "ratio": f"{ratio:.6f}",
            "threshold": str(LINK_CONSISTENCY_THRESHOLD),
            "minimum_usable_folds": LINK_MINIMUM_USABLE_FOLDS,
            "what_this_is_not": ("a sealed StatisticalValidation: the fold consistency of "
                                 "the link computed in line under the same sign rule, "
                                 "weaker than a sealed validation and recorded as such")}
    elif identity:
        evidence["link_form"] = MONITOR_LINK_M2
        evidence.update({key: identity[key] for key in
                         ("identity", "parameter", "parameter_source", "holds_for_range")})
    else:
        evidence["link_form"] = MONITOR_LINK_M1
    return evidence, outcome, ratio


def judge(spec, rows, *, monitor_inputs=None, raw_closes=None, controls=None,
          code_revision="0" * 40):
    """Judge one held-position Hypothesis. Returns everything, seals nothing.

    `rows` are the sealed dataset's rows (timestamp, close, volume,
    forward_return_1d). `monitor_inputs` is {"series": {id: {date: value}},
    "underlying": {date: close}} for monitors that need them; `raw_closes` is
    {date: close} for the distribution check; `controls` is {name: {date: return}}
    for series the same link is tested on beside the primary.
    """
    validate_spec(spec)
    column = forward_column(spec)
    rows = [row for row in rows if row.get(column)]
    if len(rows) < 2 * int(spec["level_claim"]["folds"]):
        raise ValueError(f"{len(rows)} rows cannot support {spec['level_claim']['folds']} folds")
    dates = [row["timestamp"][:10] for row in rows]
    direction = spec["instrument"].get("direction", "LONG")
    sign = -1.0 if direction == "SHORT" else 1.0
    underlying = [float(row[column]) for row in rows]
    # A short is held at a daily-rebalanced notional, so its daily return is the negative of
    # the instrument's, and anything the instrument pays (a distribution) is a cost to it.
    gross = [sign * value for value in underlying]
    positions = [1] * len(gross)
    years = len(rows) / TRADING_DAYS
    bootstrap = {**DEFAULT_BOOTSTRAP, **spec.get("bootstrap", {})}
    result = {"slug": spec["slug"], "days": len(rows), "years": years, "void": None}

    # --- the data check comes first: a void measurement judges nothing -------------
    for check in spec.get("checks", []):
        if check["kind"] != "distribution_adjustment":
            raise ValueError(f"Unknown check {check['kind']!r}")
        if raw_closes is None:
            result["void"] = ("the spec declares a distribution check and no raw closes "
                              "were supplied, so the adjustment was never examined")
            return result
        adjusted = {day: row["close"] for day, row in zip(dates, rows)}
        result["distribution_check"] = distribution_check(
            dates, adjusted, raw_closes, check["expected_annual_yield"])
        if result["distribution_check"]["status"] == "VOID":
            result["void"] = result["distribution_check"]["reason"]
            return result

    # --- position, cost, and the weight the drawdown limit derives -----------------
    spec_cost = spec["cost"]
    recurring = ("0" if direction != "SHORT" else
                 f"{float(_decimal(spec_cost['borrow_annual'], 'borrow')) / TRADING_DAYS:.12f}")
    if recurring != "0" and float(recurring) == 0.0:
        recurring = "0"
    contract = cost_contract(
        regime=COST_REGIME_HOLDING, commission_rate=spec_cost["commission"],
        half_spread_rate=spec_cost["half_spread"], slippage_rate=spec_cost["slippage"],
        recurring_rate_per_period=recurring, legs=spec_cost["legs"],
        source=spec_cost["source"])
    net = net_returns(contract, positions, gross)
    unsized_net_total = math.fsum(net)
    limit = spec["sizing"]["drawdown_limit"]
    weight = weight_within_drawdown_bound(net, drawdown_limit=limit, **bootstrap)
    if weight < 1.0:
        gross = [value * weight for value in gross]
        contract = _bounded_scale(
            spec_cost, weight,
            f"the declared rates scaled by the {weight:.4f} weight derived from the "
            f"{limit} limit", recurring=recurring)
        net = net_returns(contract, positions, gross)
    gross_total = math.fsum(gross)
    cost_total = float(cost_per_side(contract)) * TRANSITIONS
    mean_net = math.fsum(net) / len(net)

    sharpe_point = sharpe_ratio(net, periods_per_year=TRADING_DAYS)
    sharpe_bound = net_sharpe_lower_bound(net, periods_per_year=TRADING_DAYS, **bootstrap)
    drawdown_point, drawdown_bound = max_drawdown(net), drawdown_upper_bound(net, **bootstrap)
    result.update({
        "weight": weight, "gross_total": gross_total, "cost_total": cost_total,
        "net_total": math.fsum(net), "unsized_net_total": unsized_net_total,
        "mean_net": mean_net, "sharpe_point": sharpe_point, "sharpe_bound": sharpe_bound,
        "drawdown_point": drawdown_point, "drawdown_bound": drawdown_bound,
    })
    if direction == "SHORT":
        # THE PROTECTIVE STOP BOUNDS A SHORT ONLY IF PRICE DOES NOT GAP OVER IT. A day on which
        # the instrument rose by more than the declared adverse move is a day the stop would
        # have filled past its price, so the loss the executor contract budgets would have
        # been exceeded. Reported beside the verdict; the drawdown bound above already
        # includes whatever squeezes the sample holds.
        move = float(_decimal(spec["instrument"]["max_adverse_move"], "move"))
        beyond = sum(1 for value in underlying if value > move)
        result["short"] = {
            "borrow_annual": spec_cost["borrow_annual"], "max_adverse_move": str(move),
            "worst_day_against": max(underlying), "days_beyond_adverse_move": beyond,
            "frequency_beyond_adverse_move": beyond / len(underlying)}

    # --- the monitor: flags, then the link tested fold by fold ---------------------
    bounds = fold_bounds(len(rows), int(spec["level_claim"]["folds"]))
    monitor_spec = spec.get("monitor", {"kind": KIND_NONE})
    inputs = monitor_inputs or {}
    monitor = evaluate_monitor(monitor_spec, dates, inputs.get("series", {}),
                               inputs.get("underlying"))
    link = monitor_evidence = outcome = None
    if monitor is not None:
        link = link_consistency(bounds, net, monitor["flags"])
        monitor_evidence, outcome, ratio = _monitor_evidence(spec, monitor, link,
                                                             mean_net, bounds)
        result["monitor"] = {**{k: v for k, v in monitor.items() if k != "flags"},
                             "link_met": link[0], "link_usable": link[1],
                             "link_ratio": ratio, "link_outcome": outcome,
                             "link_form": monitor_evidence["link_form"]}
        # THE SAME LINK ON A CONTROL. A covered-call fund's monitor can pass for the
        # wrong reason: days when realised volatility catches the implied one are
        # mostly down days for the underlying, which are bad days for ANY equity
        # position. Reported beside the primary and never a gate -- if it holds
        # equally on the control, the monitor detects drawdowns and not this premium.
        for name, series in (controls or {}).items():
            aligned = [None if series.get(day) is None else sign * series.get(day)
                       for day in dates]
            met, usable = link_consistency(bounds, aligned, monitor["flags"])
            result.setdefault("controls", {})[name] = {
                "met": met, "usable": usable,
                "ratio": (met / usable) if usable else None}

    # --- the level claim ----------------------------------------------------------
    spec_claim = spec["level_claim"]
    claim = level_claim(
        position_description=(
            f"{spec['instrument']['symbol']} "
            f"{'held SHORT continuously' if direction == 'SHORT' else 'held continuously'} "
            f"over the window at a {weight:.4f} weight derived from the {limit} limit"),
        cost_contract=contract, minimum_folds_required=int(spec_claim["folds"]),
        consistency_threshold=spec_claim["consistency_threshold"],
        minimum_adverse_episodes=int(spec_claim["minimum_adverse_episodes"]),
        # THE ADVERSE THRESHOLD SCALES WITH THE WEIGHT, for the same reason the cost
        # rates do: -2% was a stress day for the UNSIZED position, and at a 28%
        # weight the identical market event moves it -0.56%. Left absolute it
        # reported a tail that was entirely present as absent.
        adverse_period_threshold=f"{float(_decimal(spec_claim['adverse_threshold'], 'a')) * weight:.10f}",
        maximum_drawdown=limit,
        source=f"declared in {spec['hypothesis_id'][:30]} before any bar was captured")
    folds = [evaluate_fold(
        claim, contract, fold_index=index,
        period={"start_utc": rows[lo]["timestamp"],
                "end_exclusive_utc": rows[hi - 1]["timestamp"]},
        positions=positions[lo:hi], gross_returns=gross[lo:hi])
        for index, (lo, hi) in enumerate(bounds)]
    verdict, reason, consistency, adverse = level_claim_outcome(claim, folds)
    result["level_claim"] = {"claim": claim, "folds": folds, "outcome": verdict,
                             "reason": reason, "consistency": consistency,
                             "adverse_frequency": adverse}
    if verdict != "VALIDATED":
        return result

    # --- capacity, then the evidence the admission gates read ----------------------
    volume = math.fsum(float(r["close"]) * float(r["volume"]) for r in rows) / len(rows)
    volatility = (math.fsum((v - mean_net) ** 2 for v in net) / (len(net) - 1)) ** 0.5
    tranche = str(_decimal(spec["decision"]["tranche"], "tranche").normalize())
    try:
        ceiling = capacity_ceiling(
            capacity_contract(average_daily_volume=f"{volume:.2f}", impact_coefficient="0.1",
                              degradation_tolerance="0.20",
                              source="ADV MEASURED from the sealed dataset; impact "
                                     "coefficient 0.1 DECLARED, never calibrated"),
            gross_per_period=gross_total / len(gross),
            base_cost_per_period=cost_total / len(rows), volatility_per_period=volatility,
            turnover_per_period=TRANSITIONS / len(rows), reference_capital=CAPACITY_REFERENCE,
            periods_per_year=TRADING_DAYS, search_multiple=CAPACITY_SEARCH_MULTIPLE)
        if ceiling["ceiling"] is not None:
            capacity = {"ceiling": f"{ceiling['ceiling']:.2f}", "current_tranche": tranche}
        else:
            floor = CAPACITY_REFERENCE * CAPACITY_SEARCH_MULTIPLE
            capacity = {"ceiling": f"{floor:.2f}", "current_tranche": tranche,
                        "is_lower_bound_not_the_ceiling": True,
                        "source": (f"NONE_FOUND: nothing binds below ${floor:,.0f}, so this "
                                   f"is a FLOOR on the ceiling and never an estimate of it")}
    except ValueError as error:
        capacity = {"current_tranche": tranche, "model_refusal": str(error)}

    survival = spec["survival"]
    decision = spec["decision"]
    evidence = {
        "net_sharpe": adverse_bound(
            f"{sharpe_bound:.6f}", is_adverse_bound=True, point_estimate=f"{sharpe_point:.6f}",
            source=f"moving-block bootstrap, {_canonical(bootstrap)}, on {len(net)} net days "
                   f"of {spec['slug']} at a {weight:.4f} weight"),
        "worst_fold_drawdown": adverse_bound(
            f"{drawdown_bound:.6f}", is_adverse_bound=True,
            source="moving-block bootstrap, same parameters, on the WHOLE net series, which "
                   "dominates any fold and is therefore the stricter reading of R1"),
        "survival": {"form": SURVIVAL_P2, "counterparty": survival["counterparty"],
                     "why_they_accept_losing": survival["why_they_accept_losing"],
                     "what_would_end_it": survival["what_would_end_it"]},
        "capacity": capacity,
        "decision_cost": {
            "build_cost": decision["build_cost"],
            "minimum_declared_life_years": decision["life_years"],
            "recurring_cost_per_year": "0",
            "expected_annual_net_return":
                f"{mean_net * TRADING_DAYS * float(_decimal(decision['tranche'], 't')):.2f}",
            "surveillance_is_automatable": True},
    }
    if monitor_evidence is not None:
        evidence["monitor"] = monitor_evidence
    result["evidence"] = evidence
    result["gates"] = evaluate_admission_gates(evidence)
    return result


def _canonical(mapping):
    import json
    return json.dumps(mapping, sort_keys=True)
