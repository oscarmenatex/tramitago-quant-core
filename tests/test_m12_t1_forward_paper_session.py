"""Focused local proof for isolated, recoverable FORWARD_PAPER preparation."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pipeline as p


class M12T1ForwardPaperSessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.session_path = self.root / "session" / "state.json"
        self.configuration_path = self.root / "configuration" / "forward-paper.json"
        self.invocation_path = self.root / "invocations" / "one.json"
        self.session_id = "PAPER_SESSION|m12-t1-forward-001"
        self.started_at = "2026-09-16T00:00:00Z"
        self.processing_instant = "2026-09-16T00:05:00Z"

    def prepare(self, root=None, **overrides):
        root = root or self.root
        values = {
            "session_path": self.session_path if root == self.root else root / "session.json",
            "configuration_path": (self.configuration_path if root == self.root
                                   else root / "configuration.json"),
            "invocation_path": self.invocation_path if root == self.root else root / "invocation.json",
            "session_id": self.session_id,
            "started_at": self.started_at,
            "processing_instant_utc": self.processing_instant,
        }
        values.update(overrides)
        return p.prepare_forward_paper_invocation(**values)

    def test_creates_independent_initial_session_and_versioned_configuration(self):
        result = self.prepare()
        state = result["session"]
        configuration = result["configuration"]
        invocation = result["invocation"]

        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["session_created"])
        self.assertTrue(result["configuration_created"])
        self.assertTrue(result["configuration_association_created"])
        self.assertTrue(result["invocation_created"])
        self.assertEqual(state["mode"], "PAPER")
        self.assertEqual(state["internal_position_state"], "FLAT")
        self.assertEqual(state["broker_position_observed"], "UNKNOWN")
        for name in ("warmup_observations", "processed_observations", "decisions",
                     "paper_risk_evaluations", "executions", "pending_actions"):
            self.assertEqual(state.get(name, []), [])
        self.assertFalse(state.get("proposals", []))
        self.assertEqual(configuration, p.forward_paper_configuration())
        self.assertEqual(configuration["cycle_mode"], "FORWARD_PAPER")
        self.assertEqual(configuration["session_mode"], "PAPER")
        self.assertEqual(configuration["instrument"], "BTC-USD")
        self.assertEqual(configuration["granularity_seconds"], 86400)
        self.assertEqual(configuration["strategy"], "SMA3")
        self.assertEqual(configuration["position_model"], "LONG_ONLY_0_1")
        self.assertEqual(configuration["risk_profile"], "PAPER_SCALE_80K_V1")
        self.assertEqual(configuration["data_source"], "COINBASE_PUBLIC")
        self.assertTrue(configuration["configuration_version"])
        self.assertTrue(configuration["strategy_version"])
        self.assertEqual(invocation["session_id"], self.session_id)
        self.assertEqual(invocation["configuration_id"], configuration["configuration_id"])
        self.assertEqual(invocation["cycle_mode"], "FORWARD_PAPER")
        self.assertEqual(invocation["processing_instant_utc"], self.processing_instant)
        self.assertNotIn("status", invocation)
        self.assertEqual(result["network_calls"], 0)
        self.assertFalse(result["credentials_used"])
        self.assertEqual(result["paper_orders_sent"], 0)
        self.assertEqual(result["live_orders_sent"], 0)

    def test_reloads_preparation_from_a_later_process(self):
        first = self.prepare()
        worker = (
            "import json, sys; import pipeline as p; "
            "prepared = p.load_forward_paper_preparation(sys.argv[1], sys.argv[2], sys.argv[3]); "
            "print(json.dumps(prepared, sort_keys=True))"
        )
        process = subprocess.run(
            [sys.executable, "-B", "-c", worker, str(self.session_path),
             str(self.configuration_path), str(self.invocation_path)],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        recovered = json.loads(process.stdout)
        self.assertEqual(recovered["session"], first["session"])
        self.assertEqual(recovered["configuration"], first["configuration"])
        self.assertEqual(recovered["invocation"], first["invocation"])

    def test_repeat_is_idempotent_and_conflicting_invocation_is_rejected(self):
        first = self.prepare()
        before = tuple(path.read_bytes() for path in (
            self.session_path, self.configuration_path, self.invocation_path))
        replay = self.prepare()
        self.assertFalse(replay["session_created"])
        self.assertFalse(replay["configuration_created"])
        self.assertFalse(replay["configuration_association_created"])
        self.assertFalse(replay["invocation_created"])
        self.assertEqual(tuple(path.read_bytes() for path in (
            self.session_path, self.configuration_path, self.invocation_path)), before)
        self.assertEqual(replay["configuration"]["configuration_id"],
                         first["configuration"]["configuration_id"])
        self.assertEqual(replay["invocation"]["invocation_id"],
                         first["invocation"]["invocation_id"])
        with self.assertRaisesRegex(ValueError, "cannot be silently replaced"):
            self.prepare(processing_instant_utc="2026-09-16T00:06:00Z")
        self.assertEqual(self.invocation_path.read_bytes(), before[2])

    def test_rejects_missing_non_utc_and_pre_session_processing_instants(self):
        for value in (None, "", "2026-09-16T00:05:00", "2026-09-16T00:05:00+00:00"):
            with self.subTest(value=value):
                root = self.root / f"invalid-{str(value).replace(':', '-') or 'empty'}"
                with self.assertRaises(ValueError):
                    self.prepare(root, processing_instant_utc=value)
                self.assertFalse((root / "session.json").exists())
                self.assertFalse((root / "configuration.json").exists())
                self.assertFalse((root / "invocation.json").exists())
        with self.assertRaisesRegex(ValueError, "cannot precede"):
            self.prepare(self.root / "before", processing_instant_utc="2026-09-15T23:59:59Z")

    def test_rejects_live_and_historical_replay_modes_before_writing(self):
        for name, values in (
                ("live", {"session_mode": "LIVE"}),
                ("historical", {"cycle_mode": "HISTORICAL_REPLAY"})):
            with self.subTest(name=name):
                root = self.root / name
                with self.assertRaises(ValueError):
                    self.prepare(root, **values)
                self.assertFalse((root / "session.json").exists())
                self.assertFalse((root / "configuration.json").exists())
                self.assertFalse((root / "invocation.json").exists())

    def test_rejects_a_preexisting_historical_paper_session(self):
        p.initialize_paper_session(
            self.session_path, self.root / "historical-output", self.session_id,
            "PAPER", self.started_at)
        historical_observations = [
            {"identity": f"BTC-USD|86400|{timestamp}", "instrument": "BTC-USD",
             "timestamp": timestamp, "open": opening, "high": high, "low": low,
             "close": close, "volume": volume}
            for timestamp, opening, high, low, close, volume in (
                ("2026-09-13T00:00:00Z", 10.0, 12.0, 9.0, 11.0, 1.0),
                ("2026-09-14T00:00:00Z", 11.0, 13.0, 10.0, 12.0, 1.0),
                ("2026-09-15T00:00:00Z", 12.0, 14.0, 11.0, 13.0, 1.0),
            )
        ]
        p.load_paper_session_warmup(
            self.session_path, self.root / "warmup-output", historical_observations)
        before = self.session_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "independent initial PAPER session"):
            self.prepare()
        self.assertEqual(self.session_path.read_bytes(), before)
        self.assertFalse(self.configuration_path.exists())
        self.assertFalse(self.invocation_path.exists())

    def test_rejects_a_conflicting_persisted_configuration_without_replacement(self):
        self.prepare()
        registry = json.loads(self.configuration_path.read_bytes())
        registry["configurations"][0]["risk_profile"] = "OTHER_PROFILE"
        self.configuration_path.write_bytes(p.encoded(registry))
        before = self.configuration_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "configuration registry is invalid"):
            self.prepare()
        self.assertEqual(self.configuration_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
