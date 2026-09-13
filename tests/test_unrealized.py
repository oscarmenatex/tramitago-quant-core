import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import pipeline as p
from test_realized_results import formed_state


def observation(day, close=125, **changes):
    timestamp = f"2024-01-{day:02d}T00:00:00Z"
    return {"identity": f"BTC-USD|86400|{timestamp}", "instrument": "BTC-USD",
            "timestamp": timestamp, "open": 80, "low": 70, "high": 200,
            "close": close, "volume": 1, **changes}


def open_state():
    return formed_state((100,)) | {"observations": [observation(4)], "realized_results": []}


class UnrealizedTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "state.json"

    def save(self, state):
        self.path.write_bytes(p.encoded(state))

    def mark(self, instant="2024-01-05T00:00:00Z"):
        return p.mark_unrealized(self.path, instant)

    def test_latest_eligible_close_and_original_state_preserved(self):
        state = open_state()
        state["observations"] = [observation(5, 150), observation(3, 199),
                                 observation(7, 170), observation(4), observation(6, 160)]
        state["open_events"] = [{"open": 999999, "timestamp": "2024-01-06T00:00:00Z"}]
        self.save(state)
        with patch("pipeline.urlopen", side_effect=AssertionError("No acquisition")), \
                patch("pipeline.execute_virtual", side_effect=AssertionError("No execution")), \
                patch("pipeline.decide", side_effect=AssertionError("No decision")), \
                patch("pipeline.realize_results", side_effect=AssertionError("No realized update")):
            result = self.mark("2024-01-06T12:00:00Z")
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["unrealized_return"], 0.5)
        saved = json.loads(self.path.read_bytes())
        marks = saved.pop("unrealized_marks")
        saved.pop("unrealized_valuation")
        self.assertEqual(saved, state)
        self.assertEqual(len(marks), 1)
        mark = marks[0]
        self.assertEqual(mark["mark_timestamp"], "2024-01-05T00:00:00Z")
        self.assertEqual(mark["mark_price"], 150)
        self.assertEqual(mark["entry_execution_identity"], state["executions"][0]["identity"])
        self.assertEqual(mark["mark_observation_identity"], state["observations"][0]["identity"])
        self.assertEqual(mark["valuation_instant"], "2024-01-06T12:00:00Z")
        self.assertEqual((mark["costs"], mark["slippage"]), (0, 0))

    def test_same_day_close_becomes_available_at_midnight_boundary(self):
        self.save(open_state())
        first = self.mark("2024-01-04T23:59:59.999999Z")
        self.assertEqual(first["status"], "NOT AVAILABLE")
        self.assertNotIn("unrealized_return", first)
        self.assertEqual(self.mark()["unrealized_return"], 0.25)

    def test_invalid_or_absent_observations_do_not_supply_a_mark(self):
        invalid = [[], [observation(3)], [observation(6)], [observation(4, closed=False)],
                   [observation(4, instrument="ETH-USD")], [observation(4, frequency_seconds=3600)],
                   [observation(4, identity="wrong")], [observation(4, accepted_at="bad")],
                   [observation(4, timestamp="2024-01-04T01:00:00Z")],
                   [{"timestamp": "2024-01-04T00:00:00Z", "close": 125}]]
        for key, value in (("close", None), ("close", "125"), ("close", True), ("close", 0),
                           ("close", -1), ("close", 250), ("volume", -1), ("high", None)):
            invalid.append([observation(4, **{key: value})])
        for rows in invalid:
            with self.subTest(rows=rows):
                state = open_state() | {"observations": rows}
                self.save(state)
                result = self.mark()
                self.assertEqual(result["status"], "NOT AVAILABLE")
                self.assertNotIn("unrealized_return", result)
                saved = json.loads(self.path.read_bytes())
                self.assertEqual(saved["virtual_position"], 1)
                self.assertEqual(saved["unrealized_marks"], [])
                self.assertEqual(saved["realized_results"], [])

    def test_acceptance_and_availability_are_checked_at_explicit_instant(self):
        for field in ("accepted_at", "available_at"):
            self.save(open_state() | {"observations": [observation(4, **{field: "2024-01-05T12:00:00Z"})]})
            self.assertEqual(self.mark()["status"], "NOT AVAILABLE")
            self.assertEqual(self.mark("2024-01-05T12:00:00Z")["status"], "AVAILABLE")

    def test_latest_invalid_or_unavailable_observation_does_not_hide_eligible_one(self):
        for later in (observation(5, close=500), observation(5, accepted_at="2024-01-07T00:00:00Z")):
            self.save(open_state() | {"observations": [observation(4), later]})
            self.assertEqual(self.mark("2024-01-06T00:00:00Z")["unrealized_return"], 0.25)

    def test_utc_instant_is_required_and_rejection_is_read_only(self):
        self.save(open_state())
        before = self.path.read_bytes()
        for instant in (None, "bad", "2024-01-05", "2024-01-05T00:00:00", "2024-01-05T00:00:00-05:00"):
            with self.subTest(instant=instant), self.assertRaisesRegex(ValueError, "UTC"):
                self.mark(instant)
            self.assertEqual(self.path.read_bytes(), before)

    def test_idempotence_restart_and_same_pair_at_later_instant(self):
        self.save(open_state())
        first = self.mark()
        before = self.path.read_bytes()
        self.assertEqual(first, self.mark())
        run = subprocess.run([sys.executable, "-B", str(Path(p.__file__).resolve()),
                              "mark-unrealized", "--state", str(self.path),
                              "--valuation-instant", "2024-01-05T00:00:00Z"],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), first)
        self.assertEqual(before, self.path.read_bytes())
        self.mark("2024-01-05T12:00:00Z")
        marks = json.loads(self.path.read_bytes())["unrealized_marks"]
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0]["valuation_instant"], "2024-01-05T00:00:00Z")

    def test_independent_inputs_produce_identical_bytes(self):
        self.save(open_state())
        second = self.root / "second.json"
        second.write_bytes(self.path.read_bytes())
        first_result = self.mark()
        self.assertEqual(first_result, p.mark_unrealized(second, "2024-01-05T00:00:00Z"))
        self.assertEqual(self.path.read_bytes(), second.read_bytes())

    def test_successive_marks_and_earlier_replay_without_future_mark(self):
        self.save(open_state())
        self.mark()
        state = json.loads(self.path.read_bytes())
        state["observations"].append(observation(5, 150))
        self.save(state)
        self.assertEqual(self.mark("2024-01-06T00:00:00Z")["unrealized_return"], 0.5)
        self.assertEqual(self.mark()["unrealized_return"], 0.25)
        result = self.mark("2024-01-04T12:00:00Z")
        self.assertEqual(result["status"], "NOT AVAILABLE")
        self.assertIsNone(result["mark_identity"])
        self.assertEqual(len(json.loads(self.path.read_bytes())["unrealized_marks"]), 2)

    def test_closed_position_has_no_current_mark_and_realized_results_are_preserved(self):
        self.save(open_state())
        self.mark()
        marked = json.loads(self.path.read_bytes())
        closed = formed_state((100, 125))
        self.save(closed)
        p.realize_results(self.path)
        closed = json.loads(self.path.read_bytes())
        closed["unrealized_marks"] = marked["unrealized_marks"]
        closed["unrealized_valuation"] = marked["unrealized_valuation"]
        self.save(closed)
        result = self.mark("2024-01-06T00:00:00Z")
        self.assertEqual(result["status"], "NO OPEN POSITION")
        self.assertIsNone(result["mark_identity"])
        saved = json.loads(self.path.read_bytes())
        self.assertEqual(saved["unrealized_marks"], marked["unrealized_marks"])
        self.assertEqual(saved["realized_results"], closed["realized_results"])
        self.assertEqual(saved["virtual_position"], 0)

    def test_conflicting_mark_or_duplicate_observation_is_rejected_without_write(self):
        self.save(open_state())
        self.mark()
        marked = json.loads(self.path.read_bytes())
        for kind in ("price", "duplicate"):
            state = copy.deepcopy(marked)
            if kind == "price":
                state["observations"][0]["close"] = 150
            else:
                state["observations"].append(copy.deepcopy(state["observations"][0]))
            self.save(state)
            before = self.path.read_bytes()
            with self.assertRaises(ValueError):
                self.mark()
            self.assertEqual(before, self.path.read_bytes())

    def test_zero_and_negative_returns_are_values_not_unavailable(self):
        for price, expected in ((100, 0.0), (75, -0.25)):
            self.save(open_state() | {"observations": [observation(4, price)]})
            result = self.mark()
            self.assertEqual(result["status"], "AVAILABLE")
            self.assertEqual(result["unrealized_return"], expected)

    def test_atomic_failure_and_retry(self):
        self.save(open_state())
        before = self.path.read_bytes()
        with patch("pipeline.os.replace", side_effect=OSError("controlled failure")):
            with self.assertRaises(OSError):
                self.mark()
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(self.mark()["status"], "AVAILABLE")

    def test_invalid_entry_is_rejected_without_write(self):
        for changes in ({"price": 0}, {"action": "EXIT"}, {"identity": "wrong"}, {"slippage": 1}):
            state = open_state()
            state["executions"][0].update(changes)
            self.save(state)
            before = self.path.read_bytes()
            with self.assertRaisesRegex(ValueError, "ENTER"):
                self.mark()
            self.assertEqual(before, self.path.read_bytes())
