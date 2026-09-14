"""Offline proof for the PAPER-only broker adapter boundary."""

import json
from unittest.mock import patch
import unittest

import pipeline as p
from tests import test_alpaca_request as request_tests


class PaperDouble:
    def __init__(self, response, before=None):
        self.response = response
        self.before = before
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        if self.before:
            self.before(request)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def response(result):
    return {
        "result": result,
        "category": f"MOCK_{result}",
        "external_id": "mock-order-001",
        "message": f"simulated {result.lower()}",
        "determinable": True,
        "simulated": True,
    }


class PaperAdapterBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.builder = request_tests.AlpacaRequestPreparationTests()
        self.builder.setUp()
        self.addCleanup(self.builder.temporary.cleanup)

    def request(self, name, environment="PAPER"):
        root, proposal, revalidation, config = self.builder.state(name)
        request = self.builder.prepare(
            root, proposal, revalidation, config,
            environment=environment)["prepared_request"]
        return root, request

    def execute(self, root, request, adapter, output="adapter"):
        return p.execute_paper_adapter_attempt(
            root / "state.json", root / output, request["identity"],
            "2024-01-03T16:00:00Z", adapter)

    def test_paper_attempt_is_persisted_before_adapter_and_acceptance_is_normalized(self):
        root, request = self.request("accepted")
        before = json.loads((root / "state.json").read_bytes())

        def assert_pre_persisted(_):
            attempt = json.loads((root / "state.json").read_bytes())["paper_adapter_attempts"][0]
            self.assertEqual(attempt["status"], "SEND_ATTEMPTED")
            self.assertEqual([item["status"] for item in attempt["state_history"]],
                             ["ATTEMPT_RECORDED", "SEND_ATTEMPTED"])
            self.assertFalse(attempt["paper_adapter_invoked"])

        adapter = PaperDouble(response("ACCEPTED"), assert_pre_persisted)
        with patch("pipeline.urlopen", side_effect=AssertionError("network forbidden")):
            result = self.execute(root, request, adapter)
        self.assertEqual((adapter.calls, result["status"]), (1, "ACCEPTED"))
        normalized = result["normalized_response"]
        self.assertTrue(normalized["simulated"])
        self.assertTrue(normalized["determinable"])
        self.assertEqual(normalized["environment"], "PAPER")
        self.assertFalse(result["paper_request_sent"])
        self.assertFalse(result["live_request_sent"])
        recovered = json.loads((root / "state.json").read_bytes())
        self.assertEqual(recovered["alpaca_prepared_requests"][0]["state"], "NOT_SENT")
        recovered.pop("paper_adapter_attempts")
        self.assertEqual(recovered, before)

    def test_rejection_is_persisted_and_cannot_become_acceptance(self):
        root, request = self.request("rejected")
        first_adapter = PaperDouble(response("REJECTED"))
        first = self.execute(root, request, first_adapter, "first")
        replay_adapter = PaperDouble(response("ACCEPTED"))
        replay = self.execute(root, request, replay_adapter, "replay")
        self.assertEqual(first, replay)
        self.assertEqual(first["status"], "REJECTED")
        self.assertEqual((first_adapter.calls, replay_adapter.calls), (1, 0))

    def test_timeout_exception_and_incomplete_response_become_unknown(self):
        cases = (TimeoutError("secret must not persist"),
                 RuntimeError("secret must not persist"),
                 KeyboardInterrupt(), {"result": "ACCEPTED"}, None)
        for index, candidate in enumerate(cases):
            with self.subTest(candidate=type(candidate).__name__):
                root, request = self.request(f"unknown-{index}")
                adapter = PaperDouble(candidate)
                result = self.execute(root, request, adapter)
                self.assertEqual(result["status"], "UNKNOWN")
                self.assertFalse(result["normalized_response"]["determinable"])
                self.assertNotIn(b"secret must not persist",
                                 (root / "state.json").read_bytes())
                replay = PaperDouble(response("ACCEPTED"))
                self.assertEqual(self.execute(root, request, replay, "replay")["status"],
                                 "UNKNOWN")
                self.assertEqual(replay.calls, 0)

    def test_live_ambiguous_and_invalid_provenance_are_blocked_before_adapter(self):
        root, live = self.request("live", environment="LIVE")
        adapter = PaperDouble(response("ACCEPTED"))
        result = self.execute(root, live, adapter)
        self.assertEqual(result["status"], "BLOCKED_REQUEST")
        self.assertEqual(adapter.calls, 0)
        self.assertNotIn("paper_adapter_attempts",
                         json.loads((root / "state.json").read_bytes()))

        mutators = (
            lambda state: state["alpaca_prepared_requests"][0].update(
                target_environment="UNKNOWN"),
            lambda state: state["real_order_proposals"][0].update(status="PENDING"),
            lambda state: state["real_order_proposals"][0].pop("approval_record"),
            lambda state: state["risk_revalidations"][0].update(
                result="BLOCKED_RISK_REVALIDATION"),
            lambda state: state["alpaca_prepared_requests"][0]["payload"].update(
                quantity="999"),
        )
        for index, mutate in enumerate(mutators):
            with self.subTest(index=index):
                root, request = self.request(f"blocked-{index}")
                state = json.loads((root / "state.json").read_bytes())
                mutate(state)
                (root / "state.json").write_bytes(p.encoded(state))
                adapter = PaperDouble(response("ACCEPTED"))
                result = self.execute(root, request, adapter)
                self.assertEqual(result["status"], "BLOCKED_REQUEST")
                self.assertEqual(adapter.calls, 0)

    def test_send_attempt_recovery_is_unknown_without_automatic_retry(self):
        root, request = self.request("interrupted")
        adapter = PaperDouble(response("ACCEPTED"))
        original = p._atomic_write
        calls = 0

        def fail_third(path, data):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("simulated response persistence interruption")
            return original(path, data)

        with patch("pipeline._atomic_write", side_effect=fail_third):
            with self.assertRaisesRegex(OSError, "persistence interruption"):
                self.execute(root, request, adapter)
        state = json.loads((root / "state.json").read_bytes())
        self.assertEqual(state["paper_adapter_attempts"][0]["status"], "SEND_ATTEMPTED")
        replay = PaperDouble(response("ACCEPTED"))
        result = self.execute(root, request, replay, "recovery")
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual((adapter.calls, replay.calls), (1, 0))

    def test_attempt_identity_idempotence_and_independent_determinism(self):
        first, request_a = self.request("first")
        second, request_b = self.request("second")
        result = self.execute(first, request_a, PaperDouble(response("ACCEPTED")))
        first_bytes = (first / "state.json").read_bytes()
        other = self.execute(second, request_b, PaperDouble(response("ACCEPTED")))
        self.assertEqual(result, other)
        self.assertEqual(first_bytes, (second / "state.json").read_bytes())
        replay = PaperDouble(response("REJECTED"))
        self.assertEqual(result, self.execute(first, request_a, replay, "replay"))
        self.assertEqual(replay.calls, 0)
        self.assertEqual(len(json.loads(first_bytes)["paper_adapter_attempts"]), 1)


if __name__ == "__main__":
    unittest.main()
