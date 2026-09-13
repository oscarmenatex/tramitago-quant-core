import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pipeline as p


def formed_state(prices=(100, 125)):
    """Controlled, already formed records; never run observation or strategy code."""
    state = {"instrument": "BTC-USD", "frequency_seconds": 86400,
             "virtual_position": len(prices) % 2, "executions": [], "decisions": [],
             "observations": [], "open_events": [], "pending_actions": []}
    for index, price in enumerate(prices):
        source_time = f"2024-01-{index + 3:02d}T00:00:00Z"
        timestamp = f"2024-01-{index + 4:02d}T00:00:00Z"
        source = f"BTC-USD|86400|{source_time}"
        opening = f"BTC-USD|86400|{timestamp}"
        decision_id = f"SMA3|{source}"
        action = "ENTER" if index % 2 == 0 else "EXIT"
        state["observations"].append({"identity": source, "timestamp": source_time,
                                      "close": 999999, "open": 777777})
        state["open_events"].append({"identity": opening, "timestamp": timestamp,
                                     "open": price, "closed": False})
        state["decisions"].append({"identity": decision_id, "observation_identity": source,
                                   "timestamp": source_time, "decision": action,
                                   "target_position": (index + 1) % 2})
        state["executions"].append({
            "identity": f"EXECUTION|PENDING|{decision_id}|{opening}",
            "decision_identity": decision_id, "source_observation_identity": source,
            "execution_observation_identity": opening, "timestamp": timestamp,
            "price": price, "action": action, "costs": 0, "slippage": 0,
            "virtual_position_before": index % 2, "virtual_position_after": (index + 1) % 2})
    return state


class RealizedResultTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "state.json"

    def save(self, state):
        self.path.write_bytes(p.encoded(state))

    def test_pair_calculation_links_and_preservation(self):
        before = formed_state()
        self.save(before)
        with patch("pipeline.urlopen", side_effect=AssertionError("Network forbidden")), \
                patch("pipeline.decide", side_effect=AssertionError("Strategy forbidden")), \
                patch("pipeline.execute_virtual", side_effect=AssertionError("Execution forbidden")):
            summary = p.realize_results(self.path)
        state = json.loads(self.path.read_bytes())
        result = state.pop("realized_results")[0]
        self.assertEqual(state, before)
        self.assertEqual(summary["virtual_position"], 0)
        self.assertEqual(summary["realized_result_count"], 1)
        self.assertEqual(result["gross_return"], 0.25)
        self.assertEqual((result["costs"], result["slippage"]), (0, 0))
        for side, execution in zip(("entry", "exit"), before["executions"]):
            self.assertEqual(result[side + "_execution_identity"], execution["identity"])
            for key in ("decision_identity", "source_observation_identity",
                        "execution_observation_identity", "timestamp", "price"):
                self.assertEqual(result[side + "_" + key], execution[key])

    def test_open_position_and_empty_ledger(self):
        for prices in ((100,), ()):
            with self.subTest(prices=prices):
                state = formed_state(prices)
                self.save(state)
                summary = p.realize_results(self.path)
                self.assertEqual(summary["new_realized_results"], [])
                self.assertEqual(summary["virtual_position"], len(prices))
                saved = json.loads(self.path.read_bytes())
                self.assertEqual(saved.pop("realized_results"), [])
                self.assertEqual(saved, state)

    def test_loss_and_zero_return(self):
        for price, expected in ((75, -0.25), (100, 0)):
            self.save(formed_state((100, price)))
            self.assertEqual(p.realize_results(self.path)["new_realized_results"][0]["gross_return"], expected)

    def test_incremental_close_and_multiple_positions_in_sequence(self):
        self.save(formed_state((100,)))
        p.realize_results(self.path)
        for prices, count, new in (((100, 125), 1, 1), ((100, 125, 200), 1, 0),
                                   ((100, 125, 200, 150), 2, 1)):
            previous = json.loads(self.path.read_bytes())["realized_results"]
            self.save(formed_state(prices) | {"realized_results": previous})
            summary = p.realize_results(self.path)
            self.assertEqual(summary["realized_result_count"], count)
            self.assertEqual(len(summary["new_realized_results"]), new)
            self.assertEqual(json.loads(self.path.read_bytes())["realized_results"][:len(previous)], previous)

    def test_repeat_and_new_process_recovery(self):
        self.save(formed_state())
        p.realize_results(self.path)
        before = self.path.read_bytes()
        modified = self.path.stat().st_mtime_ns
        summary = p.realize_results(self.path)
        self.assertEqual(summary["new_realized_results"], [])
        command = [sys.executable, "-B", str(Path(p.__file__).resolve()),
                   "realize-results", "--state", str(self.path)]
        run = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), summary)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_mtime_ns, modified)

    def test_independent_states_are_byte_deterministic(self):
        self.save(formed_state())
        second = self.root / "second.json"
        second.write_bytes(self.path.read_bytes())
        self.assertEqual(p.realize_results(self.path), p.realize_results(second))
        self.assertEqual(self.path.read_bytes(), second.read_bytes())

    def test_invalid_input_rejected_without_any_write(self):
        cases = []
        for key in formed_state()["executions"][1]:
            state = formed_state()
            del state["executions"][1][key]
            cases.append(state)
        for key, values in {
            "price": (0, -1, True, "125", None, float("inf"), float("nan")),
            "costs": (1, True), "slippage": (1, True),
            "timestamp": ("bad", "2024-01-05T01:00:00Z"),
            "action": ("LIQUIDATE", "ENTER"), "virtual_position_before": (0, True),
            "decision_identity": ("missing",), "identity": ("wrong",),
            "source_observation_identity": ("wrong",),
        }.items():
            for value in values:
                state = formed_state()
                state["executions"][1][key] = value
                cases.append(state)
        for mutate in (
            lambda s: s.update(virtual_position=1),
            lambda s: s.update(virtual_position=True),
            lambda s: s.update(executions=s["executions"][1:]),
            lambda s: s.update(executions=s["executions"] * 2),
            lambda s: s.update(executions=list(reversed(s["executions"]))),
            lambda s: s.update(decisions=[]),
            lambda s: s["decisions"][1].update(decision="ENTER"),
            lambda s: s.update(executions=None),
            lambda s: s.update(realized_results=[{"identity": "orphan"}]),
        ):
            state = formed_state()
            mutate(state)
            cases.append(state)
        for index, state in enumerate(cases):
            with self.subTest(index=index):
                self.path.write_text(json.dumps(state), encoding="utf-8")
                before = self.path.read_bytes()
                messages = []
                for _ in range(2):
                    with self.assertRaises(ValueError) as caught:
                        p.realize_results(self.path)
                    messages.append(str(caught.exception))
                    self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(messages[0], messages[1])

    def test_changed_source_or_result_is_not_silently_overwritten(self):
        self.save(formed_state())
        p.realize_results(self.path)
        original = json.loads(self.path.read_bytes())
        for target in ("source", "result"):
            state = copy.deepcopy(original)
            if target == "source":
                state["executions"][1]["price"] = 150
            else:
                state["realized_results"][0]["gross_return"] = 2
            self.save(state)
            before = self.path.read_bytes()
            with self.assertRaisesRegex(ValueError, "disagree"):
                p.realize_results(self.path)
            self.assertEqual(self.path.read_bytes(), before)

    def test_atomic_write_failure_preserves_original_state(self):
        self.save(formed_state())
        before = self.path.read_bytes()
        with patch("pipeline.os.replace", side_effect=OSError("controlled replace failure")):
            with self.assertRaises(OSError):
                p.realize_results(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(p.realize_results(self.path)["realized_result_count"], 1)
