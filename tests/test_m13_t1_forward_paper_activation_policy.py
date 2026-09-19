"""Focused proof for the M1.3-T1 daily FORWARD_PAPER activation policy."""

import inspect
import json
from pathlib import Path
import tempfile
import unittest

import pipeline as p


class M13T1ForwardPaperActivationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.session_path = self.root / "session" / "state.json"
        self.configuration_path = self.root / "configuration" / "forward-paper.json"
        self.invocation_path = self.root / "invocation.json"
        self.policy_path = self.root / "policy" / "activation.json"
        configuration = p.forward_paper_configuration()
        self.configuration_path.parent.mkdir(parents=True, exist_ok=True)
        self.configuration_path.write_bytes(p.encoded({
            "schema_version": p.FORWARD_PAPER_CONFIGURATION_REGISTRY_SCHEMA_VERSION,
            "configurations": [configuration],
            "session_configurations": [],
        }))
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_path.write_bytes(b"state-sentinel")
        self.invocation_path.write_bytes(b"invocation-sentinel")

    def prepare(self):
        return p.prepare_forward_paper_activation_policy(
            self.policy_path, self.configuration_path)

    def evaluate(self, now):
        return p.evaluate_forward_paper_activation(
            self.policy_path, self.configuration_path, now)

    def test_persists_reloads_and_repeats_idempotently(self):
        first = self.prepare()
        before = self.policy_path.read_bytes()
        state_before = self.session_path.read_bytes()
        configuration_before = self.configuration_path.read_bytes()

        replay = self.prepare()
        loaded = p.load_forward_paper_activation_policy(
            self.policy_path, self.configuration_path)

        self.assertTrue(first["created"])
        self.assertFalse(replay["created"])
        self.assertEqual(first["policy"], loaded)
        self.assertEqual(before, self.policy_path.read_bytes())
        self.assertEqual(state_before, self.session_path.read_bytes())
        self.assertEqual(configuration_before, self.configuration_path.read_bytes())

    def test_conflict_preserves_persisted_policy(self):
        self.prepare()
        original = self.policy_path.read_bytes()
        conflicting = json.loads(original)
        conflicting["slot_time_utc"] = "00:16:00Z"
        self.policy_path.write_bytes(p.encoded(conflicting))
        before = self.policy_path.read_bytes()

        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(before, self.policy_path.read_bytes())

    def test_policy_is_versioned_and_bound_to_m12_configuration(self):
        result = self.prepare()
        policy = result["policy"]
        configuration = p.forward_paper_configuration()
        self.assertEqual(policy["policy_id"], "FORWARD_PAPER_DAILY_V1")
        self.assertEqual(policy["policy_version"], "1")
        self.assertEqual(policy["schema_version"], "1")
        self.assertEqual(policy["timezone"], "UTC")
        self.assertEqual(policy["slot_time_utc"], "00:15:00Z")
        self.assertEqual(policy["max_latency_window"], "UNTIL_NEXT_DAILY_SLOT")
        self.assertEqual(policy["configuration_id"], configuration["configuration_id"])
        self.assertTrue(policy["policy_identity"].startswith(
            "FORWARD_PAPER_ACTIVATION_POLICY|"))

    def test_one_second_before_slot_is_nothing_due(self):
        result = self.prepare()
        evaluation = self.evaluate("2026-09-18T00:14:59Z")
        scheduled = "2026-09-18T00:15:00Z"
        self.assertEqual(evaluation["status"], "PASS")
        self.assertEqual(evaluation["condition"], "NOTHING_DUE")
        self.assertEqual(evaluation["activation_result"], "NOTHING_DUE")
        self.assertEqual(evaluation["scheduled_for_utc"], scheduled)
        self.assertEqual(evaluation["next_scheduled_for_utc"], scheduled)
        self.assertTrue(evaluation["activation_id"].endswith("|" + scheduled))
        self.assertEqual(evaluation["policy_identity"], result["policy"]["policy_identity"])

    def test_slot_boundary_is_due_and_next_slot_is_tomorrow(self):
        self.prepare()
        evaluation = self.evaluate("2026-09-18T00:15:00Z")
        self.assertEqual(evaluation["condition"], "DUE")
        self.assertEqual(evaluation["scheduled_for_utc"], "2026-09-18T00:15:00Z")
        self.assertEqual(evaluation["next_scheduled_for_utc"], "2026-09-19T00:15:00Z")

    def test_after_slot_is_same_daily_activation(self):
        self.prepare()
        boundary = self.evaluate("2026-09-18T00:15:00Z")
        later = self.evaluate("2026-09-18T23:59:59Z")
        self.assertEqual(later["condition"], "DUE")
        self.assertEqual(later["scheduled_for_utc"], "2026-09-18T00:15:00Z")
        self.assertEqual(later["activation_id"], boundary["activation_id"])

    def test_activation_identity_is_deterministic_and_changes_with_inputs(self):
        policy = p.forward_paper_activation_policy()
        configuration = p.forward_paper_configuration()
        scheduled = "2026-09-18T00:15:00Z"
        identity = p.forward_paper_activation_id(policy, configuration, scheduled)
        self.assertEqual(
            identity,
            "FORWARD_PAPER_ACTIVATION|FORWARD_PAPER_DAILY_V1|"
            + configuration["configuration_id"] + "|" + scheduled)
        altered_policy = dict(policy, policy_id="FORWARD_PAPER_DAILY_V2")
        altered_configuration = dict(configuration, configuration_id="OTHER_CONFIGURATION")
        self.assertNotEqual(identity, p.forward_paper_activation_id(
            altered_policy, configuration, scheduled))
        self.assertNotEqual(identity, p.forward_paper_activation_id(
            policy, altered_configuration, scheduled))
        self.assertNotEqual(identity, p.forward_paper_activation_id(
            policy, configuration, "2026-09-19T00:15:00Z"))

    def test_explicit_clock_and_fail_closed_inputs(self):
        source = inspect.getsource(p.evaluate_forward_paper_activation)
        for forbidden in ("datetime.now", "datetime.utcnow", "time.time"):
            self.assertNotIn(forbidden, source)
        self.prepare()
        for now in (None, "", "2026-09-18T00:15:00", "2026-09-18T00:15:00+00:00",
                    "2026-09-18T00:15:01.000Z"):
            with self.subTest(now=now), self.assertRaises(ValueError):
                self.evaluate(now)

    def test_invalid_configuration_or_policy_association_fails_closed(self):
        self.prepare()
        invalid_configuration = p.forward_paper_configuration()
        invalid_configuration["cycle_mode"] = "HISTORICAL_REPLAY"
        with self.assertRaises(ValueError):
            p.evaluate_forward_paper_activation(
                self.policy_path, invalid_configuration, "2026-09-18T00:15:00Z")

        invalid_policy = json.loads(self.policy_path.read_bytes())
        invalid_policy["configuration_id"] = "OTHER_CONFIGURATION"
        with self.assertRaises(ValueError):
            p.evaluate_forward_paper_activation(
                invalid_policy, self.configuration_path, "2026-09-18T00:15:00Z")

    def test_policy_has_no_m11_or_m12_effects(self):
        self.prepare()
        state_before = self.session_path.read_bytes()
        configuration_before = self.configuration_path.read_bytes()
        invocation_before = self.invocation_path.read_bytes()
        result = self.evaluate("2026-09-18T00:15:00Z")
        self.assertEqual(result["network_calls"], 0)
        self.assertFalse(result["credentials_used"])
        self.assertEqual(result["paper_orders_sent"], 0)
        self.assertEqual(result["live_orders_sent"], 0)
        self.assertEqual(state_before, self.session_path.read_bytes())
        self.assertEqual(configuration_before, self.configuration_path.read_bytes())
        self.assertEqual(invocation_before, self.invocation_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
