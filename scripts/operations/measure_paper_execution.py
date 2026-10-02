"""Measure one real PAPER fill against the quote that was standing when it was sent.

WHY THIS EXISTS. The carry gated by its validated exit rule needs 64 round trips
a year on two legs and came back NOT_VALIDATED on costs -- but the breakeven is
0.000494 per leg per side against 0.000650 DECLARED, and the declared number was
an assumed taker rate nobody had measured. A fill 0.000156 cheaper flips the
verdict. The machinery-verification loop has been placing PAPER orders all day
and nobody has asked it what they cost.

WHAT A PAPER FILL ANSWERS, and this is the whole point of running it rather than
reading a fee schedule:

  the COMMISSION is real          -- the venue's own schedule, on the real notional
  the HALF-SPREAD is real         -- the live book's own bid and ask, at send time
  the SLIPPAGE is NOT             -- a paper engine has no queue and no market
                                     impact, so what it reports is a FLOOR

So this measures two of the three components honestly and bounds the third from
below. A contract built from it is an OPTIMISTIC bound on cost, and it says so in
its own source text. That is still strictly better than an assumption, because
the two real components are the ones that dominate at small size -- and because a
floor that already exceeds the breakeven would settle the question outright.

THE QUOTE IS TAKEN BEFORE THE ORDER AND NEVER AFTER. Measured against a later
quote, slippage silently becomes a measure of how far the market moved while the
order travelled, which has the same units and a different meaning.

CREDENTIALS COME FROM THE ENVIRONMENT AND ARE NEVER WRITTEN ANYWHERE:

    export ALPACA_PAPER_API_KEY_ID=...
    export ALPACA_PAPER_API_SECRET_KEY=...
    python3.11 -B scripts/operations/measure_paper_execution.py --notional 20

Run it as often as you like. Each fill appends one measurement; the contract is
rebuilt from all of them and gets more honest the more there are.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pipeline as p
from tramitago_quant_core.risk.execution_cost import (
    measure_execution, append_execution_measurement, load_execution_measurements,
    measured_cost_contract, round_trip_cost, execution_summary,
    SIDE_BUY, SIDE_SELL, FILL_PAPER,
)

DATA_HOST = "data.alpaca.markets"
TRADE_HOST = "paper-api.alpaca.markets"
SYMBOL = "BTC/USD"
ROUND_TRIPS_A_YEAR = 64          # what the gated carry needs
LEGS = 2                         # spot and perpetual both pay
BREAKEVEN_PER_SIDE = 0.000494    # from the gated level-claim comparison

DEFAULT_RECORD = REPO / "artifacts" / "execution" / "paper-fills.json"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _request(method, host, path, injector, body=None, timeout=30):
    headers = injector({"Accept": "application/json", "Content-Type": "application/json"})
    payload = p.encoded(body) if body is not None else None
    request = Request(f"https://{host}{path}", data=payload, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def _quote(injector):
    """The book as it stands RIGHT NOW. Taken before the order exists."""
    payload = _request("GET", DATA_HOST,
                       f"/v1beta3/crypto/us/latest/quotes?symbols={SYMBOL.replace('/', '%2F')}",
                       injector)
    quote = payload["quotes"][SYMBOL]
    return float(quote["bp"]), float(quote["ap"])


def _await_fill(injector, order_id, attempts=20):
    for _ in range(attempts):
        order = _request("GET", TRADE_HOST, f"/v2/orders/{order_id}", injector)
        if order.get("status") == "filled":
            return order
        if order.get("status") in ("canceled", "expired", "rejected"):
            raise SystemExit(f"Order {order.get('status')}: {order}")
        time.sleep(1)
    raise SystemExit("Order did not fill in time; nothing measured, nothing recorded")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--notional", type=float, default=20.0,
                        help="USD notional per side. Small on purpose: this measures the "
                             "spread and the fee, and a paper engine cannot measure impact "
                             "at any size.")
    parser.add_argument("--record", default=str(DEFAULT_RECORD))
    parser.add_argument("--round-trip", action="store_true",
                        help="Buy and then sell, so both sides are measured. A carry pays "
                             "both, and only measuring the buy would halve the answer.")
    arguments = parser.parse_args()

    injector = p.alpaca_paper_credentials_from_environment()
    if injector is None:
        raise SystemExit(
            "No PAPER credentials in the environment. Set ALPACA_PAPER_API_KEY_ID and "
            "ALPACA_PAPER_API_SECRET_KEY as environment variables; this script never reads "
            "or writes them anywhere else.")

    record = Path(arguments.record)
    record.parent.mkdir(parents=True, exist_ok=True)
    sides = [SIDE_BUY, SIDE_SELL] if arguments.round_trip else [SIDE_BUY]
    quantity = None

    for side in sides:
        bid, ask = _quote(injector)
        sent_at = _now()
        body = {"symbol": SYMBOL, "side": side.lower(), "type": "market",
                "time_in_force": "gtc"}
        if side == SIDE_BUY:
            body["notional"] = str(round(arguments.notional, 2))
        else:
            body["qty"] = str(quantity)
        print(f"\n{side:<4} quote {bid:,.2f} / {ask:,.2f}   spread "
              f"{(ask - bid) / ((ask + bid) / 2) * 1e4:.2f} bp")

        order = _request("POST", TRADE_HOST, "/v2/orders", injector, body)
        filled = _await_fill(injector, order["id"])
        fill_price = float(filled["filled_avg_price"])
        filled_quantity = float(filled["filled_qty"])
        quantity = filled_quantity

        measurement = measure_execution(
            side=side, bid=bid, ask=ask, fill_price=fill_price,
            filled_quantity=filled_quantity,
            # Alpaca reports crypto fees on the fill; absent, the commission is
            # zero and the measurement says so rather than guessing a schedule.
            commission=float(filled.get("commission") or 0.0),
            fill_venue=FILL_PAPER, symbol=SYMBOL, observed_at=sent_at)
        append_execution_measurement(record, measurement)
        print(f"     fill  {fill_price:,.2f}  qty {filled_quantity:.8f}")
        print(f"     commission {float(measurement['commission_rate']):.6f}  "
              f"half-spread {float(measurement['half_spread_rate']):.6f}  "
              f"slippage {float(measurement['slippage_rate']):.6f}")
        print(f"     cost per side {float(measurement['cost_per_side']):.6f}")

    fills = load_execution_measurements(record, symbol=SYMBOL)
    summary = execution_summary(fills)
    contract = measured_cost_contract(
        fills, legs=LEGS,
        source="Alpaca PAPER crypto spot, measured against the quote standing at send time.")

    print(f"\n{'=' * 74}\nMEASURED SO FAR, over {summary['fills']} fills\n{'=' * 74}")
    print(f"  cost per side   min {summary['cost_per_side_min']}  "
          f"median {summary['cost_per_side_median']}  max {summary['cost_per_side_max']}")
    print(f"  commission      {summary['commission_rate_median']}")
    print(f"  half-spread     {summary['half_spread_rate_median']}")
    print(f"  slippage        {summary['slippage_rate_median']}   (PAPER: a FLOOR)")
    print(f"  contract        {contract['contract_id']}")

    per_side = (float(contract["commission_rate"]) + float(contract["half_spread_rate"])
                + float(contract["slippage_rate"]))
    annual = round_trip_cost(contract, ROUND_TRIPS_A_YEAR)
    print(f"\n{'=' * 74}")
    print(f"  {ROUND_TRIPS_A_YEAR} round trips a year on {LEGS} legs cost {annual:.4f} "
          f"of notional, i.e. {annual:.2%} a year")
    print(f"  per leg per side  {per_side:.6f}   breakeven {BREAKEVEN_PER_SIDE:.6f}")
    if per_side <= BREAKEVEN_PER_SIDE:
        print("  BELOW BREAKEVEN on this venue's paper book -- and a PAPER floor that clears "
              "it is not a result, because the component it floors is the one that decides.")
    else:
        print("  ABOVE BREAKEVEN already, and this is a FLOOR: the real cost can only be "
              "higher. On this venue the gated carry does not pay.")
    print("=" * 74)
    print("\nAlpaca sells SPOT. The short leg of a carry is a perpetual and lives on another "
          "venue entirely, so this measures one leg of two. The other one is unbuilt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
