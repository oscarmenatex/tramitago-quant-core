import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pipeline as p


class M11T7PaperCycleRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / "state.json"
        self.fixture = self.root / "operational-input.json"
        self.acceptance = self.root / "operational-acceptance.json"
        self.indicator = self.root / "indicator.json"
        self.decision_output = self.root / "decision-output"
        self.risk_output = self.root / "risk-output"
        p.initialize_paper_session(
            self.state, self.root / "init", "PAPER_SESSION|t7-recovery-001", "PAPER",
            "2024-01-05T00:00:00Z")
        p.prepare_paper_session_fixture(
            self.state, self.fixture, self.root / "fixture-output", self.observations(),
            "2024-01-05T00:05:00Z")
        p.validate_paper_session_operational_observation(
            self.state, self.fixture, self.acceptance, self.root / "acceptance-output")
        p.compose_paper_session_sma3(
            self.state, self.fixture, self.acceptance, self.indicator,
            self.root / "indicator-output")
        p.persist_paper_session_sma3_decision(
            self.state, self.fixture, self.acceptance, self.indicator,
            self.decision_output)
        decision = p.load_paper_session(self.state)["decisions"][0]
        p.apply_paper_risk_to_session(
            self.state, self.risk_output, decision["identity"], "2024-01-05T00:06:00Z")

    @staticmethod
    def observations():
        values = (
            ("2024-01-01T00:00:00Z", 42000.0, 43000.0, 41000.0, 42500.0, 100.0),
            ("2024-01-02T00:00:00Z", 42500.0, 43500.0, 41500.0, 43000.0, 110.0),
            ("2024-01-03T00:00:00Z", 43000.0, 44000.0, 42000.0, 43500.0, 120.0),
            ("2024-01-04T00:00:00Z", 43500.0, 44500.0, 42500.0, 44000.0, 130.0),
        )
        return [{"identity": f"BTC-USD|86400|{stamp}", "instrument": "BTC-USD",
                 "timestamp": stamp, "open": opening, "high": high, "low": low,
                 "close": close, "volume": volume,
                 **({"accepted_at_utc": "2024-01-05T00:01:00Z"}
                    if stamp == "2024-01-04T00:00:00Z" else {})}
                for stamp, opening, high, low, close, volume in values]

    def run_cycle(self, cycle_path):
        return p.run_paper_cycle(
            self.state, self.fixture, self.acceptance, self.indicator, cycle_path,
            self.root / "cycle-output",
            "PAPER_CYCLE|PAPER_SESSION|t7-recovery-001|BTC-USD|86400|2024-01-04T00:00:00Z|2024-01-05T00:05:00Z",
            "2024-01-05T00:05:00Z")

    def test_process_two_recovers_same_cycle_after_process_one_terminates(self):
        cycle = self.root / "cycle.json"
        first = self.run_cycle(cycle)
        self.assertEqual(first["terminal_result"], "COMPLETED")
        persisted_state = self.state.read_bytes()
        persisted_cycle = cycle.read_bytes()
        script = """
import json
from pathlib import Path
import pipeline as p
root = Path(r'{root}')
state = p.load_paper_session(root / 'state.json')
cycle = json.loads((root / 'cycle.json').read_bytes())
assert cycle['cycle_id'].startswith('PAPER_CYCLE|')
assert cycle['session_id'] == state['session_id']
assert cycle['mode'] == 'PAPER'
assert cycle['processing_instant_utc'] == '2024-01-05T00:05:00Z'
assert cycle['operational_observation_identity'] == 'BTC-USD|86400|2024-01-04T00:00:00Z'
assert cycle['indicator']['sma_close_3'] == 43500.0
assert cycle['decision']['decision'] == 'ENTER'
assert cycle['risk']['risk_result'] == 'ALLOWED'
assert cycle['position_before'] == 'FLAT'
assert cycle['position_after'] == 'LONG'
assert cycle['broker_position_observed'] == 'UNKNOWN'
assert cycle['terminal_result'] == 'COMPLETED'
assert cycle['validations']['lookahead'] == 'NOT_USED'
assert len(state['decisions']) == 1
assert len(state['executions']) == 0
assert len(state['pending_actions']) == 0
""".format(root=str(self.root).replace("'", "''"))
        completed = subprocess.run(
            [sys.executable, "-B", "-c", script], cwd=Path.cwd(),
            capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self.state.read_bytes(), persisted_state)
        self.assertEqual(cycle.read_bytes(), persisted_cycle)

    def test_incomplete_persisted_cycle_fails_closed_without_state_change(self):
        cycle = self.root / "incomplete-cycle.json"
        cycle.write_bytes(p.encoded({"cycle_id": "PAPER_CYCLE|incomplete"}))
        before = self.state.read_bytes()
        with self.assertRaises(ValueError):
            self.run_cycle(cycle)
        self.assertEqual(self.state.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
