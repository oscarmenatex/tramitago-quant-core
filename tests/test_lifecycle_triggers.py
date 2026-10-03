"""Lifecycle triggers: pure functions, facts in and notifications out."""

import json
import re
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tramitago_quant_core.research import lifecycle_triggers as triggers
from tramitago_quant_core.research.lifecycle import (
    derive_status, load_config, AUTORIZADO, MEDIDO, DENEGADO, TRIGGER_DEFAULTS)
from tramitago_quant_core.research.lifecycle_triggers import (
    remeasure_due, life_expired, forward_test_due, member_state_changes,
    family_verdict_changes, monitor_states, measurements_voided, instruments_gone_quiet,
    drifts, evaluate_triggers, CRITICAL, WARNING, INFO)

REPO = Path(__file__).resolve().parents[1]
ARTIFACTS = REPO / "artifacts" / "research"
H1 = "HYPOTHESIS|11111111-1111-1111-1111-111111111111"
H2 = "HYPOTHESIS|22222222-2222-2222-2222-222222222222"
SETTINGS = dict(TRIGGER_DEFAULTS)


def _member(hypothesis_id=H1, state=DENEGADO, due="2027-10-03", failed=()):
    return {"hypothesis_id": hypothesis_id, "label": "net_sharpe(X)", "state": state,
            "remeasure_due": due, "gates_failed": list(failed)}


def _report(as_of, *members):
    return {"as_of": as_of, "members": list(members)}


class CalendarTests(unittest.TestCase):
    def test_an_overdue_remeasurement_is_a_warning_with_its_options(self):
        notes = remeasure_due(_report("2027-11-01", _member()), warn_days=30)
        self.assertEqual([n["severity"] for n in notes], [WARNING])
        self.assertEqual(notes[0]["trigger"], "REMEASURE_OVERDUE")
        self.assertEqual(notes[0]["facts"]["days_overdue"], 29)
        self.assertTrue(notes[0]["options"])

    def test_a_remeasurement_inside_the_window_is_information_and_a_distant_one_is_silent(self):
        soon = remeasure_due(_report("2027-09-20", _member()), warn_days=30)
        self.assertEqual([n["severity"] for n in soon], [INFO])
        self.assertEqual(remeasure_due(_report("2027-01-01", _member()), warn_days=30), [])

    def test_the_window_is_the_one_passed_in_not_a_constant(self):
        report = _report("2027-09-20", _member())
        self.assertEqual(remeasure_due(report, warn_days=5), [])
        self.assertEqual(len(remeasure_due(report, warn_days=30)), 1)

    def test_a_persisting_condition_keeps_the_same_identity_so_it_is_sent_once(self):
        a = remeasure_due(_report("2027-11-01", _member()), warn_days=30)[0]
        b = remeasure_due(_report("2028-03-15", _member()), warn_days=30)[0]
        self.assertEqual(a["notification_id"], b["notification_id"])
        self.assertNotEqual(a["facts"]["days_overdue"], b["facts"]["days_overdue"])

    def test_a_declared_life_that_has_ended_or_is_ending(self):
        lives = [{"hypothesis_id": H1, "label": "a", "declared_on": "2026-10-03",
                  "life_years": 3}]
        self.assertEqual(life_expired(lives, as_of="2029-01-01", warn_days=90), [])
        ending = life_expired(lives, as_of="2029-08-01", warn_days=90)
        self.assertEqual([n["severity"] for n in ending], [INFO])
        ended = life_expired(lives, as_of="2029-10-03", warn_days=90)
        self.assertEqual([(n["trigger"], n["severity"]) for n in ended],
                         [("LIFE_EXPIRED", WARNING)])

    def test_a_forward_test_says_nothing_before_its_date(self):
        tests = [{"hypothesis_id": H1, "label": "f", "evaluable_from": "2027-10-06"}]
        self.assertEqual(forward_test_due(tests, as_of="2027-10-05"), [])
        self.assertEqual([n["trigger"] for n in forward_test_due(tests, as_of="2027-10-06")],
                         ["FORWARD_TEST_DUE"])


class StateChangeTests(unittest.TestCase):
    def _change(self, old, new):
        return member_state_changes(_report("2027-01-01", _member(state=old)),
                                    _report("2027-02-01", _member(state=new)))

    def test_losing_authorisation_is_critical_and_proposes_suspending(self):
        note = self._change(AUTORIZADO, DENEGADO)[0]
        self.assertEqual(note["severity"], CRITICAL)
        self.assertTrue(any("suspend" in option for option in note["options"]))

    def test_other_falls_warn_and_rises_inform(self):
        self.assertEqual(self._change(MEDIDO, DENEGADO)[0]["severity"], WARNING)
        self.assertEqual(self._change(DENEGADO, MEDIDO)[0]["severity"], INFO)
        self.assertEqual(self._change(MEDIDO, AUTORIZADO)[0]["severity"], INFO)

    def test_no_change_and_a_new_member_make_no_notification(self):
        self.assertEqual(self._change(DENEGADO, DENEGADO), [])
        self.assertEqual(member_state_changes(
            _report("2027-01-01"), _report("2027-02-01", _member())), [])


class VerdictChangeTests(unittest.TestCase):
    def _v(self, verdict, at):
        return {"family_id": "MECHANISM_FAMILY|x", "verdict": verdict, "reason": "r",
                "decided_at": at}

    def test_leaving_generalizes_is_critical_because_every_clearance_rested_on_it(self):
        notes = family_verdict_changes([self._v("GENERALIZES", "2026-10-03")],
                                       [self._v("GENERALIZES", "2026-10-03"),
                                        self._v("DOES_NOT_GENERALIZE", "2027-10-03")])
        self.assertEqual([n["severity"] for n in notes], [CRITICAL])

    def test_gaining_it_informs_and_no_change_is_silent(self):
        better = family_verdict_changes([self._v("DOES_NOT_GENERALIZE", "2026-10-03")],
                                        [self._v("GENERALIZES", "2027-10-03")])
        self.assertEqual([n["severity"] for n in better], [INFO])
        same = [self._v("GENERALIZES", "2026-10-03")]
        self.assertEqual(family_verdict_changes(same, same), [])

    def test_the_latest_verdict_is_the_one_compared(self):
        before = [self._v("GENERALIZES", "2026-01-01"), self._v("DOES_NOT_GENERALIZE", "2026-06-01")]
        after = before + [self._v("DOES_NOT_GENERALIZE", "2027-01-01")]
        self.assertEqual(family_verdict_changes(before, after), [])


class EventTests(unittest.TestCase):
    def test_a_suspended_or_retired_monitor_is_critical_and_a_healthy_one_is_silent(self):
        items = [{"hypothesis_id": H1, "label": "a", "state": "SUSPENDED", "since": "2027-01-01"},
                 {"hypothesis_id": H2, "label": "b", "state": "RETIRED", "since": "2027-01-02"},
                 {"hypothesis_id": "HYPOTHESIS|33333333-3333-3333-3333-333333333333",
                  "label": "c", "state": "HEALTHY"}]
        notes = monitor_states(items)
        self.assertEqual({n["trigger"] for n in notes}, {"MONITOR_SUSPENDED", "MONITOR_RETIRED"})
        self.assertTrue(all(n["severity"] == CRITICAL for n in notes))

    def test_a_voided_measurement_is_reported_with_its_reason(self):
        notes = measurements_voided([{"hypothesis_id": H1, "label": "a", "void": "bad ratio"},
                                     {"hypothesis_id": H2, "label": "b", "void": None}])
        self.assertEqual(len(notes), 1)
        self.assertIn("bad ratio", notes[0]["what_changed"])

    def test_an_instrument_that_stopped_quoting_is_critical_past_the_gap(self):
        bars = {"PUTW": {"last_bar": "2025-04-03", "hypothesis_id": H1, "label": "putw"}}
        notes = instruments_gone_quiet(bars, as_of="2025-04-20", max_gap_days=7)
        self.assertEqual([(n["severity"], n["facts"]["days_without_a_bar"]) for n in notes],
                         [(CRITICAL, 17)])
        self.assertEqual(instruments_gone_quiet(bars, as_of="2025-04-10", max_gap_days=7), [])

    def test_a_cost_is_worse_when_higher_and_a_capacity_when_lower(self):
        cost = {"hypothesis_id": H1, "label": "a", "metric": "half_spread",
                "declared": "0.0002", "observed": "0.0004", "worse_when": "higher"}
        capacity = {"hypothesis_id": H2, "label": "b", "metric": "capacity",
                    "declared": "1000000", "observed": "400000", "worse_when": "lower"}
        better = {**cost, "observed": "0.0001"}
        within = {**cost, "observed": "0.00024"}
        self.assertEqual(len(drifts([cost, capacity], tolerance="0.25")), 2)
        self.assertEqual(drifts([better, within], tolerance="0.25"), [])

    def test_a_declared_zero_cannot_be_drifted_from(self):
        zero = {"hypothesis_id": H1, "label": "a", "metric": "m", "declared": "0",
                "observed": "5", "worse_when": "higher"}
        self.assertEqual(drifts([zero], tolerance="0.25"), [])


class EvaluateTests(unittest.TestCase):
    def _run(self, **extra):
        report = _report("2027-11-01", _member(), _member(H2, state=MEDIDO))
        return evaluate_triggers(report=report, settings=SETTINGS, as_of="2027-11-01", **extra)

    def test_a_question_that_was_not_asked_is_silent_not_all_clear(self):
        notes = self._run()
        self.assertEqual({n["trigger"] for n in notes}, {"REMEASURE_OVERDUE"})

    def test_notifications_are_ordered_critical_first_and_deduplicated(self):
        monitors = [{"hypothesis_id": H1, "label": "a", "state": "SUSPENDED"}]
        notes = self._run(monitors=monitors * 2)
        self.assertEqual(notes[0]["severity"], CRITICAL)
        ids = [n["notification_id"] for n in notes]
        self.assertEqual(len(ids), len(set(ids)))
        ranks = [{"CRITICAL": 0, "WARNING": 1, "INFO": 2}[n["severity"]] for n in notes]
        self.assertEqual(ranks, sorted(ranks))

    def test_the_same_facts_give_the_same_notifications(self):
        self.assertEqual(self._run(), self._run())


class PurityTests(unittest.TestCase):
    def test_the_module_reads_no_clock_file_or_network_and_writes_nothing(self):
        source = Path(triggers.__file__).read_text(encoding="utf-8")
        code = re.sub(r'"""[\s\S]*?"""', "", source)          # the docstring may name them
        for forbidden in ("today(", "now(", "open(", "urllib", "requests", "socket",
                          "write_text", "write_bytes", "subprocess", "os.environ"):
            self.assertNotIn(forbidden, code, forbidden)


class SettingsTests(unittest.TestCase):
    def _load(self, content):
        path = Path(tempfile.mkdtemp()) / "c.json"
        path.write_text(json.dumps(content), encoding="utf-8")
        return load_config(path)

    def test_missing_trigger_settings_take_the_defaults_and_given_ones_override(self):
        self.assertEqual(self._load({"remeasure_every_months": 12})["triggers"],
                         TRIGGER_DEFAULTS)
        triggers_ = self._load({"remeasure_every_months": 12,
                                "triggers": {"remeasure_warn_days": 7}})["triggers"]
        self.assertEqual(triggers_["remeasure_warn_days"], 7)
        self.assertEqual(triggers_["life_warn_days"], TRIGGER_DEFAULTS["life_warn_days"])

    def test_invalid_settings_are_refused_not_defaulted(self):
        for bad in ({"unknown_key": 1}, {"remeasure_warn_days": -1},
                    {"life_warn_days": "90"}, {"instrument_max_gap_days": True},
                    {"drift_tolerance": "0"}, {"drift_tolerance": "abc"}):
            with self.assertRaises(ValueError, msg=str(bad)):
                self._load({"remeasure_every_months": 12, "triggers": bad})

    def test_the_repository_config_is_valid(self):
        config = load_config(REPO / "config" / "lifecycle.json")
        self.assertEqual(config["triggers"], TRIGGER_DEFAULTS)


class RealRecordsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (ARTIFACTS / "admissions.json").exists():
            raise unittest.SkipTest("the sealed artifacts are not in this checkout")

    def _report(self, as_of):
        return derive_status(
            admissions=ARTIFACTS / "admissions.json", hypotheses=ARTIFACTS / "hypotheses.json",
            families=ARTIFACTS / "families.json", verdicts=ARTIFACTS / "family-verdicts.json",
            as_of=as_of, remeasure_months=12)

    def test_nothing_is_overdue_the_day_after_the_measurements(self):
        notes = evaluate_triggers(report=self._report(date(2026, 10, 4)),
                                  settings=SETTINGS, as_of="2026-10-04")
        self.assertEqual(notes, [])

    def test_every_denied_member_is_overdue_a_year_later(self):
        report = self._report(date(2027, 10, 4))
        notes = evaluate_triggers(report=report, settings=SETTINGS, as_of="2027-10-04")
        self.assertEqual(len(notes), len(report["members"]))
        self.assertTrue(all(n["trigger"] == "REMEASURE_OVERDUE" for n in notes))


if __name__ == "__main__":
    unittest.main()
