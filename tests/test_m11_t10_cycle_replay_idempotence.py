"""A terminal PAPER cycle is replayed from disk without rewriting its receipt."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pipeline as p
import test_m11_t8_paper_cycle_idempotence as t8


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class M11T10CycleReplayIdempotenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def process(self, action):
        script = ("import sys; sys.path.insert(0, 'tests'); "
                  "from test_m11_t10_cycle_replay_idempotence import worker; worker()")
        completed = subprocess.run([sys.executable, "-B", "-c", script, action, str(self.root)],
                                    capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_completed_cycle_reloads_and_replays_without_rewriting_terminal_evidence(self):
        first = self.process("first")
        state = self.root / "state.json"
        cycle = self.root / "cycle.json"
        receipt = self.root / "cycle-output" / "cycle.json" / "paper-cycle.json"
        before = (digest(state), digest(cycle), digest(receipt))
        replay = self.process("replay")
        self.assertEqual(first["terminal_result"], "COMPLETED")
        self.assertTrue(first["created"])
        self.assertEqual(replay["terminal_result"], "COMPLETED")
        self.assertFalse(replay["created"])
        self.assertEqual((digest(state), digest(cycle), digest(receipt)), before)
        recovered = p.load_paper_session(state)
        evidence = json.loads(cycle.read_bytes())
        self.assertEqual([len(recovered[name]) for name in (
            "processed_observations", "decisions", "paper_risk_evaluations", "executions",
            "pending_actions")], [1, 1, 1, 0, 0])
        self.assertEqual(evidence["terminal_result"], "COMPLETED")
        self.assertEqual(evidence["position_after"], "LONG")
        self.assertEqual(recovered["internal_position_state"], "LONG")
        self.assertEqual(recovered["broker_position_observed"], "UNKNOWN")

    def test_altered_canonical_cycle_input_is_rejected_without_replacement(self):
        self.process("first")
        state = self.root / "state.json"
        cycle = self.root / "cycle.json"
        before = (state.read_bytes(), cycle.read_bytes())
        indicator = self.root / "indicator.json"
        altered = json.loads(indicator.read_bytes())
        altered["sma_close_3"] = 1.0
        indicator.write_bytes(p.encoded(altered))
        with t8.offline():
            with self.assertRaises(ValueError):
                t8.cycle(self.root, output="cycle-output")
        self.assertEqual(state.read_bytes(), before[0])
        self.assertEqual(cycle.read_bytes(), before[1])


def worker():
    root = Path(sys.argv[2])
    with t8.offline():
        if sys.argv[1] == "first":
            t8.prepare(root)
            decision = p.persist_paper_session_sma3_decision(
                root / "state.json", root / "fixture.json", root / "acceptance.json",
                root / "indicator.json", root / "decision-output")
            p.apply_paper_risk_to_session(
                root / "state.json", root / "risk-output", decision["decision"]["identity"],
                "2024-01-05T00:06:00Z")
        else:
            p.load_paper_session(root / "state.json")
        print(json.dumps(t8.cycle(root, output="cycle-output")))


if __name__ == "__main__":
    unittest.main()
