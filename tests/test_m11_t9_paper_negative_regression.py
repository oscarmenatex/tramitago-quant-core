"""Negative inputs must fail locally without changing persisted PAPER state."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pipeline as p
import test_m11_t8_paper_cycle_idempotence as t8


REMOVE = object()


def changed(document, path, value):
    target = document
    for key in path[:-1]:
        target = target[key]
    if value is REMOVE:
        del target[path[-1]]
    else:
        target[path[-1]] = copy.deepcopy(value)


# Each row changes one aspect of otherwise valid, completed T1-T8 evidence.
CASES = [
    ("temporal_processing_missing", "fixture", ("processing_instant_utc",), REMOVE),
    ("temporal_processing_invalid", "fixture", ("processing_instant_utc",), "invalid"),
    ("temporal_processing_ambiguous", "fixture", ("processing_instant_utc",), "2024-01-05T00:05:00"),
    ("temporal_processing_offset", "fixture", ("processing_instant_utc",), "2024-01-05T00:05:00+01:00"),
    ("temporal_open", "fixture", ("processing_instant_utc",), "2024-01-04T12:00:00Z"),
    ("temporal_future", "fixture", ("processing_instant_utc",), "2024-01-03T00:00:00Z"),
    ("temporal_interval_not_closed", "fixture", ("processing_instant_utc",), "2024-01-04T23:59:59Z"),
    ("temporal_accepted_missing", "fixture", ("operational_observations", 0, "accepted_at_utc"), REMOVE),
    ("temporal_accepted_equal", "fixture", ("operational_observations", 0, "accepted_at_utc"), "2024-01-05T00:00:00Z"),
    ("temporal_accepted_before", "fixture", ("operational_observations", 0, "accepted_at_utc"), "2024-01-04T23:59:59Z"),
    ("data_ohlc_missing", "fixture", ("operational_observations", 0, "close"), REMOVE),
    ("data_ohlc_invalid", "fixture", ("operational_observations", 0, "low"), 99999),
    ("data_ohlc_text", "fixture", ("operational_observations", 0, "open"), "43500"),
    ("data_ohlc_nonfinite", "fixture", ("operational_observations", 0, "high"), float("inf")),
    ("data_identity", "fixture", ("operational_observations", 0, "identity"), "wrong"),
    ("data_instrument", "fixture", ("instrument",), "ETH-USD"),
    ("data_timeframe", "fixture", ("frequency_seconds",), 60),
    ("data_warmup_insufficient", "fixture", ("warmup_observations",), []),
    ("data_warmup_contaminated", "fixture", ("warmup_observations", 0, "accepted_at_utc"), t8.INSTANT),
    ("data_sma_config_missing", "indicator", ("parameters",), REMOVE),
    ("data_sma_config_altered", "indicator", ("parameters", "window"), 4),
    ("state_mode_missing", "state", ("mode",), REMOVE),
    ("state_mode_ambiguous", "state", ("mode",), "UNKNOWN"),
    ("state_mode_live", "state", ("mode",), "LIVE"),
    ("state_incomplete", "state", ("pending_actions",), REMOVE),
    ("state_session", "fixture", ("session_id",), "PAPER_SESSION|other"),
    ("state_warmup_mismatch", "state", ("warmup_observations", 0, "close"), 42501),
    ("state_observation_mismatch", "state", ("processed_observations", 0, "close"), 44001),
    ("state_acceptance_session", "acceptance", ("session_id",), "PAPER_SESSION|other"),
    ("state_acceptance_observation", "acceptance", ("observation_identity",), "wrong"),
    ("state_acceptance_partial", "acceptance", ("interval_end_utc",), REMOVE),
    ("state_acceptance_manipulated", "acceptance", ("interval_end_utc",), "2024-01-06T00:00:00Z"),
    ("state_indicator_session", "indicator", ("session_id",), "PAPER_SESSION|other"),
    ("state_indicator_observation", "indicator", ("observation_identity",), "wrong"),
    ("state_indicator_value", "indicator", ("sma_close_3",), 1),
    ("state_decision_session", "state", ("decisions", 0, "session_id"), "PAPER_SESSION|other"),
    ("state_decision_observation", "state", ("decisions", 0, "observation_identity"), "wrong"),
    ("state_decision_time", "state", ("decisions", 0, "processing_instant_utc"), "2024-01-06T00:05:00Z"),
    ("state_decision_action", "state", ("decisions", 0, "decision"), "EXIT"),
    ("state_decision_duplicate", "state", ("decisions",), "DUPLICATE"),
    ("state_risk_decision", "state", ("paper_risk_evaluations", 0, "decision_identity"), "wrong"),
    ("state_risk_profile", "state", ("paper_risk_evaluations", 0, "risk_profile", "mode"), "LIVE"),
    ("state_risk_partial", "state", ("paper_risk_evaluations", 0, "paper_effect"), REMOVE),
    ("state_risk_transition", "state", ("paper_risk_evaluations", 0, "position_before"), "LONG"),
    ("state_position", "state", ("internal_position_state",), "FLAT"),
    ("state_broker_separation", "state", ("broker_position_observed",), 1),
]


class M11T9PaperNegativeRegressionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(t8.offline())
        t8.prepare(self.root)
        self.unprocessed = (self.root / "state.json").read_bytes()
        self.assertEqual(t8.execute(self.root)["cycle"]["terminal_result"], "COMPLETED")

    def snapshot(self):
        return {path.relative_to(self.root): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def assert_no_effects(self, before, result):
        for relative, content in before.items():
            self.assertEqual((self.root / relative).read_bytes(), content, str(relative))
        # Only diagnostic evidence for this rejected invocation may be added.
        self.assertLessEqual(set(self.snapshot()) - set(before), {
            Path("negative.json"), Path("negative/negative.json/paper-cycle.json")})
        for field in ("network_calls", "paper_orders_sent", "live_orders_sent"):
            self.assertEqual(result[field], 0)
        self.assertFalse(result["credentials_used"])
        evidence = json.loads((self.root / "negative.json").read_bytes())
        for field in ("executions", "pending_actions", "proposals"):
            self.assertEqual(evidence[field], 0)

    def reject(self):
        before = self.snapshot()
        result = t8.cycle(self.root, cycle_name="negative.json", output="negative")
        self.assertEqual(result["terminal_result"], "BLOCKED")
        self.assert_no_effects(before, result)

    def test_corrupt_state(self):
        (self.root / "state.json").write_bytes(b'{"mode":')
        self.reject()

    def test_phase3_state(self):
        (self.root / "state.json").write_bytes(p.encoded({
            "instrument": "BTC-USD", "frequency_seconds": 86400,
            "observations": [], "decisions": [], "virtual_position": 0}))
        self.reject()

    def test_non_object_acceptance(self):
        (self.root / "acceptance.json").write_bytes(b'[]')
        self.reject()

    def test_non_object_fixture(self):
        (self.root / "fixture.json").write_bytes(b'[]')
        self.reject()

    def test_non_object_indicator(self):
        (self.root / "indicator.json").write_bytes(b'[]')
        self.reject()

    def test_malformed_decision_record(self):
        file = self.root / "state.json"
        state = json.loads(file.read_bytes())
        state["decisions"] = [None]
        file.write_bytes(p.encoded(state))
        self.reject()

    def test_duplicate_risk_evidence(self):
        file = self.root / "state.json"
        state = json.loads(file.read_bytes())
        state["paper_risk_evaluations"] *= 2
        file.write_bytes(p.encoded(state))
        self.reject()

    def test_partial_cycle_evidence_is_preserved(self):
        (self.root / "cycle.json").write_bytes(p.encoded({"cycle_id": t8.CYCLE}))
        before = self.snapshot()
        # T7/T8 explicitly require ValueError for conflicting persisted evidence.
        with self.assertRaises(ValueError):
            t8.cycle(self.root, output="rejected")
        self.assertEqual(self.snapshot(), before)

    def test_temporary_persistence_failure_is_recoverable(self):
        before = self.snapshot()
        with patch("pipeline._atomic_write", side_effect=OSError("Local storage unavailable")):
            result = t8.cycle(self.root, cycle_name="negative.json", output="negative")
        self.assertEqual(result["terminal_result"], "RECOVERABLE_ERROR")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(result["created"])

    def test_no_broker_or_credentials_required_for_sma3_and_paper(self):
        state = p.load_paper_session(self.root / "state.json")
        self.assertEqual(state["internal_position_state"], "LONG")
        self.assertEqual(state["broker_position_observed"], "UNKNOWN")
        self.assertEqual(len(state["decisions"]), 1)
        for key in ("executions", "pending_actions", "broker_submissions"):
            self.assertEqual(state[key], [])


def negative_case(document, path, value):
    def test(self):
        file = self.root / f"{document}.json"
        data = json.loads(file.read_bytes())
        if value == "DUPLICATE":
            data["decisions"] *= 2
        else:
            changed(data, path, value)
        # Deliberately permit non-finite JSON here to exercise the input boundary.
        file.write_text(json.dumps(data), encoding="utf-8")
        self.reject()
    return test


def rejected_decision_case(document, path, value):
    def test(self):
        (self.root / "state.json").write_bytes(self.unprocessed)
        file = self.root / f"{document}.json"
        data = json.loads(file.read_bytes())
        changed(data, path, value)
        file.write_text(json.dumps(data), encoding="utf-8")
        before = self.snapshot()
        with self.assertRaises(ValueError):
            p.persist_paper_session_sma3_decision(
                self.root / "state.json", self.root / "fixture.json",
                self.root / "acceptance.json", self.root / "indicator.json",
                self.root / "rejected-decision")
        self.assertEqual(self.snapshot(), before)
        state = json.loads((self.root / "state.json").read_bytes())
        for key in ("processed_observations", "decisions", "executions", "pending_actions"):
            self.assertEqual(state[key], [])
        self.assertFalse(state.get("proposals"))
    return test


for name, document, path, value in CASES:
    setattr(M11T9PaperNegativeRegressionTests, f"test_{name}", negative_case(document, path, value))
    if document in ("fixture", "acceptance", "indicator") and name != "state_indicator_value":
        setattr(M11T9PaperNegativeRegressionTests, f"test_before_decision_{name}",
                rejected_decision_case(document, path, value))


if __name__ == "__main__":
    unittest.main()
