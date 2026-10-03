"""The equity order contract, long and short. Pure: nothing here touches a network."""

import itertools
import random
import unittest
from decimal import Decimal

from tramitago_quant_core.execution.equity_order import (
    order_side, prepare_equity_order, alpaca_paper_payload, borrow_cost_per_day,
    LONG, SHORT, ENTER, EXIT, BUY, SELL, PAPER_HOST)

ASSET = {"symbol": "VIXM", "tradable": True, "shortable": True, "easy_to_borrow": True,
         "fractionable": True}
RISK = {"max_total_exposure_usd": "1000", "max_loss_per_position_usd": "100",
        "max_adverse_move": "0.10"}


def _order(action, direction, quantity, position="0", price="20", asset=None, risk=None,
           existing="0", symbol="VIXM"):
    return prepare_equity_order(
        symbol=symbol, action=action, direction=direction, quantity=quantity,
        limit_price=price, asset=asset or ASSET, position_quantity=position,
        risk=risk or RISK, existing_gross_exposure_usd=existing)


class SideTests(unittest.TestCase):
    def test_the_side_is_derived_from_action_and_direction(self):
        self.assertEqual(order_side(ENTER, LONG), BUY)
        self.assertEqual(order_side(EXIT, LONG), SELL)
        self.assertEqual(order_side(ENTER, SHORT), SELL)
        self.assertEqual(order_side(EXIT, SHORT), BUY)

    def test_an_unknown_combination_is_refused(self):
        for bad in (("HOLD", LONG), (ENTER, "FLAT"), (None, None)):
            with self.assertRaises(ValueError):
                order_side(*bad)


class ShortEntryTests(unittest.TestCase):
    def test_a_well_formed_short_entry_is_accepted_with_its_protective_cover(self):
        result = _order(ENTER, SHORT, "30")
        self.assertTrue(result["ok"], result["reasons"])
        proposal = result["proposal"]
        self.assertEqual(proposal["side"], SELL)
        self.assertTrue(proposal["opens_short"])
        self.assertEqual(proposal["protective_cover"]["side"], BUY)
        self.assertEqual(Decimal(proposal["protective_cover"]["stop_price"]), Decimal("22.0"))
        self.assertEqual(Decimal(proposal["worst_case_loss_usd"]), Decimal("60.00"))

    def test_a_short_without_a_declared_adverse_move_is_refused(self):
        risk = {"max_total_exposure_usd": "1000"}
        result = _order(ENTER, SHORT, "30", risk=risk)
        self.assertFalse(result["ok"])
        self.assertTrue(any("without bound" in r for r in result["reasons"]))

    def test_a_short_whose_loss_at_the_stop_exceeds_the_budget_is_refused(self):
        result = _order(ENTER, SHORT, "40", risk={**RISK, "max_loss_per_position_usd": "50"})
        self.assertFalse(result["ok"])
        self.assertTrue(any("exceeds the per-position budget" in r for r in result["reasons"]))

    def test_hard_to_borrow_and_unshortable_assets_are_refused(self):
        for facts in ({"easy_to_borrow": False}, {"shortable": False}):
            result = _order(ENTER, SHORT, "30", asset={**ASSET, **facts})
            self.assertFalse(result["ok"], facts)

    def test_fractional_shorting_is_refused_but_a_fractional_long_is_not(self):
        self.assertFalse(_order(ENTER, SHORT, "30.5")["ok"])
        self.assertTrue(_order(ENTER, LONG, "30.5")["ok"])

    def test_a_non_fractionable_long_needs_whole_shares(self):
        asset = {**ASSET, "fractionable": False}
        self.assertFalse(_order(ENTER, LONG, "30.5", asset=asset)["ok"])
        self.assertTrue(_order(ENTER, LONG, "30", asset=asset)["ok"])

    def test_the_exposure_cap_counts_what_is_already_held(self):
        self.assertTrue(_order(ENTER, SHORT, "30", existing="400")["ok"])        # 600 + 400
        self.assertFalse(_order(ENTER, SHORT, "30", existing="500")["ok"])       # 600 + 500

    def test_crypto_cannot_be_shorted(self):
        asset = {**ASSET, "symbol": "BTC/USD"}
        result = _order(ENTER, SHORT, "1", asset=asset, symbol="BTC/USD")
        self.assertFalse(result["ok"])
        self.assertTrue(any("crypto" in r for r in result["reasons"]))

    def test_every_reason_is_listed_not_just_the_first(self):
        result = _order(ENTER, SHORT, "30.5",
                        asset={**ASSET, "shortable": False, "easy_to_borrow": False},
                        risk={"max_total_exposure_usd": "1000"})
        self.assertGreaterEqual(len(result["reasons"]), 4)


class CoverTests(unittest.TestCase):
    def test_a_cover_buys_back_part_or_all_of_the_short(self):
        self.assertTrue(_order(EXIT, SHORT, "10", position="-30")["ok"])
        self.assertTrue(_order(EXIT, SHORT, "30", position="-30")["ok"])
        self.assertEqual(_order(EXIT, SHORT, "30", position="-30")["proposal"]["side"], BUY)

    def test_a_cover_larger_than_the_short_is_refused_because_it_would_leave_a_long(self):
        result = _order(EXIT, SHORT, "31", position="-30")
        self.assertFalse(result["ok"])
        self.assertTrue(any("leave a long" in r for r in result["reasons"]))

    def test_there_must_be_a_short_to_cover_and_a_long_to_sell(self):
        self.assertFalse(_order(EXIT, SHORT, "1", position="0")["ok"])
        self.assertFalse(_order(EXIT, SHORT, "1", position="5")["ok"])
        self.assertFalse(_order(EXIT, LONG, "1", position="0")["ok"])
        self.assertFalse(_order(EXIT, LONG, "1", position="-5")["ok"])
        self.assertFalse(_order(EXIT, LONG, "6", position="5")["ok"])

    def test_an_entry_is_refused_while_any_position_is_open(self):
        for held in ("5", "-5"):
            for direction in (LONG, SHORT):
                self.assertFalse(_order(ENTER, direction, "1", position=held)["ok"],
                                 (held, direction))


class NoFlipPropertyTests(unittest.TestCase):
    def test_no_accepted_sequence_of_orders_can_flip_a_position(self):
        # THE PROPERTY THE CONTRACT EXISTS FOR. Random orders against a ledger that
        # fills every accepted one: the position is only ever opened from flat in the
        # direction asked, reduced toward zero, and never crosses it.
        rng = random.Random(7)
        position = Decimal(0)
        for _ in range(4000):
            action, direction = rng.choice([(ENTER, LONG), (EXIT, LONG),
                                            (ENTER, SHORT), (EXIT, SHORT)])
            quantity = rng.choice(["1", "2", "5", "30", "31", "7.5"])
            result = _order(action, direction, quantity, position=str(position))
            if not result["ok"]:
                continue
            side, qty = result["proposal"]["side"], Decimal(result["proposal"]["quantity"])
            after = position + qty if side == BUY else position - qty
            self.assertFalse(position > 0 > after or position < 0 < after,
                             (position, after, action, direction))
            if action == ENTER:
                self.assertEqual(position, 0)
                self.assertTrue(after > 0 if direction == LONG else after < 0)
            else:
                self.assertTrue(abs(after) < abs(position) or after == 0)
            position = after

    def test_an_accepted_short_always_fits_its_loss_budget_at_the_stop(self):
        for quantity, price, move, budget in itertools.product(
                ("1", "10", "40", "99"), ("5", "20", "80"), ("0.05", "0.20", "0.50"),
                ("10", "100")):
            result = _order(ENTER, SHORT, quantity, price=price,
                            risk={"max_total_exposure_usd": "100000",
                                  "max_loss_per_position_usd": budget,
                                  "max_adverse_move": move})
            if result["ok"]:
                loss = Decimal(quantity) * Decimal(price) * Decimal(move)
                self.assertLessEqual(loss, Decimal(budget))


class PayloadTests(unittest.TestCase):
    def test_a_short_entry_goes_to_the_paper_host_as_an_oto_order_with_its_stop(self):
        payload = alpaca_paper_payload(_order(ENTER, SHORT, "30")["proposal"])
        self.assertEqual(payload["host"], PAPER_HOST)
        body = payload["body"]
        self.assertEqual((body["side"], body["type"], body["order_class"]), ("sell", "limit", "oto"))
        self.assertEqual(Decimal(body["stop_loss"]["stop_price"]), Decimal("22.0"))

    def test_a_cover_and_a_long_carry_no_stop(self):
        for order in (_order(EXIT, SHORT, "10", position="-30"), _order(ENTER, LONG, "10")):
            body = alpaca_paper_payload(order["proposal"])["body"]
            self.assertNotIn("stop_loss", body)

    def test_a_short_entry_cannot_be_sent_stripped_of_its_cover(self):
        proposal = dict(_order(ENTER, SHORT, "30")["proposal"], protective_cover=None)
        with self.assertRaises(ValueError):
            alpaca_paper_payload(proposal)

    def test_only_a_paper_proposal_becomes_a_request(self):
        proposal = dict(_order(ENTER, LONG, "10")["proposal"], paper_only=False)
        with self.assertRaises(ValueError):
            alpaca_paper_payload(proposal)

    def test_the_proposal_identity_is_stable_and_content_bound(self):
        a, b = _order(ENTER, SHORT, "30")["proposal"], _order(ENTER, SHORT, "30")["proposal"]
        self.assertEqual(a["identity"], b["identity"])
        self.assertNotEqual(a["identity"], _order(ENTER, SHORT, "31")["proposal"]["identity"])


class BorrowCostTests(unittest.TestCase):
    def test_a_declared_fee_accrues_daily_and_zero_costs_nothing(self):
        self.assertEqual(borrow_cost_per_day("1000", "0"), 0)
        self.assertEqual(borrow_cost_per_day("2520", "0.10"), Decimal("1"))


if __name__ == "__main__":
    unittest.main()
