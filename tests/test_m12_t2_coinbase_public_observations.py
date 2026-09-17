"""Focused local proof for normalized, closed Coinbase public observations."""

import inspect
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pipeline as p


class M12T2CoinbasePublicObservationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.session_path = self.root / "session" / "state.json"
        self.configuration_path = self.root / "configuration" / "forward-paper.json"
        self.invocation_path = self.root / "invocations" / "one.json"
        self.dataset_path = self.root / "datasets" / "coinbase-public.json"
        self.session_id = "PAPER_SESSION|m12-t2-observations-001"
        self.started_at = "2026-01-01T00:00:00Z"
        self.processing_instant = "2026-01-06T12:00:00Z"
        p.prepare_forward_paper_invocation(
            self.session_path, self.configuration_path, self.invocation_path,
            self.session_id, self.started_at, self.processing_instant)

    @staticmethod
    def coinbase_rows():
        values = (
            ("2026-01-06T00:00:00Z", 14.0, 18.0, 15.0, 17.0, 14.0),
            ("2026-01-05T00:00:00Z", 13.0, 17.0, 14.0, 16.0, 13.0),
            ("2026-01-04T00:00:00Z", 12.0, 16.0, 13.0, 15.0, 12.0),
            ("2026-01-03T00:00:00Z", 11.0, 15.0, 12.0, 14.0, 11.0),
            ("2026-01-02T00:00:00Z", 10.0, 14.0, 11.0, 13.0, 10.0),
        )
        return [[p.epoch(timestamp), low, high, opening, close, volume]
                for timestamp, low, high, opening, close, volume in values]

    def transport(self, payload, calls):
        def get(url, headers, timeout_seconds):
            calls.append((url, headers, timeout_seconds))
            return json.dumps(payload).encode("utf-8")
        return get

    def acquire(self, payload=None, calls=None, **kwargs):
        calls = calls if calls is not None else []
        result = p.acquire_forward_paper_observations(
            self.session_path, self.configuration_path, self.invocation_path,
            self.dataset_path, transport=self.transport(payload or self.coinbase_rows(), calls),
            **kwargs)
        return result, calls

    def test_injected_public_transport_normalizes_only_closed_observations(self):
        state_before = self.session_path.read_bytes()
        with patch("pipeline.urlopen", side_effect=AssertionError("network forbidden in tests")):
            result, calls = self.acquire()
        dataset = result["dataset"]
        self.assertTrue(result["created"])
        self.assertEqual(result["public_transport_calls"], 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], p._coinbase_public_request_headers())
        self.assertEqual(calls[0][2], 30)
        self.assertIn("granularity=86400", calls[0][0])
        self.assertEqual(dataset["source"], "COINBASE_PUBLIC")
        self.assertEqual(dataset["closed_observation_count"], 4)
        self.assertEqual(dataset["excluded_open_observation_count"], 1)
        self.assertEqual(dataset["accepted_at_utc"], self.processing_instant)
        self.assertEqual([item["interval_start_utc"] for item in dataset["observations"]], [
            "2026-01-02T00:00:00Z", "2026-01-03T00:00:00Z",
            "2026-01-04T00:00:00Z", "2026-01-05T00:00:00Z",
        ])
        self.assertEqual(len({item["identity"] for item in dataset["observations"]}), 4)
        for item in dataset["observations"]:
            self.assertEqual(set(item), {
                "identity", "instrument", "granularity_seconds", "interval_start_utc",
                "interval_end_utc", "accepted_at_utc", "open", "high", "low",
                "close", "volume", "source",
            })
            self.assertEqual(item["identity"],
                             f"BTC-USD|86400|{item['interval_start_utc']}")
            self.assertEqual(item["interval_end_utc"], p.iso(
                p.epoch(item["interval_start_utc"]) + 86400))
            self.assertLessEqual(item["interval_end_utc"], self.processing_instant)
            self.assertEqual(item["accepted_at_utc"], self.processing_instant)
            self.assertEqual(item["source"], "COINBASE_PUBLIC")
        self.assertEqual(self.session_path.read_bytes(), state_before)

    def test_t2_uses_phase1_public_headers_without_authentication(self):
        expected_headers = p._coinbase_public_request_headers()
        state_before = self.session_path.read_bytes()

        def strict_transport(url, headers, timeout_seconds):
            if not headers.get("User-Agent") or headers.get("Accept") != "application/json":
                raise AssertionError("missing public Phase 1 headers")
            if any(name in headers for name in ("Authorization", "Cookie", "X-API-Key")):
                raise AssertionError("authenticated header is forbidden")
            self.assertEqual(headers, expected_headers)
            self.assertEqual(timeout_seconds, 30)
            return json.dumps(self.coinbase_rows()).encode("utf-8")

        with patch("pipeline.urlopen", side_effect=AssertionError("network forbidden in tests")):
            result = p.acquire_forward_paper_observations(
                self.session_path, self.configuration_path, self.invocation_path,
                self.dataset_path, transport=strict_transport)
        self.assertEqual(result["dataset"]["closed_observation_count"], 4)
        self.assertEqual(result["dataset"]["excluded_open_observation_count"], 1)
        self.assertEqual(self.session_path.read_bytes(), state_before)

    def test_persistence_reload_and_repeat_are_idempotent(self):
        first, calls = self.acquire()
        before = (self.session_path.read_bytes(), self.dataset_path.read_bytes())
        reloaded = p.load_forward_paper_observation_dataset(
            self.session_path, self.configuration_path, self.invocation_path, self.dataset_path)
        replay, replay_calls = self.acquire()
        self.assertEqual(reloaded, first["dataset"])
        self.assertFalse(replay["created"])
        self.assertEqual(replay["public_transport_calls"], 0)
        self.assertEqual(replay_calls, [])
        self.assertEqual(self.dataset_path.read_bytes(), before[1])
        self.assertEqual(self.session_path.read_bytes(), before[0])
        self.assertEqual(replay["dataset"]["dataset_id"], first["dataset"]["dataset_id"])
        self.assertEqual(len(calls), 1)

    def test_invalid_ohlcv_timestamp_duplicates_and_insufficient_data_are_rejected(self):
        invalid_ohlcv = self.coinbase_rows()
        invalid_ohlcv[1][2] = 1.0
        bad_timestamp = self.coinbase_rows()
        bad_timestamp[1][0] += 1
        duplicate = self.coinbase_rows()
        duplicate[-1] = list(duplicate[-2])
        insufficient = self.coinbase_rows()[1:]
        insufficient = insufficient[1:]
        for name, payload in (
                ("ohlcv", invalid_ohlcv), ("timestamp", bad_timestamp),
                ("duplicate", duplicate), ("insufficient", insufficient)):
            with self.subTest(name=name):
                root = self.root / name
                with self.assertRaises(ValueError):
                    p.acquire_forward_paper_observations(
                        self.session_path, self.configuration_path, self.invocation_path,
                        root / "dataset.json", transport=self.transport(payload, []))
                self.assertFalse((root / "dataset.json").exists())

    def test_network_failure_cannot_publish_partial_dataset(self):
        def failing_transport(url, headers, timeout_seconds):
            raise OSError("unavailable")
        with self.assertRaises(OSError):
            p.acquire_forward_paper_observations(
                self.session_path, self.configuration_path, self.invocation_path,
                self.dataset_path, transport=failing_transport)
        self.assertFalse(self.dataset_path.exists())

    def test_conflicting_dataset_is_rejected_without_replacement(self):
        self.acquire()
        corrupted = json.loads(self.dataset_path.read_bytes())
        corrupted["source"] = "OTHER_SOURCE"
        self.dataset_path.write_bytes(p.encoded(corrupted))
        before = self.dataset_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "observation dataset is invalid"):
            self.acquire()
        self.assertEqual(self.dataset_path.read_bytes(), before)

    def test_implementation_has_no_implicit_clock_or_operational_processing(self):
        source = "\n".join((
            inspect.getsource(p.acquire_forward_paper_observations),
            inspect.getsource(p._forward_paper_normalized_observations),
            inspect.getsource(p._forward_paper_query_window),
        ))
        for forbidden in ("datetime.now", "datetime.utcnow", "time.time", "now()"):
            self.assertNotIn(forbidden, source)
        result, _ = self.acquire()
        state = p.load_paper_session(self.session_path)
        self.assertEqual(result["broker_network_calls"], 0)
        self.assertFalse(result["credentials_used"])
        self.assertEqual([state.get(name, []) for name in (
            "processed_observations", "decisions", "paper_risk_evaluations",
            "executions", "pending_actions", "proposals")], [[], [], [], [], [], []])

    def test_m11_and_m12_t1_contracts_remain_loadable(self):
        historical = p.load_paper_session("artifacts/m1.1-t1-paper-session/state.json")
        preparation = p.load_forward_paper_preparation(
            self.session_path, self.configuration_path, self.invocation_path)
        self.assertEqual(historical["mode"], "PAPER")
        self.assertEqual(preparation["configuration"]["cycle_mode"], "FORWARD_PAPER")
        self.assertEqual(preparation["invocation"]["processing_instant_utc"],
                         self.processing_instant)


if __name__ == "__main__":
    unittest.main()
