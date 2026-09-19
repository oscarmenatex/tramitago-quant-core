"""M1.3-T7: negative tests and integral regression across the whole
activation system -- clock, ledger, lease, network, restart, and
concurrency failures must be blocked or recovered correctly, never
silently corrupted."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import uuid

import pipeline as p


class M13T7ClockRegressionTests(unittest.TestCase):
    """Clock: a lease ledger must fail closed if asked to act at an instant
    earlier than its own last recorded event, instead of silently trusting
    a regressed system clock."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ledger_directory = self.root / "activation-ledger"
        self.configuration_path = self.root / "configuration.json"
        self.policy_path = self.root / "policy.json"
        configuration = p.forward_paper_configuration()
        self.configuration_path.write_bytes(p.encoded({
            "schema_version": p.FORWARD_PAPER_CONFIGURATION_REGISTRY_SCHEMA_VERSION,
            "configurations": [configuration],
            "session_configurations": [],
        }))
        p.prepare_forward_paper_activation_policy(self.policy_path, self.configuration_path)
        self.activation = p.evaluate_forward_paper_activation(
            self.policy_path, self.configuration_path, "2026-09-18T00:15:01Z")

    def acquire(self, owner_id, now):
        return p.acquire_forward_paper_activation_lease(
            self.ledger_directory, self.activation, self.policy_path,
            self.configuration_path, owner_id, now)

    def release(self, owner_id, now):
        return p.release_forward_paper_activation_lease(
            self.ledger_directory, self.activation, self.policy_path,
            self.configuration_path, owner_id, now)

    def load(self):
        return p.load_forward_paper_activation_ledger(
            self.ledger_directory, self.activation, self.policy_path,
            self.configuration_path)

    def test_release_rejects_a_clock_that_regressed_before_the_last_event(self):
        owner = str(uuid.uuid4())
        acquired = self.acquire(owner, "2026-09-18T00:15:01Z")
        self.assertEqual(acquired["lease_result"], "ACQUIRED")
        before = self.load()

        regressed = self.release(owner, "2026-09-18T00:14:00Z")
        self.assertEqual(regressed["status"], "BLOCKED")
        self.assertEqual(regressed["reason"], "LEDGER_TIME_REGRESSION")
        self.assertEqual(self.load(), before)

    def test_acquire_rejects_a_clock_that_regressed_before_the_last_event(self):
        owner_a, owner_b = str(uuid.uuid4()), str(uuid.uuid4())
        self.acquire(owner_a, "2026-09-18T00:15:01Z")
        self.release(owner_a, "2026-09-18T00:20:00Z")
        before = self.load()

        # Still after the daily slot (so due-ness passes) but before the
        # ledger's last recorded event -- isolates the regression check.
        regressed = self.acquire(owner_b, "2026-09-18T00:15:02Z")
        self.assertEqual(regressed["status"], "BLOCKED")
        self.assertEqual(regressed["reason"], "LEDGER_TIME_REGRESSION")
        self.assertEqual(self.load(), before)

    def test_forward_clock_after_regression_attempt_still_works(self):
        owner_a, owner_b = str(uuid.uuid4()), str(uuid.uuid4())
        self.acquire(owner_a, "2026-09-18T00:15:01Z")
        self.release(owner_a, "2026-09-18T00:20:00Z")
        self.acquire(owner_b, "2026-09-18T00:15:02Z")  # rejected, no effect

        recovered = self.acquire(owner_b, "2026-09-18T00:21:00Z")
        self.assertEqual(recovered["lease_result"], "ACQUIRED")


class M13T7NetworkFailureTests(unittest.TestCase):
    """Network: a realistic network exception from M1.2's own acquisition
    step must be caught the same way as any other interruption -- never
    swallowed, never left half-bound."""

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
            "session_id": "PAPER_SESSION|m13-t7-network|" + root.name,
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

    def test_url_error_and_timeout_are_recovery_required_not_swallowed(self):
        for exc in (urllib.error.URLError("simulated DNS failure"),
                    TimeoutError("simulated socket timeout")):
            with self.subTest(exc=type(exc).__name__):
                case = self._make_case(self.root / f"case-{type(exc).__name__}")
                with patch.object(p, "run_forward_paper_invocation", side_effect=exc):
                    result = p.run_forward_paper_activation(
                        *self.t4_path_args(case), self.PROCESSING, str(uuid.uuid4()))
                self.assertEqual(result["status"], "RECOVERY_REQUIRED")
                self.assertEqual(result["receipt_status"], "M12_BOUND")
                self.assertTrue(result["recovery_required"])
                status = p.forward_paper_activation_status(
                    case["policy"], case["configuration"], case["ledger"],
                    case["receipts"], "2026-09-18T00:15:05Z")
                self.assertTrue(status["lease_active"])


class M13T7ConcurrencyRegressionTests(unittest.TestCase):
    """Concurrency: two real, concurrent attempt_forward_paper_activation
    processes for the same activation must never lose or duplicate an
    attempt record. This reproduces a real bug (fixed alongside this test):
    without a lock around the attempts read-decide-write, both processes
    could read zero prior attempts and one silently overwrote the other."""

    PROCESSING = "2026-09-18T00:15:00Z"
    STARTED = "2026-09-14T00:00:00Z"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.case = {
            "session": self.root / "session" / "state.json",
            "configuration": self.root / "configuration.json",
            "invocation": self.root / "invocation.json",
            "policy": self.root / "policy.json",
            "ledger": self.root / "activation-ledger",
            "receipts": self.root / "activation-receipts",
            "dataset": self.root / "dataset.json",
            "selection": self.root / "selection.json",
            "fixture": self.root / "fixture.json",
            "acceptance": self.root / "acceptance.json",
            "indicator": self.root / "indicator.json",
            "cycle": self.root / "cycle.json",
            "result": self.root / "m12-invocation-result.json",
            "output": self.root / "output",
            "session_id": "PAPER_SESSION|m13-t7-concurrency",
        }
        p.prepare_forward_paper_invocation(
            self.case["session"], self.case["configuration"], self.case["invocation"],
            self.case["session_id"], self.STARTED, self.PROCESSING)
        p.prepare_forward_paper_activation_policy(
            self.case["policy"], self.case["configuration"])

    def t4_path_args(self):
        case = self.case
        return [
            str(case["policy"]), str(case["configuration"]), str(case["session"]),
            str(case["invocation"]), str(case["ledger"]), str(case["receipts"]),
            str(case["dataset"]), str(case["selection"]), str(case["fixture"]),
            str(case["acceptance"]), str(case["indicator"]), str(case["cycle"]),
            str(case["result"]), str(case["output"]),
        ]

    def test_two_concurrent_attempts_never_lose_or_duplicate_a_record(self):
        owners = ["11111111-1111-1111-1111-111111111111",
                  "22222222-2222-2222-2222-222222222222"]
        worker = r'''
import json, pathlib, sys, time
import pipeline as p
root = pathlib.Path(sys.argv[1])
owner_id = sys.argv[2]
out = pathlib.Path(sys.argv[3])
args = json.loads(sys.argv[4])

def flaky_m12(*a, **k):
    time.sleep(0.05)
    raise RuntimeError("simulated transient M1.2 failure for " + owner_id)

p.run_forward_paper_invocation = flaky_m12
(root / ("ready-" + owner_id)).write_text("ready", encoding="utf-8")
deadline = time.monotonic() + 15
while len(list(root.glob("ready-*"))) < 2:
    if time.monotonic() >= deadline:
        raise SystemExit("barrier timed out")
    time.sleep(0.005)
result = p.attempt_forward_paper_activation(*args, "2026-09-18T00:15:00Z", owner_id)
out.write_text(json.dumps({"owner": owner_id, "status": result["status"],
                           "attempts_used": result.get("attempts_used")}),
               encoding="utf-8")
'''
        outs = [self.root / f"out-{owner}.json" for owner in owners]
        args_json = json.dumps(self.t4_path_args())
        processes = [
            subprocess.Popen(
                [sys.executable, "-B", "-c", worker, str(self.root), owner,
                 str(out), args_json],
                cwd=Path.cwd(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for owner, out in zip(owners, outs)]
        results = [process.communicate(timeout=30) for process in processes]
        for process, (_, stderr) in zip(processes, results):
            self.assertEqual(process.returncode, 0, stderr)

        attempts_path = next(self.case["receipts"].glob("*attempts.json"))
        record = json.loads(attempts_path.read_bytes())
        numbers = [item["attempt_number"] for item in record["attempts"]]
        self.assertEqual(numbers, sorted(set(numbers)),
                         "attempt_number sequence must be gap-free and duplicate-free")
        self.assertEqual(len(record["attempts"]), len(set(
            item["attempt_id"] for item in record["attempts"])),
            "no two attempts may share an attempt_id")

        owner_reports = [json.loads(out.read_text()) for out in outs]
        successful = [item for item in owner_reports if item["status"] == "RECOVERY_REQUIRED"]
        deferred = [item for item in owner_reports
                   if item["status"] in ("RECOVERABLE_ERROR", "BLOCKED")]
        self.assertEqual(len(successful) + len(deferred), 2)
        # Whichever owner actually attempted must match the one recorded on disk.
        if successful:
            self.assertIn(record["attempts"][-1]["reason"].split(" ")[-1],
                          [owner for owner in owners])


class M13T7RestartRegressionIntegrationTests(unittest.TestCase):
    """Restart: a full activation → crash → status-check → recovery
    sequence, exercised end to end rather than through isolated fixtures,
    must never require a second M1.2 call and must keep the lease and
    status consistent at every step."""

    PROCESSING = "2026-09-18T00:15:00Z"
    STARTED = "2026-09-14T00:00:00Z"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.case = {
            "session": self.root / "session" / "state.json",
            "configuration": self.root / "configuration.json",
            "invocation": self.root / "invocation.json",
            "policy": self.root / "policy.json",
            "ledger": self.root / "activation-ledger",
            "receipts": self.root / "activation-receipts",
            "dataset": self.root / "dataset.json",
            "selection": self.root / "selection.json",
            "fixture": self.root / "fixture.json",
            "acceptance": self.root / "acceptance.json",
            "indicator": self.root / "indicator.json",
            "cycle": self.root / "cycle.json",
            "result": self.root / "m12-invocation-result.json",
            "output": self.root / "output",
            "session_id": "PAPER_SESSION|m13-t7-restart",
        }
        p.prepare_forward_paper_invocation(
            self.case["session"], self.case["configuration"], self.case["invocation"],
            self.case["session_id"], self.STARTED, self.PROCESSING)
        p.prepare_forward_paper_activation_policy(
            self.case["policy"], self.case["configuration"])

    def t4_path_args(self):
        case = self.case
        return [
            str(case["policy"]), str(case["configuration"]), str(case["session"]),
            str(case["invocation"]), str(case["ledger"]), str(case["receipts"]),
            str(case["dataset"]), str(case["selection"]), str(case["fixture"]),
            str(case["acceptance"]), str(case["indicator"]), str(case["cycle"]),
            str(case["result"]), str(case["output"]),
        ]

    def status(self, now):
        return p.forward_paper_activation_status(
            self.case["policy"], self.case["configuration"], self.case["ledger"],
            self.case["receipts"], now)

    def test_full_crash_and_restart_sequence_is_consistent_at_every_step(self):
        before = self.status("2026-09-17T00:00:00Z")
        self.assertEqual(before["last_result"], "NOTHING_DUE")
        self.assertFalse(before["lease_active"])

        def interrupted(*a, **k):
            raise RuntimeError("simulated process kill mid-flight")

        with patch.object(p, "run_forward_paper_invocation", side_effect=interrupted):
            first = p.attempt_forward_paper_activation(
                *self.t4_path_args(), self.PROCESSING, str(uuid.uuid4()))
        self.assertEqual(first["status"], "RECOVERY_REQUIRED")

        mid = self.status("2026-09-18T00:15:05Z")
        self.assertEqual(mid["last_result"], "RECOVERY_REQUIRED")
        self.assertTrue(mid["lease_active"])

        preparation = p.load_forward_paper_preparation(
            self.case["session"], self.case["configuration"], self.case["invocation"])
        content = {
            "configuration_id": preparation["configuration"]["configuration_id"],
            "canonical_cycle_id": None, "dataset_id": None,
            "evidence": {"restarted": True},
            "invocation_id": preparation["invocation"]["invocation_id"],
            "mode": "FORWARD_PAPER",
            "processing_instant_utc": preparation["invocation"]["processing_instant_utc"],
            "reason": "restart integration check", "selection_receipt_id": None,
            "session_id": preparation["session"]["session_id"], "terminal_result": "COMPLETED",
        }
        record = {
            "schema_version": p.FORWARD_PAPER_INVOCATION_RESULT_SCHEMA_VERSION, **content,
            "invocation_result_id": "FORWARD_PAPER_INVOCATION_RESULT|" + p.digest(p.encoded(content)),
        }
        self.case["result"].write_bytes(p.encoded(record))

        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=AssertionError("restart must not call M1.2 again")):
            second = p.attempt_forward_paper_activation(
                *self.t4_path_args(), "2026-09-18T00:20:00Z", str(uuid.uuid4()))
        self.assertEqual(second["status"], "PASS")
        self.assertEqual(second["recovery_source"], "PERSISTED_M12_RESULT")

        after = self.status("2026-09-18T00:21:00Z")
        self.assertEqual(after["last_result"], "COMPLETED")
        self.assertFalse(after["lease_active"])
        self.assertEqual(after["attempts_used"], 2)


if __name__ == "__main__":
    unittest.main()
