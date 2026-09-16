"""Historical PAPER replay, including two fully independent process lifetimes."""
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import pipeline as p
import test_m11_t7_paper_cycle_recovery as t7


SESSION = "PAPER_SESSION|t8-idempotence-001"
INSTANT = "2024-01-05T00:05:00Z"
OBSERVATION = "BTC-USD|86400|2024-01-04T00:00:00Z"
CYCLE = f"PAPER_CYCLE|{SESSION}|{OBSERVATION}|{INSTANT}"
COLLECTIONS = ("processed_observations", "decisions", "executions", "pending_actions")


def offline():
    stack = ExitStack()
    for target in ("socket.socket.connect", "socket.create_connection", "pipeline.urlopen",
                   "pipeline.HTTPSConnection", "pipeline.alpaca_paper_credentials_from_environment",
                   "pipeline.alpaca_live_credentials_from_environment"):
        stack.enter_context(patch(target, side_effect=AssertionError("External access forbidden")))
    return stack


def prepare(root):
    p.initialize_paper_session(root / "state.json", root / "init", SESSION, "PAPER",
                               "2024-01-05T00:00:00Z")
    p.prepare_paper_session_fixture(root / "state.json", root / "fixture.json",
                                   root / "fixture-output",
                                   t7.M11T7PaperCycleRecoveryTests.observations(), INSTANT)
    p.validate_paper_session_operational_observation(
        root / "state.json", root / "fixture.json", root / "acceptance.json", root / "acceptance-output")
    p.compose_paper_session_sma3(root / "state.json", root / "fixture.json",
                               root / "acceptance.json", root / "indicator.json", root / "indicator-output")


def cycle(root, cycle_id=CYCLE, cycle_name="cycle.json", output="first"):
    return p.run_paper_cycle(root / "state.json", root / "fixture.json",
                            root / "acceptance.json", root / "indicator.json",
                            root / cycle_name, root / output / cycle_name, cycle_id, INSTANT)


def execute(root, output="first"):
    # Publication directories are immutable; each invocation has its own receipt.
    # The persisted session, inputs and canonical cycle evidence remain identical.
    decision = p.persist_paper_session_sma3_decision(
        root / "state.json", root / "fixture.json", root / "acceptance.json",
        root / "indicator.json", root / output / "decision-output")
    risk = p.apply_paper_risk_to_session(root / "state.json", root / output / "risk-output",
                                       decision["decision"]["identity"], "2024-01-05T00:06:00Z")
    return {"decision": decision, "risk": risk, "cycle": cycle(root, output=output)}


class M11T8PaperCycleIdempotenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.guard = self.enterContext(offline())

    def snapshot(self):
        return ((self.root / "state.json").read_bytes(), (self.root / "cycle.json").read_bytes())

    def assert_preserved(self, before, first, replay):
        self.assertEqual(self.snapshot(), before)
        state = p.load_paper_session(self.root / "state.json")
        self.assertEqual([len(state[key]) for key in COLLECTIONS], [1, 1, 0, 0])
        for key in COLLECTIONS:
            self.assertEqual(state[key], json.loads(before[0])[key])
        self.assertEqual(len(state["paper_risk_evaluations"]), 1)
        self.assertEqual(state["mode"], "PAPER")
        self.assertEqual(state["internal_position_state"], "LONG")
        self.assertEqual(state["broker_position_observed"], "UNKNOWN")
        for key in ("proposals", "alpaca_prepared_requests", "broker_submissions"):
            self.assertFalse(state.get(key))
        evidence = json.loads(before[1])
        self.assertEqual(evidence["cycle_id"], CYCLE)
        self.assertEqual(evidence["session_id"], SESSION)
        self.assertEqual(evidence["operational_observation_identity"], OBSERVATION)
        self.assertEqual(evidence["processing_instant_utc"], INSTANT)
        self.assertEqual(evidence["decision"]["decision"], "ENTER")
        self.assertEqual(evidence["risk"]["risk_result"], "ALLOWED")
        self.assertEqual(evidence["position_after"], "LONG")
        self.assertEqual(evidence["broker_position_observed"], "UNKNOWN")
        for stage in ("decision", "risk", "cycle"):
            self.assertTrue(first[stage]["created"])
            self.assertFalse(replay[stage]["created"])
            self.assertEqual(replay[stage]["network_calls"], 0)
            self.assertFalse(replay[stage]["credentials_used"])
        self.assertEqual(first["decision"]["decision"], replay["decision"]["decision"])
        self.assertEqual(first["risk"]["evaluation"], replay["risk"]["evaluation"])
        self.assertEqual(replay["cycle"]["terminal_result"], "COMPLETED")
        self.assertEqual(replay["cycle"]["paper_orders_sent"], 0)
        self.assertEqual(replay["cycle"]["live_orders_sent"], 0)
        published = json.loads((self.root / "replay" / "cycle.json" / "paper-cycle.json").read_bytes())
        self.assertEqual(published, replay["cycle"])

    def test_immediate_repetition_preserves_state_and_evidence(self):
        prepare(self.root)
        first = execute(self.root)
        before = self.snapshot()
        self.assert_preserved(before, first, execute(self.root, "replay"))

    def process(self, mode):
        script = ("import sys; sys.path.insert(0, 'tests'); "
                  "from test_m11_t8_paper_cycle_idempotence import worker; worker()")
        result = subprocess.run([sys.executable, "-B", "-c", script, mode, str(self.root)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_repetition_after_first_process_exits_and_second_reloads(self):
        first = self.process("first")
        before = self.snapshot()
        replay = self.process("replay")
        self.assert_preserved(before, first, replay)

    def test_evidence_can_be_reconstructed_without_reapplying_effects(self):
        prepare(self.root)
        execute(self.root)
        before = self.snapshot()
        recovered = cycle(self.root, cycle_name="recovered.json")
        self.assertEqual(recovered["terminal_result"], "COMPLETED")
        self.assertEqual((self.root / "recovered.json").read_bytes(), before[1])
        self.assertEqual(self.snapshot(), before)

    def test_invalid_cycle_is_blocked_without_state_effects(self):
        prepare(self.root)
        execute(self.root)
        before = self.snapshot()
        result = cycle(self.root, cycle_id="invalid", cycle_name="blocked.json")
        self.assertEqual(result["terminal_result"], "BLOCKED")
        self.assertEqual(self.snapshot(), before)

    def test_incomplete_persisted_evidence_is_not_overwritten(self):
        prepare(self.root)
        execute(self.root)
        (self.root / "cycle.json").write_bytes(p.encoded({"cycle_id": CYCLE}))
        before = self.snapshot()
        with self.assertRaises(ValueError):
            cycle(self.root)
        self.assertEqual(self.snapshot(), before)

    def test_missing_risk_evidence_blocks_cycle_without_effects(self):
        prepare(self.root)
        p.persist_paper_session_sma3_decision(
            self.root / "state.json", self.root / "fixture.json", self.root / "acceptance.json",
            self.root / "indicator.json", self.root / "decision-output")
        before = (self.root / "state.json").read_bytes()
        self.assertEqual(cycle(self.root)["terminal_result"], "BLOCKED")
        self.assertEqual((self.root / "state.json").read_bytes(), before)


def worker():
    root = Path(sys.argv[2])
    with offline():
        if sys.argv[1] == "first":
            prepare(root)
        else:
            p.load_paper_session(root / "state.json")
        print(json.dumps(execute(root, sys.argv[1])))


if __name__ == "__main__":
    unittest.main()
