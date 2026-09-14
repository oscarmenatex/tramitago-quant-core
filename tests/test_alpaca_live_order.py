"""Offline proof for the isolated Alpaca LIVE transport boundary."""

import json
import unittest
from unittest.mock import patch

import pipeline as p
from tests import test_alpaca_request as request_tests


def credentials():
    return lambda headers: {**headers, "APCA-API-KEY-ID": "local-double",
                            "APCA-API-SECRET-KEY": "local-double"}


def broker_response(code, payload=None):
    return {"status_code": code, "content_type": "application/json" if payload else None,
            "location": None, "body": p.encoded(payload) if payload else b""}


class LiveTransportDouble:
    def __init__(self, replies, before=None):
        self.replies = list(replies)
        self.before = before
        self.calls = []

    def __call__(self, method, host, path, body, timeout, injector):
        self.calls.append((method, host, path, body, timeout))
        if self.before:
            self.before()
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


class AlpacaLiveOrderTests(unittest.TestCase):
    def setUp(self):
        self.builder = request_tests.AlpacaRequestPreparationTests()
        self.builder.setUp()
        self.addCleanup(self.builder.temporary.cleanup)

    def request(self, name, environment="LIVE"):
        root, proposal, revalidation, config = self.builder.state(name)
        request = self.builder.prepare(
            root, proposal, revalidation, config,
            environment=environment)["prepared_request"]
        return root, request

    @staticmethod
    def accepted(request, status="new", filled="0"):
        body = p._live_order_body(request)
        return broker_response(200, {
            "id": "live-order-001", "client_order_id": body["client_order_id"],
            "symbol": "BTC/USD", "status": status, "filled_qty": filled,
            "filled_avg_price": "10" if filled != "0" else None,
            "submitted_at": "2024-01-03T14:05:00Z",
            "updated_at": "2024-01-03T14:05:00Z",
        })

    def submit(self, root, request, transport, output="submit",
               attempted_at="2024-01-03T14:05:00Z", provider=credentials):
        return p.execute_alpaca_live_order(
            root / "state.json", root / output, request["identity"], attempted_at,
            5.0, provider, transport)

    def test_valid_live_attempt_is_persisted_before_transport_and_accepted(self):
        root, request = self.request("accepted")

        def before():
            attempt = json.loads((root / "state.json").read_bytes())["alpaca_live_order_attempts"][0]
            self.assertEqual(attempt["state"], "SEND_ATTEMPTED")
            self.assertFalse(attempt["live_request_sent"])
            self.assertEqual(attempt["environment"], "LIVE")

        transport = LiveTransportDouble([self.accepted(request)], before)
        result = self.submit(root, request, transport)
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertTrue(result["live_request_sent"])
        self.assertFalse(result["paper_request_sent"])
        method, host, path, body, _ = transport.calls[0]
        self.assertEqual((method, host, path), ("POST", p.ALPACA_LIVE_HOST, "/v2/orders"))
        self.assertEqual(body["symbol"], "BTC/USD")
        self.assertTrue(body["client_order_id"].startswith("tg-p4-live-"))
        persisted = (root / "state.json").read_text()
        self.assertNotIn("local-double", persisted)

    def test_paper_missing_ambiguous_stale_and_invalid_requests_never_reach_live(self):
        root, paper = self.request("paper", "PAPER")
        transport = LiveTransportDouble([self.accepted(paper)])
        self.assertEqual(self.submit(root, paper, transport)["status"], "BLOCKED_REQUEST")
        self.assertEqual(transport.calls, [])

        mutations = (
            lambda state: state["alpaca_prepared_requests"][0].update(state="SENT"),
            lambda state: state["alpaca_prepared_requests"][0].update(target_environment=None),
            lambda state: state["alpaca_prepared_requests"][0].update(target_environment="UNKNOWN"),
            lambda state: state["real_order_proposals"][0].update(status="PENDING"),
            lambda state: state["risk_revalidations"][0].update(result="BLOCKED_RISK_REVALIDATION"),
            lambda state: state["real_order_proposals"][0].update(instrument="ETH-USD"),
            lambda state: state["alpaca_prepared_requests"][0]["payload"].update(quantity="999"),
            lambda state: state["alpaca_prepared_requests"][0]["payload"].update(limit_price="0"),
            lambda state: state["alpaca_prepared_requests"][0]["payload"].update(exposure_usd="51"),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                root, request = self.request(f"invalid-{index}")
                state = json.loads((root / "state.json").read_bytes())
                mutate(state)
                (root / "state.json").write_bytes(p.encoded(state))
                transport = LiveTransportDouble([self.accepted(request)])
                self.assertEqual(self.submit(root, request, transport)["status"],
                                 "BLOCKED_REQUEST")
                self.assertEqual(transport.calls, [])

        root, request = self.request("stale")
        transport = LiveTransportDouble([self.accepted(request)])
        self.assertEqual(self.submit(
            root, request, transport, attempted_at="2024-01-03T14:15:01Z")["status"],
            "BLOCKED_REQUEST")
        self.assertEqual(transport.calls, [])

    def test_absent_and_invalid_credentials_block_before_attempt(self):
        providers = (lambda: None, lambda: (_ for _ in ()).throw(ValueError("secret")))
        for index, provider in enumerate(providers):
            root, request = self.request(f"credentials-{index}")
            transport = LiveTransportDouble([self.accepted(request)])
            result = self.submit(root, request, transport, provider=provider)
            self.assertEqual(result["status"], "BLOCKED_CREDENTIALS")
            self.assertEqual(transport.calls, [])
            self.assertNotIn("alpaca_live_order_attempts",
                             json.loads((root / "state.json").read_bytes()))
            self.assertNotIn("secret", (root / "state.json").read_text())

    def test_rejection_unknown_and_replay_are_persisted_without_resend(self):
        cases = ((broker_response(422, {"message": "rejected"}), "REJECTED"),
                 (TimeoutError("secret-text"), "UNKNOWN"),
                 (RuntimeError("secret-text"), "UNKNOWN"),
                 (KeyboardInterrupt(), "UNKNOWN"),
                 (broker_response(200, {"status": "new"}), "UNKNOWN"))
        for index, (reply, expected) in enumerate(cases):
            root, request = self.request(f"result-{index}")
            first_transport = LiveTransportDouble([reply])
            first = self.submit(root, request, first_transport)
            self.assertEqual(first["status"], expected)
            replay_transport = LiveTransportDouble([self.accepted(request)])
            replay = self.submit(root, request, replay_transport, output="replay")
            self.assertEqual(replay["status"], expected)
            self.assertEqual(replay_transport.calls, [])
            self.assertNotIn("secret-text", (root / "state.json").read_text())

    def test_restart_recovers_incomplete_send_as_unknown_without_transport(self):
        root, request = self.request("restart")
        body = p._live_order_body(request)
        content = {"request_id": request["identity"],
                   "request_hash": p.digest(p.encoded(request)),
                   "payload_hash": request["payload_sha256"],
                   "client_order_id": body["client_order_id"]}
        state = json.loads((root / "state.json").read_bytes())
        state["alpaca_live_order_attempts"] = [{
            "identity": "ALPACA_LIVE_ORDER_ATTEMPT|" + p.digest(p.encoded(content)),
            **content, "environment": "LIVE", "state": "SEND_ATTEMPTED",
            "result": None}]
        (root / "state.json").write_bytes(p.encoded(state))
        transport = LiveTransportDouble([self.accepted(request)])
        result = self.submit(root, request, transport)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(transport.calls, [])

    def test_unknown_is_queried_by_client_id_and_unknown_cancel_is_not_retried(self):
        root, request = self.request("unknown-query")
        attempt = self.submit(
            root, request, LiveTransportDouble([TimeoutError()]))["attempt"]
        recovered = json.loads(self.accepted(request, "partially_filled", "2")["body"])
        query_transport = LiveTransportDouble([broker_response(200, recovered)])
        observed = p.observe_alpaca_live_order(
            root / "state.json", root / "query", attempt["identity"],
            "2024-01-03T14:06:00Z", 5, credentials, query_transport)
        self.assertEqual(observed["status"], "OBSERVED")
        self.assertTrue(query_transport.calls[0][2].startswith(
            "/v2/orders:by_client_order_id?client_order_id=tg-p4-live-"))
        cancel_transport = LiveTransportDouble([TimeoutError()])
        cancelled = p.cancel_alpaca_live_remainder(
            root / "state.json", root / "cancel", attempt["identity"],
            "2024-01-03T14:07:00Z", 5, credentials, cancel_transport)
        self.assertEqual(cancelled["status"], "UNKNOWN")
        self.assertTrue(cancelled["cancellation"]["reconciliation_required"])
        replay_transport = LiveTransportDouble([])
        replay = p.cancel_alpaca_live_remainder(
            root / "state.json", root / "cancel-replay", attempt["identity"],
            "2024-01-03T14:08:00Z", 5, credentials, replay_transport)
        self.assertEqual(replay["status"], "UNKNOWN")
        self.assertEqual(replay_transport.calls, [])

    def test_order_fill_position_and_partial_cancel_paths_are_linked_and_idempotent(self):
        root, request = self.request("lifecycle")
        attempt = self.submit(
            root, request, LiveTransportDouble([self.accepted(request)]))["attempt"]
        filled = json.loads(self.accepted(request, "filled", "5")["body"])
        order_transport = LiveTransportDouble([broker_response(200, filled)])
        observed = p.observe_alpaca_live_order(
            root / "state.json", root / "order", attempt["identity"],
            "2024-01-03T14:06:00Z", 5, credentials, order_transport)
        self.assertEqual(observed["observation"]["order"]["filled_qty"], "5")
        self.assertEqual(observed["observation"]["reconciliation_status"],
                         "FILLED_AWAITING_POSITION")
        position_transport = LiveTransportDouble([broker_response(200, {
            "symbol": "BTC/USD", "qty": "5", "side": "long",
            "market_value": "50", "avg_entry_price": "10"})])
        position = p.observe_alpaca_live_position(
            root / "state.json", root / "position", attempt["identity"],
            "2024-01-03T14:07:00Z", 5, credentials, position_transport)
        self.assertEqual(position["status"], "OBSERVED")
        self.assertFalse(position["observation"]["internal_target_position_changed"])
        self.assertFalse(position["observation"]["virtual_position_changed"])

        partial_root, partial_request = self.request("partial")
        partial_attempt = self.submit(
            partial_root, partial_request,
            LiveTransportDouble([self.accepted(partial_request)]))["attempt"]
        partial_payload = json.loads(
            self.accepted(partial_request, "partially_filled", "2")["body"])
        p.observe_alpaca_live_order(
            partial_root / "state.json", partial_root / "partial-order",
            partial_attempt["identity"], "2024-01-03T14:06:00Z", 5,
            credentials, LiveTransportDouble([broker_response(200, partial_payload)]))
        cancel_transport = LiveTransportDouble([broker_response(204)])
        cancelled = p.cancel_alpaca_live_remainder(
            partial_root / "state.json", partial_root / "cancel",
            partial_attempt["identity"], "2024-01-03T14:07:00Z", 5,
            credentials, cancel_transport)
        self.assertEqual(cancelled["status"], "CANCELLED")
        self.assertFalse(cancelled["cancellation"]["quantity_increased"])
        self.assertFalse(cancelled["cancellation"]["new_order_created"])
        replay_transport = LiveTransportDouble([])
        p.cancel_alpaca_live_remainder(
            partial_root / "state.json", partial_root / "cancel-replay",
            partial_attempt["identity"], "2024-01-03T14:08:00Z", 5,
            credentials, replay_transport)
        self.assertEqual(replay_transport.calls, [])

    def test_https_boundary_rejects_paper_and_injects_only_live_credentials(self):
        calls = []

        class Response:
            status = 200

            @staticmethod
            def getheader(name):
                return "application/json" if name == "Content-Type" else None

            @staticmethod
            def read(_limit):
                return b"{}"

        class Connection:
            def __init__(self, host, timeout):
                calls.append(("connect", host, timeout))

            def request(self, method, path, body, headers):
                calls.append((method, path, json.loads(body), headers))

            @staticmethod
            def getresponse():
                return Response()

            @staticmethod
            def close():
                pass

        with patch("pipeline.HTTPSConnection", Connection):
            response = p.alpaca_live_https_request(
                "POST", p.ALPACA_LIVE_HOST, "/v2/orders", {"symbol": "BTC/USD"},
                5, credentials())
        self.assertEqual(response["status_code"], 200)
        self.assertEqual(calls[0], ("connect", p.ALPACA_LIVE_HOST, 5))
        with self.assertRaisesRegex(ValueError, "forbidden target"):
            p.alpaca_live_https_request(
                "POST", p.ALPACA_PAPER_HOST, "/v2/orders", {}, 5, credentials())


if __name__ == "__main__":
    unittest.main()
