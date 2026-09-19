"""Focused tests for the M1.3-T4 authorized M1.2 invocation boundary."""

import inspect
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

import pipeline as p


class M13T4AuthorizedForwardPaperInvocationTests(unittest.TestCase):
    PROCESSING = "2026-09-18T00:15:00Z"
    STARTED = "2026-09-14T00:00:00Z"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.case = self._make_case(self.root / "case", self.PROCESSING)

    def _make_case(self, root, m12_processing_instant):
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
            "session_id": "PAPER_SESSION|m13-t4|" + root.name,
        }
        p.prepare_forward_paper_invocation(
            case["session"], case["configuration"], case["invocation"],
            case["session_id"], self.STARTED, m12_processing_instant)
        p.prepare_forward_paper_activation_policy(
            case["policy"], case["configuration"])
        return case

    @staticmethod
    def rows():
        return [
            [p.epoch(f"2026-09-{day:02d}T00:00:00Z"), day + 7, day + 12,
             day + 8, day + 10, 1.0]
            for day in range(14, 19)
        ]

    @staticmethod
    def transport_calls(case):
        calls = []

        def synthetic(url, headers, timeout_seconds):
            calls.append((url, headers, timeout_seconds))
            return json.dumps(M13T4AuthorizedForwardPaperInvocationTests.rows()).encode(
                "utf-8")

        return synthetic, calls

    def t4_path_args(self, case):
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

    def activation(self, case=None, processing=None):
        case = self.case if case is None else case
        processing = self.PROCESSING if processing is None else processing
        policy = p.load_forward_paper_activation_policy(
            case["policy"], case["configuration"])
        configuration = p.load_forward_paper_configuration(
            case["session"], case["configuration"])
        return p.evaluate_forward_paper_activation(policy, configuration, processing)

    def due(self, case=None, processing=None, owner_id=None):
        case = self.case if case is None else case
        return p.evaluate_forward_paper_activation_due(
            case["policy"], case["configuration"], case["session"],
            case["invocation"], case["ledger"],
            self.PROCESSING if processing is None else processing,
            str(uuid.uuid4()) if owner_id is None else owner_id)

    def ledger_record(self, case=None, processing=None):
        case = self.case if case is None else case
        return p.load_forward_paper_activation_ledger(
            case["ledger"], self.activation(case, processing),
            case["policy"], case["configuration"])

    @staticmethod
    def business_files(case):
        paths = [case[name] for name in (
            "session", "dataset", "selection", "fixture", "acceptance",
            "indicator", "cycle", "result")]
        files = {}
        for path in paths:
            files[str(path)] = path.read_bytes() if path.is_file() else None
        output = case["output"]
        files[str(output)] = (
            {str(path.relative_to(output)): path.read_bytes()
             for path in output.rglob("*") if path.is_file()}
            if output.exists() else None)
        return files

    def _fake_m12(self, case, terminal, calls):
        def fake(*args, **kwargs):
            calls.append((args, kwargs))
            preparation = p.load_forward_paper_preparation(
                case["session"], case["configuration"], case["invocation"])
            content = {
                "configuration_id": preparation["configuration"]["configuration_id"],
                "canonical_cycle_id": None,
                "dataset_id": None,
                "evidence": {"test_double": True},
                "invocation_id": preparation["invocation"]["invocation_id"],
                "mode": "FORWARD_PAPER",
                "processing_instant_utc": preparation["invocation"][
                    "processing_instant_utc"],
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

    def test_real_m12_public_entrypoint_creates_receipt_and_paper_effect(self):
        state_before = self.case["session"].read_bytes()
        synthetic, calls = self.transport_calls(self.case)
        public_entrypoint = p.run_forward_paper_invocation

        def guarded_public_entrypoint(*args, **kwargs):
            self.assertEqual(self.case["session"].read_bytes(), state_before)
            receipts = list(self.case["receipts"].glob("*.json"))
            self.assertEqual(len(receipts), 1)
            self.assertEqual(json.loads(receipts[0].read_bytes())["status"], "M12_BOUND")
            return public_entrypoint(*args, **kwargs)

        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=guarded_public_entrypoint) as m12:
            result = self.invoke(transport=synthetic)

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["activation_result"], "DUE")
        self.assertTrue(result["m12_invoked"])
        self.assertEqual(result["m12_invocations"], 1)
        self.assertEqual(result["m12_terminal_result"], "COMPLETED")
        self.assertEqual(m12.call_count, 1)
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(self.case["session"].read_bytes(), state_before)

        receipt = result["receipt"]
        self.assertEqual(receipt["status"], "M12_RESULT_RECORDED")
        self.assertEqual(receipt["activation_id"], self.activation()["activation_id"])
        self.assertEqual(receipt["policy_id"], self.activation()["policy_id"])
        self.assertEqual(receipt["configuration_id"], self.activation()["configuration_id"])
        self.assertEqual(receipt["scheduled_for_utc"], self.activation()["scheduled_for_utc"])
        self.assertEqual(receipt["processing_instant_utc"], self.PROCESSING)
        self.assertEqual(receipt["invocation_id"],
                         p.load_forward_paper_preparation(
                             self.case["session"], self.case["configuration"],
                             self.case["invocation"])["invocation"]["invocation_id"])
        self.assertEqual(receipt["m12_terminal_result"], "COMPLETED")
        self.assertEqual(receipt["m12_result_reference"], str(self.case["result"].resolve()))
        self.assertEqual(receipt["m12_result_hash"], p.digest(self.case["result"].read_bytes()))
        self.assertEqual(result["m12_result_hash"], receipt["m12_result_hash"])
        self.assertEqual(receipt["m12_invocation_result_id"],
                         json.loads(self.case["result"].read_bytes())["invocation_result_id"])
        ledger = self.ledger_record()
        self.assertEqual(ledger["lease_status"], "RELEASED")
        self.assertEqual(ledger["event_history"][-1]["event_type"], "LEASE_RELEASED")
        self.assertEqual(ledger["event_history"][-1]["owner_id"], receipt["owner_id"])
        state = p.load_paper_session(self.case["session"])
        self.assertEqual(state["internal_position_state"], "LONG")
        self.assertEqual(len(state["processed_observations"]), 1)
        self.assertTrue(json.loads(self.case["cycle"].read_bytes())["cycle_id"])

    def test_all_canonical_m12_terminals_are_referenced_without_transformation(self):
        for terminal in ("COMPLETED", "NOTHING_DUE", "BLOCKED", "RECOVERABLE_ERROR"):
            with self.subTest(terminal=terminal):
                case = self._make_case(self.root / ("terminal-" + terminal), self.PROCESSING)
                calls = []
                with patch.object(p, "run_forward_paper_invocation",
                                  side_effect=self._fake_m12(case, terminal, calls)) as m12:
                    result = self.invoke(case, owner_id=str(uuid.uuid4()))
                self.assertEqual(result["status"], "PASS")
                self.assertEqual(result["m12_terminal_result"], terminal)
                self.assertEqual(result["receipt"]["m12_terminal_result"], terminal)
                self.assertEqual(result["receipt_status"], "M12_RESULT_RECORDED")
                self.assertEqual(m12.call_count, 1)
                self.assertEqual(len(calls), 1)
                self.assertEqual(self.ledger_record(case)["lease_status"], "RELEASED")
                self.assertEqual(result["receipt"]["m12_result_hash"],
                                 p.digest(case["result"].read_bytes()))

    def test_recorded_result_replay_is_immutable_and_never_reinvokes_m12(self):
        synthetic, _ = self.transport_calls(self.case)
        first = self.invoke(transport=synthetic)
        receipt_before = next(self.case["receipts"].glob("*.json")).read_bytes()
        result_before = self.case["result"].read_bytes()
        state_before = self.case["session"].read_bytes()
        ledger_before = next(self.case["ledger"].glob("activation-*.json")).read_bytes()

        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=AssertionError("replay must not call M1.2")):
            replay = self.invoke(owner_id=str(uuid.uuid4()), transport=lambda *args: b"")

        self.assertTrue(replay["replay"])
        self.assertFalse(replay["m12_invoked"])
        self.assertEqual(replay["m12_invocations"], 0)
        self.assertEqual(replay["receipt"], first["receipt"])
        self.assertEqual(replay["m12_terminal_result"], "COMPLETED")
        self.assertEqual(next(self.case["receipts"].glob("*.json")).read_bytes(), receipt_before)
        self.assertEqual(self.case["result"].read_bytes(), result_before)
        self.assertEqual(self.case["session"].read_bytes(), state_before)
        self.assertEqual(next(self.case["ledger"].glob("activation-*.json")).read_bytes(),
                         ledger_before)

    def test_pending_binding_is_preserved_and_deferred_without_second_m12_call(self):
        attempts = []

        def interrupted(*args, **kwargs):
            attempts.append((args, kwargs))
            raise RuntimeError("simulated interruption after binding")

        with patch.object(p, "run_forward_paper_invocation", side_effect=interrupted):
            first = self.invoke()
        self.assertEqual(first["status"], "RECOVERY_REQUIRED")
        self.assertEqual(first["receipt_status"], "M12_BOUND")
        self.assertEqual(len(attempts), 1)
        receipt_path = next(self.case["receipts"].glob("*.json"))
        receipt_before = receipt_path.read_bytes()
        ledger_path = next(self.case["ledger"].glob("activation-*.json"))
        ledger_before = ledger_path.read_bytes()

        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=AssertionError("pending binding must defer to T5")):
            pending = self.invoke(owner_id=str(uuid.uuid4()))
        self.assertEqual(pending["status"], "RECOVERY_REQUIRED")
        self.assertTrue(pending["recovery_required"])
        self.assertFalse(pending["m12_invoked"])
        self.assertEqual(pending["receipt"], first["receipt"])
        self.assertEqual(receipt_path.read_bytes(), receipt_before)
        self.assertEqual(ledger_path.read_bytes(), ledger_before)

    def test_nothing_due_blocked_owner_expiry_invalid_identity_and_corrupt_ledger_stop_locally(self):
        state_before = self.case["session"].read_bytes()
        business_before = self.business_files(self.case)
        nothing_due = self.invoke(processing="2026-09-18T00:14:59Z")
        self.assertEqual(nothing_due["activation_result"], "NOTHING_DUE")
        self.assertEqual(self.case["session"].read_bytes(), state_before)
        self.assertEqual(self.business_files(self.case), business_before)
        self.assertFalse(self.case["receipts"].exists())

        invalid_owner = self.invoke(owner_id="not-a-uuid")
        invalid_clock = self.invoke(processing="2026-09-18T00:15:00+00:00")
        self.assertEqual(invalid_owner["status"], "BLOCKED")
        self.assertEqual(invalid_clock["status"], "BLOCKED")
        self.assertFalse(self.case["receipts"].exists())

        owner = str(uuid.uuid4())
        acquired = self.due(owner_id=owner)
        self.assertEqual(acquired["result"], "DUE")
        before_conflict = self.ledger_record()
        wrong_owner = self.invoke(owner_id=str(uuid.uuid4()))
        self.assertEqual(wrong_owner["status"], "BLOCKED")
        self.assertEqual(wrong_owner["m12_invocations"], 0)
        self.assertEqual(self.case["session"].read_bytes(), state_before)
        self.assertEqual(self.business_files(self.case), business_before)
        self.assertFalse(self.case["receipts"].exists())
        after_conflict = self.ledger_record()
        self.assertEqual(after_conflict["lease_owner_id"], before_conflict["lease_owner_id"])
        self.assertEqual(after_conflict["expires_at_utc"], before_conflict["expires_at_utc"])

        expired_case = self._make_case(self.root / "expired", self.PROCESSING)
        old_owner = str(uuid.uuid4())
        self.assertEqual(self.due(expired_case, owner_id=old_owner)["result"], "DUE")
        expired_before = expired_case["session"].read_bytes()
        expired = self.invoke(expired_case, processing="2026-09-18T00:30:00Z",
                              owner_id=str(uuid.uuid4()))
        self.assertEqual(expired["status"], "BLOCKED")
        self.assertEqual(expired["reason"], "T2_LEASE_EXPIRED")
        self.assertEqual(expired_case["session"].read_bytes(), expired_before)
        self.assertFalse(expired_case["receipts"].exists())

        altered_case = self._make_case(self.root / "altered", self.PROCESSING)
        altered_state = altered_case["session"].read_bytes()
        policy = json.loads(altered_case["policy"].read_bytes())
        policy["policy_version"] = "tampered"
        altered_case["policy"].write_bytes(p.encoded(policy))
        altered = self.invoke(altered_case)
        self.assertEqual(altered["status"], "BLOCKED")
        self.assertEqual(altered_case["session"].read_bytes(), altered_state)
        self.assertFalse(altered_case["receipts"].exists())

        corrupt_case = self._make_case(self.root / "corrupt", self.PROCESSING)
        self.assertEqual(self.due(corrupt_case, owner_id=str(uuid.uuid4()))["result"], "DUE")
        ledger_path = next(corrupt_case["ledger"].glob("activation-*.json"))
        ledger_path.write_bytes(b'{"invalid":true}\n')
        corrupt_before = ledger_path.read_bytes()
        corrupt_state = corrupt_case["session"].read_bytes()
        corrupt = self.invoke(corrupt_case)
        self.assertEqual(corrupt["status"], "BLOCKED")
        self.assertEqual(corrupt_case["session"].read_bytes(), corrupt_state)
        self.assertEqual(ledger_path.read_bytes(), corrupt_before)
        self.assertFalse(corrupt_case["receipts"].exists())

    def test_binding_conflicts_on_changed_processing_or_canonical_invocation(self):
        attempts = []
        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=self._fake_m12(self.case, "COMPLETED", attempts)):
            first = self.invoke()
        self.assertEqual(first["receipt_status"], "M12_RESULT_RECORDED")
        receipt_path = next(self.case["receipts"].glob("*.json"))
        original = receipt_path.read_bytes()

        changed_processing = self.invoke(processing="2026-09-18T00:16:00Z")
        self.assertEqual(changed_processing["status"], "BLOCKED")
        self.assertEqual(changed_processing["m12_invocations"], 0)
        self.assertEqual(receipt_path.read_bytes(), original)

        alternate = self._make_case(self.root / "alternate-session", self.PROCESSING)
        alternate["configuration"].write_bytes(self.case["configuration"].read_bytes())
        # Re-register the second valid session against the unchanged canonical configuration.
        p._persist_forward_paper_configuration(alternate["configuration"],
                                               p.load_paper_session(alternate["session"]))
        alternate["policy"].write_bytes(self.case["policy"].read_bytes())
        alternate["receipts"] = self.case["receipts"]
        alternate["ledger"] = self.case["ledger"]
        alternate["result"] = self.case["result"]
        changed_invocation = self.invoke(alternate, owner_id=str(uuid.uuid4()))
        self.assertEqual(changed_invocation["status"], "BLOCKED")
        self.assertEqual(changed_invocation["m12_invocations"], 0)
        self.assertEqual(receipt_path.read_bytes(), original)

    def test_two_independent_processes_enter_public_t4_with_one_m12_call(self):
        counter_path = self.root / "m12-call-counter.txt"
        payload = {
            "paths": self.t4_path_args(self.case),
            "processing": self.PROCESSING,
            "counter": str(counter_path),
            "rows": self.rows(),
        }
        child = r'''import json, sys, uuid
from pathlib import Path
import pipeline as p
payload = json.loads(sys.argv[1])
original = p.run_forward_paper_invocation
def counted(*args, **kwargs):
    with open(payload["counter"], "ab") as stream:
        stream.write(b"M12_PUBLIC_ENTRYPOINT\n")
        stream.flush()
    return original(*args, **kwargs)
def synthetic(url, headers, timeout_seconds):
    return json.dumps(payload["rows"]).encode("utf-8")
p.run_forward_paper_invocation = counted
result = p.run_forward_paper_activation(
    *payload["paths"], payload["processing"], str(uuid.uuid4()),
    transport=synthetic)
print(json.dumps({"status": result["status"],
                  "activation_result": result.get("activation_result"),
                  "m12_invocations": result["m12_invocations"],
                  "receipt_status": result.get("receipt_status"),
                  "recovery_required": result["recovery_required"]}))
'''
        processes = [
            subprocess.Popen(
                [sys.executable, "-B", "-c", child, json.dumps(payload)],
                cwd=str(Path(__file__).resolve().parents[1]),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(2)
        ]
        outputs = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=60)
            self.assertEqual(process.returncode, 0, stderr)
            outputs.append(json.loads(stdout.strip()))
        self.assertEqual(counter_path.read_bytes().splitlines(), [b"M12_PUBLIC_ENTRYPOINT"])
        self.assertEqual(sum(item["m12_invocations"] for item in outputs), 1)
        self.assertEqual(len(list(self.case["receipts"].glob("*.json"))), 1)
        self.assertEqual(len(p.load_paper_session(
            self.case["session"])["processed_observations"]), 1)
        self.assertEqual(self.ledger_record()["lease_status"], "RELEASED",
                         (outputs, self.ledger_record()["event_history"]))

    def test_t4_only_calls_public_m12_and_has_no_implicit_clock_or_state_writer(self):
        source = inspect.getsource(p.run_forward_paper_activation)
        self.assertIn("evaluate_forward_paper_activation_due(", source)
        self.assertIn("run_forward_paper_invocation(", source)
        for forbidden in ("datetime.now", "datetime.utcnow", "time.time",
                          "compose_forward_paper_cycle(",
                          "select_forward_paper_eligible_observation(",
                          "state.json"):
            self.assertNotIn(forbidden, source)
        self.assertEqual(source.count("run_forward_paper_invocation("), 1)
        self.assertNotIn("run_forward_paper_invocation", inspect.getsource(
            p._forward_paper_activation_existing_receipt_response))


if __name__ == "__main__":
    unittest.main()
