"""Focused tests for M1.3-T8: the one non-interactive entrypoint a
persistent remote host repeats -- activates, terminates, and goes back to
waiting without any daily manual command."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import pipeline as p

PIPELINE_PY = str(Path(__file__).resolve().parent.parent / "pipeline.py")


class M13T8EntrypointFunctionTests(unittest.TestCase):
    STARTED = "2026-09-14T00:00:00Z"
    PROCESSING = "2026-09-18T00:15:00Z"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name) / "data"

    def bootstrap(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        configuration = p.forward_paper_configuration()
        (self.data_dir / "configuration.json").write_bytes(p.encoded({
            "schema_version": p.FORWARD_PAPER_CONFIGURATION_REGISTRY_SCHEMA_VERSION,
            "configurations": [configuration], "session_configurations": [],
        }))
        p.initialize_paper_session(
            self.data_dir / "session" / "state.json", self.data_dir / "session-init",
            "PAPER_SESSION|t8-entrypoint", "PAPER", self.STARTED)
        p.prepare_forward_paper_activation_policy(
            self.data_dir / "policy.json", self.data_dir / "configuration.json")
        p.prepare_forward_paper_invocation(
            self.data_dir / "session" / "state.json", self.data_dir / "configuration.json",
            self.data_dir / "invocation.json", "PAPER_SESSION|t8-entrypoint",
            self.STARTED, self.PROCESSING)

    def _fake_m12(self, terminal):
        def fake(*args, **kwargs):
            preparation = p.load_forward_paper_preparation(
                self.data_dir / "session" / "state.json",
                self.data_dir / "configuration.json", self.data_dir / "invocation.json")
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
            (self.data_dir / "m12-invocation-result.json").write_bytes(p.encoded(record))
            return {"status": "PASS", **record}
        return fake

    def test_owner_id_is_created_once_and_reused(self):
        self.bootstrap()
        first = p.forward_paper_activation_entrypoint(
            self.data_dir, now_utc="2026-09-17T00:00:00Z")
        owner_path = self.data_dir / p.FORWARD_PAPER_HOST_OWNER_FILENAME
        self.assertTrue(owner_path.exists())
        persisted = owner_path.read_text(encoding="utf-8").strip()
        self.assertEqual(first["owner_id"], persisted)

        second = p.forward_paper_activation_entrypoint(
            self.data_dir, now_utc="2026-09-17T00:05:00Z")
        self.assertEqual(second["owner_id"], persisted)

    def test_corrupt_owner_id_file_fails_closed(self):
        self.bootstrap()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / p.FORWARD_PAPER_HOST_OWNER_FILENAME).write_text(
            "not-a-uuid", encoding="utf-8")
        with self.assertRaises(ValueError):
            p.forward_paper_activation_entrypoint(self.data_dir, now_utc="2026-09-17T00:00:00Z")

    def test_missing_bootstrap_is_blocked_not_crashed(self):
        result = p.forward_paper_activation_entrypoint(
            self.data_dir, now_utc="2026-09-17T00:00:00Z")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("INVALID_T1_ASSOCIATION", result["reason"])

    def test_nothing_due_before_slot_touches_no_activation_state(self):
        self.bootstrap()
        result = p.forward_paper_activation_entrypoint(
            self.data_dir, now_utc="2026-09-17T00:00:00Z")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["activation_result"], "NOTHING_DUE")
        self.assertFalse((self.data_dir / "activation-receipts").exists())

    def test_due_activation_completes_and_replay_never_repeats_m12(self):
        self.bootstrap()
        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=self._fake_m12("COMPLETED")):
            first = p.forward_paper_activation_entrypoint(
                self.data_dir, now_utc="2026-09-18T00:15:01Z")
        self.assertEqual(first["status"], "PASS")
        self.assertEqual(first["m12_terminal_result"], "COMPLETED")

        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=AssertionError("replay must not call M1.2 again")):
            second = p.forward_paper_activation_entrypoint(
                self.data_dir, now_utc="2026-09-18T00:16:00Z")
        self.assertEqual(second["status"], "PASS")
        self.assertEqual(second["m12_terminal_result"], "COMPLETED")

    def test_omitted_now_uses_the_real_wall_clock(self):
        self.bootstrap()
        with patch("pipeline.datetime") as mock_datetime:
            import datetime as real_datetime
            mock_datetime.now.return_value = real_datetime.datetime(
                2026, 9, 17, 0, 0, 0, tzinfo=real_datetime.timezone.utc)
            mock_datetime.fromisoformat = real_datetime.datetime.fromisoformat
            result = p.forward_paper_activation_entrypoint(self.data_dir)
        self.assertEqual(result["now_utc"], "2026-09-17T00:00:00Z")


class M13T8RealChainBootstrapTests(unittest.TestCase):
    """Exercise the entrypoint through the REAL M1.1/M1.2 acquisition,
    selection, and decision chain (only the HTTP transport is a double) --
    this is what would have caught the deploy/README.md bootstrap bug
    where started_at == processing_instant_utc silently made every
    observation permanently ineligible."""

    NOW = "2026-09-20T00:15:01Z"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name) / "data"

    @staticmethod
    def bootstrap_correctly(data_dir, *, started_at, now_utc):
        """Mirrors deploy/README.md's corrected bootstrap: started_at must
        be strictly earlier than the invocation's processing instant."""
        data_dir.mkdir(parents=True, exist_ok=True)
        configuration = p.forward_paper_configuration()
        (data_dir / "configuration.json").write_bytes(p.encoded({
            "schema_version": p.FORWARD_PAPER_CONFIGURATION_REGISTRY_SCHEMA_VERSION,
            "configurations": [configuration], "session_configurations": [],
        }))
        session_id = "PAPER_SESSION|t8-real-chain"
        p.initialize_paper_session(
            data_dir / "session" / "state.json", data_dir / "session-init",
            session_id, "PAPER", started_at)
        p.prepare_forward_paper_activation_policy(
            data_dir / "policy.json", data_dir / "configuration.json")
        p.prepare_forward_paper_invocation(
            data_dir / "session" / "state.json", data_dir / "configuration.json",
            data_dir / "invocation.json", session_id, started_at, now_utc)

    @staticmethod
    def synthetic_coinbase_transport():
        candles = [
            [p.epoch(f"2026-09-{day:02d}T00:00:00Z"), close - 1, close + 1,
             close - 0.5, close, 100.0]
            for day, close in ((16, 100.0), (17, 105.0), (18, 110.0), (19, 130.0))
        ]

        def transport(url, headers, timeout_seconds):
            return json.dumps(candles).encode("utf-8")
        return transport

    def test_broken_bootstrap_with_equal_started_at_is_permanently_ineligible(self):
        """The exact bug found on the real Oracle Cloud VM: started_at ==
        processing_instant_utc makes every observation forever BLOCKED."""
        same_instant = self.NOW
        self.bootstrap_correctly(self.data_dir, started_at=same_instant, now_utc=same_instant)
        result = p.forward_paper_activation_entrypoint(
            self.data_dir, now_utc=self.NOW,
            transport=self.synthetic_coinbase_transport())
        self.assertEqual(result["m12_terminal_result"], "BLOCKED")

    def test_correct_bootstrap_processes_a_real_eligible_observation(self):
        self.bootstrap_correctly(
            self.data_dir, started_at="2026-09-19T00:00:00Z", now_utc=self.NOW)
        result = p.forward_paper_activation_entrypoint(
            self.data_dir, now_utc=self.NOW,
            transport=self.synthetic_coinbase_transport())
        self.assertEqual(result["status"], "PASS")
        self.assertIn(result["m12_terminal_result"], ("COMPLETED",))
        evidence = json.loads((self.data_dir / "m12-invocation-result.json").read_bytes())["evidence"]
        self.assertIsNotNone(evidence["selection_receipt_id"])
        selection = json.loads((self.data_dir / "selection.json").read_bytes())
        self.assertEqual(selection["result"], "OPERATIONAL_OBSERVATION_ACCEPTED")
        self.assertEqual(selection["operational_observation"]["identity"],
                         "BTC-USD|86400|2026-09-19T00:00:00Z")
        self.assertEqual(len(selection["warmup_observations"]), 3)

        # Replay at a later instant must not repeat the acquisition or decision.
        replay = p.forward_paper_activation_entrypoint(
            self.data_dir, now_utc="2026-09-20T00:20:00Z",
            transport=lambda *a: (_ for _ in ()).throw(
                AssertionError("must not re-acquire once already processed")))
        self.assertEqual(replay["status"], "PASS")
        self.assertEqual(replay["m12_terminal_result"], "COMPLETED")

    def test_entrypoint_continues_on_a_new_daily_slot_after_paper_state_changes(self):
        self.bootstrap_correctly(
            self.data_dir, started_at="2026-09-19T00:00:00Z", now_utc=self.NOW)
        first = p.forward_paper_activation_entrypoint(
            self.data_dir, now_utc=self.NOW,
            transport=self.synthetic_coinbase_transport())
        state_after_day_one = (self.data_dir / "session" / "state.json").read_bytes()
        public_entrypoint = p.run_forward_paper_invocation

        with patch.object(p, "run_forward_paper_invocation",
                          side_effect=public_entrypoint) as m12:
            second = p.forward_paper_activation_entrypoint(
                self.data_dir, now_utc="2026-09-21T00:15:01Z",
                transport=lambda *args: (_ for _ in ()).throw(
                    AssertionError("the previous M1.2 result must replay")))

        self.assertEqual(first["status"], "PASS")
        self.assertEqual(second["status"], "PASS")
        self.assertEqual(second["m12_terminal_result"], "COMPLETED")
        self.assertEqual(m12.call_count, 1)
        self.assertNotEqual(first["activation_id"], second["activation_id"])
        self.assertEqual((self.data_dir / "session" / "state.json").read_bytes(),
                         state_after_day_one)


class M13T8CliSubprocessTests(unittest.TestCase):
    """Prove the file is directly invocable the way a cron entry would call
    it: no interactive input, structured JSON on stdout, exit code 0 for
    every normal terminal outcome."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name) / "data"
        configuration = p.forward_paper_configuration()
        (self.data_dir / "configuration.json").parent.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "configuration.json").write_bytes(p.encoded({
            "schema_version": p.FORWARD_PAPER_CONFIGURATION_REGISTRY_SCHEMA_VERSION,
            "configurations": [configuration], "session_configurations": [],
        }))
        p.initialize_paper_session(
            self.data_dir / "session" / "state.json", self.data_dir / "session-init",
            "PAPER_SESSION|t8-cli", "PAPER", "2026-09-14T00:00:00Z")
        p.prepare_forward_paper_activation_policy(
            self.data_dir / "policy.json", self.data_dir / "configuration.json")
        p.prepare_forward_paper_invocation(
            self.data_dir / "session" / "state.json", self.data_dir / "configuration.json",
            self.data_dir / "invocation.json", "PAPER_SESSION|t8-cli",
            "2026-09-14T00:00:00Z", "2026-09-18T00:15:00Z")

    def run_cli(self, now):
        return subprocess.run(
            [sys.executable, "-B", PIPELINE_PY, "run-forward-paper-activation",
             "--data-dir", str(self.data_dir), "--now", now],
            cwd=Path(PIPELINE_PY).parent, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)

    def test_cli_nothing_due_exits_zero_with_json_and_no_stdin_read(self):
        completed = self.run_cli("2026-09-17T00:00:00Z")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["activation_result"], "NOTHING_DUE")

    def test_cli_persists_owner_id_across_two_real_invocations(self):
        first = self.run_cli("2026-09-17T00:00:00Z")
        self.assertEqual(first.returncode, 0, first.stderr)
        owner_path = self.data_dir / p.FORWARD_PAPER_HOST_OWNER_FILENAME
        self.assertTrue(owner_path.exists())
        owner_id = owner_path.read_text(encoding="utf-8").strip()

        second = self.run_cli("2026-09-17T00:05:00Z")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(owner_path.read_text(encoding="utf-8").strip(), owner_id)
        self.assertEqual(json.loads(second.stdout)["owner_id"], owner_id)

    def test_missing_data_dir_argument_fails_closed_via_argparse(self):
        completed = subprocess.run(
            [sys.executable, "-B", PIPELINE_PY, "run-forward-paper-activation"],
            cwd=Path(PIPELINE_PY).parent, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        self.assertNotEqual(completed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
