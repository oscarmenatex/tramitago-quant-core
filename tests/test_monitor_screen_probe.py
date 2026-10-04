"""The availability probe: facts about a series, never a value, never the key."""

import importlib.util
import json
import unittest
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PROBE = REPO / "scripts" / "research" / "monitor_screen" / "probe_availability.py"
SPACE = REPO / "scripts" / "research" / "monitor_screen" / "space.json"


def _load():
    spec = importlib.util.spec_from_file_location("probe_availability", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe_module = _load()
SENTINEL = "987654.321"


def _weekdays(start, count, skip=()):
    day, out = date.fromisoformat(start), []
    while len(out) < count:
        if day.weekday() < 5 and day.isoformat() not in skip:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _fetch(dates, meta=None, missing=()):
    meta = {"title": "A public title", "frequency_short": "D", "units_short": "Percent",
            "observation_start": "1976-06-01", "observation_end": "2026-10-01", **(meta or {})}

    def fetch(path, params):
        if path == "series":
            return {"seriess": [meta]}
        return {"observations": [{"date": d, "value": "." if d in missing else SENTINEL}
                                 for d in dates]}
    return fetch


class ProbeTests(unittest.TestCase):
    START, END = "2018-03-01", "2018-06-01"

    def _dates(self, skip=()):
        count = probe_module.weekdays_between(self.START, self.END)
        return _weekdays(self.START, count, skip)

    def test_a_complete_daily_series_is_available(self):
        result = probe_module.probe("X", self.START, self.END, _fetch(self._dates()))
        self.assertEqual(result["verdict"], "AVAILABLE")
        self.assertEqual(result["completeness"], 1.0)
        self.assertEqual(result["missing_in_window"], 0)

    def test_missing_observations_count_as_missing_not_as_data(self):
        dates = self._dates()
        result = probe_module.probe("X", self.START, self.END, _fetch(dates, missing=set(dates[:5])))
        self.assertEqual(result["missing_in_window"], 5)
        self.assertEqual(result["observations_in_window"], len(dates) - 5)

    def test_a_series_that_starts_late_is_flagged(self):
        dates = self._dates()[30:]
        result = probe_module.probe("X", self.START, self.END, _fetch(dates))
        self.assertTrue(result["verdict"].startswith("FLAG"))
        self.assertIn("after it opens", result["verdict"])

    def test_a_series_with_a_long_gap_or_low_completeness_is_flagged(self):
        dates = self._dates()
        gap = set(dates[20:40])
        result = probe_module.probe("X", self.START, self.END, _fetch(dates, missing=gap))
        self.assertIn("longest gap", result["verdict"])
        sparse = dates[::3]
        self.assertIn("completeness", probe_module.probe(
            "X", self.START, self.END, _fetch(sparse))["verdict"])

    def test_a_series_that_is_not_daily_is_flagged(self):
        result = probe_module.probe("X", self.START, self.END,
                                    _fetch(self._dates(), meta={"frequency_short": "M"}))
        self.assertIn("not daily", result["verdict"])

    def test_a_window_with_no_observation_is_flagged(self):
        result = probe_module.probe("X", self.START, self.END, _fetch([]))
        self.assertIn("no observation", result["verdict"])

    def test_a_failure_reports_its_kind_and_nothing_that_could_carry_a_key(self):
        def broken(path, params):
            raise RuntimeError("https://example/fred?api_key=SECRET-KEY-VALUE")
        result = probe_module.probe("X", self.START, self.END, broken)
        self.assertEqual(result["verdict"], "ERROR: RuntimeError")
        self.assertNotIn("SECRET", json.dumps(result))


class NothingLeaksTests(unittest.TestCase):
    def test_no_value_appears_in_the_results_or_the_rendered_table(self):
        dates = _weekdays("2018-03-01", 60)
        result = probe_module.probe("X", "2018-03-01", "2018-06-01", _fetch(dates))
        self.assertNotIn(SENTINEL, json.dumps(result))
        self.assertNotIn(SENTINEL, probe_module.render([result]))

    def test_the_result_carries_no_value_field(self):
        result = probe_module.probe("X", "2018-03-01", "2018-06-01",
                                    _fetch(_weekdays("2018-03-01", 60)))
        for key in result:
            self.assertNotIn("value", key)
            self.assertNotIn("return", key)

    def test_the_source_never_prints_the_key(self):
        source = PROBE.read_text(encoding="utf-8")
        self.assertNotIn("print(key", source)
        self.assertNotIn("print(f\"{key", source)


class DefaultsAgreeWithTheSpaceTests(unittest.TestCase):
    def test_the_default_series_are_exactly_the_ones_that_were_not_already_captured(self):
        space = json.loads(SPACE.read_text(encoding="utf-8"))
        probed = {v["series"] for v in space["variables"]
                  if not v["availability"].startswith("CAPTURED")}
        self.assertEqual(set(probe_module.DEFAULT_SERIES), probed)

    def test_the_space_records_what_the_probe_found_for_each_of_them(self):
        space = json.loads(SPACE.read_text(encoding="utf-8"))
        for variable in space["variables"]:
            if variable["series"] in probe_module.DEFAULT_SERIES:
                self.assertEqual(variable["availability"], "PROBED_AVAILABLE")

    def test_the_default_window_is_the_whole_span_the_screen_will_use(self):
        space = json.loads(SPACE.read_text(encoding="utf-8"))
        windows = space["windows"]
        self.assertEqual(probe_module.WINDOW_START, windows["discovery"]["start_utc"][:10])
        self.assertEqual(probe_module.WINDOW_END, windows["holdout"]["end_exclusive_utc"][:10])


class WeekdayTests(unittest.TestCase):
    def test_weekdays_between_counts_monday_to_friday_in_a_half_open_window(self):
        self.assertEqual(probe_module.weekdays_between("2018-03-05", "2018-03-12"), 5)   # Mon..Fri
        self.assertEqual(probe_module.weekdays_between("2018-03-03", "2018-03-05"), 0)   # Sat, Sun
        self.assertEqual(probe_module.weekdays_between("2018-03-05", "2018-03-05"), 0)


if __name__ == "__main__":
    unittest.main()
