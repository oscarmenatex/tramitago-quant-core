"""Offline proof for the local transport-attempt boundary."""

import json
from unittest.mock import patch
import unittest

import pipeline as p
from tests import test_alpaca_request as request_tests


class CountingTransport:
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


class LocalTransportBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.builder = request_tests.AlpacaRequestPreparationTests()
        self.builder.setUp()
        self.addCleanup(self.builder.temporary.cleanup)

    def request(self, name):
        root, proposal, revalidation, config = self.builder.state(name)
        prepared = self.builder.prepare(
            root, proposal, revalidation, config)["prepared_request"]
        return root, prepared

    def execute(self, root, request, transport, output="attempt"):
        return p.execute_local_transport_attempt(
            root / "state.json", root / output, request["identity"],
            "2024-01-03T15:00:00Z", transport)

    def test_attempt_is_persisted_before_mock_and_acceptance_is_recovered(self):
        root, request = self.request("accepted")
        before = json.loads((root / "state.json").read_bytes())

        def assert_registered(_):
            state = json.loads((root / "state.json").read_bytes())
            self.assertEqual(state["local_transport_attempts"][0]["status"],
                             "ATTEMPT_RECORDED")
            self.assertFalse(state["local_transport_attempts"][0]["mock_transport_invoked"])

        transport = CountingTransport("MOCK_ACCEPTED", assert_registered)
        with patch("pipeline.urlopen", side_effect=AssertionError("network forbidden")):
            result = self.execute(root, request, transport)
        self.assertEqual((transport.calls, result["status"]), (1, "MOCK_ACCEPTED"))
        attempt = result["attempt"]
        self.assertTrue(attempt["result_persisted"])
        self.assertFalse(attempt["real_transport_used"])
        self.assertFalse(attempt["broker_request_sent"])
        recovered = json.loads((root / "state.json").read_bytes())
        self.assertEqual(recovered.pop("local_transport_attempts"), [attempt])
        self.assertEqual(recovered, before)

    def test_mock_rejection_is_terminal_and_replay_does_not_invoke(self):
        root, request = self.request("rejected")
        first_transport = CountingTransport("MOCK_REJECTED")
        first = self.execute(root, request, first_transport, "first")
        replay_transport = CountingTransport("MOCK_ACCEPTED")
        replay = self.execute(root, request, replay_transport, "replay")
        self.assertEqual(first, replay)
        self.assertEqual(first["status"], "MOCK_REJECTED")
        self.assertEqual((first_transport.calls, replay_transport.calls), (1, 0))

    def test_errors_timeouts_interruptions_and_incomplete_responses_are_unknown(self):
        cases = (RuntimeError("local error"), TimeoutError("local timeout"),
                 KeyboardInterrupt(), "MOCK_ERROR", "MOCK_TIMEOUT", None)
        for index, response in enumerate(cases):
            with self.subTest(response=type(response).__name__):
                root, request = self.request(f"unknown-{index}")
                transport = CountingTransport(response)
                result = self.execute(root, request, transport)
                self.assertEqual(result["status"], "UNKNOWN")
                self.assertEqual(transport.calls, 1)
                replay = CountingTransport("MOCK_ACCEPTED")
                recovered = self.execute(root, request, replay, "replay")
                self.assertEqual(recovered["status"], "UNKNOWN")
                self.assertEqual(replay.calls, 0)

    def test_interrupted_result_persistence_recovers_unknown_without_resend(self):
        root, request = self.request("interrupted")
        transport = CountingTransport("MOCK_ACCEPTED")
        original = p._atomic_write
        calls = 0

        def fail_second(path, data):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated persistence interruption")
            return original(path, data)

        with patch("pipeline._atomic_write", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "persistence interruption"):
                self.execute(root, request, transport)
        recorded = json.loads((root / "state.json").read_bytes())
        self.assertEqual(recorded["local_transport_attempts"][0]["status"],
                         "ATTEMPT_RECORDED")
        replay = CountingTransport("MOCK_ACCEPTED")
        result = self.execute(root, request, replay, "recovery")
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual((transport.calls, replay.calls), (1, 0))

    def test_invalid_or_altered_request_never_creates_attempt(self):
        mutators = (
            lambda state: state["alpaca_prepared_requests"][0].update(state="PENDING"),
            lambda state: state["alpaca_prepared_requests"][0].update(
                target_environment="UNKNOWN"),
            lambda state: state["real_order_proposals"][0].update(status="REJECTED"),
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
                transport = CountingTransport("MOCK_ACCEPTED")
                result = self.execute(root, request, transport)
                self.assertEqual(result["status"], "BLOCKED_REQUEST")
                self.assertEqual(transport.calls, 0)
                self.assertNotIn("local_transport_attempts",
                                 json.loads((root / "state.json").read_bytes()))

    def test_attempt_identity_idempotence_and_independent_determinism(self):
        first, request_a = self.request("first")
        second, request_b = self.request("second")
        result = self.execute(first, request_a, CountingTransport("MOCK_ACCEPTED"))
        first_bytes = (first / "state.json").read_bytes()
        other = self.execute(second, request_b, CountingTransport("MOCK_ACCEPTED"))
        self.assertEqual(result, other)
        self.assertEqual(first_bytes, (second / "state.json").read_bytes())
        replay = CountingTransport("MOCK_REJECTED")
        repeated = self.execute(first, request_a, replay, "replay")
        self.assertEqual(result, repeated)
        self.assertEqual(replay.calls, 0)
        self.assertEqual(len(json.loads(first_bytes)["local_transport_attempts"]), 1)


if __name__ == "__main__":
    unittest.main()
