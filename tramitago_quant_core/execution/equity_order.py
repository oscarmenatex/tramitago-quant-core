"""The order contract for US equities, long AND short. Pure, PAPER only, no network.

WHY THIS IS A NEW MODULE AND NOT A CHANGE TO pipeline.py. The operating chain there is
bound to one instrument: BTC-USD is a literal at about sixty places, and the risk terms
are literals too (capital exactly 200 USD, exposure at most 50, risk budget exactly 5).
It accepts only (ENTER, BUY) and (EXIT, SELL), and Alpaca does not allow shorting crypto
at all. Widening that chain to equities relaxes constraints that guard real capital, and
that is a decision for the Director, not a side effect of adding a side to an order.
Nothing here touches it. This module is what a wired chain would call.

WHAT A SHORT CHANGES, and what the contract therefore refuses:

  A short loses without bound. A long loses at most its notional; a short that doubles
  loses twice what it was opened for and keeps going. So an ENTER short is refused unless
  it carries a DECLARED maximum adverse move and a protective cover order at that price,
  and the loss at that move must fit a per-position budget.

  A short can be called in and cannot always be opened. Only an asset the broker marks
  shortable AND easy to borrow may be shorted; a hard-to-borrow name is refused.

  A short needs whole shares. Fractional shorting does not exist at the broker.

  Covering must not flip. An EXIT short that bought more than the short held would leave a
  long nobody asked for, so a cover is limited to the open short and an ENTER is refused
  while any position in the instrument is open. One position per instrument.

THE SIDE IS DERIVED FROM TWO FACTS, never typed: the action (ENTER or EXIT) and the
direction of the position (LONG or SHORT).

      ENTER LONG  -> BUY        EXIT LONG  -> SELL
      ENTER SHORT -> SELL       EXIT SHORT -> BUY
"""

from decimal import Decimal, InvalidOperation

from tramitago_quant_core.shared.util import digest, encoded

LONG, SHORT = "LONG", "SHORT"
ENTER, EXIT = "ENTER", "EXIT"
BUY, SELL = "BUY", "SELL"

_SIDE = {(ENTER, LONG): BUY, (EXIT, LONG): SELL, (ENTER, SHORT): SELL, (EXIT, SHORT): BUY}

PAPER_HOST = "paper-api.alpaca.markets"
ORDER_PATH = "/v2/orders"


def order_side(action, direction):
    try:
        return _SIDE[(action, direction)]
    except KeyError:
        raise ValueError(f"action must be ENTER or EXIT and direction LONG or SHORT, "
                         f"got {action!r} and {direction!r}") from None


def _decimal(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{name} must be a number, got {value!r}") from None
    if not number.is_finite():
        raise ValueError(f"{name} must be finite")
    return number


def is_crypto(symbol):
    """Crypto pairs are written BASE/QUOTE or BASE-QUOTE; no equity ticker is."""
    return "/" in symbol or "-" in symbol


def borrow_cost_per_day(notional_usd, annual_fee, trading_days=252):
    """What holding a short costs per day at a DECLARED annual borrow fee.

    Alpaca charges nothing for an easy-to-borrow name, so the honest default is zero;
    the parameter exists so a hard-to-borrow fee, or a changed policy, is a declared
    number and never an omission.
    """
    return _decimal(notional_usd, "notional") * _decimal(annual_fee, "annual fee") / trading_days


def prepare_equity_order(*, symbol, action, direction, quantity, limit_price, asset,
                         position_quantity, risk, existing_gross_exposure_usd="0"):
    """Validate one order and return {"ok", "reasons", "proposal"}. Never raises on a
    refusal: every reason is listed, so nobody fixes one and meets the next.

    `position_quantity` is SIGNED: positive long, negative short, zero flat.
    `asset` carries the broker facts: tradable, shortable, easy_to_borrow, fractionable.
    `risk` carries max_total_exposure_usd, and for a short max_loss_per_position_usd and
    max_adverse_move (a fraction: 0.10 means the cover triggers 10 percent above entry).
    """
    reasons = []
    side = order_side(action, direction)
    quantity = _decimal(quantity, "quantity")
    limit_price = _decimal(limit_price, "limit price")
    position = _decimal(position_quantity, "position quantity")
    existing = _decimal(existing_gross_exposure_usd, "existing gross exposure")

    if is_crypto(symbol):
        reasons.append("crypto cannot be shorted at the broker, and this contract is for "
                       "equities")
    if asset.get("symbol") != symbol:
        reasons.append("the asset facts are for a different symbol")
    if asset.get("tradable") is not True:
        reasons.append("the asset is not tradable")
    if quantity <= 0:
        reasons.append("quantity must be positive")
    if limit_price <= 0:
        reasons.append("a positive limit price is required: only limit orders are allowed")

    if action == ENTER:
        if position != 0:
            reasons.append("one position per instrument: an ENTER is refused while any "
                           "position in it is open, so an order can never flip one")
    elif direction == LONG:
        if position <= 0:
            reasons.append("there is no long position to exit")
        elif quantity > position:
            reasons.append("an exit cannot sell more than the long held")
    else:
        if position >= 0:
            reasons.append("there is no short position to cover")
        elif quantity > -position:
            reasons.append("a cover cannot buy more than the short held: it would "
                           "leave a long")

    whole = quantity == quantity.to_integral_value()
    if direction == SHORT and not whole:
        reasons.append("a short needs whole shares: fractional shorting does not exist")
    if direction == LONG and action == ENTER and not whole \
            and asset.get("fractionable") is not True:
        reasons.append("the asset is not fractionable, so a long needs whole shares")

    notional = quantity * limit_price if quantity > 0 and limit_price > 0 else Decimal(0)
    protective = worst_loss = None
    if action == ENTER:
        cap = _decimal(risk.get("max_total_exposure_usd", "0"), "exposure cap")
        if cap <= 0:
            reasons.append("the risk contract declares no exposure cap")
        elif existing + notional > cap:
            reasons.append(f"gross exposure {existing + notional} would exceed the cap {cap}")

    if direction == SHORT and action == ENTER:
        if asset.get("shortable") is not True:
            reasons.append("the asset is not shortable")
        if asset.get("easy_to_borrow") is not True:
            reasons.append("the asset is not easy to borrow: a hard-to-borrow short can be "
                           "recalled or carry a fee nobody priced")
        move = risk.get("max_adverse_move")
        budget = risk.get("max_loss_per_position_usd")
        if move is None or budget is None:
            reasons.append("a short loses without bound, so it must declare a maximum "
                           "adverse move and a per-position loss budget")
        else:
            move, budget = _decimal(move, "max adverse move"), _decimal(budget, "loss budget")
            if not 0 < move < 1:
                reasons.append("the maximum adverse move must be a fraction between 0 and 1")
            elif budget <= 0:
                reasons.append("the per-position loss budget must be positive")
            else:
                worst_loss = notional * move
                if worst_loss > budget:
                    reasons.append(f"the loss at the declared adverse move, {worst_loss}, "
                                   f"exceeds the per-position budget {budget}")
                protective = {"side": BUY, "type": "STOP", "stop_price": str(limit_price * (1 + move)),
                              "quantity": str(quantity)}

    if reasons:
        return {"ok": False, "reasons": reasons, "proposal": None}

    content = {
        "symbol": symbol, "action": action, "direction": direction, "side": side,
        "quantity": str(quantity), "limit_price": str(limit_price), "order_type": "LIMIT",
        "time_in_force": "day", "notional_usd": str(notional),
        "opens_short": action == ENTER and direction == SHORT,
        "protective_cover": protective,
        "worst_case_loss_usd": None if worst_loss is None else str(worst_loss),
        "paper_only": True, "manual_approval_required": True,
    }
    return {"ok": True, "reasons": [],
            "proposal": {**content, "identity": "EQUITY_ORDER_PROPOSAL|" + digest(encoded(content))}}


def alpaca_paper_payload(proposal):
    """The body for POST /v2/orders on the PAPER host. A short entry carries its cover as
    an OTO stop so the loss is bounded at the broker, not only in a document."""
    if proposal.get("paper_only") is not True:
        raise ValueError("only a PAPER proposal can be turned into a request here")
    body = {"symbol": proposal["symbol"], "qty": proposal["quantity"],
            "side": proposal["side"].lower(), "type": "limit",
            "time_in_force": proposal["time_in_force"], "limit_price": proposal["limit_price"]}
    cover = proposal.get("protective_cover")
    if proposal["opens_short"]:
        if not cover:
            raise ValueError("a short entry cannot be sent without its protective cover")
        body["order_class"] = "oto"
        body["stop_loss"] = {"stop_price": cover["stop_price"]}
    return {"host": PAPER_HOST, "path": ORDER_PATH, "method": "POST", "body": body}
