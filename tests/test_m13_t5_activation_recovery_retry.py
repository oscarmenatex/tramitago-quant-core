"""Focused tests for M1.3-T5: recovery of an interrupted M1.2 binding, and
the bounded, result-dependent retry policy wrapping the T4 runner."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import pipeline as p


class M13T5ActivationRecoveryRetryTests(unittest.TestCase):
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
            "root": root,
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
            "session_id": "PAPER_SESSION|m13-t6|" + root.name,
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

    def invoke(self, case=None, *, processing=None, owner_id=None, transport=None):
        case = self.case if case is None else case
        return p.run_forward_paper_activation(
            *self.t4_path_args(case),
            self.PROCESSING if processing is None else processing,
            str(uuid.uuid4()) if owner_id is None else owner_id,
            transport=transport)

    def attempt(self, case=None, *, processing=None, owner_id=None, transport=None):
        case = self.case if case is None else case
        return p.attempt_forward_paper_activation(
            *self.t4_path_args(case),
            self.PROCESSING if processing is None else processing,
            str(uuid.uuid4()) if owner_id is None else owner_id,
            transport=transport)

    def _fake_m12(self, case, terminal, *, also_write_result=True):
        def fake(*args, **kwargs):
            preparation = p.load_forward_paper_preparation(
                case["session"], case["configuration"], case["invocation"])
            content = {
                "configuration_id": preparation["configuration"]["configuration_id"],
                "canonical_cycle_id": None,
                "dataset_id": None,
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
            if also_write_result:
                case["result"].write_bytes(p.encoded(record))
            return {"status": "PASS", **record}
        return fake

    def test_crash_after_m12_persists_is_recovered_without_a_second_m12_call(self):
        crashing = self._fake_m12(self.case, "COMPLETED")

        def crash_after_persist(*args, **kwargs):
            result = crashing(*args, **kwargs)
            raise RuntimeError("simulated crash after M1.2 persisted its result")

        with patch.object(p, "run_forward_paper_invocation", side_effect=crash_after_persist):
            first = self.invoke()
        self.assertEqual(first["status"], "RECOVERY_REQUIRED")
        self.assertEqual(first["receipt_status"], "M12_BOUND")

        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=AssertionError("must not call M1.2 again")):
            recovered = self.invoke(owner_id=str(uuid.uuid4()))
        self.assertEqual(recovered["status"], "PASS")
        self.assertEqual(recovered["receipt_status"], "M12_RESULT_RECORDED")
        self.assertEqual(recovered["m12_terminal_result"], "COMPLETED")
        self.assertEqual(recovered["recovery_source"], "PERSISTED_M12_RESULT")
        self.assertTrue(recovered["replay"])
        self.assertTrue(recovered["lease_released"])
        ledger = self.ledger_record()
        self.assertEqual(ledger["lease_status"], "RELEASED")

    def ledger_record(self, case=None):
        case = self.case if case is None else case
        activation = p.evaluate_forward_paper_activation(
            p.load_forward_paper_activation_policy(case["policy"], case["configuration"]),
            p.load_forward_paper_configuration(case["session"], case["configuration"]),
            self.PROCESSING)
        return p.load_forward_paper_activation_ledger(
            case["ledger"], activation, case["policy"], case["configuration"])

    def test_still_in_flight_interruption_stays_recovery_required_without_retry(self):
        def interrupted(*args, **kwargs):
            raise RuntimeError("simulated interruption before any M1.2 persistence")

        with patch.object(p, "run_forward_paper_invocation", side_effect=interrupted):
            first = self.invoke()
        self.assertEqual(first["status"], "RECOVERY_REQUIRED")

        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=AssertionError("must not call M1.2 again")):
            pending = self.invoke(owner_id=str(uuid.uuid4()))
        self.assertEqual(pending["status"], "RECOVERY_REQUIRED")
        self.assertEqual(pending["receipt"], first["receipt"])

    def test_recoverable_error_is_retried_with_backoff_then_exhausts(self):
        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=self._fake_m12(self.case, "RECOVERABLE_ERROR")):
            first = self.attempt(processing="2026-09-18T00:15:00Z")
        self.assertEqual(first["status"], "PASS")
        self.assertEqual(first["m12_terminal_result"], "RECOVERABLE_ERROR")
        self.assertEqual(first["attempt_number"], 1)
        self.assertEqual(first["attempts_used"], 1)
        self.assertEqual(first["next_retry_at_utc"], "2026-09-18T00:16:00Z")
        self.assertFalse(first["operator_action_required"])

        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=AssertionError("backoff not elapsed yet")):
            too_soon = self.attempt(processing="2026-09-18T00:15:30Z")
        self.assertEqual(too_soon["reason"], "T5_BACKOFF_NOT_ELAPSED")
        self.assertEqual(too_soon["attempts_used"], 1)

        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=self._fake_m12(self.case, "RECOVERABLE_ERROR")):
            second = self.attempt(processing="2026-09-18T00:16:00Z")
        self.assertEqual(second["attempt_number"], 2)
        self.assertEqual(second["next_retry_at_utc"], "2026-09-18T00:21:00Z")

        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=self._fake_m12(self.case, "RECOVERABLE_ERROR")):
            third = self.attempt(processing="2026-09-18T00:21:00Z")
        self.assertEqual(third["attempt_number"], 3)
        self.assertEqual(third["status"], "RECOVERABLE_ERROR")
        self.assertEqual(third["next_retry_at_utc"], None)
        self.assertTrue(third["operator_action_required"])
        self.assertEqual(third["reason"], "T5_MAX_ATTEMPTS_EXHAUSTED")

        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=AssertionError("attempts already exhausted")):
            fourth = self.attempt(processing="2026-09-18T00:30:00Z")
        self.assertEqual(fourth["reason"], "T5_MAX_ATTEMPTS_EXHAUSTED")
        self.assertEqual(fourth["attempts_used"], 3)

    def test_completed_is_never_retried_and_blocked_is_never_retried(self):
        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=self._fake_m12(self.case, "COMPLETED")):
            completed = self.attempt()
        self.assertEqual(completed["status"], "PASS")
        self.assertFalse(completed["operator_action_required"])

        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=AssertionError("must not repeat a completed activation")):
            replay = self.attempt(owner_id=str(uuid.uuid4()))
        self.assertEqual(replay["status"], "PASS")
        self.assertEqual(replay["attempts_used"], 1)

        blocked_case = self._make_case(self.root / "blocked-case")
        state = json.loads(blocked_case["session"].read_bytes())
        state["mode"] = "LIVE"
        blocked_case["session"].write_bytes(p.encoded(state))
        blocked = self.attempt(blocked_case)
        self.assertEqual(blocked["status"], "BLOCKED")
        self.assertTrue(blocked["operator_action_required"])
        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=AssertionError("must not retry a BLOCKED activation")):
            blocked_again = self.attempt(blocked_case, owner_id=str(uuid.uuid4()))
        self.assertEqual(blocked_again["status"], "BLOCKED")

    def test_nothing_due_short_circuits_before_touching_attempts(self):
        result = self.attempt(processing="2026-09-17T00:00:00Z")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["activation_result"], "NOTHING_DUE")
        self.assertEqual(result["attempts_used"], 0)
        self.assertFalse(
            self.case["receipts"].exists()
            and any(self.case["receipts"].glob("*attempts.json")))


if __name__ == "__main__":
    unittest.main()
