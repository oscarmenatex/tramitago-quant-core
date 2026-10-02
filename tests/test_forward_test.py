"""A Hypothesis whose evaluation period has not happened yet.

Forty-five Hypotheses and not one was tested forward: every verdict is
retrospective and every holdout a slice of the past declared unread. That is a
finite resource, and two populations were declared EXHAUSTED BY MEASUREMENT on
2026-10-02, which is what running out looks like.
"""

import tempfile
import unittest
from pathlib import Path

import pipeline as p
from tramitago_quant_core.research.forward_test import (
    is_forward_hypothesis, forward_test_status, require_forward_test_ready,
    pending_forward_tests, FORWARD_TEST_PENDING, FORWARD_TEST_READY,
    FORWARD_TEST_NOT_FORWARD,
)


def _hypothesis(created_at, start, end, identity="HYPOTHESIS|test"):
    return {
        "hypothesis_id": identity,
        "creation_timestamp": created_at,
        "creation_context": {"created_by": "test", "provenance": []},
        "constraints": {"period": {"start_utc": start, "end_exclusive_utc": end},
                        "universe": ["BTC-USD"], "variables": ["close"]},
    }


RETROSPECTIVE = _hypothesis("2026-10-02T00:00:00Z",
                            "2024-01-01T00:00:00Z", "2025-01-01T00:00:00Z")
FORWARD = _hypothesis("2026-10-02T00:00:00Z",
                      "2026-10-03T00:00:00Z", "2027-10-06T00:00:00Z")


class DerivationTests(unittest.TestCase):
    """Forward-dating is a property of the sealed content, not a flag."""

    def test_a_period_beginning_after_the_seal_is_forward(self):
        self.assertTrue(is_forward_hypothesis(FORWARD))

    def test_a_period_beginning_before_the_seal_is_not(self):
        self.assertFalse(is_forward_hypothesis(RETROSPECTIVE))

    def test_a_period_beginning_exactly_at_the_seal_counts_as_forward(self):
        same = _hypothesis("2026-10-02T00:00:00Z",
                           "2026-10-02T00:00:00Z", "2027-10-02T00:00:00Z")
        self.assertTrue(is_forward_hypothesis(same))

    def test_a_hypothesis_without_the_dates_it_needs_is_refused(self):
        for broken in ({"hypothesis_id": "x", "constraints": {}},
                       {"hypothesis_id": "x", "creation_timestamp": "2026-10-02",
                        "constraints": {"period": {"start_utc": "2026-10-03T00:00:00Z",
                                                   "end_exclusive_utc": "2027-01-01T00:00:00Z"}}}):
            with self.assertRaises(ValueError):
                is_forward_hypothesis(broken)


class StatusTests(unittest.TestCase):
    def test_it_is_pending_until_the_period_has_fully_elapsed(self):
        self.assertEqual(forward_test_status(FORWARD, "2027-10-05T00:00:00Z"),
                         FORWARD_TEST_PENDING)

    def test_mostly_elapsed_is_still_pending(self):
        # The test that was declared is the whole period; a partial one is a
        # different test wearing its name.
        self.assertEqual(forward_test_status(FORWARD, "2027-10-05T23:59:59Z"),
                         FORWARD_TEST_PENDING)

    def test_it_is_ready_once_the_period_is_over(self):
        self.assertEqual(forward_test_status(FORWARD, "2027-10-06T00:00:00Z"),
                         FORWARD_TEST_READY)

    def test_a_retrospective_hypothesis_is_not_a_forward_test_at_all(self):
        self.assertEqual(forward_test_status(RETROSPECTIVE, "2026-10-02T00:00:00Z"),
                         FORWARD_TEST_NOT_FORWARD)


class GuardTests(unittest.TestCase):
    def test_it_raises_before_the_period_is_over_and_says_how_long_is_left(self):
        with self.assertRaises(ValueError) as caught:
            require_forward_test_ready(FORWARD, "2027-09-06T00:00:00Z")
        message = str(caught.exception)
        self.assertIn("30 day(s) left", message)
        self.assertIn("pre-declaration", message)

    def test_it_permits_a_retrospective_hypothesis_without_comment(self):
        self.assertEqual(require_forward_test_ready(RETROSPECTIVE, "2026-10-02T00:00:00Z"),
                         FORWARD_TEST_NOT_FORWARD)

    def test_it_permits_a_forward_test_whose_period_is_over(self):
        self.assertEqual(require_forward_test_ready(FORWARD, "2027-11-01T00:00:00Z"),
                         FORWARD_TEST_READY)

    def test_dataset_creation_refuses_a_pending_forward_test(self):
        # Wired in rather than left as advice, because the moment it matters is
        # the moment somebody is curious how it is going.
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "hypotheses.json"
            metric = "mean_forward_return_1d(a) - mean_forward_return_1d(b)"
            hypothesis = p.constitute_hypothesis(
                registry, description="forward test " + "x" * 20, target_metric=metric,
                expected_direction="INCREASE",
                constraints={"period": {"start_utc": "2099-01-01T00:00:00Z",
                                        "end_exclusive_utc": "2099-12-27T00:00:00Z"},
                             "universe": ["BTC-USD"],
                             "variables": ["close", "sma_close_3", "forward_return_1d"]},
                acceptance_criterion={"comparison": "GT", "expected_direction": "INCREASE",
                                      "metric": metric, "threshold": "0"},
                creation_timestamp="2026-10-02T00:00:00Z", status="CONSTITUTED",
                created_by="test",
                provenance=["DISCOVERY|artifacts/live-run-1", "SNAPSHOT_SHA256|" + "a" * 64],
                system_version="0.1.0", code_revision="a" * 40)
            with self.assertRaises(ValueError) as caught:
                p.create_hypothesis_dataset(
                    registry, hypothesis["hypothesis_id"], 1, Path(tmp) / "dataset",
                    acquired_at="2026-10-02T00:00:00Z")
            self.assertIn("left to run", str(caught.exception))


class PendingTests(unittest.TestCase):
    def test_pending_tests_are_listed_soonest_first(self):
        far = _hypothesis("2026-10-02T00:00:00Z", "2026-10-03T00:00:00Z",
                          "2028-01-01T00:00:00Z", "HYPOTHESIS|far")
        near = _hypothesis("2026-10-02T00:00:00Z", "2026-10-03T00:00:00Z",
                           "2027-01-01T00:00:00Z", "HYPOTHESIS|near")
        pending = pending_forward_tests([far, near, RETROSPECTIVE], "2026-10-02T00:00:00Z")
        self.assertEqual([item["hypothesis_id"] for item in pending],
                         ["HYPOTHESIS|near", "HYPOTHESIS|far"])

    def test_an_elapsed_forward_test_is_no_longer_pending(self):
        self.assertEqual(pending_forward_tests([FORWARD], "2028-01-01T00:00:00Z"), [])

    def test_a_malformed_hypothesis_is_skipped_rather_than_crashing_the_listing(self):
        # A listing that dies on one bad record hides every good one behind it.
        self.assertEqual(len(pending_forward_tests(
            [FORWARD, {"hypothesis_id": "broken"}], "2026-10-02T00:00:00Z")), 1)

    def test_it_reports_how_long_is_left(self):
        pending = pending_forward_tests([FORWARD], "2027-10-01T00:00:00Z")
        self.assertEqual(pending[0]["days_remaining"], 5)


if __name__ == "__main__":
    unittest.main()
