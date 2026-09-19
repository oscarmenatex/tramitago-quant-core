"""Cross-process proof for the M1.3-T2 activation ledger and lease."""

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


class M13T2ActivationLedgerLeaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ledger_directory = self.root / "activation-ledger"
        self.configuration_path = self.root / "configuration.json"
        self.policy_path = self.root / "policy.json"
        self.activation_path = self.root / "activation.json"
        self.state_path = self.root / "state.json"
        self.invocation_path = self.root / "invocation.json"

        configuration = p.forward_paper_configuration()
        self.configuration_path.write_bytes(p.encoded({
            "schema_version": p.FORWARD_PAPER_CONFIGURATION_REGISTRY_SCHEMA_VERSION,
            "configurations": [configuration],
            "session_configurations": [],
        }))
        p.prepare_forward_paper_activation_policy(
            self.policy_path, self.configuration_path)
        self.activation = p.evaluate_forward_paper_activation(
            self.policy_path, self.configuration_path, "2026-09-18T00:15:01Z")
        self.activation_path.write_bytes(p.encoded(self.activation))
        self.state_path.write_bytes(b"state-sentinel")
        self.invocation_path.write_bytes(b"invocation-sentinel")

    def owner(self):
        return str(uuid.uuid4())

    def acquire(self, owner_id, now="2026-09-18T00:15:01Z", activation=None):
        return p.acquire_forward_paper_activation_lease(
            self.ledger_directory, activation or self.activation,
            self.policy_path, self.configuration_path, owner_id, now)

    def release(self, owner_id, now="2026-09-18T00:16:00Z", activation=None):
        return p.release_forward_paper_activation_lease(
            self.ledger_directory, activation or self.activation,
            self.policy_path, self.configuration_path, owner_id, now)

    def load(self, activation=None):
        return p.load_forward_paper_activation_ledger(
            self.ledger_directory, activation or self.activation,
            self.policy_path, self.configuration_path)

    def test_ledger_persists_reloads_and_same_owner_is_idempotent(self):
        owner_id = self.owner()
        first = self.acquire(owner_id)
        self.assertEqual(first["lease_result"], "ACQUIRED")
        before = p.encoded(first["ledger"])
        replay = self.acquire(owner_id, now="2026-09-18T00:16:00Z")
        loaded = self.load()

        self.assertEqual(replay["lease_result"], "ALREADY_OWNED")
        self.assertEqual(replay["ledger"], first["ledger"])
        self.assertEqual(loaded, first["ledger"])
        self.assertEqual(p.encoded(loaded), before)
        self.assertEqual(loaded["lease_duration_seconds"], 900)
        self.assertEqual(loaded["expires_at_utc"], "2026-09-18T00:30:01Z")

    def test_other_owner_conflicts_and_conflict_evidence_is_persisted(self):
        first_owner, second_owner = self.owner(), self.owner()
        self.assertEqual(self.acquire(first_owner)["lease_result"], "ACQUIRED")
        conflict = self.acquire(second_owner, now="2026-09-18T00:16:00Z")

        self.assertEqual(conflict["status"], "BLOCKED")
        self.assertEqual(conflict["reason"], "ACTIVATION_IN_PROGRESS")
        ledger = self.load()
        self.assertEqual(ledger["lease_owner_id"], first_owner)
        self.assertEqual([item["event_type"] for item in ledger["event_history"]],
                         ["LEASE_ACQUIRED", "LEASE_CONFLICT"])
        self.assertEqual(ledger["event_history"][-1]["owner_id"], second_owner)
        self.assertEqual(ledger["event_history"][-1]["reason"], "ACTIVATION_IN_PROGRESS")

    def test_two_subprocesses_competing_for_same_activation_have_one_winner(self):
        owners = [self.owner(), self.owner()]
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
activation = json.loads((root / "activation.json").read_bytes())
result = p.acquire_forward_paper_activation_lease(
    root / "activation-ledger", activation,
    root / "policy.json", root / "configuration.json", owner_id,
    "2026-09-18T00:15:01Z")
print(json.dumps({"status": result["status"], "lease_result": result["lease_result"],
                  "reason": result["reason"]}, sort_keys=True))
'''
        processes = [subprocess.Popen(
            [sys.executable, "-B", "-c", worker, str(self.root), owner_id],
            cwd=Path.cwd(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for owner_id in owners]
        outputs = [process.communicate(timeout=30) for process in processes]
        for process, (stdout, stderr) in zip(processes, outputs):
            self.assertEqual(process.returncode, 0, stderr)
        results = [json.loads(stdout) for stdout, _ in outputs]
        self.assertEqual(sum(result["lease_result"] == "ACQUIRED" for result in results), 1)
        self.assertEqual(sum(result["reason"] == "ACTIVATION_IN_PROGRESS"
                             for result in results), 1)
        ledger = self.load()
        self.assertEqual(ledger["lease_status"], "ACTIVE")
        self.assertEqual(len([item for item in ledger["event_history"]
                              if item["event_type"] == "LEASE_ACQUIRED"]), 1)
        self.assertEqual(len([item for item in ledger["event_history"]
                              if item["event_type"] == "LEASE_CONFLICT"]), 1)

    def test_distinct_activation_ids_have_independent_leases(self):
        other = p.evaluate_forward_paper_activation(
            self.policy_path, self.configuration_path, "2026-09-19T00:15:01Z")
        first_path, first_lock = p._forward_paper_activation_ledger_paths(
            self.ledger_directory, self.activation["activation_id"])
        other_path, other_lock = p._forward_paper_activation_ledger_paths(
            self.ledger_directory, other["activation_id"])
        self.assertNotEqual(first_path, other_path)
        self.assertNotEqual(first_lock, other_lock)
        first = self.acquire(self.owner())
        second = self.acquire(self.owner(), "2026-09-19T00:15:01Z", other)

        self.assertEqual(first["lease_result"], "ACQUIRED")
        self.assertEqual(second["lease_result"], "ACQUIRED")
        self.assertNotEqual(first["ledger"]["activation_id"],
                            second["ledger"]["activation_id"])
        self.assertEqual(first["ledger"]["lease_status"], "ACTIVE")
        self.assertEqual(second["ledger"]["lease_status"], "ACTIVE")

    def test_release_requires_same_owner_and_records_events(self):
        owner_id, other_owner = self.owner(), self.owner()
        self.acquire(owner_id)
        denied = self.release(other_owner)
        self.assertEqual(denied["status"], "BLOCKED")
        self.assertEqual(denied["reason"], "LEASE_OWNER_MISMATCH")
        self.assertEqual(self.load()["lease_status"], "ACTIVE")
        self.assertEqual(self.load()["event_history"][-1]["event_type"], "LEASE_CONFLICT")

        released = self.release(owner_id)
        self.assertEqual(released["lease_result"], "RELEASED")
        self.assertEqual(released["ledger"]["lease_status"], "RELEASED")
        self.assertEqual(released["ledger"]["event_history"][-1]["event_type"],
                         "LEASE_RELEASED")
        replay = self.release(owner_id, "2026-09-18T00:17:00Z")
        self.assertEqual(replay["lease_result"], "ALREADY_RELEASED")

    def test_expired_lease_is_persisted_then_recovered_by_new_owner(self):
        old_owner, new_owner = self.owner(), self.owner()
        self.acquire(old_owner)
        recovered = self.acquire(new_owner, "2026-09-18T00:30:01Z")

        self.assertEqual(recovered["lease_result"], "RECOVERED")
        ledger = self.load()
        self.assertEqual(ledger["lease_owner_id"], new_owner)
        self.assertEqual(ledger["lease_status"], "ACTIVE")
        self.assertEqual(ledger["acquired_at_utc"], "2026-09-18T00:30:01Z")
        self.assertEqual(ledger["expires_at_utc"], "2026-09-18T00:45:01Z")
        self.assertEqual([item["event_type"] for item in ledger["event_history"]],
                         ["LEASE_ACQUIRED", "LEASE_EXPIRED", "LEASE_RECOVERED"])
        self.assertEqual(ledger["event_history"][1]["owner_id"], old_owner)
        self.assertEqual(ledger["event_history"][2]["previous_owner_id"], old_owner)

    def test_invalid_owner_clock_and_t1_association_fail_closed(self):
        for owner_id in (None, "", "not-a-uuid", "{" + str(uuid.uuid4()) + "}"):
            with self.subTest(owner_id=owner_id):
                result = self.acquire(owner_id)
                self.assertEqual(result["status"], "BLOCKED")
                self.assertEqual(result["reason"], "INVALID_OWNER_ID")
        invalid_clock = self.acquire(self.owner(), now="2026-09-18T00:15:01+00:00")
        self.assertEqual(invalid_clock["reason"], "INVALID_UTC_INSTANT")

        altered = dict(self.activation, configuration_id="OTHER_CONFIGURATION")
        invalid_association = self.acquire(self.owner(), activation=altered)
        self.assertEqual(invalid_association["status"], "BLOCKED")
        self.assertIn("INVALID_T1_ASSOCIATION", invalid_association["reason"])
        self.assertFalse(self.ledger_directory.exists())

    def test_invalid_policy_file_fails_closed_without_ledger(self):
        original = self.policy_path.read_bytes()
        altered = json.loads(original)
        altered["slot_time_utc"] = "00:16:00Z"
        self.policy_path.write_bytes(p.encoded(altered))
        result = self.acquire(self.owner())
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("INVALID_T1_ASSOCIATION", result["reason"])
        self.assertFalse(self.ledger_directory.exists())

    def test_persistence_failure_never_leaves_partial_ledger_or_replaces_original(self):
        owner_id, other_owner = self.owner(), self.owner()
        with patch("pipeline._atomic_write", side_effect=OSError("disk unavailable")):
            failed_create = self.acquire(owner_id)
        self.assertEqual(failed_create["status"], "RECOVERABLE_ERROR")
        self.assertEqual(list(self.ledger_directory.glob("activation-*.json")), [])

        acquired = self.acquire(owner_id)
        ledger_path = next(self.ledger_directory.glob("activation-*.json"))
        original = ledger_path.read_bytes()
        with patch("pipeline._atomic_write", side_effect=OSError("disk unavailable")):
            failed_conflict = self.acquire(other_owner, "2026-09-18T00:16:00Z")
        self.assertEqual(failed_conflict["status"], "RECOVERABLE_ERROR")
        self.assertEqual(ledger_path.read_bytes(), original)
        self.assertEqual(self.load(), acquired["ledger"])

    def test_atomic_replace_failure_preserves_ledger_and_cleans_temporary_file(self):
        owner_id, other_owner = self.owner(), self.owner()
        with patch("pipeline.os.replace", side_effect=OSError("replace unavailable")):
            failed_create = self.acquire(owner_id)
        self.assertEqual(failed_create["status"], "RECOVERABLE_ERROR")
        self.assertEqual(list(self.ledger_directory.glob("activation-*.json")), [])
        self.assertEqual(list(self.ledger_directory.glob("*.tmp")), [])

        acquired = self.acquire(owner_id)
        ledger_path = next(self.ledger_directory.glob("activation-*.json"))
        original = ledger_path.read_bytes()
        with patch("pipeline.os.replace", side_effect=OSError("replace unavailable")):
            failed_conflict = self.acquire(other_owner, "2026-09-18T00:16:00Z")
        self.assertEqual(failed_conflict["status"], "RECOVERABLE_ERROR")
        self.assertEqual(ledger_path.read_bytes(), original)
        self.assertEqual(list(self.ledger_directory.glob("*.tmp")), [])
        self.assertEqual(self.load(), acquired["ledger"])

    def test_no_state_or_m11_m12_effects_and_explicit_clock_only(self):
        import inspect
        source = inspect.getsource(p.acquire_forward_paper_activation_lease)
        source += inspect.getsource(p.release_forward_paper_activation_lease)
        for forbidden in ("datetime.now", "datetime.utcnow", "time.time",
                          "run_forward_paper_invocation", "select_forward_paper_"):
            self.assertNotIn(forbidden, source)
        self.assertEqual(self.acquire(self.owner())["status"], "PASS")
        self.assertEqual(self.state_path.read_bytes(), b"state-sentinel")
        self.assertEqual(self.invocation_path.read_bytes(), b"invocation-sentinel")
        self.assertEqual(self.load()["lease_duration_seconds"], 900)


if __name__ == "__main__":
    unittest.main()
