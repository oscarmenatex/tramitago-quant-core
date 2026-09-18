"""Negative and regression coverage for FORWARD_PAPER T7."""

import json
from pathlib import Path
from unittest.mock import patch
import tempfile
import unittest

import pipeline as p


class M12T7ForwardPaperNegativeRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.paths = {
            name: root / dirname / filename
            for name, (dirname, filename) in {
                "session": ("session", "state.json"),
                "configuration": ("configuration", "forward-paper.json"),
                "invocation": ("invocation", "invocation.json"),
                "dataset": ("dataset", "dataset.json"),
                "selection": ("selection", "selection.json"),
                "fixture": ("fixture", "fixture.json"),
                "acceptance": ("acceptance", "acceptance.json"),
                "indicator": ("indicator", "indicator.json"),
                "cycle": ("cycle", "cycle.json"),
                "result": ("result", "invocation-result.json"),
                "output": ("output", "unused"),
            }.items()
        }
        self.session_id = "PAPER_SESSION|m12-t7-negative-001"
        self.started_at = "2026-01-02T12:00:00Z"
        self.processing = "2026-01-05T12:00:00Z"
        p.prepare_forward_paper_invocation(
            self.paths["session"], self.paths["configuration"],
            self.paths["invocation"], self.session_id, self.started_at,
            self.processing)

    @staticmethod
    def valid_rows():
        return [[p.epoch(f"2026-01-{day:02d}T00:00:00Z"), day + 8, day + 12,
                 day + 9, day + 10, 1.0] for day in range(1, 6)]

    def _invoke(self, transport):
        return p.run_forward_paper_invocation(
            self.paths["session"], self.paths["configuration"],
            self.paths["invocation"], self.paths["dataset"],
            self.paths["selection"], self.paths["fixture"],
            self.paths["acceptance"], self.paths["indicator"],
            self.paths["cycle"], self.paths["result"], self.paths["output"],
            self.session_id, self.started_at, self.processing,
            "2026-01-05T12:01:00Z", transport=transport)

    def _state_bytes(self):
        return self.paths["session"].read_bytes()

    def _effects(self):
        state = p.load_paper_session(self.paths["session"])
        return {
            "processed_observations": len(state["processed_observations"]),
            "decisions": len(state["decisions"]),
            "paper_risk_evaluations": len(state.get("paper_risk_evaluations", [])),
            "executions": len(state["executions"]),
            "pending_actions": len(state["pending_actions"]),
            "broker_submissions": len(state["broker_submissions"]),
        }

    def assert_no_effects(self, state_before):
        self.assertEqual(self._state_bytes(), state_before)
        self.assertEqual(self._effects(), {
            "processed_observations": 0, "decisions": 0,
            "paper_risk_evaluations": 0, "executions": 0,
            "pending_actions": 0, "broker_submissions": 0})
        self.assertFalse(self.paths["dataset"].exists())
        self.assertFalse(self.paths["selection"].exists())
        self.assertFalse(self.paths["fixture"].exists())
        self.assertFalse(self.paths["cycle"].exists())

    def test_transport_failures_are_recoverable_without_partial_dataset(self):
        for error in (OSError("transport unavailable"),
                      TimeoutError("transport timeout")):
            with self.subTest(error=type(error).__name__):
                state_before = self._state_bytes()
                result = self._invoke(lambda *args, error=error: (_ for _ in ()).throw(error))
                self.assertEqual(result["terminal_result"], "RECOVERABLE_ERROR")
                self.assertTrue(self.paths["result"].exists())
                self.assertEqual(
                    p._load_forward_paper_invocation_result(self.paths["result"])
                    ["terminal_result"], "RECOVERABLE_ERROR")
                self.assert_no_effects(state_before)
                self.paths["result"].unlink()

    def test_invalid_payloads_are_blocked_without_completed_result(self):
        payloads = [b"", b"[1", b"not-json",
                    json.dumps(self.valid_rows()[:3]).encode("utf-8")]
        for payload in payloads:
            with self.subTest(payload=payload):
                state_before = self._state_bytes()
                result = self._invoke(lambda *args, payload=payload: payload)
                self.assertEqual(result["terminal_result"], "BLOCKED")
                self.assertEqual(
                    p._load_forward_paper_invocation_result(self.paths["result"])
                    ["terminal_result"], "BLOCKED")
                self.assert_no_effects(state_before)
                self.paths["result"].unlink()

    def test_invalid_ohlcv_and_duplicate_observations_are_blocked(self):
        rows = self.valid_rows()
        rows[0][1], rows[0][2] = 20, 10
        duplicate_rows = self.valid_rows()
        duplicate_rows[1][0] = duplicate_rows[0][0]
        for payload in (rows, duplicate_rows):
            with self.subTest(payload=payload):
                state_before = self._state_bytes()
                result = self._invoke(
                    lambda *args, payload=json.dumps(payload).encode("utf-8"): payload)
                self.assertEqual(result["terminal_result"], "BLOCKED")
                self.assert_no_effects(state_before)
                self.paths["result"].unlink()

    def test_invalid_processing_and_state_associations_block_locally(self):
        with self.assertRaises(ValueError):
            p.prepare_forward_paper_invocation(
                self.paths["session"], self.paths["configuration"],
                self.paths["invocation"], self.session_id, self.started_at, None)
        state_before = self._state_bytes()
        invocation = json.loads(self.paths["invocation"].read_bytes())
        invocation["configuration_id"] = "FORWARD_PAPER_CONFIGURATION|altered"
        self.paths["invocation"].write_bytes(p.encoded(invocation))
        result = self._invoke(
            lambda *args: (_ for _ in ()).throw(
                AssertionError("invalid association must not acquire"))
        )
        self.assertEqual(result["terminal_result"], "BLOCKED")
        self.assert_no_effects(state_before)

    def test_corrupt_state_blocks_without_transport(self):
        original = self._state_bytes()
        self.paths["session"].write_bytes(b"{not-json")
        result = self._invoke(
            lambda *args: (_ for _ in ()).throw(
                AssertionError("corrupt state must not acquire"))
        )
        self.assertEqual(result["terminal_result"], "BLOCKED")
        self.assertEqual(self._state_bytes(), b"{not-json")
        self.assertFalse(self.paths["dataset"].exists())
        self.paths["session"].write_bytes(original)

    def test_dataset_atomic_write_failure_is_recoverable_without_effects(self):
        state_before = self._state_bytes()
        original_atomic_write = p._atomic_write

        def fail_dataset(path, data):
            if Path(path) == self.paths["dataset"]:
                raise OSError("dataset persistence unavailable")
            return original_atomic_write(path, data)

        with patch.object(p, "_atomic_write", side_effect=fail_dataset):
            result = self._invoke(
                lambda *args: json.dumps(self.valid_rows()).encode("utf-8"))
        self.assertEqual(result["terminal_result"], "RECOVERABLE_ERROR")
        self.assert_no_effects(state_before)

    def test_tampered_completed_result_fails_closed_and_valid_result_replays(self):
        completed = self._invoke(
            lambda *args: json.dumps(self.valid_rows()).encode("utf-8"))
        self.assertEqual(completed["terminal_result"], "COMPLETED")
        original = self.paths["result"].read_bytes()
        tampered = json.loads(original)
        tampered["evidence"] = {}
        self.paths["result"].write_bytes(p.encoded(tampered))
        state_before = self._state_bytes()
        blocked = self._invoke(
            lambda *args: (_ for _ in ()).throw(
                AssertionError("tampered result must not acquire")))
        self.assertEqual(blocked["terminal_result"], "BLOCKED")
        self.assertEqual(self.paths["result"].read_bytes(), p.encoded(tampered))
        self.assertEqual(self._state_bytes(), state_before)
        self.paths["result"].write_bytes(original)
        replay = self._invoke(
            lambda *args: (_ for _ in ()).throw(
                AssertionError("valid completed result must not acquire"))
        )
        self.assertEqual(replay["terminal_result"], "COMPLETED")
        self.assertTrue(replay["replay"])


if __name__ == "__main__":
    unittest.main()
