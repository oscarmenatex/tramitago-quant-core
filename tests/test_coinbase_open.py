import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pipeline as p


NOW = datetime(2024, 1, 4, 12, tzinfo=timezone.utc)
STAMP = p.epoch("2024-01-04T00:00:00Z")
CLOSED = [[STAMP - (3 - i) * 86400, 9, 20, 10, 10 + i, 1] for i in range(3)]


def acquire(root, name, payload, now=NOW):
    raw = json.dumps(payload).encode()
    with patch("pipeline.urlopen", return_value=io.BytesIO(raw)) as request:
        result = p.observe(root, root / "state.json", root / name, now=now)
    url = urlparse(request.call_args.args[0].full_url)
    assert url.path == "/products/BTC-USD/candles"
    assert parse_qs(url.query)["granularity"] == ["86400"]
    return result


def prepare(root):
    acquire(root, "closed", CLOSED)
    p.decide(root / "state.json", root / "decision")
    p.execute_virtual(root / "state.json", root / "pending")


class CoinbaseOpenTests(unittest.TestCase):
    def test_controlled_acquisition_capture_execution_and_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare(root)
            before = json.loads((root / "state.json").read_bytes())
            with patch("pipeline.capture_open_event", wraps=p.capture_open_event) as capture:
                acquire(root, "mixed", CLOSED + [[STAMP, None, None, 25]])
            capture.assert_called_once()
            state = json.loads((root / "state.json").read_bytes())
            self.assertEqual(state["observations"], before["observations"])
            self.assertEqual(state["decisions"], before["decisions"])
            self.assertEqual(state["virtual_position"], 0)
            event = state["open_events"][0]
            self.assertEqual(set(event), {"identity", "instrument", "frequency_seconds", "timestamp", "open", "closed"})
            self.assertFalse(event["closed"])
            self.assertEqual(event["timestamp"], before["pending_actions"][0]["expected_execution_timestamp"])
            execution = p.execute_virtual(root / "state.json", root / "executed")
            self.assertEqual(execution["new_executions"][0]["price"], 25)
            self.assertEqual(execution["new_executions"][0]["costs"], 0)
            self.assertEqual(execution["new_executions"][0]["slippage"], 0)
            original = (root / "state.json").read_bytes()
            acquire(root, "replay", CLOSED + [[STAMP, None, None, 25]])
            self.assertEqual(original, (root / "state.json").read_bytes())
            self.assertEqual(p.execute_virtual(root / "state.json", root / "again")["new_executions"], [])

    def test_missing_and_invalid_open_keep_pending(self):
        for row in ([], [STAMP], *[[STAMP, None, None, value] for value in
                                 (None, True, "25", 0, -1, float("nan"), float("inf"))]):
            with self.subTest(row=row), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                prepare(root)
                acquire(root, "invalid", [row] if row else [])
                result = p.execute_virtual(root / "state.json", root / "execution")
                self.assertEqual(result["new_executions"], [])
                self.assertEqual(len(result["pending_actions"]), 1)
                self.assertEqual(result["virtual_position"], 0)
                self.assertFalse(json.loads((root / "state.json").read_bytes()).get("open_events"))

    def test_determinism_and_capture_before_decision(self):
        states = []
        results = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                results.append(acquire(root, "mixed", CLOSED + [[STAMP, "ignored", {}, 25, "ignored"]]))
                p.decide(root / "state.json", root / "decision")
                result = p.execute_virtual(root / "state.json", root / "execution")
                self.assertEqual(result["execution_count"], 1)
                states.append((root / "state.json").read_bytes())
        self.assertEqual(states[0], states[1])
        self.assertEqual(results[0], results[1])

    def test_boundary_future_and_closed_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = acquire(root, "boundary", CLOSED + [[STAMP + 86400, None, None, 30]],
                             datetime(2024, 1, 4, tzinfo=timezone.utc))
            self.assertEqual(len(result["new_observations"]), 3)
            self.assertEqual(result["captured_open_events"], [])
            before = (root / "state.json").read_bytes()
            with self.assertRaises(ValueError):
                acquire(root, "bad-closed", [[STAMP - 86400, None, None, 25]])
            self.assertEqual(before, (root / "state.json").read_bytes())

    def test_conflicting_open_does_not_replace_captured_price(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare(root)
            acquire(root, "first", [[STAMP, None, None, 25]])
            result = acquire(root, "conflict", [[STAMP, None, None, 26]])
            self.assertEqual(len(result["rejected_open_events"]), 1)
            self.assertEqual(p.execute_virtual(root / "state.json", root / "execution")
                             ["new_executions"][0]["price"], 25)
