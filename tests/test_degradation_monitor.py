"""Degradation monitor: the gate that makes "stop when evidence stops" real.

DOC-011 §9.4 made monitorability a prerequisite because an opportunity that
cannot be watched is missing half its lifecycle. These prove the three
properties the design turns on: degradation is read off a DECLARED VARIABLE and
not off P&L, a single bad observation does NOT trigger, and re-entry is strictly
harder than exit so a variable on the boundary cannot flap.
"""

import unittest

import pipeline as p


def _carry_contract(confirmation=3, action=p.ACTION_SUSPEND, re_entry="0.0002"):
    """Funding published every 8 hours; degraded when it falls below a floor."""
    return p.monitoring_contract(
        variable="funding_rate_8h", observation_period_seconds=8 * 3600,
        degradation_threshold="0.0001", degrades_when=p.DEGRADES_BELOW,
        confirmation_periods=confirmation, action=action,
        re_entry_threshold=re_entry if action == p.ACTION_SUSPEND else None,
        source="exchange funding endpoint, published every 8h")


class MonitoringContractTests(unittest.TestCase):
    def test_a_contract_reproduces_its_identity_and_detects_an_altered_threshold(self):
        contract = _carry_contract()
        self.assertEqual(p.verified_monitoring_contract(contract), contract)
        with self.assertRaises(ValueError):
            p.verified_monitoring_contract({**contract, "degradation_threshold": "-1"})

    def test_a_contract_must_declare_the_variable_and_its_source(self):
        for field in ("variable", "source"):
            with self.assertRaises(ValueError):
                p.monitoring_contract(**{
                    "variable": "f", "observation_period_seconds": 3600,
                    "degradation_threshold": "0.0001", "degrades_when": p.DEGRADES_BELOW,
                    "confirmation_periods": 2, "action": p.ACTION_RETIRE,
                    "source": "s", field: "   "})

    def test_re_entry_must_be_strictly_better_than_exit(self):
        # Without hysteresis a variable resting on the boundary flaps in and out,
        # paying transaction costs on every oscillation.
        with self.assertRaises(ValueError):
            _carry_contract(re_entry="0.0001")       # equal to the exit threshold
        with self.assertRaises(ValueError):
            _carry_contract(re_entry="0.00005")      # worse than it

    def test_a_retiring_contract_has_no_re_entry(self):
        retiring = _carry_contract(action=p.ACTION_RETIRE)
        self.assertIsNone(retiring["re_entry_threshold"])
        with self.assertRaises(ValueError):
            p.monitoring_contract(
                variable="f", observation_period_seconds=3600,
                degradation_threshold="0.0001", degrades_when=p.DEGRADES_BELOW,
                confirmation_periods=2, action=p.ACTION_RETIRE,
                re_entry_threshold="0.0002", source="s")


class AdmissibilityTests(unittest.TestCase):
    """R3 against R1: detection must beat the drawdown it protects."""

    def test_fast_detection_against_a_slow_bleed_is_admissible(self):
        contract = _carry_contract(confirmation=3)          # 1 day
        ok, detail = p.monitoring_is_admissible(
            contract, drawdown_limit="0.15", expected_daily_loss_if_dead="0.01")
        self.assertTrue(ok, detail)

    def test_slow_detection_against_a_fast_bleed_is_refused(self):
        contract = _carry_contract(confirmation=90)         # 30 days
        ok, detail = p.monitoring_is_admissible(
            contract, drawdown_limit="0.15", expected_daily_loss_if_dead="0.02")
        self.assertFalse(ok)
        self.assertIn("drawdown limit", detail)

    def test_claiming_a_dead_edge_costs_nothing_is_refused(self):
        ok, detail = p.monitoring_is_admissible(
            _carry_contract(), drawdown_limit="0.15", expected_daily_loss_if_dead="0")
        self.assertFalse(ok)
        self.assertIn("has to be argued", detail)

    def test_latency_is_confirmation_times_period(self):
        self.assertEqual(p.detection_latency_seconds(_carry_contract(confirmation=3)),
                         3 * 8 * 3600)


class DegradationDetectionTests(unittest.TestCase):
    def test_one_bad_observation_does_not_trigger(self):
        # The measured crypto funding was negative on 3-5% of days while the
        # carry was healthy. A hair trigger would liquidate for noise.
        contract = _carry_contract(confirmation=3)
        result = p.evaluate_monitor(contract, ["0.0005", "-0.0001", "0.0005", "0.0004"])
        self.assertEqual(result["state"], p.STATE_HEALTHY)
        self.assertEqual(result["consecutive_run"], 0)

    def test_sustained_breach_confirms_and_suspends(self):
        contract = _carry_contract(confirmation=3)
        result = p.evaluate_monitor(contract, ["0.0005", "0.0", "-0.0001", "-0.0002"])
        self.assertEqual(result["state"], p.STATE_SUSPENDED)
        self.assertEqual(result["transition_index"], 3)

    def test_an_interrupted_breach_resets_the_run(self):
        contract = _carry_contract(confirmation=3)
        result = p.evaluate_monitor(contract, ["0.0", "0.0", "0.0009", "0.0", "0.0"])
        self.assertEqual(result["state"], p.STATE_HEALTHY)
        self.assertEqual(result["consecutive_run"], 2)

    def test_degradation_above_a_ceiling_works_symmetrically(self):
        contract = p.monitoring_contract(
            variable="basis_spread", observation_period_seconds=3600,
            degradation_threshold="0.05", degrades_when=p.DEGRADES_ABOVE,
            confirmation_periods=2, action=p.ACTION_SUSPEND, re_entry_threshold="0.02",
            source="test")
        self.assertEqual(p.evaluate_monitor(contract, ["0.01", "0.09", "0.08"])["state"],
                         p.STATE_SUSPENDED)


class LifecycleTests(unittest.TestCase):
    def test_a_suspended_position_re_enters_only_after_sustained_recovery(self):
        contract = _carry_contract(confirmation=2, re_entry="0.0002")
        suspended = p.evaluate_monitor(contract, ["-0.0001", "-0.0002"])
        self.assertEqual(suspended["state"], p.STATE_SUSPENDED)

        brief = p.evaluate_monitor(contract, ["0.0005", "0.0"], state=p.STATE_SUSPENDED)
        self.assertEqual(brief["state"], p.STATE_SUSPENDED)   # one good tick is not enough

        sustained = p.evaluate_monitor(contract, ["0.0005", "0.0005"],
                                       state=p.STATE_SUSPENDED)
        self.assertEqual(sustained["state"], p.STATE_HEALTHY)

    def test_recovery_between_the_thresholds_does_not_re_enter(self):
        # The hysteresis gap in action: above the exit floor, below re-entry.
        contract = _carry_contract(confirmation=2, re_entry="0.0002")
        result = p.evaluate_monitor(contract, ["0.00015"] * 5, state=p.STATE_SUSPENDED)
        self.assertEqual(result["state"], p.STATE_SUSPENDED)

    def test_retired_is_terminal(self):
        contract = _carry_contract(confirmation=2, action=p.ACTION_RETIRE)
        retired = p.evaluate_monitor(contract, ["-0.001", "-0.001"])
        self.assertEqual(retired["state"], p.STATE_RETIRED)
        recovered = p.evaluate_monitor(contract, ["0.01"] * 20, state=p.STATE_RETIRED)
        self.assertEqual(recovered["state"], p.STATE_RETIRED)


if __name__ == "__main__":
    unittest.main()
