"""A census of what could be traded, by mechanism class. METADATA ONLY: no return is read or computed.

THE QUESTION IT ANSWERS. Option A of the blocker analysis is to widen the supply of candidate
premia. Before building a searcher, this counts what supply there is: how many instruments exist,
by economic class, that are tradable, shortable, old enough to have a history worth measuring and
liquid enough to hold. If a class holds one or two instruments, the supply is the limit and a
searcher would only multiply the same few.

WHAT IT READS about each instrument: its name, exchange, whether it is tradable and shortable, the
DATE of its first monthly bar, and the dollar volume traded in recent months. It reads no return and
computes none. The first bar and the volume come from bars the broker serves, and only a date and
a product of two quantities leave this module; a price level is never kept.

THE DEFINITION IS FIXED BEFORE ANY COUNT IS SEEN. The classes, their keywords, the history tiers and
the liquidity floor are constants here, and the census record carries their digest, so a class
cannot be widened or a tier moved after a result disappoints. A change is a new definition and
a new census.

IT IS A HEURISTIC, AND SAYS SO. Classes are assigned from the NAME of an instrument, because the
broker serves no category. A name can mislead in both directions: a fund that holds a class without
saying so is missed, and an ordinary company whose name contains a keyword and a fund word is
counted. The counts are therefore approximate and are meant to tell 3 from 300, not 12 from 14.
Any class that looks promising has to be verified instrument by instrument before anything is
declared on it.

IT CANNOT COUNT IDEAS. Two instruments in one class may be the same bet in two wrappers, as XYLD,
QYLD and SPY were. Telling them apart takes returns, which this census does not read, so a class
count is an UPPER BOUND on the number of independent premia in it.
"""

import re
from collections import defaultdict
from datetime import date

from tramitago_quant_core.shared.util import digest, encoded

SCHEMA_VERSION = "1"
DEFINITION_VERSION = "2"

# History tiers, by the date of the first monthly bar. A reaches back as far as the monitor screen
# window, which is the longest window the platform has used; B is five years.
TIER_A_ON_OR_BEFORE = "2018-03-01"
TIER_B_ON_OR_BEFORE = "2021-10-01"

# A monthly dollar volume of 20 million is about a million dollars a day, a thousand times the
# first tranche. It is a floor against instruments that could not be traded, not a capacity model.
LIQUIDITY_FLOOR_MONTHLY_DOLLAR_VOLUME = 20_000_000
LIQUIDITY_MONTHS = 4

EXCHANGES = ("NYSE", "NASDAQ", "ARCA", "AMEX", "BATS")

# DEFINITION VERSION 2. The first version also accepted "shares", "trust", "portfolio", "index" and
# "futures" as marks of a fund. A trial of 30 names showed the cost: preferred stock of single
# companies ("Depositary Shares ... Series D Cumulative Redeemable Preferred Stock") and a mining
# company ("Alamos Gold Inc. Class A Common Shares") passed as funds. Those words name a SECURITY as
# often as a fund, so they are no longer a mark; issuers of funds and the words that only funds
# carry (ETF, ETN, fund) are. The change only REMOVES false positives, was made after a trial of
# 30 names and before any census was sealed or any class count was read, and the digest changes
# with it, so the first definition cannot be mistaken for this one.
FUND_MARKERS = (
    r"etf", r"etn", r"fund", r"etfs",
    r"ishares", r"spdr", r"vanguard", r"invesco", r"proshares", r"global x", r"wisdomtree",
    r"vaneck", r"direxion", r"first trust", r"schwab", r"pimco", r"ark ", r"graniteshares",
    r"amplify", r"roundhill", r"simplify", r"jpmorgan", r"fidelity", r"flexshares",
    r"grayscale", r"bitwise", r"sprott", r"abrdn",
)

# class -> (keywords that put an instrument in it, keywords that take it out again).
CLASSES = {
    "COVERED_CALL": (("covered call", "buywrite", "buy-write", "buy write", "option income",
                      "premium income"), ()),
    "PUT_WRITE": (("put write", "putwrite", "cash-secured put"), ()),
    "VOLATILITY": (("vix", "volatility"), ("low volatility", "minimum volatility", "min vol",
                                           "managed volatility")),
    "TREASURY_DURATION": (("treasury", "t-bill", "t-bond", "government bond"), ("inflation",)),
    "INFLATION_LINKED": (("tips", "inflation-protected", "inflation protected", "inflation"), ()),
    "INVESTMENT_GRADE_CREDIT": (("investment grade", "investment-grade", "corporate bond",
                                 "corp bond"), ()),
    "HIGH_YIELD_CREDIT": (("high yield", "high-yield", "junk"), ("dividend",)),
    "EMERGING_DEBT": (("emerging markets bond", "emerging market bond", "emerging markets debt",
                       "emerging markets local"), ()),
    "MUNICIPAL": (("municipal", "muni"), ()),
    "MORTGAGE": (("mortgage", "mbs"), ()),
    "PREFERRED": (("preferred",), ("series", "cumulative", "depositary", "redeemable",
                                   "perpetual", "fixed rate", "floating rate")),
    "CURRENCY": (("currencyshares", "currency", "euro trust", "japanese yen", "swiss franc",
                  "british pound", "canadian dollar", "australian dollar", "dollar index",
                  "bullish dollar", "bearish dollar"), ()),
    "COMMODITY_BROAD": (("commodity", "commodities"), ()),
    "PRECIOUS_METALS": (("gold", "silver", "platinum", "palladium"), ("miners", "mining")),
    "ENERGY_FUTURES": (("crude oil", "natural gas", "brent"), ()),
    "REAL_ESTATE": (("real estate", "reit"), ()),
    "DIVIDEND_FACTOR": (("dividend",), ()),
    "LOW_VOLATILITY_FACTOR": (("low volatility", "minimum volatility", "min vol"), ()),
    "OTHER_FACTOR": (("momentum", "quality", "value factor", "multifactor", "size factor"), ()),
    "CRYPTO": (("bitcoin", "ethereum"), ()),
}

# Instruments whose daily return is a multiple of another or its inverse are a different object,
# and are flagged apart so a class count can be read with and without them.
LEVERAGED_OR_INVERSE = (r"inverse", r"\bbear\b", r"ultra", r"\b2x\b", r"\b3x\b", r"\b-1x\b",
                        r"leveraged", r"\bshort\b(?!-term)")


def definition():
    return {"schema_version": SCHEMA_VERSION, "definition_version": DEFINITION_VERSION,
            "tiers": {"A": TIER_A_ON_OR_BEFORE,
                                                       "B": TIER_B_ON_OR_BEFORE},
            "liquidity_floor_monthly_dollar_volume": LIQUIDITY_FLOOR_MONTHLY_DOLLAR_VOLUME,
            "liquidity_months": LIQUIDITY_MONTHS, "exchanges": list(EXCHANGES),
            "fund_markers": list(FUND_MARKERS),
            "classes": {k: [list(v[0]), list(v[1])] for k, v in sorted(CLASSES.items())},
            "leveraged_or_inverse": list(LEVERAGED_OR_INVERSE)}


def definition_digest():
    return digest(encoded(definition()))


def _contains(text, phrase):
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text) is not None


def is_fund_like(name):
    lowered = (name or "").lower()
    return any(re.search(r"(?<![a-z0-9])" + marker + r"(?![a-z0-9])", lowered)
               for marker in FUND_MARKERS)


def is_leveraged_or_inverse(name):
    lowered = (name or "").lower()
    return any(re.search(pattern, lowered) for pattern in LEVERAGED_OR_INVERSE)


def classify(name):
    """The mechanism classes a fund-like name falls into. An ordinary company falls into none."""
    if not is_fund_like(name):
        return []
    lowered = (name or "").lower()
    tags = []
    for label, (includes, excludes) in sorted(CLASSES.items()):
        if any(_contains(lowered, word) for word in includes) \
                and not any(_contains(lowered, word) for word in excludes):
            tags.append(label)
    return tags


def candidates(assets):
    """The instruments worth probing: active, tradable, on a real exchange, fund-like, tagged."""
    rows = []
    for asset in assets:
        if asset.get("status") != "active" or asset.get("tradable") is not True \
                or asset.get("exchange") not in EXCHANGES:
            continue
        tags = classify(asset.get("name"))
        if not tags:
            continue
        rows.append({"symbol": asset["symbol"], "name": asset["name"], "exchange": asset["exchange"],
                     "classes": tags, "shortable": asset.get("shortable") is True,
                     "easy_to_borrow": asset.get("easy_to_borrow") is True,
                     "fractionable": asset.get("fractionable") is True,
                     "leveraged_or_inverse": is_leveraged_or_inverse(asset["name"])})
    return sorted(rows, key=lambda row: row["symbol"])


def history_tier(first_bar_date):
    if first_bar_date is None:
        return None
    if first_bar_date <= TIER_A_ON_OR_BEFORE:
        return "A"
    return "B" if first_bar_date <= TIER_B_ON_OR_BEFORE else "C"


def monthly_dollar_volume(bars):
    """Mean dollar volume over the complete months supplied, and nothing else about the bars.

    The last bar is dropped: it is the month in progress. Only the product of two quantities is
    kept, so no price level leaves this function and no return can be rebuilt from its result.
    """
    complete = bars[:-1] if len(bars) > 1 else []
    complete = complete[-LIQUIDITY_MONTHS:]
    if not complete:
        return None
    return sum(float(bar["v"]) * float(bar["c"]) for bar in complete) / len(complete)


def facts_from_bars(first_bars, recent_bars):
    """What the census keeps about one instrument: a date and a dollar volume."""
    first = first_bars[0]["t"][:10] if first_bars else None
    volume = monthly_dollar_volume(recent_bars)
    return {"first_bar": first, "tier": history_tier(first),
            "monthly_dollar_volume": None if volume is None else round(volume),
            "liquid": bool(volume is not None and volume >= LIQUIDITY_FLOOR_MONTHLY_DOLLAR_VOLUME)}


def census_rows(candidate_rows, facts_by_symbol):
    out = []
    for row in candidate_rows:
        facts = facts_by_symbol.get(row["symbol"], {})
        out.append({**row, **{k: facts.get(k) for k in ("first_bar", "tier",
                                                         "monthly_dollar_volume", "liquid")},
                    "error": facts.get("error")})
    return out


def summarise(rows):
    """Counts by class, with and without leveraged and inverse products."""
    summary = {}
    by_class = defaultdict(list)
    for row in rows:
        for label in row["classes"]:
            by_class[label].append(row)
    for label in sorted(CLASSES):
        group = by_class.get(label, [])
        plain = [r for r in group if not r["leveraged_or_inverse"]]
        usable = [r for r in plain if r.get("tier") == "A" and r.get("liquid")]
        summary[label] = {
            "named": len(group), "plain": len(plain),
            "plain_tier_a_liquid": len(usable),
            "plain_tier_a_or_b_liquid": sum(1 for r in plain if r.get("tier") in ("A", "B") and r.get("liquid")),
            "plain_tier_a_liquid_shortable": sum(1 for r in usable if r["shortable"]),
            "examples": [r["symbol"] for r in sorted(usable, key=lambda r: -(r["monthly_dollar_volume"] or 0))[:6]],
            "errors": sum(1 for r in group if r.get("error"))}
    return summary


def census_record(rows, summary, *, asof, assets_total, fund_like_total):
    """The record, content addressed. The same data on the same day is the same census."""
    date.fromisoformat(asof)
    content = {"schema_version": SCHEMA_VERSION, "kind": "instrument-census", "asof": asof,
               "definition_digest": definition_digest(), "definition": definition(),
               "assets_active_tradable_total": assets_total, "fund_like_total": fund_like_total,
               "probed": len(rows), "summary": summary, "rows": rows,
               "what_it_cannot_say": ("An upper bound on independent premia: instruments in a "
                                      "class may be one bet in several wrappers, which only "
                                      "returns could show and this census reads none.")}
    return {**content, "census_id": "INSTRUMENT_CENSUS|" + digest(encoded(content))}


# --- running it: the network is injected, so all of this is testable with a dictionary -----------

HISTORY_START = "2015-12-01"
RECENT_LOOKBACK_DAYS = 150


def probe_symbol(symbol, fetch_bars, asof):
    """The two requests that give a date and a dollar volume. `fetch_bars(symbol, start, limit)`
    returns the broker bars as a list of dicts and is the only thing that touches the network."""
    from datetime import timedelta
    first = fetch_bars(symbol, HISTORY_START, 1)
    recent_start = (date.fromisoformat(asof) - timedelta(days=RECENT_LOOKBACK_DAYS)).isoformat()
    recent = fetch_bars(symbol, recent_start, 10)
    return facts_from_bars(first, recent)


def run_census(assets, fetch_bars, *, asof, cache=None, limit=None, on_progress=None):
    """Probe every candidate, resuming from a cache. A symbol that fails is recorded as an error
    of that kind and the census goes on: one delisted fund must not lose an hour of requests.

    Returns (rows, facts). `facts` is the very dict passed as `cache`, updated in place so a caller
    can persist it while the run is going; errors are retried on the next run.
    """
    rows = candidates(assets)
    if limit is not None:
        rows = rows[:limit]
    facts = cache if cache is not None else {}        # updated IN PLACE, so a caller can persist it
    for index, row in enumerate(rows):
        known = facts.get(row["symbol"])
        if known is not None and "error" not in known:
            continue
        try:
            facts[row["symbol"]] = probe_symbol(row["symbol"], fetch_bars, asof)
        except Exception as error:
            facts[row["symbol"]] = {"error": type(error).__name__}
        if on_progress is not None:
            on_progress(index + 1, len(rows), row["symbol"])
    return census_rows(rows, facts), facts


# --- the reading rule, declared in docs/censo-instrumentos.md BEFORE any count was seen ------------

READING_FAMILY_SIZED, READING_THIN, READING_SINGLE = "FAMILY_SIZED", "THIN", "SINGLE_INSTRUMENT"
FAMILY_SIZED_AT_LEAST = 5
THIN_AT_LEAST = 2


def reading(plain_tier_a_liquid):
    """What a count of plain, tier A, liquid instruments means. Five or more can be a family; two
    to four is thin; one or none cannot be a family, because the joint rule needs two members."""
    if plain_tier_a_liquid >= FAMILY_SIZED_AT_LEAST:
        return READING_FAMILY_SIZED
    return READING_THIN if plain_tier_a_liquid >= THIN_AT_LEAST else READING_SINGLE
