"""What crossing both legs of a carry costs, measured on live books.

THE QUESTION, in the terms the verdict left it in: the gated carry needs 64 round
trips a year on two legs, and it came back NOT_VALIDATED at a breakeven of
0.000494 per leg per side against 0.000650 ASSUMED. A fill 0.000156 cheaper flips
it. Nobody had measured any.

This measures the two components that are readable without trading: the spread
each leg quotes, and what crossing it for a real size actually costs by walking
real depth. Both books are public, so this needs no credentials and no wallet.

WHAT IT STILL CANNOT TELL YOU, and it is the whole remaining gap: the COMMISSION.
A venue's fee is a published number that this cannot read, and it is the
component with the most room in it -- the measured spread turns out to be a small
fraction of the budget, so what remains is almost entirely a fee question. The
script therefore reports the commission BUDGET left after the measured spread,
which is the number to check a fee schedule against.

NOT TESTNET. Hyperliquid's testnet quotes a tighter spread over ninety times less
depth, so a cost derived from it is better than reality. The capture module
refuses it outright.

    python3.11 -B scripts/operations/measure_carry_legs.py --notional 5000
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pipeline as p
from tramitago_quant_core.data.order_book import (
    capture_order_book, verified_order_book_capture, walk_book, mid_price, half_spread_rate,
    VENUE_HYPERLIQUID_PERPETUAL, VENUE_COINBASE_SPOT, SIDE_BUY, SIDE_SELL,
)
from tramitago_quant_core.risk.execution_cost import (
    measure_execution, append_execution_measurement, FILL_BOOK,
)

# A carry is long spot and short perpetual, so the legs cross in OPPOSITE
# directions: you lift the spot offer and hit the perpetual bid.
LEGS = [
    {"venue": VENUE_COINBASE_SPOT, "instrument": "BTC-USD", "side": SIDE_BUY,
     "label": "spot  (long)"},
    {"venue": VENUE_HYPERLIQUID_PERPETUAL, "instrument": "BTC", "side": SIDE_SELL,
     "label": "perp  (short)"},
]
ROUND_TRIPS_A_YEAR = 64
BREAKEVEN_PER_SIDE = 0.000494
ARTIFACTS = REPO / "artifacts" / "execution"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--notional", type=float, default=5000.0,
                        help="USD per leg. The cost of crossing depends on it, so there is "
                             "no size-free answer and none is offered.")
    parser.add_argument("--record", default=str(ARTIFACTS / "book-measurements.json"))
    arguments = parser.parse_args()
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    at = _now()

    total_spread, total_crossing = 0.0, 0.0
    print(f"notional per leg  ${arguments.notional:,.0f}\n")
    for leg in LEGS:
        book, capture, raw = capture_order_book(leg["venue"], leg["instrument"], at)
        if verified_order_book_capture(raw, capture) != book:
            raise SystemExit(f"{leg['venue']} book does not re-verify from its own bytes")
        (ARTIFACTS / f"book-{leg['venue'].lower()}-{at[:19].replace(':', '')}.json").write_bytes(
            p.encoded({"capture": capture, "raw": json.loads(raw)}))

        walk = walk_book(book, leg["side"], arguments.notional)
        spread = half_spread_rate(book)
        print(f"{leg['label']}  {leg['instrument']:<8} mid {mid_price(book):>12,.2f}")
        print(f"              half-spread {spread:.8f}   "
              f"crossing {walk['cost_rate']:.8f}   "
              f"{walk['levels_consumed']} level(s), "
              f"{'DEPTH EXHAUSTED' if walk['depth_exhausted'] else 'book deep enough'}")
        if walk["depth_exhausted"]:
            raise SystemExit(
                "The walk ran out of visible book. That measures nothing except that the "
                "book was too thin at this size; reduce --notional or wait for depth.")

        measurement = measure_execution(
            side=leg["side"], bid=book["bids"][0][0], ask=book["asks"][0][0],
            fill_price=walk["vwap"], filled_quantity=walk["quantity"], commission=0.0,
            fill_venue=FILL_BOOK, symbol=f"{leg['venue']}:{leg['instrument']}",
            observed_at=at)
        append_execution_measurement(Path(arguments.record), measurement)
        total_spread += spread
        total_crossing += walk["cost_rate"]

    print(f"\n{'=' * 74}")
    print(f"  crossing BOTH legs once   {total_crossing:.8f} of notional")
    print(f"  of which spread            {total_spread:.8f}")
    print(f"  impact beyond the spread   {total_crossing - total_spread:.8f}")
    print(f"\n  breakeven budget           {BREAKEVEN_PER_SIDE * len(LEGS):.8f} "
          f"({BREAKEVEN_PER_SIDE:.6f} per leg per side)")
    remaining = BREAKEVEN_PER_SIDE * len(LEGS) - total_crossing
    print(f"  LEFT FOR COMMISSIONS       {remaining:.8f} across both legs, i.e. "
          f"{remaining / len(LEGS):.8f} per leg")
    print(f"\n  {ROUND_TRIPS_A_YEAR} round trips a year cost "
          f"{total_crossing * 2 * ROUND_TRIPS_A_YEAR:.4f} of notional in crossing alone, "
          f"i.e. {total_crossing * 2 * ROUND_TRIPS_A_YEAR:.2%} a year")
    print("=" * 74)
    if remaining <= 0:
        print("\n  The spread alone exhausts the budget. No fee schedule rescues this.")
    else:
        print(f"\n  A venue charging more than {remaining / len(LEGS):.6f} per side per leg "
              f"puts the gated carry back under water.\n  That is the number to check a fee "
              f"schedule against -- and it is the one thing here nobody has measured.")
    print("\n  Books are a snapshot: no queue position, no allowance for the book moving "
          "while\n  an order executes. For this size against this depth that is nearly true, "
          "and it\n  is still stronger evidence than a paper engine's slippage.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
