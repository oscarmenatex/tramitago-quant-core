"""Focused tests for M1.3-T5: recovery of an interrupted M1.2 binding, and
the bounded, result-dependent retry policy wrapping the T4 runner."""

import hashlib
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
                Path(args[9]).write_bytes(p.encoded(record))
            return {"status": "PASS", **record}
        return fake

    @staticmethod
    def _historical_v1_json_bytes(value):
        """The baseline V1 canonical JSON, deliberately independent of pipeline."""
        return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False)
                + "\n").encode("utf-8")

    @classmethod
    def _historical_v1_digest(cls, value):
        return hashlib.sha256(cls._historical_v1_json_bytes(value)).hexdigest()

    @staticmethod
    def _historical_v1_receipt_path(case, activation_id):
        key = hashlib.sha256(activation_id.encode("utf-8")).hexdigest()
        return case["receipts"] / ("activation-" + key + ".json")

    @staticmethod
    def _path_evidence(*paths):
        return {
            path: {
                "bytes": path.read_bytes(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns,
            }
            for path in paths
        }

    def _build_independent_historical_v1_receipt(self, case=None):
        """Write baseline-format V1 bytes without any current receipt helper."""
        case = self.case if case is None else case
        owner_id = str(uuid.uuid4())
        due = p.evaluate_forward_paper_activation_due(
            case["policy"], case["configuration"], case["session"],
            case["invocation"], case["ledger"], self.PROCESSING, owner_id)
        self.assertEqual(due["result"], "DUE")
        preparation = p.load_forward_paper_preparation(
            case["session"], case["configuration"], case["invocation"])
        policy = p.load_forward_paper_activation_policy(
            case["policy"], case["configuration"])

        result_content = {
            "configuration_id": preparation["configuration"]["configuration_id"],
            "canonical_cycle_id": None,
            "dataset_id": None,
            "evidence": {"historical_v1_fixture": True},
            "invocation_id": preparation["invocation"]["invocation_id"],
            "mode": "FORWARD_PAPER",
            "processing_instant_utc":
                preparation["invocation"]["processing_instant_utc"],
            "reason": "independent historical V1 fixture",
            "selection_receipt_id": None,
            "session_id": preparation["session"]["session_id"],
            "terminal_result": "COMPLETED",
        }
        result = {
            "schema_version": "1",
            **result_content,
            "invocation_result_id": "FORWARD_PAPER_INVOCATION_RESULT|"
            + self._historical_v1_digest(result_content),
        }
        result_bytes = self._historical_v1_json_bytes(result)
        case["result"].write_bytes(result_bytes)

        receipt_content = {
            "schema_version": "1",
            "activation_id": due["activation_id"],
            "policy_id": policy["policy_id"],
            "policy_identity": policy["policy_identity"],
            "configuration_id": preparation["configuration"]["configuration_id"],
            "scheduled_for_utc": due["scheduled_for_utc"],
            "processing_instant_utc": self.PROCESSING,
            "owner_id": owner_id,
            "expires_at_utc": due["expires_at_utc"],
            "invocation_id": preparation["invocation"]["invocation_id"],
            "m12_processing_instant_utc":
                preparation["invocation"]["processing_instant_utc"],
            "m12_result_target": str(case["result"].resolve()),
            "status": "M12_RESULT_RECORDED",
            "m12_terminal_result": result["terminal_result"],
            "m12_result_reference": str(case["result"].resolve()),
            "m12_result_hash": hashlib.sha256(result_bytes).hexdigest(),
            "m12_invocation_result_id": result["invocation_result_id"],
        }
        receipt = {
            **receipt_content,
            "receipt_identity": "FORWARD_PAPER_ACTIVATION_RECEIPT|"
            + self._historical_v1_digest(receipt_content),
        }
        receipt_path = self._historical_v1_receipt_path(
            case, due["activation_id"])
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_bytes = self._historical_v1_json_bytes(receipt)
        receipt_path.write_bytes(receipt_bytes)
        return {
            "owner_id": owner_id,
            "activation": due,
            "receipt": receipt,
            "receipt_path": receipt_path,
            "receipt_bytes": receipt_bytes,
            "result": result,
            "result_bytes": result_bytes,
        }

    def _independent_historical_v1_fixture(self, case=None):
        # These guards cover fixture construction only.  T4's production
        # validator legitimately uses its seal helper to check the old seal.
        with patch.object(p, "_create_forward_paper_activation_receipt",
                          side_effect=AssertionError("fixture must not use receipt creation")), \
                patch.object(p, "_forward_paper_activation_receipt_seal",
                             side_effect=AssertionError("fixture must not use receipt sealing")), \
                patch.object(p, "run_forward_paper_activation",
                             side_effect=AssertionError("fixture must not use current T4/V2 paths")), \
                patch.object(p, "attempt_forward_paper_activation",
                             side_effect=AssertionError("fixture must not use current T5/V2 paths")):
            return self._build_independent_historical_v1_receipt(case)

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

    def test_independent_historical_v1_replays_without_rewrite_or_m12_call(self):
        fixture = self._independent_historical_v1_fixture()
        receipt_path = fixture["receipt_path"]
        evidence_before = self._path_evidence(receipt_path, self.case["result"])
        receipt_files_before = {
            path.relative_to(self.case["receipts"]): path.read_bytes()
            for path in self.case["receipts"].rglob("*") if path.is_file()
        }
        state_before = self.case["session"].read_bytes()

        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=AssertionError("legacy replay must not invoke M1.2")):
            replay = self.invoke(owner_id=fixture["owner_id"])

        self.assertEqual(replay["status"], "PASS")
        self.assertTrue(replay["replay"])
        self.assertFalse(replay["m12_invoked"])
        self.assertEqual(replay["m12_invocations"], 0)
        self.assertEqual(replay["receipt"], fixture["receipt"])
        self.assertEqual(replay["m12_invocation_result_id"],
                         fixture["result"]["invocation_result_id"])
        self.assertEqual(replay["m12_result_hash"],
                         hashlib.sha256(fixture["result_bytes"]).hexdigest())
        self.assertEqual(self.case["session"].read_bytes(), state_before)
        self.assertEqual(self._path_evidence(receipt_path, self.case["result"]),
                         evidence_before)
        self.assertEqual({
            path.relative_to(self.case["receipts"]): path.read_bytes()
            for path in self.case["receipts"].rglob("*") if path.is_file()
        }, receipt_files_before)
        self.assertEqual(json.loads(receipt_path.read_bytes())["schema_version"], "1")

        public_entrypoint = p.run_forward_paper_invocation
        with patch.object(p, "run_forward_paper_invocation",
                          wraps=public_entrypoint) as public_m12:
            later = self.invoke(
                processing="2026-09-19T00:15:00Z", owner_id=str(uuid.uuid4()),
                transport=lambda *args: (_ for _ in ()).throw(
                    AssertionError("persisted M1.2 result must replay")))

        self.assertEqual(later["status"], "PASS")
        self.assertNotEqual(later["activation_id"], fixture["activation"]["activation_id"])
        self.assertEqual(public_m12.call_count, 1)
        self.assertTrue(self._historical_v1_receipt_path(
            self.case, later["activation_id"]).exists())
        self.assertEqual(self._path_evidence(receipt_path, self.case["result"]),
                         evidence_before)

    def test_malformed_independent_historical_v1_fails_closed_without_repair(self):
        cases = (
            ("missing required field", lambda receipt: receipt.pop("owner_id")),
            ("incorrect receipt identity", lambda receipt: receipt.update(
                receipt_identity="FORWARD_PAPER_ACTIVATION_RECEIPT|" + "0" * 64)),
            ("incompatible activation", lambda receipt: receipt.update(
                activation_id="FORWARD_PAPER_ACTIVATION|other|2026-09-18T00:15:00Z")),
            ("incompatible invocation", lambda receipt: receipt.update(
                invocation_id="FORWARD_PAPER_INVOCATION|other")),
            ("invalid terminal status", lambda receipt: receipt.update(
                status="INVALID_TERMINAL_STATUS")),
        )
        for label, mutate in cases:
            with self.subTest(label=label):
                case = self._make_case(self.root / label.replace(" ", "-"))
                fixture = self._independent_historical_v1_fixture(case)
                receipt = dict(fixture["receipt"])
                mutate(receipt)
                if label not in {"missing required field", "incorrect receipt identity"}:
                    unsigned = {key: value for key, value in receipt.items()
                                if key != "receipt_identity"}
                    receipt["receipt_identity"] = (
                        "FORWARD_PAPER_ACTIVATION_RECEIPT|"
                        + self._historical_v1_digest(unsigned))
                fixture["receipt_path"].write_bytes(
                    self._historical_v1_json_bytes(receipt))
                evidence_before = self._path_evidence(
                    fixture["receipt_path"], case["result"], case["session"])
                files_before = {
                    path.relative_to(case["root"]): path.read_bytes()
                    for path in case["root"].rglob("*") if path.is_file()
                }

                with patch.object(
                        p, "run_forward_paper_invocation",
                        side_effect=AssertionError("invalid legacy receipt must not invoke M1.2")):
                    result = self.invoke(case, owner_id=fixture["owner_id"])

                self.assertEqual(result["status"], "BLOCKED")
                self.assertEqual(result["m12_invocations"], 0)
                self.assertEqual(self._path_evidence(
                    fixture["receipt_path"], case["result"], case["session"]),
                    evidence_before)
                self.assertEqual({
                    path.relative_to(case["root"]): path.read_bytes()
                    for path in case["root"].rglob("*") if path.is_file()
                }, files_before)

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
        calls = []

        def recoverable(*args, **kwargs):
            calls.append(args)
            return self._fake_m12(self.case, "RECOVERABLE_ERROR")(*args, **kwargs)

        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=recoverable):
            first = self.attempt(processing="2026-09-18T00:15:00Z")
        self.assertEqual(first["status"], "PASS")
        self.assertEqual(first["m12_terminal_result"], "RECOVERABLE_ERROR")
        self.assertEqual(first["attempt_number"], 1)
        self.assertEqual(first["attempts_used"], 1)
        self.assertEqual(first["next_retry_at_utc"], "2026-09-18T00:16:00Z")
        self.assertFalse(first["operator_action_required"])
        first_result_bytes = self.case["result"].read_bytes()
        first_receipt_path = p._forward_paper_activation_receipt_path(
            self.case["receipts"], first["activation_id"])
        first_receipt_bytes = first_receipt_path.read_bytes()

        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=AssertionError("backoff not elapsed yet")):
            too_soon = self.attempt(processing="2026-09-18T00:15:30Z")
        self.assertEqual(too_soon["reason"], "T5_BACKOFF_NOT_ELAPSED")
        self.assertEqual(too_soon["attempts_used"], 1)

        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=recoverable):
            second = self.attempt(processing="2026-09-18T00:16:00Z")
        self.assertEqual(second["attempt_number"], 2)
        self.assertEqual(second["next_retry_at_utc"], "2026-09-18T00:21:00Z")
        self.assertEqual(len(calls), 2)
        self.assertFalse(second["replay"])
        self.assertEqual(second["m12_invocations"], 1)

        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=recoverable):
            third = self.attempt(processing="2026-09-18T00:21:00Z")
        self.assertEqual(third["attempt_number"], 3)
        self.assertEqual(third["status"], "RECOVERABLE_ERROR")
        self.assertEqual(third["next_retry_at_utc"], None)
        self.assertTrue(third["operator_action_required"])
        self.assertEqual(third["reason"], "T5_MAX_ATTEMPTS_EXHAUSTED")
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.case["result"].read_bytes(), first_result_bytes)
        self.assertEqual(first_receipt_path.read_bytes(), first_receipt_bytes)

        attempts = p._load_forward_paper_activation_attempts(
            p._forward_paper_activation_attempts_path(
                self.case["receipts"], first["activation_id"]),
            first["activation_id"], self.PROCESSING)["attempts"]
        self.assertEqual([item["attempt_number"] for item in attempts], [1, 2, 3])
        self.assertEqual(len({item["attempt_id"] for item in attempts}), 3)
        for attempt in attempts[1:]:
            receipt_path = p._forward_paper_activation_attempt_receipt_path(
                self.case["receipts"], first["activation_id"], attempt["attempt_id"])
            result_path = p._forward_paper_activation_attempt_result_path(
                self.case["receipts"], first["activation_id"], attempt["attempt_id"])
            self.assertTrue(receipt_path.exists())
            self.assertTrue(result_path.exists())

        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=AssertionError("attempts already exhausted")):
            fourth = self.attempt(processing="2026-09-18T00:30:00Z")
        self.assertEqual(fourth["reason"], "T5_MAX_ATTEMPTS_EXHAUSTED")
        self.assertEqual(fourth["attempts_used"], 3)
        self.assertEqual(len(calls), 3)

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
