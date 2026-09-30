"""Proof that Risk Control is wired into the operational chain as a CLI gate
(Etapa 4.5, M4.5-T1 wired, 2026-09-29). A COMPLETED evaluation maps to PASS
(the sequence proceeds); a BLOCKED one maps to FAIL (the sequence halts
before any order is submitted)."""

import json
import tempfile
import unittest
from pathlib import Path

import pipeline as p


class RiskControlGateCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _run(self, payload):
        input_path = self.root / "in.json"
        input_path.write_bytes(p.encoded(payload))
        return p.run_risk_control_gate(input_path, self.root / "out")

    def test_within_limits_is_pass(self):
        result = self._run({"max_total_exposure_usd": "50", "max_drawdown_ratio": 0.15,
                            "proposed_total_exposure_usd": "40",
                            "equity_curve": [100.0, 108.0, 104.0]})
        self.assertEqual(result["outcome"], p.RISK_CONTROL_COMPLETED)
        self.assertEqual(result["status"], "PASS")

    def test_breach_is_fail_and_names_the_limits(self):
        result = self._run({"max_total_exposure_usd": "50", "max_drawdown_ratio": 0.15,
                            "proposed_total_exposure_usd": "80",
                            "equity_curve": [100.0, 130.0, 90.0]})
        self.assertEqual(result["outcome"], p.RISK_CONTROL_BLOCKED)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual({b["limit"] for b in result["breaches"]},
                         {p.RISK_LIMIT_TOTAL_EXPOSURE, p.RISK_LIMIT_MAX_DRAWDOWN})

    def test_missing_equity_curve_defaults_to_no_drawdown(self):
        result = self._run({"max_total_exposure_usd": "50", "max_drawdown_ratio": 0.15,
                            "proposed_total_exposure_usd": "40"})
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["evaluated"]["observed_max_drawdown_ratio"], 0.0)


if __name__ == "__main__":
    unittest.main()
