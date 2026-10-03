"""The PAPER chain for contracted equities, long and short, end to end.

The transport is a double: nothing here touches a network. What these pin is the part that
matters more than the new capability, which is everything that must stay CLOSED: BTC-USD
unchanged, a contracted instrument never reaching LIVE, a changed contract invalidating an
approved proposal, and no order the order contract refuses getting through the chain.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pipeline as p
from tramitago_quant_core.execution import instrument_contract

OBSERVED_AT = "2026-10-03T15:00:00Z"
CREATED_AT = "2026-10-03T15:10:00Z"
APPROVED_AT = "2026-10-03T15:11:00Z"
REVALIDATED_AT = "2026-10-03T15:12:00Z"
PREPARED_AT = "2026-10-03T15:13:00Z"
ATTEMPTED_AT = "2026-10-03T15:14:00Z"
OBSERVED_LATER = "2026-10-03T15:30:00Z"

CONTRACTS = {"instruments": {
    "VIXM": {"max_capital_usd": "1000", "max_exposure_usd": "250", "risk_budget_usd": "25",
             "can_short": True, "max_adverse_move": "0.10", "max_loss_per_position_usd": "25"},
    "XYLD": {"max_capital_usd": "1000", "max_exposure_usd": "250", "risk_budget_usd": "25",
             "can_short": False},
}}


def credentials():
    return lambda headers: {**headers, "APCA-API-KEY-ID": "memory-only",
                            "APCA-API-SECRET-KEY": "memory-only"}


def reply(code, payload=None):
    return {"status_code": code, "content_type": "application/json" if payload else None,
            "location": None, "body": p.encoded(payload) if payload else b""}


def asset(symbol, **facts):
    return {"symbol": symbol, "tradable": True, "shortable": True, "easy_to_borrow": True,
            "fractionable": True, "status": "active", **facts}


class Broker:
    """A transport double keyed by (method, path); records everything it is asked."""

    def __init__(self, routes):
        self.routes, self.calls = dict(routes), []

    def __call__(self, method, host, path, body, timeout, injector):
        self.calls.append((method, host, path, body))
        value = self.routes[(method, path)]
        return value.pop(0) if isinstance(value, list) else value


class Chain(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / "instrument_contracts.json"
        self.write_contracts(CONTRACTS)
        patcher = patch.object(p, "INSTRUMENT_CONTRACT_PATH", self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.state = self.root / "state.json"
        self.state.write_bytes(p.encoded({}))

    def out(self, name):
        """A fresh output directory per call: published outputs are immutable."""
        self.counter = getattr(self, "counter", 0) + 1
        return self.root / f"{name}-{self.counter}"

    def write_contracts(self, document):
        self.config.write_text(json.dumps(document), encoding="utf-8")

    def load(self):
        return json.loads(self.state.read_bytes())

    def observe(self, symbol, position, at=OBSERVED_AT, **asset_facts):
        broker = Broker({
            ("GET", f"/v2/assets/{symbol}"): reply(200, asset(symbol, **asset_facts)),
            ("GET", f"/v2/positions/{symbol}"): position})
        result = p.observe_alpaca_paper_instrument(
            self.state, self.out("obs"), symbol, at, 5.0, credentials, broker)
        return result["observation"]

    def flat(self):
        return reply(404)

    def propose(self, observation, action, direction, quantity, price, risk=None,
                created_at=CREATED_AT, operational_risk="10"):
        risk = risk or p.instrument_risk_config(
            observation["instrument"], action, quantity, price, operational_risk)
        return p.prepare_instrument_order_proposal(
            self.state, self.out("prop"), observation["identity"], action, direction,
            quantity, price, risk, created_at, hypothesis_id="HYPOTHESIS|test")["proposal"], risk

    def approve_and_revalidate(self, proposal, risk):
        p.record_manual_approval(self.state, self.out("appr"), proposal["identity"],
                                 proposal["proposal_identity"], "Oscar", APPROVED_AT, "APPROVED")
        return p.revalidate_approved_proposal(
            self.state, self.out("reval"), proposal["identity"],
            proposal["proposal_identity"], risk, REVALIDATED_AT)

    def prepare_request(self, proposal, risk, revalidation, environment="PAPER"):
        return p.prepare_alpaca_request(
            self.state, self.out("req"), proposal["identity"], proposal["proposal_identity"],
            revalidation["revalidation"]["identity"], risk, environment, PREPARED_AT)

    def full_path(self, symbol, direction, action, quantity, price, position, **asset_facts):
        observation = self.observe(symbol, position, **asset_facts)
        proposal, risk = self.propose(observation, action, direction, quantity, price)
        self.assertEqual(proposal["status"], "PENDING_MANUAL_APPROVAL", proposal["rejection_reasons"])
        revalidation = self.approve_and_revalidate(proposal, risk)
        self.assertEqual(revalidation["status"], "READY_FOR_SUBMISSION", revalidation["revalidation"])
        prepared = self.prepare_request(proposal, risk, revalidation)
        self.assertTrue(prepared["request_prepared"], prepared["preparation"])
        return proposal, risk, prepared["prepared_request"]

    def submit(self, request, symbol):
        body_holder = {}

        class Capture(Broker):
            def __call__(inner, method, host, path, body, timeout, injector):
                body_holder["body"] = body
                return reply(200, {"id": "order-1", "client_order_id": body["client_order_id"],
                                   "symbol": symbol, "status": "new", "filled_qty": "0"})

        result = p.execute_alpaca_paper_order(
            self.state, self.out("sub"), request["identity"], ATTEMPTED_AT, 5.0, credentials,
            Capture({}))
        return result, body_holder.get("body")


class ShortChainTests(Chain):
    def test_a_short_goes_from_observation_to_an_oto_order_with_its_stop(self):
        proposal, risk, request = self.full_path("VIXM", "SHORT", "ENTER", "12", "20.00",
                                                 self.flat())
        self.assertEqual((proposal["side"], proposal["direction"]), ("SELL", "SHORT"))
        self.assertEqual(proposal["protective_stop"], "22.00")
        self.assertEqual(request["payload"]["direction"], "SHORT")
        result, body = self.submit(request, "VIXM")
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertTrue(result["paper_request_sent"])
        self.assertFalse(result["live_request_sent"])
        self.assertEqual((body["symbol"], body["side"], body["qty"], body["order_class"]),
                         ("VIXM", "sell", "12", "oto"))
        self.assertEqual(body["stop_loss"], {"stop_price": "22.00"})
        self.assertEqual(body["time_in_force"], "day")

    def test_the_short_position_is_observed_with_a_negative_quantity(self):
        _, _, request = self.full_path("VIXM", "SHORT", "ENTER", "12", "20.00", self.flat())
        result, _ = self.submit(request, "VIXM")
        broker = Broker({("GET", "/v2/positions/VIXM"): reply(
            200, {"symbol": "VIXM", "qty": "12", "side": "short", "market_value": "-240",
                  "avg_entry_price": "20"})})
        seen = p.observe_alpaca_paper_position(
            self.state, self.out("pos"), result["attempt"]["identity"], OBSERVED_LATER, 5.0,
            credentials, broker)
        self.assertEqual(seen["status"], "OBSERVED")
        self.assertEqual(seen["observation"]["position"]["symbol"], "VIXM")
        self.assertEqual(broker.calls[0][2], "/v2/positions/VIXM")
        observation = self.observe("VIXM", reply(200, {"symbol": "VIXM", "qty": "12",
                                                       "side": "short"}), at=OBSERVED_LATER)
        self.assertEqual(observation["position_quantity"], "-12")

    def test_a_cover_buys_back_with_no_stop_and_never_more_than_the_short(self):
        short = reply(200, {"symbol": "VIXM", "qty": "12", "side": "short"})
        observation = self.observe("VIXM", short)
        proposal, risk = self.propose(observation, "EXIT", "SHORT", "12", "21.00")
        self.assertEqual((proposal["status"], proposal["side"]), ("PENDING_MANUAL_APPROVAL", "BUY"))
        self.assertNotIn("protective_stop", proposal)
        too_much, _ = self.propose(observation, "EXIT", "SHORT", "13", "21.00")
        self.assertEqual(too_much["status"], "REJECTED")
        self.assertTrue(any("leave a long" in r for r in too_much["rejection_reasons"]))

    def test_an_entry_while_a_short_is_open_cannot_flip_it(self):
        observation = self.observe("VIXM", reply(200, {"symbol": "VIXM", "qty": "12",
                                                       "side": "short"}))
        for direction in ("LONG", "SHORT"):
            proposal, _ = self.propose(observation, "ENTER", direction, "1", "20.00")
            self.assertEqual(proposal["status"], "REJECTED", direction)

    def test_a_cover_has_its_own_larger_cap_but_a_cap_nonetheless(self):
        observation = self.observe("VIXM", reply(200, {"symbol": "VIXM", "qty": "12",
                                                       "side": "short"}))
        inside, _ = self.propose(observation, "EXIT", "SHORT", "12", "41.00")     # 492 <= 500
        beyond, _ = self.propose(observation, "EXIT", "SHORT", "12", "43.00")     # 516 > 500
        self.assertEqual(inside["status"], "PENDING_MANUAL_APPROVAL")
        self.assertEqual(beyond["status"], "REJECTED")
        self.assertTrue(any("exit cap" in r for r in beyond["rejection_reasons"]))
        self.assertEqual(inside["risk"]["max_exposure_usd"], "500")


class ProposalRefusalTests(Chain):
    def test_a_short_is_refused_on_what_the_broker_was_observed_to_say(self):
        for facts in ({"shortable": False}, {"easy_to_borrow": False}, {"tradable": False}):
            observation = self.observe("VIXM", self.flat(), **facts)
            proposal, _ = self.propose(observation, "ENTER", "SHORT", "12", "20.00")
            self.assertEqual(proposal["status"], "REJECTED", facts)

    def test_an_instrument_that_cannot_short_by_contract_is_refused_a_short(self):
        observation = self.observe("XYLD", self.flat())
        proposal, _ = self.propose(observation, "ENTER", "SHORT", "5", "20.00")
        self.assertEqual(proposal["status"], "REJECTED")
        self.assertTrue(any("does not allow a short" in r for r in proposal["rejection_reasons"]))

    def test_a_long_works_for_a_contracted_equity_with_fractional_shares(self):
        proposal, risk, request = self.full_path("XYLD", "LONG", "ENTER", "10.5", "20.00",
                                                 self.flat())
        self.assertEqual((proposal["side"], proposal["quantity"]), ("BUY", "10.5"))
        result, body = self.submit(request, "XYLD")
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertNotIn("order_class", body)
        self.assertNotIn("stop_loss", body)

    def test_the_entry_cap_the_loss_budget_and_the_operational_risk_are_enforced(self):
        observation = self.observe("VIXM", self.flat())
        over_cap, _ = self.propose(observation, "ENTER", "SHORT", "13", "20.00")        # 260 > 250
        over_loss, _ = self.propose(observation, "ENTER", "SHORT", "12", "25.00")       # loss 30 > 25
        over_budget, _ = self.propose(observation, "ENTER", "SHORT", "5", "20.00",
                                      operational_risk="26")
        for proposal in (over_cap, over_loss, over_budget):
            self.assertEqual(proposal["status"], "REJECTED")

    def test_a_stale_or_undeterminable_observation_is_refused(self):
        observation = self.observe("VIXM", self.flat())
        stale, _ = self.propose(observation, "ENTER", "SHORT", "5", "20.00",
                                created_at="2026-10-03T17:00:01Z")
        self.assertEqual(stale["status"], "REJECTED")
        self.assertTrue(any("stale" in r for r in stale["rejection_reasons"]))
        broker = Broker({("GET", "/v2/assets/VIXM"): reply(404),
                         ("GET", "/v2/positions/VIXM"): self.flat()})
        unknown = p.observe_alpaca_paper_instrument(
            self.state, self.out("o2"), "VIXM", "2026-10-03T15:01:00Z", 5.0, credentials,
            broker)["observation"]
        self.assertEqual(unknown["status"], "UNKNOWN")
        refused, _ = self.propose(unknown, "ENTER", "SHORT", "5", "20.00")
        self.assertEqual(refused["status"], "REJECTED")

    def test_a_hand_written_risk_contract_is_refused(self):
        observation = self.observe("VIXM", self.flat())
        risk = p.instrument_risk_config("VIXM", "ENTER", "5", "20.00", "10")
        risk["max_capital_usd"] = "5000"
        risk["risk_contract_identity"] = p.phase4_risk_contract_identity(risk)
        proposal, _ = self.propose(observation, "ENTER", "SHORT", "5", "20.00", risk=risk)
        self.assertEqual(proposal["status"], "REJECTED")

    def test_an_uncontracted_instrument_cannot_even_be_observed(self):
        for symbol in ("AAPL", "BTC-USD"):
            with self.assertRaises(ValueError):
                p.observe_alpaca_paper_instrument(
                    self.state, self.out("o"), symbol, OBSERVED_AT, 5.0, credentials, Broker({}))

    def test_the_same_decision_is_one_proposal(self):
        observation = self.observe("VIXM", self.flat())
        first, risk = self.propose(observation, "ENTER", "SHORT", "5", "20.00")
        second = p.prepare_instrument_order_proposal(
            self.state, self.out("p2"), observation["identity"], "ENTER", "SHORT", "5",
            "20.00", risk, CREATED_AT, hypothesis_id="HYPOTHESIS|test")
        self.assertFalse(second["proposal_created"])
        self.assertEqual(len(self.load()["real_order_proposals"]), 1)


class StaysClosedTests(Chain):
    def test_a_contracted_instrument_never_gets_a_live_request(self):
        observation = self.observe("VIXM", self.flat())
        proposal, risk = self.propose(observation, "ENTER", "SHORT", "5", "20.00")
        revalidation = self.approve_and_revalidate(proposal, risk)
        prepared = self.prepare_request(proposal, risk, revalidation, environment="LIVE")
        self.assertFalse(prepared["request_prepared"])
        self.assertEqual(prepared["status"], "BLOCKED_ENVIRONMENT")
        self.assertIn("does not allow this environment",
                      " ".join(prepared["preparation"]["blocking_reasons"]))

    def test_the_live_path_refuses_a_contracted_instrument_at_every_layer(self):
        proposal, risk, request = self.full_path("VIXM", "SHORT", "ENTER", "5", "20.00",
                                                 self.flat())
        live_request = {**request, "target_environment": "LIVE"}
        self.assertFalse(p._request_instrument_allowed(live_request))
        self.assertTrue(p._request_instrument_allowed(request))
        with self.assertRaises(ValueError):
            p._live_order_body(request)
        result = p.execute_alpaca_live_order(
            self.state, self.out("live"), request["identity"], ATTEMPTED_AT, 5.0,
            credentials, Broker({}))
        self.assertEqual(result["status"], "BLOCKED_REQUEST")
        self.assertFalse(result["live_request_sent"])

    def test_a_proposal_carries_no_committee_so_real_capital_cannot_be_reached(self):
        observation = self.observe("VIXM", self.flat())
        proposal, _ = self.propose(observation, "ENTER", "SHORT", "5", "20.00")
        self.assertIsNone(p._request_committee_purpose(
            self.load(), {"proposal_id": proposal["identity"]}))

    def test_a_contract_changed_after_approval_blocks_the_revalidation(self):
        observation = self.observe("VIXM", self.flat())
        proposal, risk = self.propose(observation, "ENTER", "SHORT", "5", "20.00")
        p.record_manual_approval(self.state, self.out("a"), proposal["identity"],
                                 proposal["proposal_identity"], "Oscar", APPROVED_AT, "APPROVED")
        looser = json.loads(json.dumps(CONTRACTS))
        looser["instruments"]["VIXM"]["max_adverse_move"] = "0.20"
        looser["instruments"]["VIXM"]["max_loss_per_position_usd"] = "25"
        self.write_contracts(looser)
        revalidation = p.revalidate_approved_proposal(
            self.state, self.out("r"), proposal["identity"], proposal["proposal_identity"],
            risk, REVALIDATED_AT)
        self.assertEqual(revalidation["status"], "BLOCKED_RISK_REVALIDATION")
        self.assertFalse(revalidation["revalidation"]["checks"]["instrument_contract"])

    def test_removing_an_instrument_from_the_contract_blocks_the_revalidation(self):
        observation = self.observe("VIXM", self.flat())
        proposal, risk = self.propose(observation, "ENTER", "SHORT", "5", "20.00")
        p.record_manual_approval(self.state, self.out("a"), proposal["identity"],
                                 proposal["proposal_identity"], "Oscar", APPROVED_AT, "APPROVED")
        self.write_contracts({"instruments": {}})
        revalidation = p.revalidate_approved_proposal(
            self.state, self.out("r"), proposal["identity"], proposal["proposal_identity"],
            risk, REVALIDATED_AT)
        self.assertEqual(revalidation["status"], "BLOCKED_RISK_REVALIDATION")

    def test_a_short_entry_cannot_be_turned_into_an_order_without_its_stop(self):
        _, _, request = self.full_path("VIXM", "SHORT", "ENTER", "5", "20.00", self.flat())
        stripped = json.loads(json.dumps(request))
        del stripped["payload"]["protective_stop"]
        with self.assertRaises(ValueError):
            p._paper_order_body(stripped)

    def test_the_transport_allows_lookups_only_for_contracted_symbols(self):
        for path in ("/v2/positions/AAPL", "/v2/assets/AAPL", "/v2/positions/VIXM/extra",
                     "/v2/assets/"):
            with self.assertRaises(ValueError, msg=path):
                p.alpaca_paper_https_request("GET", p.ALPACA_PAPER_HOST, path, None, 5.0,
                                             credentials())
        with self.assertRaises(ValueError):
            p.alpaca_paper_https_request("DELETE", p.ALPACA_PAPER_HOST, "/v2/positions/VIXM",
                                         None, 5.0, credentials())
        with self.assertRaises(ValueError):
            p.alpaca_paper_https_request("GET", p.ALPACA_LIVE_HOST, "/v2/positions/VIXM", None,
                                         5.0, credentials())

    def test_the_live_transport_still_knows_only_bitcoin(self):
        with self.assertRaises(ValueError):
            p.alpaca_live_https_request("GET", p.ALPACA_LIVE_HOST, "/v2/positions/VIXM", None,
                                        5.0, credentials())


class BitcoinUnchangedTests(unittest.TestCase):
    def test_the_bitcoin_terms_are_the_old_literals_and_allow_both_environments(self):
        terms = instrument_contract.terms_for("BTC-USD")
        self.assertEqual((terms["max_capital_usd"], terms["max_exposure_usd"],
                          terms["risk_budget_usd"]), ("200", "50", "5"))
        self.assertEqual(terms["environments"], ["PAPER", "LIVE"])
        self.assertEqual(terms["symbol"], "BTC/USD")
        self.assertFalse(terms["can_short"])
        self.assertEqual(instrument_contract.exposure_cap(terms, "ENTER"),
                         instrument_contract.exposure_cap(terms, "EXIT"))

    def test_a_bitcoin_proposal_made_before_the_contract_is_still_long_only(self):
        for action, side, expected in (("ENTER", "BUY", True), ("EXIT", "SELL", True),
                                       ("ENTER", "SELL", False), ("EXIT", "BUY", False)):
            proposal = {"instrument": "BTC-USD", "action": action, "side": side}
            self.assertEqual(p._proposal_action_side_valid(proposal), expected, (action, side))

    def test_bitcoin_cannot_be_given_a_direction_it_cannot_take(self):
        proposal = {"instrument": "BTC-USD", "action": "ENTER", "side": "SELL",
                    "direction": "SHORT"}
        self.assertFalse(p._proposal_action_side_valid(proposal))

    def test_the_bitcoin_order_body_is_exactly_what_it_was(self):
        request = {"instrument": "BTC-USD", "order_type": "LIMIT", "quantity": "5",
                   "side": "buy", "limit_price": "10", "identity": "ALPACA_REQUEST|x"}
        body = p._paper_order_body(request)
        self.assertEqual((body["symbol"], body["time_in_force"], body["type"]),
                         ("BTC/USD", "gtc", "limit"))
        self.assertNotIn("order_class", body)

    def test_an_unknown_instrument_has_no_order_body(self):
        with self.assertRaises(ValueError):
            p._paper_order_body({"instrument": "AAPL", "order_type": "LIMIT"})


if __name__ == "__main__":
    unittest.main()
