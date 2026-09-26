"""Focused proof for M2.5-T1: incorporate PAPER evidence into Knowledge.

Links the most recently completed M1.3 activation receipt as a
verifiable, self-describing pointer -- the operational producer (M1.x)
keeps sole authority over its own result; Knowledge never rewrites it
and never duplicates its substantive content.
"""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

import pipeline as p


ROOT = Path(__file__).resolve().parents[1]
PROCESSING = "2026-09-26T00:15:00Z"
STARTED = "2026-09-14T00:00:00Z"
LINKED_AT = "2026-09-26T09:00:00Z"
LINKING_REVISION = "c" * 40


class M25T1PaperEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.case = self._make_case(self.root / "case")
        self.evidence = self.root / "paper-evidence.json"

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
            "session_id": "PAPER_SESSION|m25-t1-evidence|" + root.name,
        }
        p.prepare_forward_paper_invocation(
            case["session"], case["configuration"], case["invocation"],
            case["session_id"], STARTED, PROCESSING)
        p.prepare_forward_paper_activation_policy(case["policy"], case["configuration"])
        return case

    def _t4_path_args(self):
        case = self.case
        return [
            str(case["policy"]), str(case["configuration"]), str(case["session"]),
            str(case["invocation"]), str(case["ledger"]), str(case["receipts"]),
            str(case["dataset"]), str(case["selection"]), str(case["fixture"]),
            str(case["acceptance"]), str(case["indicator"]), str(case["cycle"]),
            str(case["result"]), str(case["output"]),
        ]

    def _fake_m12(self, terminal):
        case = self.case

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

    def complete_an_activation(self):
        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=self._fake_m12("COMPLETED")):
            return p.attempt_forward_paper_activation(
                *self._t4_path_args(), PROCESSING, str(uuid.uuid4()))

    def link(self, *, linked_at=LINKED_AT, linking_code_revision=LINKING_REVISION,
             evidence=None):
        case = self.case
        return p.constitute_paper_evidence(
            evidence or self.evidence, policy_path=case["policy"],
            configuration_path=case["configuration"], ledger_directory=case["ledger"],
            receipt_directory=case["receipts"], linked_at=linked_at,
            linking_code_revision=linking_code_revision)

    def verify(self, evidence_id, *, evidence=None):
        case = self.case
        return p.verified_paper_evidence(
            evidence or self.evidence, evidence_id, policy_path=case["policy"],
            configuration_path=case["configuration"], receipt_directory=case["receipts"])

    def test_links_the_completed_receipt_and_reloads_exactly(self):
        activation = self.complete_an_activation()
        record = self.link()

        self.assertEqual(self.verify(record["evidence_id"]), record)
        self.assertTrue(record["evidence_id"].startswith("PAPER_EVIDENCE|"))
        self.assertTrue(record["record_id"].startswith("PAPER_EVIDENCE_RECORD|"))
        self.assertEqual(record["snapshot"]["kind"], "FORWARD_PAPER_ACTIVATION_RECEIPT")
        self.assertEqual(record["snapshot"]["activation_id"], activation["activation_id"])
        self.assertEqual(record["snapshot"]["m12_terminal_result"], "COMPLETED")
        self.assertTrue(
            record["reference"]["receipt_identity"].startswith("FORWARD_PAPER_ACTIVATION_RECEIPT|"))
        self.assertEqual(record["status"], p.PAPER_EVIDENCE_STATUS)
        # Distinguishable from a historical Research Result by construction: a
        # different identity prefix and an explicit 'kind' tag.
        self.assertFalse(record["evidence_id"].startswith("RESEARCH_RESULT|"))

    def test_never_writes_to_the_receipt_ledger_or_attempts(self):
        self.complete_an_activation()
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.link()
        after = {path: path.read_bytes() for path in self.root.rglob("*")
                 if path.is_file() and path != self.evidence}
        self.assertEqual(before, after)

    def test_reloads_exactly_from_a_second_process(self):
        self.complete_an_activation()
        record = self.link()
        case = self.case
        command = (
            "import json, pipeline as p; r=p.verified_paper_evidence("
            + repr(str(self.evidence)) + ", " + repr(record["evidence_id"]) + ", "
            "policy_path=" + repr(str(case["policy"])) + ", "
            "configuration_path=" + repr(str(case["configuration"])) + ", "
            "receipt_directory=" + repr(str(case["receipts"])) + "); "
            "print(json.dumps(r, sort_keys=True))")
        process = subprocess.run(
            [sys.executable, "-B", "-c", command], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout), record)

    def test_is_idempotent_and_ignores_a_later_linking_attempt(self):
        self.complete_an_activation()
        first = self.link(linked_at=LINKED_AT)
        second = self.link(linked_at="2026-09-27T09:00:00Z", linking_code_revision="9" * 40)
        self.assertEqual(first, second)

    def test_rejects_linking_before_any_receipt_exists(self):
        with self.assertRaisesRegex(ValueError, "No PAPER activation receipt"):
            self.link()
        self.assertFalse(self.evidence.exists())

    def test_rejects_linking_an_open_lease_without_a_recorded_result(self):
        def interrupted(*args, **kwargs):
            raise RuntimeError("simulated interruption while the lease is held")

        with patch.object(p, "run_forward_paper_invocation", side_effect=interrupted):
            p.attempt_forward_paper_activation(*self._t4_path_args(), PROCESSING, str(uuid.uuid4()))
        with self.assertRaisesRegex(ValueError, "recorded M1.2 result"):
            self.link()
        self.assertFalse(self.evidence.exists())

    def test_rejects_a_tampered_receipt_on_reverification(self):
        self.complete_an_activation()
        record = self.link()
        receipt_path = p._forward_paper_activation_receipt_path(
            self.case["receipts"], record["snapshot"]["activation_id"])
        original = receipt_path.read_bytes()
        tampered = json.loads(original)
        tampered["m12_terminal_result"] = "BLOCKED"
        with self.assertRaises(ValueError):
            receipt_path.write_bytes(p.encoded(tampered))
            self.verify(record["evidence_id"])
        receipt_path.write_bytes(original)

    def test_query_filters_by_configuration_and_returns_empty_before_anything_is_linked(self):
        self.assertEqual(p.query_paper_evidence(self.evidence), [])
        self.assertFalse(self.evidence.exists())
        self.complete_an_activation()
        record = self.link()
        matching = p.query_paper_evidence(
            self.evidence, configuration_id=record["snapshot"]["configuration_id"])
        self.assertEqual([item["evidence_id"] for item in matching], [record["evidence_id"]])
        self.assertEqual(p.query_paper_evidence(self.evidence, configuration_id="none"), [])

    def test_load_rejects_a_malformed_identity(self):
        with self.assertRaisesRegex(ValueError, "valid"):
            p.load_paper_evidence(self.evidence, "not-a-valid-id")


if __name__ == "__main__":
    unittest.main()
