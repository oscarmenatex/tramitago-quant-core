"""Independent-process recovery and idempotency proof for FORWARD_PAPER T6."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pipeline as p


class M12T6ForwardPaperCycleRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paths = {
            "session": self.root / "session" / "state.json",
            "configuration": self.root / "configuration" / "forward-paper.json",
            "invocation": self.root / "invocation.json",
            "dataset": self.root / "dataset.json",
            "selection": self.root / "selection.json",
            "fixture": self.root / "fixture.json",
            "acceptance": self.root / "acceptance.json",
            "indicator": self.root / "indicator.json",
            "cycle": self.root / "cycle.json",
            "result": self.root / "invocation-result.json",
            "output": self.root / "output",
        }
        self.session_id = "PAPER_SESSION|m12-t6-recovery-001"
        self.started_at = "2026-01-02T12:00:00Z"
        self.processing_instant = "2026-01-05T12:00:00Z"
        p.prepare_forward_paper_invocation(
            self.paths["session"], self.paths["configuration"],
            self.paths["invocation"], self.session_id, self.started_at,
            self.processing_instant)

    @staticmethod
    def rows():
        return [[p.epoch(f"2026-01-{day:02d}T00:00:00Z"), day + 8, day + 12,
                 day + 9, day + 10, 1.0] for day in range(1, 6)]

    @staticmethod
    def _hash(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def _transport(self, calls):
        def transport(url, headers, timeout_seconds):
            calls.append((url, headers, timeout_seconds))
            return json.dumps(self.rows()).encode("utf-8")
        return transport

    def _invoke_process_one(self):
        calls = []
        result = p.run_forward_paper_invocation(
            self.paths["session"], self.paths["configuration"],
            self.paths["invocation"], self.paths["dataset"],
            self.paths["selection"], self.paths["fixture"],
            self.paths["acceptance"], self.paths["indicator"],
            self.paths["cycle"], self.paths["result"], self.paths["output"],
            self.session_id, self.started_at, self.processing_instant,
            "2026-01-05T12:01:00Z", transport=self._transport(calls))
        self.assertEqual(result["terminal_result"], "COMPLETED")
        self.assertEqual(len(calls), 1)
        return result

    def _hashes(self):
        return {name: self._hash(self.paths[name])
                for name in ("session", "dataset", "selection", "cycle", "result")}

    def _counts(self):
        state = p.load_paper_session(self.paths["session"])
        return {name: len(state[name]) for name in (
            "processed_observations", "decisions", "executions",
            "pending_actions", "broker_submissions")} | {
                "paper_risk_evaluations": len(state.get("paper_risk_evaluations", []))
            }

    def _run_independent_process(self):
        child = r'''
import hashlib
import json
from pathlib import Path
import sys
import pipeline as p

paths = {name: Path(value) for name, value in json.loads(sys.argv[1]).items()}
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
def forbidden_transport(*args):
    raise AssertionError("process 2 attempted acquisition")

preparation = p.load_forward_paper_preparation(
    paths["session"], paths["configuration"], paths["invocation"])
dataset = p.load_forward_paper_observation_dataset(
    paths["session"], paths["configuration"], paths["invocation"], paths["dataset"])
selection_preparation, selection_dataset, selection = p._load_forward_paper_selection_receipt(
    paths["session"], paths["configuration"], paths["invocation"],
    paths["dataset"], paths["selection"])
cycle = json.loads(paths["cycle"].read_bytes())
try:
    persisted = p._load_forward_paper_invocation_result(paths["result"])
except ValueError:
    preparation = p.load_forward_paper_preparation(
        paths["session"], paths["configuration"], paths["invocation"])
    result = p.run_forward_paper_invocation(
        paths["session"], paths["configuration"], paths["invocation"],
        paths["dataset"], paths["selection"], paths["fixture"],
        paths["acceptance"], paths["indicator"], paths["cycle"],
        paths["result"], paths["output"], preparation["session"]["session_id"],
        preparation["session"]["started_at"],
        preparation["invocation"]["processing_instant_utc"],
        "2026-01-05T12:01:00Z", transport=forbidden_transport)
    print(json.dumps({"result": result}, sort_keys=True))
    raise SystemExit(0)
before = p.load_paper_session(paths["session"])
counts_before = {name: len(before[name]) for name in (
    "processed_observations", "decisions", "executions",
    "pending_actions", "broker_submissions")} | {
        "paper_risk_evaluations": len(before.get("paper_risk_evaluations", []))
    }
result = p.run_forward_paper_invocation(
    paths["session"], paths["configuration"], paths["invocation"],
    paths["dataset"], paths["selection"], paths["fixture"],
    paths["acceptance"], paths["indicator"], paths["cycle"],
    paths["result"], paths["output"], preparation["session"]["session_id"],
    preparation["session"]["started_at"],
    preparation["invocation"]["processing_instant_utc"],
    "2026-01-05T12:01:00Z", transport=forbidden_transport)
after = p.load_paper_session(paths["session"])
counts_after = {name: len(after[name]) for name in (
    "processed_observations", "decisions", "executions",
    "pending_actions", "broker_submissions")} | {
        "paper_risk_evaluations": len(after.get("paper_risk_evaluations", []))
    }
reloaded = p._load_forward_paper_invocation_result(paths["result"])
print(json.dumps({
    "result": result, "persisted": persisted, "reloaded": reloaded,
    "session_id": preparation["session"]["session_id"],
    "configuration_id": preparation["configuration"]["configuration_id"],
    "invocation_id": preparation["invocation"]["invocation_id"],
    "dataset_id": dataset["dataset_id"],
    "selection_id": selection["selection_id"],
    "cycle_id": cycle["cycle_id"],
    "hashes": {name: sha(paths[name]) for name in (
        "session", "dataset", "selection", "cycle", "result")},
    "counts_before": counts_before, "counts_after": counts_after,
    "evidence": reloaded["evidence"],
}, sort_keys=True))
'''
        completed = subprocess.run(
            [sys.executable, "-c", child, json.dumps(
                {name: str(path) for name, path in self.paths.items()})],
            cwd=Path.cwd(), capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0,
                         completed.stderr or completed.stdout)
        return json.loads(completed.stdout)

    def test_independent_process_recovers_completed_invocation_without_effects(self):
        process_one = self._invoke_process_one()
        hashes_before = self._hashes()
        counts_before = self._counts()
        independent = self._run_independent_process()
        self.assertEqual(independent["result"]["terminal_result"], "COMPLETED")
        self.assertTrue(independent["result"]["replay"])
        self.assertFalse(independent["result"]["created"])
        self.assertEqual(independent["result"]["invocation_result_id"],
                         process_one["invocation_result_id"])
        self.assertEqual(independent["persisted"], independent["reloaded"])
        for key in ("session_id", "configuration_id", "invocation_id", "dataset_id"):
            self.assertEqual(independent[key], process_one[key])
        self.assertEqual(independent["selection_id"],
                         process_one["selection_receipt_id"])
        self.assertEqual(independent["cycle_id"],
                         process_one["canonical_cycle_id"])
        self.assertEqual(independent["hashes"], hashes_before)
        self.assertEqual(independent["counts_before"], counts_before)
        self.assertEqual(independent["counts_after"], counts_before)
        self.assertEqual(independent["evidence"], process_one["evidence"])

    def test_tampered_completed_result_fails_closed_without_replacement(self):
        self._invoke_process_one()
        original = self.paths["result"].read_bytes()
        tampered = json.loads(original)
        tampered["terminal_result"] = "NOTHING_DUE"
        self.paths["result"].write_bytes(p.encoded(tampered))
        independent = self._run_independent_process()
        self.assertEqual(independent["result"]["terminal_result"], "BLOCKED")
        self.assertEqual(independent["result"].get("network_calls", 0), 0)
        self.assertEqual(self.paths["result"].read_bytes(), p.encoded(tampered))
        with self.assertRaisesRegex(ValueError, "identity is invalid"):
            p._load_forward_paper_invocation_result(self.paths["result"])
        self.paths["result"].write_bytes(original)
        self.assertEqual(
            p._load_forward_paper_invocation_result(self.paths["result"])["terminal_result"],
            "COMPLETED")


if __name__ == "__main__":
    unittest.main()
