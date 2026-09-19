"""Focused tests for M1.3-T6: minimal, read-only operator-consultable status.

Note on numbering: this file targets forward_paper_activation_status, the
task labelled M1.3-T6 ("exponer evidencia operativa minima") in the current
task tracking. The recovery/retry work landing in PR #22 was authored under
the branch name codex/m1.3-t5-... but its own test file was named
test_m13_t6_activation_recovery_retry.py before this corrected numbering was
confirmed -- that mismatch is pre-existing and out of scope here.
"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import pipeline as p


class M13T6ForwardPaperActivationStatusTests(unittest.TestCase):
    PROCESSING = "2026-09-18T00:15:00Z"
    STARTED = "2026-09-14T00:00:00Z"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.case = self._make_case(self.root / "case")

    def _make_case(self, root):
        root.mkdir(parents=True, exist_ok=True)
        case = {
            "session": root / "session" / "state.json",
            "configuration": root / "configuration.json",
            "invocation": root / "invocation.json",
            "policy": root / "policy.json",
            "ledger": root / "activation-ledger",
            "receipts": root / "activation-receipts",
            "dataset": root / "dataset.json",
            "selection": root / "selection.json",
            "fixture": root / "fixture.json",
            "acceptance": root / "acceptance.json",
            "indicator": root / "indicator.json",
            "cycle": root / "cycle.json",
            "result": root / "m12-invocation-result.json",
            "output": root / "output",
            "session_id": "PAPER_SESSION|m13-t6-status|" + root.name,
        }
        p.prepare_forward_paper_invocation(
            case["session"], case["configuration"], case["invocation"],
            case["session_id"], self.STARTED, self.PROCESSING)
        p.prepare_forward_paper_activation_policy(case["policy"], case["configuration"])
        return case

    def t4_path_args(self, case=None):
        case = self.case if case is None else case
        return [
            str(case["policy"]), str(case["configuration"]), str(case["session"]),
            str(case["invocation"]), str(case["ledger"]), str(case["receipts"]),
            str(case["dataset"]), str(case["selection"]), str(case["fixture"]),
            str(case["acceptance"]), str(case["indicator"]), str(case["cycle"]),
            str(case["result"]), str(case["output"]),
        ]

    def attempt(self, case=None, *, processing=None, owner_id=None, transport=None):
        case = self.case if case is None else case
        return p.attempt_forward_paper_activation(
            *self.t4_path_args(case),
            self.PROCESSING if processing is None else processing,
            str(uuid.uuid4()) if owner_id is None else owner_id,
            transport=transport)

    def status(self, case=None, *, now=None):
        case = self.case if case is None else case
        return p.forward_paper_activation_status(
            case["policy"], case["configuration"], case["ledger"], case["receipts"],
            self.PROCESSING if now is None else now)

    def _fake_m12(self, case, terminal):
        def fake(*args, **kwargs):
            preparation = p.load_forward_paper_preparation(
                case["session"], case["configuration"], case["invocation"])
            content = {
                "configuration_id": preparation["configuration"]["configuration_id"],
                "canonical_cycle_id": None, "dataset_id": None,
                "evidence": {"test_double": True},
                "invocation_id": preparation["invocation"]["invocation_id"],
                "mode": "FORWARD_PAPER",
                "processing_instant_utc":
                    preparation["invocation"]["processing_instant_utc"],
                "reason": "deterministic M1.2 public-entrypoint double",
                "selection_receipt_id": None,
                "session_id": preparation["session"]["session_id"],
                "terminal_result": terminal,
            }
            record = {
                "schema_version": p.FORWARD_PAPER_INVOCATION_RESULT_SCHEMA_VERSION,
                **content,
                "invocation_result_id": "FORWARD_PAPER_INVOCATION_RESULT|"
                + p.digest(p.encoded(content)),
            }
            case["result"].write_bytes(p.encoded(record))
            return {"status": "PASS", **record}
        return fake

    def test_never_calls_m12(self):
        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=AssertionError("status must never call M1.2")):
            status = self.status()
        self.assertEqual(status["status"], "PASS")

    def test_before_slot_is_nothing_due_with_next_scheduled_and_no_lease(self):
        status = self.status(now="2026-09-17T00:00:00Z")
        self.assertEqual(status["last_result"], "NOTHING_DUE")
        self.assertIsNone(status["reason"])
        self.assertFalse(status["lease_active"])
        self.assertIsNone(status["lease_owner_id"])
        self.assertEqual(status["attempts_used"], 0)
        self.assertEqual(status["next_scheduled_for_utc"], "2026-09-17T00:15:00Z")

    def test_completed_activation_is_visible_and_lease_is_released(self):
        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=self._fake_m12(self.case, "COMPLETED")):
            self.attempt()
        status = self.status(now="2026-09-18T00:20:00Z")
        self.assertEqual(status["last_result"], "COMPLETED")
        self.assertIsNone(status["reason"])
        self.assertEqual(status["attempts_used"], 1)
        self.assertFalse(status["lease_active"])

    def test_blocked_activation_shows_reason_without_lease(self):
        state = json.loads(self.case["session"].read_bytes())
        state["mode"] = "LIVE"
        self.case["session"].write_bytes(p.encoded(state))
        self.attempt()
        status = self.status(now="2026-09-18T00:20:00Z")
        self.assertEqual(status["last_result"], "BLOCKED")
        self.assertIsNotNone(status["reason"])
        self.assertFalse(status["lease_active"])

    def test_recoverable_error_shows_reason_and_active_lease_until_backoff(self):
        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=self._fake_m12(self.case, "RECOVERABLE_ERROR")):
            self.attempt(processing="2026-09-18T00:15:00Z")
        status = self.status(now="2026-09-18T00:15:10Z")
        self.assertEqual(status["last_result"], "RECOVERABLE_ERROR")
        self.assertEqual(status["attempts_used"], 1)
        self.assertIsNotNone(status["next_scheduled_for_utc"])

    def test_active_lease_is_visible_while_a_call_is_bound(self):
        def interrupted(*args, **kwargs):
            raise RuntimeError("simulated interruption while lease is held")

        with patch.object(p, "run_forward_paper_invocation", side_effect=interrupted):
            self.attempt()
        status = self.status(now="2026-09-18T00:15:05Z")
        self.assertEqual(status["last_result"], "RECOVERY_REQUIRED")
        self.assertTrue(status["lease_active"])
        self.assertIsNotNone(status["lease_owner_id"])
        self.assertIsNotNone(status["lease_expires_at_utc"])

    def test_status_writes_nothing(self):
        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=self._fake_m12(self.case, "COMPLETED")):
            self.attempt()
        receipts_before = {
            path: path.read_bytes() for path in self.case["receipts"].rglob("*") if path.is_file()}
        self.status(now="2026-09-18T01:00:00Z")
        receipts_after = {
            path: path.read_bytes() for path in self.case["receipts"].rglob("*") if path.is_file()}
        self.assertEqual(receipts_before, receipts_after)


if __name__ == "__main__":
    unittest.main()
