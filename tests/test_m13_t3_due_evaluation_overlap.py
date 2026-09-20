"""Focused tests for M1.3-T3 due evaluation and T2 lease overlap."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

import pipeline as p


class M13T3DueEvaluationOverlapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.session_path = self.root / "session" / "state.json"
        self.configuration_path = self.root / "configuration.json"
        self.invocation_path = self.root / "invocation.json"
        self.policy_path = self.root / "policy.json"
        self.ledger_directory = self.root / "activation-ledger"

        p.prepare_forward_paper_invocation(
            self.session_path,
            self.configuration_path,
            self.invocation_path,
            "PAPER_SESSION|m13-t3-due-evaluation",
            "2026-09-17T00:00:00Z",
            "2026-09-17T00:00:00Z",
        )
        p.prepare_forward_paper_activation_policy(
            self.policy_path, self.configuration_path)
        self.initial_bytes = self._contract_bytes()

        self.invocation_guard = patch.object(
            p, "run_forward_paper_invocation",
            side_effect=AssertionError("M1.2 invocation is out of scope"))
        self.http_guard = patch.object(
            p, "_coinbase_public_http_get",
            side_effect=AssertionError("Network access is out of scope"))
        self.invocation_guard.start()
        self.http_guard.start()
        self.addCleanup(self.invocation_guard.stop)
        self.addCleanup(self.http_guard.stop)

    def _contract_bytes(self):
        return {
            "state": self.session_path.read_bytes(),
            "configuration": self.configuration_path.read_bytes(),
            "invocation": self.invocation_path.read_bytes(),
            "policy": self.policy_path.read_bytes(),
        }

    def _evaluate(self, now, owner_id=None):
        return p.evaluate_forward_paper_activation_due(
            self.policy_path,
            self.configuration_path,
            self.session_path,
            self.invocation_path,
            self.ledger_directory,
            now,
            str(uuid.uuid4()) if owner_id is None else owner_id,
        )

    def _assert_result_contract(self, result):
        self.assertIn(result["result"], {"DUE", "NOTHING_DUE", "BLOCKED"})
        self.assertEqual(result["network_calls"], 0)
        self.assertFalse(result["credentials_used"])
        self.assertEqual(result["paper_orders_sent"], 0)
        self.assertEqual(result["live_orders_sent"], 0)

    def _assert_preparation_unchanged(self, before=None):
        self.assertEqual(self._contract_bytes(), self.initial_bytes if before is None else before)

    def _canonical_activation(self, now):
        policy = p.load_forward_paper_activation_policy(
            self.policy_path, self.configuration_path)
        configuration = p.load_forward_paper_configuration(
            self.session_path, self.configuration_path)
        return p.evaluate_forward_paper_activation(policy, configuration, now)

    def test_before_slot_is_nothing_due_without_activation_or_ledger(self):
        result = self._evaluate("2026-09-18T00:14:59Z")

        self._assert_result_contract(result)
        self.assertEqual(result["result"], "NOTHING_DUE")
        self.assertEqual(result["next_scheduled_for_utc"], "2026-09-18T00:15:00Z")
        self.assertNotIn("activation_id", result)
        self.assertNotIn("scheduled_for_utc", result)
        self.assertFalse(self.ledger_directory.exists())
        self._assert_preparation_unchanged()

    def test_due_boundary_and_later_same_slot_use_t1_and_t2_idempotently(self):
        owner_id = str(uuid.uuid4())
        now = "2026-09-18T00:15:00Z"
        canonical = self._canonical_activation(now)
        boundary = self._evaluate(now, owner_id)
        later = self._evaluate("2026-09-18T00:16:00Z", owner_id)

        self._assert_result_contract(boundary)
        self._assert_result_contract(later)
        self.assertEqual(boundary["result"], "DUE")
        self.assertEqual(boundary["lease_acquisition"], "ACQUIRED")
        self.assertEqual(boundary["policy_id"], canonical["policy_id"])
        self.assertEqual(boundary["configuration_id"], canonical["configuration_id"])
        self.assertEqual(boundary["scheduled_for_utc"], canonical["scheduled_for_utc"])
        self.assertEqual(boundary["activation_id"], canonical["activation_id"])
        self.assertEqual(boundary["owner_id"], owner_id)
        self.assertEqual(boundary["expires_at_utc"], "2026-09-18T00:30:00Z")
        self.assertEqual(boundary["next_scheduled_for_utc"], "2026-09-19T00:15:00Z")
        self.assertEqual(boundary["lease_evidence"]["lease_duration_seconds"], 900)
        self.assertEqual(boundary["lease_evidence"]["lease_status"], "ACTIVE")
        self.assertEqual(later["result"], "DUE")
        self.assertEqual(later["lease_acquisition"], "ALREADY_OWNED")
        self.assertEqual(later["activation_id"], boundary["activation_id"])
        self.assertEqual(later["expires_at_utc"], boundary["expires_at_utc"])
        acquired = [event for event in later["lease_evidence"]["event_history"]
                    if event["event_type"] == "LEASE_ACQUIRED"]
        self.assertEqual(len(acquired), 1)
        self._assert_preparation_unchanged()

    def test_other_owner_is_blocked_without_replacing_active_lease(self):
        first_owner, second_owner = str(uuid.uuid4()), str(uuid.uuid4())
        first = self._evaluate("2026-09-18T00:15:01Z", first_owner)
        second = self._evaluate("2026-09-18T00:16:00Z", second_owner)

        self._assert_result_contract(first)
        self._assert_result_contract(second)
        self.assertEqual(first["result"], "DUE")
        self.assertEqual(second["result"], "BLOCKED")
        self.assertEqual(second["reason"], "ACTIVE_LEASE")
        self.assertEqual(second["lease_evidence"]["lease_owner_id"], first_owner)
        self.assertEqual(second["lease_evidence"]["lease_status"], "ACTIVE")
        events = second["lease_evidence"]["event_history"]
        self.assertEqual([event["event_type"] for event in events],
                         ["LEASE_ACQUIRED", "LEASE_CONFLICT"])
        self.assertEqual(events[0]["owner_id"], first_owner)
        self.assertEqual(events[1]["owner_id"], second_owner)
        self._assert_preparation_unchanged()

    def test_two_processes_evaluating_same_activation_have_one_due(self):
        owners = [str(uuid.uuid4()), str(uuid.uuid4())]
        worker = r'''
import json, pathlib, sys, time
import pipeline as p
root = pathlib.Path(sys.argv[1])
owner_id = sys.argv[2]
(root / ("ready-" + owner_id)).write_text("ready", encoding="utf-8")
deadline = time.monotonic() + 15
while len(list(root.glob("ready-*"))) < 2:
    if time.monotonic() >= deadline:
        raise SystemExit("barrier timed out")
    time.sleep(0.01)
result = p.evaluate_forward_paper_activation_due(
    root / "policy.json", root / "configuration.json",
    root / "session" / "state.json", root / "invocation.json",
    root / "activation-ledger", "2026-09-18T00:15:01Z", owner_id)
print(json.dumps({"result": result["result"], "reason": result["reason"],
                  "activation_id": result.get("activation_id")}, sort_keys=True))
'''
        processes = [subprocess.Popen(
            [sys.executable, "-B", "-c", worker, str(self.root), owner_id],
            cwd=Path.cwd(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for owner_id in owners]
        outputs = [process.communicate(timeout=30) for process in processes]
        for process, (stdout, stderr) in zip(processes, outputs):
            self.assertEqual(process.returncode, 0, stderr)
        results = [json.loads(stdout) for stdout, _ in outputs]

        self.assertEqual(sum(item["result"] == "DUE" for item in results), 1)
        self.assertEqual(sum(item["result"] == "BLOCKED"
                             and item["reason"] == "ACTIVE_LEASE"
                             for item in results), 1)
        self.assertEqual(len({item["activation_id"] for item in results}), 1)
        ledger = p.load_forward_paper_activation_ledger(
            self.ledger_directory, self._canonical_activation("2026-09-18T00:15:01Z"),
            self.policy_path, self.configuration_path)
        self.assertEqual(ledger["lease_status"], "ACTIVE")
        self.assertEqual(sum(event["event_type"] == "LEASE_ACQUIRED"
                             for event in ledger["event_history"]), 1)
        self.assertEqual(sum(event["event_type"] == "LEASE_CONFLICT"
                             for event in ledger["event_history"]), 1)
        self._assert_preparation_unchanged()

    def test_new_owner_recovers_expired_lease_with_persisted_evidence(self):
        old_owner, new_owner = str(uuid.uuid4()), str(uuid.uuid4())
        first = self._evaluate("2026-09-18T00:15:01Z", old_owner)
        recovered = self._evaluate("2026-09-18T00:30:01Z", new_owner)

        self.assertEqual(first["result"], "DUE")
        self._assert_result_contract(recovered)
        self.assertEqual(recovered["result"], "DUE")
        self.assertEqual(recovered["lease_acquisition"], "RECOVERED")
        self.assertEqual(recovered["owner_id"], new_owner)
        self.assertEqual(recovered["expires_at_utc"], "2026-09-18T00:45:01Z")
        self.assertEqual([event["event_type"]
                          for event in recovered["lease_evidence"]["event_history"]],
                         ["LEASE_ACQUIRED", "LEASE_EXPIRED", "LEASE_RECOVERED"])
        self.assertEqual(recovered["lease_evidence"]["event_history"][1]["owner_id"],
                         old_owner)
        self.assertEqual(recovered["lease_evidence"]["event_history"][2][
            "previous_owner_id"], old_owner)
        self._assert_preparation_unchanged()

    def test_ongoing_canonical_paper_session_is_due_on_a_new_daily_slot(self):
        state = json.loads(self.session_path.read_bytes())
        state["processed_observations"].append({"persisted": True})
        state["decisions"].append({"persisted": True})
        state["paper_risk_evaluations"] = [{"persisted": True}]
        state["internal_position_state"] = "LONG"
        state["broker_position_observed"] = 1
        state["reconciliation_status"] = "RECONCILED"
        self.session_path.write_bytes(p.encoded(state))

        first = self._evaluate("2026-09-18T00:15:01Z", str(uuid.uuid4()))
        second = self._evaluate("2026-09-19T00:15:01Z", str(uuid.uuid4()))

        self.assertEqual(first["result"], "DUE")
        self.assertEqual(second["result"], "DUE")
        self.assertNotEqual(first["activation_id"], second["activation_id"])
        self.assertNotEqual(first["scheduled_for_utc"], second["scheduled_for_utc"])
        with self.assertRaisesRegex(ValueError, "initial PAPER session"):
            p.prepare_forward_paper_invocation(
                self.session_path, self.configuration_path, self.invocation_path,
                "PAPER_SESSION|m13-t3-due-evaluation", "2026-09-17T00:00:00Z",
                "2026-09-17T00:00:00Z")

    def test_invalid_owner_and_non_utc_time_fail_closed_without_lease(self):
        before = self._contract_bytes()
        for now, owner_id, expected_reason in (
                ("2026-09-18T00:15:01Z", "not-a-uuid", "INVALID_OWNER_ID"),
                ("2026-09-18T00:15:01+00:00", str(uuid.uuid4()),
                 "INVALID_UTC_INSTANT")):
            with self.subTest(now=now, owner_id=owner_id):
                result = self._evaluate(now, owner_id)
                self._assert_result_contract(result)
                self.assertEqual(result["result"], "BLOCKED")
                self.assertEqual(result["reason"], expected_reason)
                self.assertFalse(self.ledger_directory.exists())
                self._assert_preparation_unchanged(before)

    def test_invalid_session_configuration_invocation_association_fails_closed(self):
        original = self.configuration_path.read_bytes()
        registry = json.loads(original)
        registry["session_configurations"] = []
        self.configuration_path.write_bytes(p.encoded(registry))
        before = self._contract_bytes()

        result = self._evaluate("2026-09-18T00:15:01Z")

        self._assert_result_contract(result)
        self.assertEqual(result["result"], "BLOCKED")
        self.assertEqual(result["reason"], "INVALID_T1_ASSOCIATION")
        self.assertFalse(self.ledger_directory.exists())
        self._assert_preparation_unchanged(before)

    def test_invalid_invocation_and_policy_fail_closed_without_replacement(self):
        original_invocation = self.invocation_path.read_bytes()
        original_policy = self.policy_path.read_bytes()
        for path, invalid_bytes in (
                (self.invocation_path, b"{invalid-invocation"),
                (self.policy_path, b"{invalid-policy")):
            with self.subTest(path=path.name):
                self.invocation_path.write_bytes(original_invocation)
                self.policy_path.write_bytes(original_policy)
                path.write_bytes(invalid_bytes)
                before = self._contract_bytes()
                result = self._evaluate("2026-09-18T00:15:01Z")

                self._assert_result_contract(result)
                self.assertEqual(result["result"], "BLOCKED")
                self.assertEqual(result["reason"], "INVALID_T1_ASSOCIATION")
                self.assertFalse(self.ledger_directory.exists())
                self._assert_preparation_unchanged(before)

    def test_corrupt_ledger_is_blocked_and_preserved_byte_for_byte(self):
        owner_id = str(uuid.uuid4())
        first = self._evaluate("2026-09-18T00:15:01Z", owner_id)
        activation = self._canonical_activation("2026-09-18T00:15:01Z")
        ledger_path, _ = p._forward_paper_activation_ledger_paths(
            self.ledger_directory, activation["activation_id"])
        ledger_path.write_bytes(b"{corrupt-ledger")
        corrupt_bytes = ledger_path.read_bytes()

        result = self._evaluate("2026-09-18T00:16:00Z", str(uuid.uuid4()))

        self.assertEqual(first["result"], "DUE")
        self._assert_result_contract(result)
        self.assertEqual(result["result"], "BLOCKED")
        self.assertEqual(result["reason"], "INVALID_LEDGER")
        self.assertEqual(ledger_path.read_bytes(), corrupt_bytes)
        self._assert_preparation_unchanged()

    def test_evaluator_has_no_implicit_clock_scheduler_or_m12_execution(self):
        import inspect
        source = inspect.getsource(p.evaluate_forward_paper_activation_due)
        for forbidden in (
                "datetime.now", "datetime.utcnow", "time.time", "scheduler",
                "retry", "run_forward_paper_invocation", "select_forward_paper_",
                "_coinbase_public_http_get", "run_forward_paper_cycle"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
