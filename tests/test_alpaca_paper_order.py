"""Offline proof of the real Alpaca PAPER order boundary."""

import json
import unittest
from unittest.mock import patch

import pipeline as p
from tests import test_alpaca_request as request_tests


def credentials():
    return lambda headers: {**headers, "APCA-API-KEY-ID": "memory-only",
                            "APCA-API-SECRET-KEY": "memory-only"}


def broker_response(code, payload=None):
    return {"status_code": code, "content_type": "application/json" if payload else None,
            "location": None, "body": p.encoded(payload) if payload else b""}


class TransportDouble:
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


class AlpacaPaperOrderTests(unittest.TestCase):
    def setUp(self):
        self.builder = request_tests.AlpacaRequestPreparationTests()
        self.builder.setUp()
        self.addCleanup(self.builder.temporary.cleanup)

    def request(self, name, environment="PAPER"):
        root, proposal, revalidation, config = self.builder.state(name)
        request = self.builder.prepare(root, proposal, revalidation, config,
                                       environment=environment)["prepared_request"]
        return root, request

    def accepted(self, request, status="new", filled="0"):
        body = p._paper_order_body(request)
        return broker_response(200, {
            "id": "paper-order-001", "client_order_id": body["client_order_id"],
            "symbol": "BTC/USD", "status": status, "filled_qty": filled,
            "filled_avg_price": None, "submitted_at": "2024-01-03T16:00:00Z",
            "updated_at": "2024-01-03T16:00:00Z",
        })

    def submit(self, root, request, transport, output="submit"):
        return p.execute_alpaca_paper_order(
            root / "state.json", root / output, request["identity"],
            "2024-01-03T16:00:00Z", 5.0, credentials, transport)

    def test_paper_is_pre_persisted_then_accepted_with_sanitized_evidence(self):
        root, request = self.request("accepted")

        def before():
            attempt = json.loads((root / "state.json").read_bytes())["alpaca_paper_order_attempts"][0]
            self.assertEqual(attempt["status"], "SEND_ATTEMPTED")
            self.assertFalse(attempt["paper_request_sent"])

        transport = TransportDouble([self.accepted(request)], before)
        result = self.submit(root, request, transport)
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertTrue(result["paper_request_sent"])
        self.assertFalse(result["live_request_sent"])
        method, host, path, body, _ = transport.calls[0]
        self.assertEqual((method, host, path), ("POST", p.ALPACA_PAPER_HOST, "/v2/orders"))
        self.assertEqual(body["symbol"], "BTC/USD")
        self.assertLessEqual(float(request["exposure_usd"]), 50)
        persisted = (root / "state.json").read_text()
        self.assertNotIn("memory-only", persisted)

    def test_https_transport_is_paper_only_and_injects_credentials_in_memory(self):
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
            response = p.alpaca_paper_https_request(
                "POST", p.ALPACA_PAPER_HOST, "/v2/orders", {"symbol": "BTC/USD"},
                5, credentials())
        self.assertEqual(response["status_code"], 200)
        self.assertEqual(calls[0], ("connect", p.ALPACA_PAPER_HOST, 5))
        self.assertEqual(calls[1][3]["APCA-API-KEY-ID"], "memory-only")
        with self.assertRaisesRegex(ValueError, "forbidden target"):
            p.alpaca_paper_https_request(
                "POST", "api.alpaca.markets", "/v2/orders", {}, 5, credentials())

    def test_live_altered_and_unapproved_requests_are_blocked_without_send(self):
        root, request = self.request("live", "LIVE")
        transport = TransportDouble([self.accepted(request)])
        self.assertEqual(self.submit(root, request, transport)["status"], "BLOCKED_REQUEST")
        self.assertEqual(transport.calls, [])
        for index, mutate in enumerate((
                lambda state: state["real_order_proposals"][0].update(status="PENDING"),
                lambda state: state["alpaca_prepared_requests"][0]["payload"].update(quantity="999"))):
            root, request = self.request(f"bad-{index}")
            state = json.loads((root / "state.json").read_bytes())
            mutate(state)
            (root / "state.json").write_bytes(p.encoded(state))
            transport = TransportDouble([self.accepted(request)])
            self.assertEqual(self.submit(root, request, transport)["status"], "BLOCKED_REQUEST")
            self.assertEqual(transport.calls, [])

    def test_absent_or_invalid_credentials_block_without_attempt_or_send(self):
        for index, provider in enumerate((lambda: None, lambda: (_ for _ in ()).throw(ValueError()))):
            root, request = self.request(f"credentials-{index}")
            transport = TransportDouble([self.accepted(request)])
            result = p.execute_alpaca_paper_order(
                root / "state.json", root / "blocked", request["identity"],
                "2024-01-03T16:00:00Z", 5, provider, transport)
            self.assertEqual(result["status"], "BLOCKED_CREDENTIALS")
            self.assertEqual(transport.calls, [])
            self.assertNotIn("alpaca_paper_order_attempts",
                             json.loads((root / "state.json").read_bytes()))

    def test_reject_timeout_and_replay_are_persisted_without_resend(self):
        cases = ((broker_response(422, {"message": "rejected"}), "REJECTED"),
                 (TimeoutError("credential text"), "UNKNOWN"),
                 (KeyboardInterrupt(), "UNKNOWN"),
                 (broker_response(200, {"status": "new"}), "UNKNOWN"))
        for index, (reply, expected) in enumerate(cases):
            root, request = self.request(f"result-{index}")
            transport = TransportDouble([reply])
            result = self.submit(root, request, transport)
            self.assertEqual(result["status"], expected)
            replay = TransportDouble([self.accepted(request)])
            self.assertEqual(self.submit(root, request, replay, "replay")["status"], expected)
            self.assertEqual(replay.calls, [])
            self.assertNotIn("credential text", (root / "state.json").read_text())

    def test_restart_recovery_of_incomplete_attempt_is_unknown_without_send(self):
        root, request = self.request("recovery")
        content = {"request_id": request["identity"],
                   "request_hash": p.digest(p.encoded(request)),
                   "payload_hash": request["payload_sha256"], "client_order_id": "client"}
        state = json.loads((root / "state.json").read_bytes())
        state["alpaca_paper_order_attempts"] = [{
            "identity": "ALPACA_PAPER_ORDER_ATTEMPT|" + p.digest(p.encoded(content)),
            **content, "environment": "PAPER", "status": "SEND_ATTEMPTED"}]
        (root / "state.json").write_bytes(p.encoded(state))
        transport = TransportDouble([self.accepted(request)])
        result = self.submit(root, request, transport)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(transport.calls, [])

    def test_unknown_attempt_can_be_observed_by_client_id_without_resend(self):
        root, request = self.request("unknown-observation")
        attempt = self.submit(root, request, TransportDouble([TimeoutError()]))["attempt"]
        recovered = json.loads(self.accepted(request, "accepted")["body"])
        transport = TransportDouble([broker_response(200, recovered)])
        result = p.observe_alpaca_paper_order(
            root / "state.json", root / "lookup", attempt["identity"],
            "2024-01-03T16:01:00Z", 5, credentials, transport)
        self.assertEqual(result["status"], "OBSERVED")
        self.assertTrue(transport.calls[0][2].startswith(
            "/v2/orders:by_client_order_id?client_order_id=tg-p4-"))
        self.assertEqual(transport.calls[0][0], "GET")

    def test_status_cancel_and_position_are_persisted_and_idempotent(self):
        root, request = self.request("lifecycle")
        submit_transport = TransportDouble([self.accepted(request)])
        attempt = self.submit(root, request, submit_transport)["attempt"]
        order_payload = json.loads(self.accepted(request, "partially_filled", "2")["body"])
        status_transport = TransportDouble([broker_response(200, order_payload)])
        status = p.observe_alpaca_paper_order(
            root / "state.json", root / "status", attempt["identity"],
            "2024-01-03T16:01:00Z", 5, credentials, status_transport)
        self.assertEqual(status["observation"]["order"]["status"], "partially_filled")
        self.assertEqual(p.observe_alpaca_paper_order(
            root / "state.json", root / "status-replay", attempt["identity"],
            "2024-01-03T16:01:00Z", 5, credentials,
            TransportDouble([]))["status"], "OBSERVED")

        cancel_transport = TransportDouble([broker_response(204)])
        cancelled = p.cancel_alpaca_paper_remainder(
            root / "state.json", root / "cancel", attempt["identity"],
            "2024-01-03T16:02:00Z", 5, credentials, cancel_transport)
        self.assertEqual(cancelled["status"], "CANCELLED")
        self.assertFalse(cancelled["cancellation"]["quantity_increased"])
        replay_transport = TransportDouble([])
        p.cancel_alpaca_paper_remainder(
            root / "state.json", root / "cancel-replay", attempt["identity"],
            "2024-01-03T16:03:00Z", 5, credentials, replay_transport)
        self.assertEqual(replay_transport.calls, [])

        position_transport = TransportDouble([broker_response(404, {"message": "not found"})])
        position = p.observe_alpaca_paper_position(
            root / "state.json", root / "position", attempt["identity"],
            "2024-01-03T16:04:00Z", 5, credentials, position_transport)
        self.assertEqual(position["status"], "UNAVAILABLE")
        self.assertEqual(position["observation"]["error_category"], "NO_OPEN_POSITION")
        state = json.loads((root / "state.json").read_bytes())
        self.assertEqual(len(state["alpaca_paper_order_observations"]), 1)
        self.assertEqual(len(state["alpaca_paper_order_cancellations"]), 1)
        self.assertEqual(len(state["alpaca_paper_position_observations"]), 1)


if __name__ == "__main__":
    unittest.main()
