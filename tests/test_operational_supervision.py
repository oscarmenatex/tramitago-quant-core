"""Supervision of a running loop: the first thing the platform watches by itself.

Until now `evaluate_monitor` existed only as an import -- verdicts nobody
consumed. A machinery-verification loop now ticks every five minutes and nothing
read it. These cover the two failures that matter, and in particular the one the
record cannot show: a loop that STOPS leaves no entry saying so.
"""

import json
import tempfile
import unittest
from pathlib import Path

import pipeline as p
from tramitago_quant_core.risk.operational_supervision import (
    read_observations, tick_success_rate, staleness_seconds, supervise,
    SUPERVISION_OK, SUPERVISION_STALE, SUPERVISION_DEGRADED,
)

CADENCE = 300          # the installed timer: every five minutes


def _contract(degrade_below="0.80", re_enter="0.95", confirmation=2):
    """Degraded when the tick success rate falls below a floor."""
    return p.monitoring_contract(
        variable="tick_success_rate", observation_period_seconds=CADENCE,
        degradation_threshold=degrade_below, degrades_when=p.DEGRADES_BELOW,
        confirmation_periods=confirmation, action=p.ACTION_SUSPEND,
        re_entry_threshold=re_enter, source="machinery-verification observations.jsonl")


def _ticks(outcomes, start="2026-10-01T00:00:00Z", step=CADENCE):
    base = p.epoch(start)
    return [{"observed_at": p.iso(base + i * step), "outcome": outcome}
            for i, outcome in enumerate(outcomes)]


class RecordTests(unittest.TestCase):
    def test_a_corrupt_line_is_raised_not_skipped(self):
        # Silently dropping lines would make a corrupted log look healthy.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "observations.jsonl"
            path.write_text('{"outcome": "PASS"}\nnot json\n', encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                read_observations(path)
            self.assertIn("line 2", str(caught.exception))

    def test_a_missing_record_reads_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(read_observations(Path(tmp) / "absent.jsonl"), [])


class SuccessRateTests(unittest.TestCase):
    def test_the_rate_counts_only_completed_ticks(self):
        ticks = _ticks(["PASS", "PASS", "BLOCKED", "PASS"])
        self.assertAlmostEqual(tick_success_rate(ticks, 4), 0.75)

    def test_the_window_bounds_how_far_back_it_looks(self):
        ticks = _ticks(["BLOCKED"] * 10 + ["PASS"] * 2)
        self.assertAlmostEqual(tick_success_rate(ticks, 2), 1.0)
        self.assertAlmostEqual(tick_success_rate(ticks, 12), 2 / 12)

    def test_a_rate_rather_than_a_boolean_is_what_lets_hysteresis_work(self):
        # With a binary signal there is no gap between exit and re-entry and the
        # state flaps on every single failure.
        ticks = _ticks(["PASS"] * 11 + ["BLOCKED"])
        rate = tick_success_rate(ticks, 12)
        self.assertGreater(rate, 0.80)       # one failure does not breach the floor


class StalenessTests(unittest.TestCase):
    """The half of supervision the record cannot show."""

    def test_a_stopped_loop_is_detected_although_it_wrote_nothing(self):
        ticks = _ticks(["PASS"] * 5)
        result = supervise(contract=_contract(), observations=ticks,
                           now_utc="2026-10-01T02:00:00Z",
                           expected_cadence_seconds=CADENCE)
        self.assertEqual(result["state"], SUPERVISION_STALE)
        self.assertIn("no tick for", result["reason"])

    def test_staleness_short_circuits_before_the_rate(self):
        # A loop that stopped is not "healthy with an old rate"; computing a rate
        # over ticks no longer arriving would report health from history.
        ticks = _ticks(["PASS"] * 12)
        result = supervise(contract=_contract(), observations=ticks,
                           now_utc="2026-10-01T06:00:00Z",
                           expected_cadence_seconds=CADENCE)
        self.assertEqual(result["state"], SUPERVISION_STALE)
        self.assertIsNone(result["success_rate"])

    def test_a_loop_that_never_ran_is_stale_not_healthy(self):
        result = supervise(contract=_contract(), observations=[],
                           now_utc="2026-10-01T00:00:00Z",
                           expected_cadence_seconds=CADENCE)
        self.assertEqual(result["state"], SUPERVISION_STALE)
        self.assertIn("ever been recorded", result["reason"])

    def test_a_tick_within_tolerance_is_not_stale(self):
        ticks = _ticks(["PASS"] * 12)
        last = ticks[-1]["observed_at"]
        result = supervise(contract=_contract(), observations=ticks,
                           now_utc=p.iso(p.epoch(last) + CADENCE),
                           expected_cadence_seconds=CADENCE)
        self.assertEqual(result["state"], SUPERVISION_OK)


class DegradationTests(unittest.TestCase):
    def test_a_healthy_loop_reports_running(self):
        ticks = _ticks(["PASS"] * 12)
        result = supervise(contract=_contract(), observations=ticks,
                           now_utc=ticks[-1]["observed_at"],
                           expected_cadence_seconds=CADENCE)
        self.assertEqual(result["state"], SUPERVISION_OK)
        self.assertAlmostEqual(result["success_rate"], 1.0)

    def test_sustained_failure_degrades_after_confirmation(self):
        # One call is enough: supervise feeds evaluate_monitor a SERIES of
        # rolling rates, because a single value per call would reset the
        # confirmation counter forever and nothing could ever be confirmed.
        ticks = _ticks(["BLOCKED"] * 12)
        result = supervise(contract=_contract(), observations=ticks,
                           now_utc=ticks[-1]["observed_at"],
                           expected_cadence_seconds=CADENCE)
        self.assertEqual(result["state"], SUPERVISION_DEGRADED)

    def test_one_bad_tick_does_not_degrade(self):
        ticks = _ticks(["PASS"] * 11 + ["BLOCKED"])
        result = supervise(contract=_contract(), observations=ticks,
                           now_utc=ticks[-1]["observed_at"],
                           expected_cadence_seconds=CADENCE)
        self.assertEqual(result["state"], SUPERVISION_OK)


class OperationalNotFinancialTests(unittest.TestCase):
    def test_every_verdict_declares_it_is_not_financial(self):
        # Structural rather than a reminder: a P&L from a rejected signal must
        # never reach a supervision decision.
        for observations, now in (([], "2026-10-01T00:00:00Z"),
                                  (_ticks(["PASS"] * 12), "2026-10-01T06:00:00Z"),
                                  (_ticks(["PASS"] * 12), _ticks(["PASS"] * 12)[-1]["observed_at"])):
            result = supervise(contract=_contract(), observations=observations,
                               now_utc=now, expected_cadence_seconds=CADENCE)
            self.assertTrue(result["is_operational_not_financial"])


if __name__ == "__main__":
    unittest.main()
