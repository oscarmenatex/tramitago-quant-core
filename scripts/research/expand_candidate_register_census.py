"""Expand the candidate register with the classes the instrument census found, and correct what is stale.

    python3.11 -B scripts/research/expand_candidate_register_census.py            # dry run: prints, seals nothing
    python3.11 -B scripts/research/expand_candidate_register_census.py --seal     # seals the new register

WHY. The census showed that supply of wrappers is not the limit of option A: nine unmeasured classes
are family-sized. What decided the blocker was never supply but two other things, whether a class pays
a net Sharpe of 0.5 and whether anything watchable leads its adverse returns. The register answers
the first from RECALLED ranges, with no data read, and refuses what cannot reach its own bar.

TWO PARTS, and the second matters more than it looks.

  CORRECTIONS. The register still carried XYLD and QYLD as UNTRIED with a TESTABLE monitor, and DIVO
  likewise. XYLD and QYLD have since been measured and denied on R3 alone, with links of 4 of 6 and 3
  of 7, and the joint family verdict is DOES_NOT_GENERALIZE. A register that still says UNTRIED
  recommends what the platform has already refuted, which is the failure it was built to stop.

  ADDITIONS. One entry for each census class that is unmeasured, is not equity beta and has a
  payer. Facts about each instrument (first bar, liquidity, shortability) come from the SEALED census.

WHAT IS RECALLED, AND SAYS SO. Every effect range below is recalled, wide and weaker than the ranges
that were later measured here. It is an input to a feasibility test and never evidence. Where this
project has a measured sibling, the range is anchored on it and the entry says so.

THE DEFAULT IS A DRY RUN. The ranges are estimates by the assistant and the Director has not reviewed
them; sealing is an append-only act under his name, so it needs his deliberate flag.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tramitago_quant_core.governance.evidence_host import require_evidence_host
from tramitago_quant_core.research.candidate_register import (
    candidate, monitor_status, constitute_candidate_register, rank_candidates, what_would_unlock,
    admissible_in_principle, candidate_verdict, STATUS_UNTRIED, STATUS_MEASURED_DEAD,
    MONITOR_UNEXAMINED, MONITOR_LINK_REFUTED, CERTAINTY_MEASURED, CERTAINTY_INFERRED,
    EXECUTOR_CAN_OPEN_SHORTS)
from tramitago_quant_core.research.pre_declaration import CLAIM_PREMIUM

ARTIFACTS = REPO / "artifacts" / "research"
REGISTER = ARTIFACTS / "candidate-registers.json"
CENSUS = ARTIFACTS / "instrument-census.json"
CENSUS_DATE = "2026-10-01"

JUSTIFICATION = (
    "The register of 2026-10-03, corrected for what has been measured since and expanded with the classes "
    "the instrument census of 2026-10-03 found. CORRECTED: XYLD and QYLD are MEASURED_DEAD (denied on R3 "
    "alone, links of 4 of 6 and 3 of 7) and DIVO now carries that refutation as inferred, because the joint "
    "family verdict of their mechanism is DOES_NOT_GENERALIZE. ADDED: one entry for each census class that is "
    "unmeasured, is not equity beta and has a payer, with the facts about its instruments taken from the "
    "sealed census. LEFT OUT, each for a stated reason: precious metals (gold and silver pay no carry, so "
    "there is no payer and the destination of risk premia does not apply), crypto (one instrument with a "
    "long enough history and spot funds that pay nothing), real estate, dividend, low-volatility and other "
    "factor funds (equity beta or mispricing under another label), and the classes already in the register "
    "(currency carry, term premium, commodity roll, covered calls, put-write, volatility, investment-grade "
    "credit). EVERY EFFECT RANGE IS RECALLED, wide and weak, and anchored on a measured sibling where one "
    "exists. EVERY PAIR EXPRESSION REQUIRES A SHORT, which the operating chain can open only in PAPER, so a "
    "pair is blocked for real capital until the Director decides otherwise.")

RECALLED = ("RECALLED, wide and weaker than any range measured on this platform; an input to a "
            "feasibility test and never evidence")


def _census_rows():
    census = json.loads(CENSUS.read_bytes())["censuses"][-1]
    return {row["symbol"]: row for row in census["rows"]}, census["census_id"]


def _years(first_bar):
    return round((date.fromisoformat(CENSUS_DATE) - date.fromisoformat(first_bar)).days / 365.25, 2)


def _facts(rows, *symbols):
    """Years of history, and a sentence of measured facts, from the SEALED census."""
    missing = [s for s in symbols if s not in rows]
    if missing:
        raise SystemExit(f"Not in the sealed census: {', '.join(missing)}")
    years = min(_years(rows[s]["first_bar"]) for s in symbols)
    text = "; ".join(f"{s} first monthly bar {rows[s]['first_bar']}, "
                     f"{'liquid' if rows[s]['liquid'] else 'NOT liquid'} by the census floor, "
                     f"{'shortable' if rows[s]['shortable'] else 'not shortable'}" for s in symbols)
    return str(years), f"Alpaca SIP, MEASURED in the instrument census of 2026-10-03: {text}"


def revised(entry, **changes):
    """Rebuild an existing entry through candidate() so a correction is validated like a new entry."""
    fields = {"name": entry["name"], "claim_class": entry["claim_class"], "payer": entry["payer"],
              "effect_low": entry["effect"]["low"], "effect_high": entry["effect"]["high"],
              "effect_source": entry["effect"]["source"], "available_years": entry["available_years"],
              "data_source": entry["data_source"], "status": entry["status"],
              "requires_short": entry["requires_short"], "monitor": entry["monitor"],
              "reachable_today": entry["reachable_today"]}
    for key in ("unreachable_reason", "minimum_position_usd"):
        if key in entry:
            fields[key] = entry[key]
    fields.update(changes)
    return candidate(**fields)


def corrections(previous):
    """XYLD, QYLD and DIVO, brought up to what has been measured."""
    out = []
    for entry in previous:
        name = entry["name"]
        if "via XYLD" in name:
            out.append(revised(entry, status=STATUS_MEASURED_DEAD, monitor=monitor_status(
                variable=entry["monitor"]["variable"], status=MONITOR_LINK_REFUTED,
                certainty=CERTAINTY_MEASURED,
                evidence="MEASURED 2026-10-03: the empirical link held in 4 of 6 usable folds, 0.667 "
                         "against a threshold of 0.70, and the SPY control scored 3 of 6. Admission "
                         "denied on R3 alone, ADMISSION|81bdb90c; measured at a net Sharpe of 0.660 "
                         "with a lower bound of +0.200, so it paid and could not be watched")))
        elif "via QYLD" in name:
            out.append(revised(entry, status=STATUS_MEASURED_DEAD, monitor=monitor_status(
                variable="VXN minus the 21-day realised volatility of the Nasdaq-100",
                status=MONITOR_LINK_REFUTED, certainty=CERTAINTY_MEASURED,
                evidence="MEASURED 2026-10-03: the empirical link held in 3 of 7 usable folds, 0.429, "
                         "BELOW its own QQQ control of 4 of 7. Admission denied on R3 alone; net Sharpe "
                         "0.677 with a lower bound of +0.222. Measured as diluted beta: beta 0.612 on "
                         "QQQ with a negative alpha, and a Sharpe below the index it holds")))
        elif "via DIVO" in name:
            out.append(revised(entry, monitor=monitor_status(
                variable=entry["monitor"]["variable"], status=MONITOR_LINK_REFUTED,
                certainty=CERTAINTY_INFERRED,
                measured_on="XYLD and QYLD, the two covered-call funds of the same mechanism",
                evidence="the joint family verdict of the covered-call mechanism is DOES_NOT_GENERALIZE: "
                         "the same monitor failed its link on both siblings. DIVO writes options on single "
                         "stocks, so this weighs against it and does not eliminate it")))
        else:
            out.append(entry)
    return out


def additions(rows):
    pair_note = ("a PAIR: long the credit fund against the ten-year Treasury fund at equal notional, as "
                 "the investment-grade pair already measured here, which requires a short")
    out = []

    years, data = _facts(rows, "HYG", "IEF")
    out.append(candidate(
        name="high-yield credit premium, via HYG against IEF", claim_class=CLAIM_PREMIUM,
        payer="corporate borrowers with weaker credit, who pay a spread above Treasuries for the money",
        effect_low="0.15", effect_high="0.50",
        effect_source=f"{RECALLED}. Anchored on the investment-grade pair MEASURED on this platform at a net "
                      f"Sharpe of 0.19 over 2018 to 2026: high yield pays a larger spread but carries a "
                      f"larger equity beta, so the range is widened upward from that point and stays weak. "
                      f"The expression is {pair_note}",
        available_years=years, data_source=data, status=STATUS_UNTRIED, requires_short=True,
        monitor=monitor_status(
            variable="Moody's Baa yield relative to the 10-year Treasury, BAA10Y, published daily",
            status=MONITOR_LINK_REFUTED, certainty=CERTAINTY_INFERRED,
            measured_on="the investment-grade credit pair LQD against IEF, in the monitor screen sealed 2026-10-03",
            evidence="the screen measured BAA10Y states against the investment-grade pair: the 21-day "
                     "widening met the link in 2 of 7 folds (C08) and the level above its median in 1 of 6 "
                     "(C09), both refuted and the second with the sign opposite to the declared one. High "
                     "yield is a sibling exposure, so this weighs against it and does not eliminate it. "
                     "The ICE high-yield spread itself holds about three years on FRED")))

    years, data = _facts(rows, "EMB", "IEF")
    out.append(candidate(
        name="emerging-market sovereign debt premium, via EMB against IEF", claim_class=CLAIM_PREMIUM,
        payer="emerging-market governments, who pay a spread above Treasuries to borrow in dollars",
        effect_low="0.10", effect_high="0.40",
        effect_source=f"{RECALLED}. Sovereign spread premia are documented to be positive and unstable, with "
                      f"losses concentrated in crises. The expression is {pair_note}",
        available_years=years, data_source=data, status=STATUS_UNTRIED, requires_short=True,
        monitor=monitor_status(
            variable="an emerging-market sovereign spread",
            status=MONITOR_UNEXAMINED,
            evidence="no daily public series of an emerging-market sovereign spread was identified for this "
                     "project: a gap in the search and not a finding about the premium")))

    years, data = _facts(rows, "MBB", "IEF")
    out.append(candidate(
        name="mortgage premium, via MBB against IEF", claim_class=CLAIM_PREMIUM,
        payer="homeowners, whose option to prepay investors are paid to bear",
        effect_low="0.00", effect_high="0.30",
        effect_source=f"{RECALLED}. The excess return of agency mortgage securities over Treasuries is small "
                      f"and mostly compensation for the prepayment option. The expression is {pair_note}",
        available_years=years, data_source=data, status=STATUS_UNTRIED, requires_short=True,
        monitor=monitor_status(
            variable="the 30-year mortgage rate minus the 10-year Treasury yield",
            status=MONITOR_UNEXAMINED,
            evidence="the mortgage rate is published WEEKLY, so a decision latency of days is built in, and "
                     "whether the spread leads the pair's returns has not been tested")))

    years, data = _facts(rows, "MUB", "IEF")
    out.append(candidate(
        name="municipal bond premium, via MUB against IEF", claim_class=CLAIM_PREMIUM,
        payer="municipal issuers and the tax code, which pay investors through a tax exemption",
        effect_low="0.00", effect_high="0.35",
        effect_source=f"{RECALLED}. Before tax, the excess of municipal over Treasury bonds is near zero; the "
                      f"benefit is the exemption, which a tax-free account cannot use and which the "
                      f"platform's pre-tax returns do not count. The expression is {pair_note}",
        available_years=years, data_source=data, status=STATUS_UNTRIED, requires_short=True,
        monitor=monitor_status(
            variable="the ratio of municipal to Treasury yields",
            status=MONITOR_UNEXAMINED,
            evidence="no daily public series of that ratio was identified: a gap in the search and not a "
                     "finding about the premium")))

    years, data = _facts(rows, "PFF")
    out.append(candidate(
        name="preferred securities premium, via PFF held long", claim_class=CLAIM_PREMIUM,
        payer="banks and other issuers, who pay for loss-absorbing capital that ranks below debt and above "
              "common stock",
        effect_low="0.10", effect_high="0.45",
        effect_source=f"{RECALLED}. Preferred funds carry interest-rate duration and a large equity beta, so "
                      f"most of what they earn is exposure already measured under other names, and 2022 was "
                      f"a heavy loss for rates and for financials together",
        available_years=years, data_source=data, status=STATUS_UNTRIED, requires_short=False,
        monitor=monitor_status(
            variable="no variable was identified that is not the fund's own return",
            status=MONITOR_UNEXAMINED,
            evidence="a gap in the search and not a finding; if none exists the candidate falls under the same "
                     "no-monitor ruling as the equity premium")))

    years, data = _facts(rows, "TIP", "IEF")
    out.append(candidate(
        name="inflation risk premium, via TIP against IEF", claim_class=CLAIM_PREMIUM,
        payer="investors who pay for protection against inflation, through a lower yield than a nominal bond",
        effect_low="0.00", effect_high="0.30",
        effect_source=f"{RECALLED}. The inflation risk premium in breakevens is small and changes sign, and "
                      f"2021 to 2022 rewarded the protection heavily, so a window containing it is "
                      f"flattering. The expression is {pair_note}",
        available_years=years, data_source=data, status=STATUS_UNTRIED, requires_short=True,
        monitor=monitor_status(
            variable="the 10-year breakeven inflation rate, T10YIE, published daily on FRED",
            status=MONITOR_UNEXAMINED,
            evidence="published daily with a long history, but its trigger frequency over this window has not "
                     "been measured and whether its changes lead the pair's returns has not been tested")))

    years, data = _facts(rows, "UNG", "BNO")
    out.append(candidate(
        name="energy futures roll premium, via energy futures funds held SHORT (UNG, BNO)",
        claim_class=CLAIM_PREMIUM,
        payer="holders of long energy futures funds, who pay the roll cost when the curve is in contango",
        effect_low="0.20", effect_high="0.90",
        effect_source=f"{RECALLED}, and the widest of all because the sign is well documented and the tail is "
                      f"the whole story: a short in a contango fund harvests the roll and loses sharply in "
                      f"a squeeze. Needs the short-simple measurement the engine now supports, with a "
                      f"declared borrow cost and adverse move",
        available_years=years, data_source=data, status=STATUS_UNTRIED, requires_short=True,
        monitor=monitor_status(
            variable="the shape of the futures curve, contango against backwardation",
            status=MONITOR_UNEXAMINED,
            evidence="no daily public series of the curve shape was identified on FRED: a gap in the search "
                     "and not a finding about the premium")))
    return out


def build(previous, rows):
    return corrections(previous) + additions(rows)


def _latest_schema_2():
    registers = json.loads(REGISTER.read_bytes())["registers"]
    return [r for r in registers if r["schema_version"] == "2"][-1]["candidates"]


def _blockers(item):
    parts = []
    if any(b["kind"] == "REQUIRES_SHORT" for b in item["hard_blockers"]):
        parts.append("SHORT")
    if any(b["kind"] in ("MONITOR_REFUTED", "NO_MONITOR_POSSIBLE") for b in item["hard_blockers"]):
        parts.append("R3!")
    if item["probable_blockers"]:
        parts.append("r3?")
    return " ".join(parts) or "-"


def main(argv=None):
    require_evidence_host(REPO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--seal", action="store_true", help="seal the new register (append-only)")
    args = parser.parse_args(argv)
    rows, census_id = _census_rows()
    previous = _latest_schema_2()
    candidates = build(previous, rows)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True,
                              check=True).stdout.strip()
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    path = REGISTER if args.seal else Path(tempfile.mkdtemp()) / "dry-run-register.json"
    record = constitute_candidate_register(
        path, justification=JUSTIFICATION, candidates=candidates,
        declared_by=f"Oscar Carmenate Rodriguez, at {revision[:12]}", declared_at=now)
    added = {c["name"] for c in additions(rows)}
    print("=" * 112)
    print(f"{'REGISTER SEALED' if args.seal else 'DRY RUN -- nothing sealed'}   {len(previous)} existing + "
          f"{len(added)} added = {len(record['candidates'])}   census {census_id[:40]}")
    print(f"executor can open shorts with real capital: {EXECUTOR_CAN_OPEN_SHORTS}")
    print("=" * 112)
    print(f"{'':2} {'candidate':<58} {'range':>9} {'bar':>6} {'yrs':>6}  {'verdict':<17} blockers")
    for item in rank_candidates(record):
        mark = {"MEASURED_DEAD": "x", "DECLARED": ">", "UNTRIED": " "}[item["status"]]
        new = "+" if item["name"] in added else " "
        span = f"{item['effect']['low']}-{item['effect']['high']}"
        print(f"{mark}{new} {item['name'][:58]:<58} {span:>9} {item['required']:>6} {item['available_years']:>6}  "
              f"{item['verdict']:<17} {_blockers(item)}")
    print("\n  x measured and dead   + added   SHORT needs a short   R3! monitor refuted   r3? monitor probably refuted")
    scored = [{**entry, **candidate_verdict(entry)} for entry in record["candidates"]]
    admissible = [item["name"] for item in scored if admissible_in_principle(item)]
    print(f"\n  ADMISSIBLE IN PRINCIPLE: {len(admissible)} of {len(scored)}")
    for name in admissible:
        print(f"    {name}")
    print("\n  WHAT EACH SINGLE CHANGE WOULD STILL UNLOCK:")
    for kind, names in what_would_unlock(record).items():
        print(f"    remove {kind}: " + (", ".join(e["name"][:40] for e in names) or "nobody"))
    print(f"\n  {record['register_id'][:62]}")
    if not args.seal:
        print("  (dry run: run again with --seal to append this register)")
    print("=" * 112)
    return 0


if __name__ == "__main__":
    sys.exit(main())
