"""Focused proof for terminal FORWARD_PAPER invocation results."""

import json
from pathlib import Path
import tempfile
import unittest
import inspect
from unittest.mock import patch

import pipeline as p


class M12T5ForwardPaperInvocationResultTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.session_path = self.root / "session" / "state.json"
        self.configuration_path = self.root / "configuration" / "forward-paper.json"
        self.invocation_path = self.root / "invocation.json"
        self.dataset_path = self.root / "dataset.json"
        self.selection_path = self.root / "selection.json"
        self.fixture_path = self.root / "fixture.json"
        self.acceptance_path = self.root / "acceptance.json"
        self.indicator_path = self.root / "indicator.json"
        self.cycle_path = self.root / "cycle.json"
        self.result_path = self.root / "invocation-result.json"
        self.output = self.root / "output"
        self.session_id = "PAPER_SESSION|m12-t5-result-001"
        self.started_at = "2026-01-02T12:00:00Z"
        self.processing_instant = "2026-01-05T12:00:00Z"
        p.prepare_forward_paper_invocation(
            self.session_path, self.configuration_path, self.invocation_path,
            self.session_id, self.started_at, self.processing_instant)

    @staticmethod
    def rows():
        return [[p.epoch(f"2026-01-{day:02d}T00:00:00Z"), day + 8, day + 12,
                 day + 9, day + 10, 1.0] for day in range(1, 6)]

    def invoke(self, transport=None):
        calls = []

        def wrapped(url, headers, timeout_seconds):
            calls.append((url, headers, timeout_seconds))
            return json.dumps(self.rows()).encode("utf-8")

        result = p.run_forward_paper_invocation(
            self.session_path, self.configuration_path, self.invocation_path,
            self.dataset_path, self.selection_path, self.fixture_path,
            self.acceptance_path, self.indicator_path, self.cycle_path,
            self.result_path, self.output, self.session_id, self.started_at,
            self.processing_instant, "2026-01-05T12:01:00Z",
            transport=transport or wrapped)
        return result, calls

    def m11_observations(self, accepted_at=None):
        observations = []
        for day, low, high, opening, close, volume in self.rows()[:4]:
            timestamp = p.iso(day)
            observations.append({
                "identity": f"BTC-USD|86400|{timestamp}", "instrument": "BTC-USD",
                "timestamp": timestamp, "open": float(opening), "high": float(high),
                "low": float(low), "close": float(close), "volume": float(volume),
            })
        if accepted_at is None:
            accepted_at = self.processing_instant
        observations[3]["accepted_at_utc"] = accepted_at
        return observations

    def prepare_forward_fixture(self, name="forward", observations=None):
        root = self.root / name
        state_path = root / "state.json"
        fixture_path = root / "fixture.json"
        p.prepare_forward_paper_invocation(
            state_path, root / "configuration.json", root / "invocation.json",
            self.session_id + "|" + name, self.started_at, self.processing_instant)
        preparation = p.load_forward_paper_preparation(
            state_path, root / "configuration.json", root / "invocation.json")
        return root, state_path, fixture_path, preparation, observations or self.m11_observations()

    def test_completed_result_is_persisted_and_reloaded(self):
        result, calls = self.invoke()
        self.assertEqual(result["terminal_result"], "COMPLETED")
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["mode"], "FORWARD_PAPER")
        self.assertEqual(result["evidence"]["dataset_id"], result["dataset_id"])
        self.assertTrue(result["selection_receipt_id"])
        self.assertTrue(result["canonical_cycle_id"])
        self.assertEqual(result["evidence"]["sma3"]["sma_close_3"], 13.0)
        self.assertEqual(result["evidence"]["decision"]["decision"]["decision"], "ENTER")
        self.assertEqual(result["evidence"]["risk"]["risk_result"], "ALLOWED")
        self.assertEqual(result["evidence"]["position_after"], "LONG")
        self.assertEqual(result["evidence"]["broker_position_observed"], "UNKNOWN")
        persisted = p._load_forward_paper_invocation_result(self.result_path)
        self.assertEqual(persisted["invocation_result_id"], result["invocation_result_id"])
        self.assertEqual(persisted["evidence"], result["evidence"])

    def test_reload_matches_independent_canonical_contract(self):
        result, _ = self.invoke()
        preparation = p.load_forward_paper_preparation(
            self.session_path, self.configuration_path, self.invocation_path)
        dataset = p.load_forward_paper_observation_dataset(
            self.session_path, self.configuration_path, self.invocation_path,
            self.dataset_path)
        loaded_preparation, loaded_dataset, selection = p._load_forward_paper_selection_receipt(
            self.session_path, self.configuration_path, self.invocation_path,
            self.dataset_path, self.selection_path)
        cycle = json.loads(self.cycle_path.read_bytes())
        indicator = json.loads(
            (self.output / "indicator" / "paper-sma3-indicator.json").read_bytes())
        decision = json.loads(
            (self.output / "decision" / "paper-sma3-decision.json").read_bytes())
        risk = json.loads((self.output / "risk" / "paper-risk.json").read_bytes())
        state = p.load_paper_session(self.session_path)
        reloaded = p._load_forward_paper_invocation_result(self.result_path)
        self.assertEqual(loaded_preparation, preparation)
        self.assertEqual(loaded_dataset, dataset)
        evidence = {
            "dataset_id": dataset["dataset_id"],
            "selection_receipt_id": selection["selection_id"],
            "cycle_id": cycle["cycle_id"],
            "operational_observation": selection["operational_observation"],
            "sma3": indicator,
            "decision": decision,
            "risk": risk,
            "position_before": cycle["position_before"],
            "position_after": cycle["position_after"],
            "broker_position_observed": cycle["broker_position_observed"],
        }
        content = {
            "configuration_id": preparation["configuration"]["configuration_id"],
            "canonical_cycle_id": cycle["cycle_id"],
            "dataset_id": dataset["dataset_id"],
            "evidence": evidence,
            "invocation_id": preparation["invocation"]["invocation_id"],
            "mode": "FORWARD_PAPER",
            "processing_instant_utc": preparation["invocation"]["processing_instant_utc"],
            "reason": "FORWARD_PAPER cycle completed",
            "selection_receipt_id": selection["selection_id"],
            "session_id": preparation["session"]["session_id"],
            "terminal_result": "COMPLETED",
        }
        expected = {
            "schema_version": p.FORWARD_PAPER_INVOCATION_RESULT_SCHEMA_VERSION,
            **content,
            "invocation_result_id": "FORWARD_PAPER_INVOCATION_RESULT|"
            + p.digest(p.encoded(content)),
        }
        self.assertEqual(reloaded, expected)
        self.assertEqual(reloaded["processing_instant_utc"],
                         preparation["invocation"]["processing_instant_utc"])
        self.assertEqual(reloaded["terminal_result"], cycle["terminal_result"])
        self.assertEqual(reloaded["evidence"]["sma3"], indicator)
        self.assertEqual(reloaded["evidence"]["decision"], decision)
        self.assertEqual(reloaded["evidence"]["risk"], risk)
        self.assertEqual(state["internal_position_state"], "LONG")
        operational = selection["operational_observation"]
        expected_processed = {
            "identity": operational["identity"],
            "instrument": operational["instrument"],
            "timestamp": operational["interval_start_utc"],
            "open": operational["open"], "high": operational["high"],
            "low": operational["low"], "close": operational["close"],
            "volume": operational["volume"],
            "accepted_at_utc": operational["accepted_at_utc"],
        }
        self.assertEqual(state["processed_observations"], [expected_processed])

    def test_exact_replay_does_not_acquire_or_repeat_effects(self):
        first, calls = self.invoke()
        state_before = self.session_path.read_bytes()
        result_before = self.result_path.read_bytes()
        with patch.object(p, "compose_forward_paper_cycle",
                          side_effect=AssertionError("replay must not recompose")):
            replay, replay_calls = self.invoke(transport=lambda *args: (_ for _ in ()).throw(
                AssertionError("replay must not acquire")))
        state = p.load_paper_session(self.session_path)
        self.assertFalse(replay["created"])
        self.assertTrue(replay["replay"])
        self.assertEqual(replay["terminal_result"], first["terminal_result"])
        self.assertEqual(replay_calls, [])
        self.assertEqual(self.session_path.read_bytes(), state_before)
        self.assertEqual(self.result_path.read_bytes(), result_before)
        self.assertEqual(len(state["processed_observations"]), 1)
        self.assertEqual(len(state["decisions"]), 1)
        self.assertEqual(len(state["paper_risk_evaluations"]), 1)

    def test_tampered_result_is_rejected_without_replacing_valid_evidence(self):
        self.invoke()
        original_result = self.result_path.read_bytes()
        state_before = self.session_path.read_bytes()
        cycle_before = self.cycle_path.read_bytes()
        tampered = json.loads(original_result)
        tampered["terminal_result"] = "NOTHING_DUE"
        self.result_path.write_bytes(p.encoded(tampered))
        with self.assertRaisesRegex(ValueError, "identity is invalid"):
            p._load_forward_paper_invocation_result(self.result_path)
        self.assertEqual(self.session_path.read_bytes(), state_before)
        self.assertEqual(self.cycle_path.read_bytes(), cycle_before)
        self.result_path.write_bytes(original_result)
        self.assertEqual(p._load_forward_paper_invocation_result(self.result_path),
                         json.loads(original_result))

    def test_nothing_due_stops_without_t4_or_effects(self):
        result, _ = self.invoke()
        self.assertEqual(result["terminal_result"], "COMPLETED")
        state = json.loads(self.session_path.read_bytes())
        state["processed_observations"] = [
            {"identity": item["identity"], "instrument": item["instrument"],
             "timestamp": item["interval_start_utc"], "open": item["open"],
             "high": item["high"], "low": item["low"], "close": item["close"],
             "volume": item["volume"]}
            for item in json.loads(self.dataset_path.read_bytes())["observations"]
        ]
        state["decisions"] = []
        state["paper_risk_evaluations"] = []
        state["internal_position_state"] = "FLAT"
        self.session_path.write_bytes(p.encoded(state))
        fixture_before = self.fixture_path.read_bytes()
        self.result_path.unlink()
        self.selection_path.unlink()
        result, calls = self.invoke()
        self.assertEqual(result["terminal_result"], "NOTHING_DUE")
        self.assertEqual(len(calls), 0)
        self.assertEqual(self.fixture_path.read_bytes(), fixture_before)
        self.assertEqual(p.load_paper_session(self.session_path)["decisions"], [])

    def test_blocked_temporal_input_preserves_state_and_stops_locally(self):
        result, _ = self.invoke()
        self.assertEqual(result["terminal_result"], "COMPLETED")
        state_before = self.session_path.read_bytes()
        self.result_path.unlink()
        self.selection_path.unlink()
        self.dataset_path.unlink()
        self.invocation_path.unlink()
        self.configuration_path.unlink()
        self.session_path.unlink()
        p.prepare_forward_paper_invocation(
            self.session_path, self.configuration_path, self.invocation_path,
            self.session_id, self.processing_instant, self.processing_instant)
        state_before_blocked = self.session_path.read_bytes()
        invalid_dataset = self.root / "invalid-dataset.json"
        self.dataset_path = invalid_dataset
        result, calls = self.invoke()
        self.assertEqual(result["terminal_result"], "BLOCKED")
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.session_path.read_bytes(), state_before_blocked)
        self.assertTrue(self.fixture_path.exists())
        self.assertNotEqual(state_before, state_before_blocked)

    def test_transport_error_is_recoverable_without_partial_dataset(self):
        def failing_transport(*args):
            raise OSError("public transport unavailable")

        result, calls = self.invoke(transport=failing_transport)
        self.assertEqual(result["terminal_result"], "RECOVERABLE_ERROR")
        self.assertEqual(len(calls), 0)
        self.assertFalse(self.dataset_path.exists())
        self.assertFalse(self.fixture_path.exists())
        reloaded = p._load_forward_paper_invocation_result(self.result_path)
        self.assertEqual(reloaded["terminal_result"], "RECOVERABLE_ERROR")

    def test_historical_fixture_preserves_pre_session_warmup_rule(self):
        root = self.root / "historical"
        state_path = root / "state.json"
        fixture_path = root / "fixture.json"
        p.initialize_paper_session(
            state_path, root / "init", "PAPER_SESSION|historical", "PAPER",
            self.started_at)
        before = state_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "before the PAPER session"):
            p.prepare_paper_session_fixture(
                state_path, fixture_path, root / "output", self.m11_observations(),
                self.processing_instant)
        self.assertEqual(state_path.read_bytes(), before)
        self.assertFalse(fixture_path.exists())
        self.assertNotIn("fixture_context",
                         inspect.signature(p.prepare_paper_session_fixture).parameters)

    def test_forward_adapter_rejects_missing_or_altered_t1_association(self):
        root, state_path, fixture_path, preparation, observations = \
            self.prepare_forward_fixture("association")
        invocation_path = root / "invocation.json"
        invocation = json.loads(invocation_path.read_bytes())
        invocation["configuration_id"] = "FORWARD_PAPER_CONFIGURATION|altered"
        invocation_path.write_bytes(p.encoded(invocation))
        before = state_path.read_bytes()
        with self.assertRaises(ValueError):
            p.prepare_forward_paper_session_fixture(
                state_path, root / "configuration.json", invocation_path,
                fixture_path, root / "output", observations)
        self.assertEqual(state_path.read_bytes(), before)
        self.assertFalse(fixture_path.exists())

        with self.assertRaises((ValueError, FileNotFoundError)):
            p.prepare_forward_paper_session_fixture(
                state_path, root / "missing-configuration.json", invocation_path,
                fixture_path, root / "output", observations)

    def test_forward_fixture_allows_post_start_warmup_and_reaches_t4(self):
        root, state_path, fixture_path, preparation, observations = \
            self.prepare_forward_fixture()
        result = p.prepare_forward_paper_session_fixture(
            state_path, root / "configuration.json", root / "invocation.json",
            fixture_path, root / "output", observations)
        self.assertEqual(result["warmup_observations"], 3)
        self.assertGreater(p.epoch(observations[2]["timestamp"] + ""),
                           p.epoch(self.started_at))

    def test_forward_fixture_rejects_open_or_nonpreceding_warmup(self):
        cases = {
            "open": lambda observations: observations[1].update(
                {"timestamp": "2026-01-04T12:00:00Z"}),
            "duplicate": lambda observations: observations[1].update(
                {"identity": observations[0]["identity"],
                 "timestamp": observations[0]["timestamp"]}),
            "not-before-candidate": lambda observations: observations[2].update(
                {"timestamp": observations[3]["timestamp"],
                 "identity": observations[3]["identity"]}),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                root, state_path, fixture_path, preparation, observations = \
                    self.prepare_forward_fixture(name)
                mutate(observations)
                before = state_path.read_bytes()
                with self.assertRaises(ValueError):
                    p.prepare_forward_paper_session_fixture(
                        state_path, root / "configuration.json", root / "invocation.json",
                        fixture_path, root / "output", observations)
                self.assertEqual(state_path.read_bytes(), before)
                self.assertFalse(fixture_path.exists())

    def test_forward_fixture_rejects_candidate_acceptance_boundaries(self):
        for name, accepted_at in (
                ("missing", None), ("equal", self.started_at),
                ("before", "2026-01-02T11:59:59Z")):
            with self.subTest(name=name):
                root, state_path, fixture_path, preparation, observations = \
                    self.prepare_forward_fixture(name)
                if accepted_at is None:
                    del observations[3]["accepted_at_utc"]
                else:
                    observations[3]["accepted_at_utc"] = accepted_at
                before = state_path.read_bytes()
                with self.assertRaises(ValueError):
                    p.prepare_forward_paper_session_fixture(
                        state_path, root / "configuration.json", root / "invocation.json",
                        fixture_path, root / "output", observations)
                self.assertEqual(state_path.read_bytes(), before)
                self.assertFalse(fixture_path.exists())


if __name__ == "__main__":
    unittest.main()
