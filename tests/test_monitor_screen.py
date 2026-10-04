"""The monitor screen scanner: sealing, states without look-ahead, a scan confined to discovery,
and a holdout that can be opened once. All on synthetic data: nothing here reads a real series."""

import copy
import json
import random
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tramitago_quant_core.research.monitor_screen import (
    canonical_space, space_identity, seal_space, load_sealed_space, state_flags, scan,
    seal_scan, confirm_on_holdout, holdout_confidence, holdout_was_opened,
    LINK_MET, LINK_FAILED, LINK_UNREACHABLE)

SPACE_FILE = Path(__file__).resolve().parents[1] / "scripts" / "research" / "monitor_screen" / "space.json"
SEAL = dict(sealed_at="2026-10-03T22:00:00.000000Z", code_revision="a" * 40,
            authorised_by="test", link_rule_id="MONITOR_SCREEN_LINK_RULE|test")


def _space():
    return json.loads(SPACE_FILE.read_text(encoding="utf-8"))


def _weekdays(start, end):
    day, out = date.fromisoformat(start), []
    stop = date.fromisoformat(end)
    while day < stop:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _series_for(space, seed=1):
    """One synthetic series per variable, from the declared history start to the holdout end."""
    history = space["fixed_parameters"]["series_history_start"]
    end = space["windows"]["holdout"]["end_exclusive_utc"][:10]
    dates = _weekdays(history, end)
    out = {}
    for index, variable in enumerate(space["variables"]):
        rng = random.Random(seed * 100 + index)
        level, values = 1.0, {}
        for day in dates:
            level += rng.gauss(0, 0.15)
            values[day] = level
        out[variable["series"]] = values
    return out


def _window(space, name):
    window = space["windows"][name]
    return _weekdays(window["start_utc"][:10], window["end_exclusive_utc"][:10])


def _noise(dates, seed, sd=0.01):
    rng = random.Random(seed)
    return {day: rng.gauss(0, sd) for day in dates}


def _flags(space, candidate, series, dates):
    variable = next(v for v in space["variables"] if v["id"] == candidate["variable"])
    fixed = space["fixed_parameters"]
    return state_flags(candidate["form"], dates, series[variable["series"]],
                       lag_days=variable["lag_days"], change_horizon=fixed["change_horizon_days"],
                       median_window=fixed["median_window_days"])[0]


def _returns(space, series, window, planted=None, effect=-0.01, seed=7):
    """Noise for every exposure; for the planted candidate, a lower return on its ON days."""
    dates = _window(space, window)
    out = {e["id"]: _noise(dates, seed + i) for i, e in enumerate(space["exposures"])}
    if planted:
        candidate = next(c for c in space["candidates"] if c["id"] == planted)
        flags = _flags(space, candidate, series, dates)
        for day, flag in zip(dates, flags):
            out[candidate["exposure"]][day] += effect * flag
    return out


class Fixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.space = _space()
        self.series = _series_for(self.space)
        self.spaces = self.root / "spaces.json"
        self.holdout = self.root / "holdout.json"
        self.record, _ = seal_space(self.spaces, self.space, **SEAL)

    def scan_with(self, planted=None, effect=-0.01):
        returns = _returns(self.space, self.series, "discovery", planted, effect)
        observations, summary = scan(self.record["space"], series=self.series,
                                     returns_by_exposure=returns)
        return observations, summary, returns

    def confirm(self, scan_record, planted=None, effect=-0.01, loader=None, seed=9):
        def holdout_loader():
            return _returns(self.space, self.series, "holdout", planted, effect, seed=seed)
        return confirm_on_holdout(
            self.record, scan_record, series=self.series, load_holdout_returns=loader or holdout_loader,
            path=self.holdout, opened_at="2026-10-04T10:00:00.000000Z", code_revision="b" * 40)

    def sealed_scan(self, planted=None):
        observations, summary, returns = self.scan_with(planted)
        record, _ = seal_scan(self.root / "scans.json", self.record, observations, summary,
                              series=self.series, returns_by_exposure=returns,
                              scanned_at="2026-10-04T09:00:00.000000Z", code_revision="b" * 40)
        return record


class SealingTests(Fixture):
    def test_the_same_space_seals_once_and_is_found_again(self):
        _, is_new = seal_space(self.spaces, self.space, **SEAL)
        self.assertFalse(is_new)
        self.assertEqual(load_sealed_space(self.spaces, self.space)["space_id"], self.record["space_id"])

    def test_the_sealed_copy_says_so_and_the_input_is_untouched(self):
        before = copy.deepcopy(self.space)
        self.assertEqual(canonical_space(self.space)["status"], "SEALED")
        self.assertEqual(self.space, before)
        self.assertEqual(before["status"], "DRAFT_FOR_REVIEW_NOT_SEALED")

    def test_a_space_changed_in_any_way_after_sealing_is_refused(self):
        for change in (lambda s: s["candidates"][0].update(form="CHANGE_21D_ABOVE_ZERO"),
                       lambda s: s["windows"]["holdout"].update(start_utc="2023-11-01T00:00:00Z"),
                       lambda s: s["fixed_parameters"].update(change_horizon_days=22),
                       lambda s: s["candidates"].pop()):
            moved = copy.deepcopy(self.space)
            change(moved)
            with self.assertRaises(ValueError):
                load_sealed_space(self.spaces, moved)

    def test_a_tampered_sealed_record_does_not_reproduce_its_identity(self):
        registry = json.loads(self.spaces.read_text(encoding="utf-8"))
        registry["spaces"][0]["space"]["candidates"][0]["mechanism"] += " tampered"
        self.spaces.write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            load_sealed_space(self.spaces, self.space)
        self.assertIn("reproduce", str(caught.exception))

    def test_a_space_the_validator_refuses_cannot_be_sealed(self):
        bad = copy.deepcopy(self.space)
        bad["pass_rule"]["consistency_threshold"] = "0.60"
        with self.assertRaises(ValueError):
            seal_space(self.root / "other.json", bad, **SEAL)

    def test_the_identity_is_the_content_and_not_the_status_line(self):
        sealed = copy.deepcopy(self.space)
        sealed["status"] = "SEALED"
        self.assertEqual(space_identity(sealed), space_identity(self.space))


class StateTests(unittest.TestCase):
    D = ["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08"]

    def _flags(self, form, values, **kwargs):
        series = {d: v for d, v in zip(self.D, values)}
        defaults = dict(lag_days=0, change_horizon=2, median_window=3)
        return state_flags(form, self.D, series, **{**defaults, **kwargs})

    def test_the_sign_form_is_at_or_below_zero(self):
        flags, unknown = self._flags("SIGN_AT_OR_BELOW_ZERO", [1, 0.5, 0, -1, 2, 0])
        self.assertEqual((flags, unknown), ([0, 0, 1, 1, 0, 1], 0))

    def test_the_change_form_compares_with_the_value_a_fixed_horizon_earlier(self):
        flags, unknown = self._flags("CHANGE_21D_ABOVE_ZERO", [1, 2, 3, 2, 1, 5])
        self.assertEqual((flags, unknown), ([0, 0, 1, 0, 0, 1], 2))

    def test_the_median_excludes_the_day_it_is_compared_with(self):
        flags, unknown = self._flags("ABOVE_TRAILING_MEDIAN_252", [1, 2, 3, 10, 0, 5])
        self.assertEqual((flags, unknown), ([0, 0, 0, 1, 0, 1], 3))

    def test_no_state_looks_forward(self):
        # THE PROPERTY. Whatever the series does AFTER a day cannot change that day.
        rng = random.Random(3)
        values = [rng.gauss(0, 1) for _ in range(40)]
        dates = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(40)]
        for form in ("SIGN_AT_OR_BELOW_ZERO", "CHANGE_21D_ABOVE_ZERO", "ABOVE_TRAILING_MEDIAN_252"):
            base, _ = state_flags(form, dates, dict(zip(dates, values)), lag_days=0,
                                  change_horizon=3, median_window=5)
            for cut in (10, 20, 30):
                changed = values[:cut] + [rng.gauss(50, 10) for _ in values[cut:]]
                other, _ = state_flags(form, dates, dict(zip(dates, changed)), lag_days=0,
                                       change_horizon=3, median_window=5)
                self.assertEqual(base[:cut], other[:cut], (form, cut))

    def test_a_lagged_series_is_read_one_trading_day_late(self):
        flags, unknown = self._flags("SIGN_AT_OR_BELOW_ZERO", [1, -1, 1, -1, 1, -1], lag_days=1)
        self.assertEqual((flags, unknown), ([0, 0, 1, 0, 1, 0], 1))

    def test_a_day_with_no_observation_is_unknown_and_never_on(self):
        series = {d: v for d, v in zip(self.D, [-1, None, -1, -1, None, -1])}
        flags, unknown = state_flags("SIGN_AT_OR_BELOW_ZERO", self.D, series, lag_days=0,
                                     change_horizon=2, median_window=3)
        self.assertEqual((flags, unknown), ([1, 0, 1, 1, 0, 1], 2))

    def test_an_unknown_form_is_refused(self):
        with self.assertRaises(ValueError):
            self._flags("ABOVE_90TH_PERCENTILE", [1] * 6)


class ScanTests(Fixture):
    def test_every_candidate_is_observed_and_the_multiplicity_travels_with_the_scan(self):
        observations, summary, _ = self.scan_with()
        self.assertEqual(len(observations), 14)
        self.assertEqual(summary["examined"], 14)
        self.assertEqual(summary["total_tested_on_this_question"], 19)
        self.assertAlmostEqual(summary["expected_false_passes_by_chance"], 3.2, places=1)
        self.assertEqual(sum(summary["by_outcome"].values()), 14)
        self.assertIn("NOT evidence", summary["reading"])

    def test_a_real_relation_is_found(self):
        observations, summary, _ = self.scan_with(planted="C03", effect=-0.02)
        by_id = {o["candidate"]: o for o in observations}
        self.assertEqual(by_id["C03"]["outcome"], LINK_MET)
        self.assertIn("C03", summary["passes"])
        self.assertGreaterEqual(by_id["C03"]["usable"], 5)

    def test_the_opposite_sign_is_not_read_as_a_finding(self):
        # The direction is part of the claim: returns HIGHER on ON days is not a monitor.
        observations, _, _ = self.scan_with(planted="C03", effect=+0.02)
        by_id = {o["candidate"]: o for o in observations}
        self.assertEqual(by_id["C03"]["outcome"], LINK_FAILED)
        self.assertEqual(by_id["C03"]["met"], 0)

    def test_a_state_that_almost_never_occurs_is_unreachable_and_not_failed(self):
        series = copy.deepcopy(self.series)
        for day in series["T10Y2Y"]:
            series["T10Y2Y"][day] = 1.0
        returns = _returns(self.space, series, "discovery")
        observations, _ = scan(self.record["space"], series=series, returns_by_exposure=returns)
        by_id = {o["candidate"]: o for o in observations}
        self.assertEqual(by_id["C01"]["outcome"], LINK_UNREACHABLE)
        self.assertEqual(by_id["C01"]["on_days"], 0)

    def test_a_row_outside_the_discovery_window_is_refused_not_ignored(self):
        returns = _returns(self.space, self.series, "discovery")
        for stray in ("2023-10-02", "2018-02-28"):
            leaked = copy.deepcopy(returns)
            leaked["SPY"][stray] = 0.01
            with self.assertRaises(ValueError) as caught:
                scan(self.record["space"], series=self.series, returns_by_exposure=leaked)
            self.assertIn("outside the discovery window", str(caught.exception))

    def test_a_holdout_row_cannot_reach_the_scan(self):
        returns = _returns(self.space, self.series, "holdout")
        with self.assertRaises(ValueError):
            scan(self.record["space"], series=self.series, returns_by_exposure=returns)

    def test_the_fold_rule_is_the_engines_own(self):
        # One fold short of the declared minimum is UNREACHABLE, whatever the ratio of those it has.
        observations, _, _ = self.scan_with()
        for o in observations:
            if o["usable"] < 5:
                self.assertEqual(o["outcome"], LINK_UNREACHABLE)
            elif o["met"] / o["usable"] >= 0.70:
                self.assertEqual(o["outcome"], LINK_MET)
            else:
                self.assertEqual(o["outcome"], LINK_FAILED)

    def test_a_scan_seals_once_on_content_and_not_on_the_clock(self):
        observations, summary, returns = self.scan_with()
        path = self.root / "scans.json"
        _, first = seal_scan(path, self.record, observations, summary, series=self.series,
                             returns_by_exposure=returns, scanned_at="2026-10-04T09:00:00.000000Z",
                             code_revision="b" * 40)
        _, again = seal_scan(path, self.record, observations, summary, series=self.series,
                             returns_by_exposure=returns, scanned_at="2026-10-05T09:00:00.000000Z",
                             code_revision="c" * 40)
        self.assertTrue(first)
        self.assertFalse(again)


class HoldoutTests(Fixture):
    def test_nothing_that_passed_means_the_holdout_stays_unopened(self):
        record = self.sealed_scan()
        record = {**record, "observations": [dict(o, outcome=LINK_FAILED) for o in record["observations"]]}
        called = []
        result = self.confirm(record, loader=lambda: called.append(1) or {})
        self.assertEqual(result["status"], "NOTHING_TO_CONFIRM")
        self.assertFalse(result["holdout_opened"])
        self.assertEqual(called, [])
        self.assertFalse(holdout_was_opened(self.holdout, self.record["space_id"]))

    def test_a_relation_that_persists_is_confirmed_at_the_declared_confidence(self):
        record = self.sealed_scan(planted="C03")
        passed = [o for o in record["observations"] if o["outcome"] == LINK_MET]
        result = self.confirm(record, planted="C03", effect=-0.02)["result"]
        self.assertEqual(result["confidence"], holdout_confidence(len(passed)))
        confirmed = {r["candidate"]: r for r in result["results"]}
        self.assertTrue(confirmed["C03"]["confirmed"])
        self.assertLess(confirmed["C03"]["difference_upper_bound"], 0)

    def test_a_relation_that_does_not_persist_is_not_confirmed(self):
        record = self.sealed_scan(planted="C03")
        result = self.confirm(record, planted=None)["result"]
        self.assertNotIn("C03", result["confirmed"])

    def test_the_confidence_is_one_minus_five_percent_over_the_number_that_passed(self):
        self.assertEqual(holdout_confidence(1), "0.95000000")
        self.assertEqual(holdout_confidence(2), "0.97500000")
        self.assertEqual(holdout_confidence(5), "0.99000000")
        with self.assertRaises(ValueError):
            holdout_confidence(0)

    def test_the_marker_is_sealed_before_the_holdout_returns_are_read(self):
        record = self.sealed_scan(planted="C03")
        seen = {}

        def loader():
            records = json.loads(self.holdout.read_text(encoding="utf-8"))["records"]
            seen["kinds"] = [r["kind"] for r in records]
            return _returns(self.space, self.series, "holdout")

        self.confirm(record, loader=loader)
        self.assertEqual(seen["kinds"], ["holdout-opened"])

    def test_the_holdout_is_spent_once_even_when_the_first_attempt_crashed(self):
        record = self.sealed_scan(planted="C03")

        def crashes():
            raise RuntimeError("the disk failed after the returns were read")

        with self.assertRaises(RuntimeError):
            self.confirm(record, loader=crashes)
        self.assertTrue(holdout_was_opened(self.holdout, self.record["space_id"]))
        with self.assertRaises(ValueError) as caught:
            self.confirm(record, planted="C03")
        self.assertIn("already opened", str(caught.exception))

    def test_a_second_confirmation_after_a_recorded_one_is_refused(self):
        record = self.sealed_scan(planted="C03")
        self.confirm(record, planted="C03", effect=-0.02)
        with self.assertRaises(ValueError):
            self.confirm(record, planted="C03", effect=-0.02)

    def test_a_group_too_small_to_claim_on_is_untestable_and_not_a_pass(self):
        record = self.sealed_scan(planted="C03")
        series = copy.deepcopy(self.series)
        for day in series["BAA10Y"]:
            series["BAA10Y"][day] = 1.0 + 1e-6 * hash(day) % 7           # no 21 day change above zero
        flat = {day: 1.0 for day in series["BAA10Y"]}
        series["BAA10Y"] = flat
        result = confirm_on_holdout(
            self.record, record, series=series,
            load_holdout_returns=lambda: _returns(self.space, series, "holdout"),
            path=self.holdout, opened_at="2026-10-04T10:00:00.000000Z", code_revision="b" * 40)["result"]
        untested = [r for r in result["results"] if r["candidate"] == "C03"][0]
        self.assertEqual(untested["status"], "UNTESTABLE")
        self.assertFalse(untested["confirmed"])

    def test_holdout_rows_inside_the_discovery_window_are_refused(self):
        record = self.sealed_scan(planted="C03")
        with self.assertRaises(ValueError):
            self.confirm(record, loader=lambda: _returns(self.space, self.series, "discovery"))


class EndToEndTests(Fixture):
    def test_seal_scan_confirm_leaves_a_record_of_every_step(self):
        record = self.sealed_scan(planted="C05")
        self.assertIn("C05", [o["candidate"] for o in record["observations"] if o["outcome"] == LINK_MET])
        outcome = self.confirm(record, planted="C05", effect=-0.02)["result"]
        self.assertEqual(outcome["scan_id"], record["scan_id"])
        self.assertEqual(outcome["space_id"], self.record["space_id"])
        self.assertTrue(outcome["record_id"].startswith("MONITOR_SCREEN_HOLDOUT|"))
        self.assertIn("C05", outcome["confirmed"])


if __name__ == "__main__":
    unittest.main()
