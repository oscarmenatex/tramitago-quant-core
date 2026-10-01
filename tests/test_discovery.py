"""Bounded Discovery: the mechanism that produces Findings.

The Finding contract has existed since 2026-09-29 with nothing to fill it. These
cover what makes a wide search defensible rather than dredging: the space is
sealed before the data is read, the holdout is unreadable by construction, the
multiplicity travels with the observation, and the representativeness gate runs
before anything expensive does.
"""

import tempfile
import unittest
from pathlib import Path

import pipeline as p
from tramitago_quant_core.research.discovery import (
    constitute_discovery_space, load_discovery_space, candidate_strategy,
    minority_state_frequency, scan_discovery_space, rank_observations,
    candidate_is_examinable, constitute_discovery_scan, load_discovery_scan,
    finding_from_scan, _candidate_observation,
)
from tramitago_quant_core.research.finding import load_finding, FINDING_STATUS_OPEN

DAY = 86400
REVISION = "a" * 40
DISCOVERY = {"start_utc": "2025-01-01T00:00:00Z", "end_exclusive_utc": "2025-07-01T00:00:00Z"}
HOLDOUT = {"start_utc": "2025-07-01T00:00:00Z", "end_exclusive_utc": "2026-01-01T00:00:00Z"}
CANDIDATES = [{"strategy_id": "SMA_CROSSOVER", "parameters": {"window": window}}
              for window in (3, 5, 7)]


def _space(registry_path, **overrides):
    arguments = {
        "justification": "Declared before any row was read: the SMA family already "
                         "investigated manually, widened by two windows and nothing else.",
        "candidates": CANDIDATES,
        "discovery_window": DISCOVERY,
        "holdout_window": HOLDOUT,
        "minimum_support": 10,
        "minimum_minority_state_frequency": "0.20",
        "declared_by": "test",
        "declared_at": "2025-01-01T00:00:00Z",
    }
    arguments.update(overrides)
    return constitute_discovery_space(registry_path, **arguments)


def _rows(closes, start="2025-01-01T00:00:00Z"):
    base = p.epoch(start)
    return [{"timestamp": p.iso(base + index * DAY), "close": float(close)}
            for index, close in enumerate(closes)]


def _trending_rows(count=120):
    # A clean uptrend: close above its own moving average predicts the next up
    # move, so every candidate is examinable and the effect is positive.
    return _rows([100.0 + index + (3.0 if index % 5 == 0 else 0.0) for index in range(count)])


class SpaceDeclarationTests(unittest.TestCase):
    def test_a_space_without_a_disjoint_holdout_is_refused(self):
        # No flag skips it: with nothing held back, a Finding cannot produce a
        # Hypothesis testable on data the search never saw.
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                _space(Path(tmp) / "spaces.json",
                       holdout_window={"start_utc": "2025-06-01T00:00:00Z",
                                       "end_exclusive_utc": "2026-01-01T00:00:00Z"})

    def test_a_space_needs_a_written_justification(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                _space(Path(tmp) / "spaces.json", justification="")

    def test_the_space_is_empty_or_duplicated_at_its_peril(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spaces.json"
            with self.assertRaises(ValueError):
                _space(path, candidates=[])
            with self.assertRaises(ValueError):
                _space(path, candidates=CANDIDATES + [CANDIDATES[0]])

    def test_a_candidate_the_sealed_machinery_cannot_rebuild_is_refused(self):
        # Discovery must not propose what validation could never re-derive.
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                _space(Path(tmp) / "spaces.json",
                       candidates=[{"strategy_id": "NOT_A_STRATEGY", "parameters": {}}])

    def test_identity_covers_the_thresholds_so_they_cannot_be_relaxed_quietly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spaces.json"
            strict = _space(path)
            loose = _space(path, minimum_minority_state_frequency="0.01")
            self.assertNotEqual(strict["space_id"], loose["space_id"])

    def test_redeclaring_the_same_space_keeps_the_first_declaration_time(self):
        # The declaration that matters is the one that precedes the data.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spaces.json"
            first = _space(path)
            again = _space(path, declared_at="2025-12-31T00:00:00Z")
            self.assertEqual(again["declared_at"], first["declared_at"])
            self.assertEqual(len(load_discovery_space(path, first["space_id"])["candidates"]), 3)

    def test_candidates_rebuild_into_real_strategies(self):
        strategy = candidate_strategy(CANDIDATES[0])
        self.assertEqual(strategy["strategy_id"], "SMA_CROSSOVER")
        self.assertEqual(strategy["parameters"], {"window": 3})


class HoldoutTests(unittest.TestCase):
    """The protection that actually binds."""

    def test_a_scan_refuses_rows_from_the_holdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            space = _space(Path(tmp) / "spaces.json")
            # 200 days from 2025-01-01 runs past 2025-07-01 into the holdout.
            with self.assertRaises(ValueError) as caught:
                scan_discovery_space(space, _trending_rows(200))
            self.assertIn("outside the declared discovery window", str(caught.exception))

    def test_a_scan_accepts_rows_inside_the_discovery_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            space = _space(Path(tmp) / "spaces.json")
            observations = scan_discovery_space(space, _trending_rows(100))
            self.assertEqual(len(observations), 3)


class RepresentativenessTests(unittest.TestCase):
    """The gate that until now lived only in a governance document."""

    def test_the_minority_state_is_what_bounds_observability(self):
        self.assertAlmostEqual(minority_state_frequency(90, 10), 0.10)
        self.assertAlmostEqual(minority_state_frequency(10, 90), 0.10)
        self.assertIsNone(minority_state_frequency(0, 0))

    def test_a_rare_state_candidate_is_refused_outright(self):
        with tempfile.TemporaryDirectory() as tmp:
            space = _space(Path(tmp) / "spaces.json")
            # Ample support -- 50 rows clears the floor of 10 -- so what refuses
            # this candidate is representativeness alone, not thin data.
            observation = {"effect": "0.500000000000", "upper_count": 950, "lower_count": 50,
                           "minority_state_frequency": "0.050000000000"}
            examinable, reason = candidate_is_examinable(observation, space)
            self.assertFalse(examinable)
            self.assertIn("no monitor could observe this state", reason)

    def test_a_big_effect_does_not_rescue_a_rare_state(self):
        # Inadmissible outright, not merely weak.
        with tempfile.TemporaryDirectory() as tmp:
            space = _space(Path(tmp) / "spaces.json")
            huge = {"effect": "99.000000000000", "upper_count": 9900, "lower_count": 100,
                    "minority_state_frequency": "0.010000000000"}
            self.assertFalse(candidate_is_examinable(huge, space)[0])

    def test_thin_support_is_refused_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            space = _space(Path(tmp) / "spaces.json")
            thin = {"effect": "0.100000000000", "upper_count": 6, "lower_count": 6,
                    "minority_state_frequency": "0.500000000000"}
            examinable, reason = candidate_is_examinable(thin, space)
            self.assertFalse(examinable)
            self.assertIn("below the declared minimum 10", reason)


class ObservationTests(unittest.TestCase):
    def test_an_observation_reports_counts_and_an_effect_but_never_a_verdict(self):
        strategy = candidate_strategy(CANDIDATES[0])
        observation = _candidate_observation(strategy, _trending_rows(100))
        self.assertEqual(set(observation), {
            "strategy_id", "parameters", "outcome_id", "upper_count", "lower_count",
            "minority_state_frequency", "effect"})
        self.assertNotIn("p_value", observation)
        self.assertGreater(observation["upper_count"] + observation["lower_count"], 0)

    def test_an_observation_is_reproducible_to_the_last_decimal(self):
        # A float repr that varied by platform would make a sealed scan
        # irreproducible on another machine.
        strategy = candidate_strategy(CANDIDATES[1])
        rows = _trending_rows(100)
        self.assertEqual(_candidate_observation(strategy, rows),
                         _candidate_observation(strategy, rows))

    def test_too_few_rows_leaves_a_group_empty_and_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            space = _space(Path(tmp) / "spaces.json")
            observation = _candidate_observation(candidate_strategy(CANDIDATES[2]), _rows([1, 2]))
            self.assertIsNone(observation["effect"])
            self.assertFalse(candidate_is_examinable(observation, space)[0])


class ScanTests(unittest.TestCase):
    def test_the_scan_keeps_every_candidate_including_the_refused_ones(self):
        # A scan reporting only survivors would hide its own multiplicity.
        with tempfile.TemporaryDirectory() as tmp:
            space = _space(Path(tmp) / "spaces.json", minimum_support=40)
            observations = scan_discovery_space(space, _trending_rows(60))
            self.assertEqual(len(observations), len(CANDIDATES))
            self.assertTrue(any(not item["examinable"] for item in observations))
            record = constitute_discovery_scan(
                Path(tmp) / "scans.json", space=space, observations=observations,
                scanned_at="2025-07-01T00:00:00Z", scan_code_revision=REVISION)
            self.assertEqual(record["summary"]["candidates_examined"], len(CANDIDATES))
            self.assertEqual(
                record["summary"]["candidates_examinable"]
                + record["summary"]["candidates_refused"], len(CANDIDATES))

    def test_the_summary_reports_the_spread_not_only_the_winner(self):
        # The first real scan returned best +0.0029 and worst -0.0029 on an axis
        # already measured empty. The winner alone reads as an edge; beside its
        # mirror image it reads as the right tail of noise.
        with tempfile.TemporaryDirectory() as tmp:
            space = _space(Path(tmp) / "spaces.json")
            observations = scan_discovery_space(space, _trending_rows(100))
            record = constitute_discovery_scan(
                Path(tmp) / "scans.json", space=space, observations=observations,
                scanned_at="2025-07-01T00:00:00Z", scan_code_revision=REVISION)
            summary = record["summary"]
            self.assertLessEqual(float(summary["worst_effect"]), float(summary["best_effect"]))
            self.assertEqual(summary["positive_effects"] + summary["negative_effects"]
                             <= summary["candidates_examinable"], True)
            self.assertIsNotNone(summary["median_effect"])

    def test_the_finding_carries_the_spread_so_the_winner_cannot_stand_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            space = _space(Path(tmp) / "spaces.json")
            observations = scan_discovery_space(space, _trending_rows(100))
            scan = constitute_discovery_scan(
                Path(tmp) / "scans.json", space=space, observations=observations,
                scanned_at="2025-07-01T00:00:00Z", scan_code_revision=REVISION)
            finding = finding_from_scan(Path(tmp) / "findings.json", scan=scan, space=space,
                                        created_by="test", created_at="2025-07-01T00:00:00Z")
            self.assertTrue(any(item.startswith("SCAN_EFFECT_SPREAD|")
                                for item in finding["supporting_evidence"]))
            self.assertIn("median", finding["observation"])

    def test_ranking_is_deterministic_and_ties_break_on_declared_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            space = _space(Path(tmp) / "spaces.json")
            observations = scan_discovery_space(space, _trending_rows(100))
            ranked = rank_observations(observations)
            self.assertEqual([item["declared_order"] for item in ranked],
                             [item["declared_order"] for item in rank_observations(observations)])
            effects = [item["effect"] for item in ranked]
            self.assertEqual(effects, sorted(effects, reverse=True))

    def test_a_sealed_scan_round_trips_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            space = _space(Path(tmp) / "spaces.json")
            path = Path(tmp) / "scans.json"
            observations = scan_discovery_space(space, _trending_rows(100))
            first = constitute_discovery_scan(path, space=space, observations=observations,
                                              scanned_at="2025-07-01T00:00:00Z",
                                              scan_code_revision=REVISION)
            again = constitute_discovery_scan(path, space=space, observations=observations,
                                              scanned_at="2025-07-01T00:00:00Z",
                                              scan_code_revision=REVISION)
            self.assertEqual(first, again)
            self.assertEqual(load_discovery_scan(path, first["scan_id"]), first)

    def test_a_tampered_scan_is_rejected_on_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            space = _space(Path(tmp) / "spaces.json")
            path = Path(tmp) / "scans.json"
            observations = scan_discovery_space(space, _trending_rows(100))
            record = constitute_discovery_scan(path, space=space, observations=observations,
                                               scanned_at="2025-07-01T00:00:00Z",
                                               scan_code_revision=REVISION)
            raw = path.read_text("utf-8").replace(
                f'"candidates_examined": {len(CANDIDATES)}', '"candidates_examined": 1')
            path.write_text(raw, encoding="utf-8")
            with self.assertRaises(ValueError):
                load_discovery_scan(path, record["scan_id"])


PAIR_CANDIDATES = [
    {"strategy_id": "PAIR_RATIO_REVERSION",
     "parameters": {"window": window, "pair_variable": "pair_close"}, "series": series}
    for series in ("ETH-USD/ETC-USD", "BTC-USD/LTC-USD")
    for window in (5, 10)
]


def _pair_rows(count=120, drift=0.0, start="2025-01-01T00:00:00Z"):
    base = p.epoch(start)
    return [{"timestamp": p.iso(base + index * DAY),
             "close": 100.0 + index + (2.0 if index % 4 == 0 else 0.0),
             "pair_close": 50.0 + index * (0.5 + drift)}
            for index in range(count)]


class SeriesTests(unittest.TestCase):
    """A relative-value Strategy carries its second leg as a COLUMN NAME, not as
    the pair's identity -- so without a series the same eighteen pair-window
    combinations collapse to three candidates."""

    def test_without_a_series_pair_candidates_are_indistinguishable(self):
        with tempfile.TemporaryDirectory() as tmp:
            bare = [{k: v for k, v in item.items() if k != "series"}
                    for item in PAIR_CANDIDATES]
            with self.assertRaises(ValueError):
                _space(Path(tmp) / "spaces.json", candidates=bare)

    def test_a_space_cannot_mix_series_bearing_and_series_less_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                _space(Path(tmp) / "spaces.json",
                       candidates=[PAIR_CANDIDATES[0], CANDIDATES[0]])

    def test_each_candidate_is_measured_on_its_own_series(self):
        with tempfile.TemporaryDirectory() as tmp:
            space = _space(Path(tmp) / "spaces.json", candidates=PAIR_CANDIDATES)
            observations = scan_discovery_space(space, {
                "ETH-USD/ETC-USD": _pair_rows(drift=0.0),
                "BTC-USD/LTC-USD": _pair_rows(drift=0.01)})
            self.assertEqual(len(observations), 4)
            self.assertEqual({item["series"] for item in observations},
                             {"ETH-USD/ETC-USD", "BTC-USD/LTC-USD"})
            # Different legs, different numbers -- the series is not decoration.
            by_series = {}
            for item in observations:
                by_series.setdefault(item["series"], []).append(item["effect"])
            self.assertNotEqual(by_series["ETH-USD/ETC-USD"], by_series["BTC-USD/LTC-USD"])

    def test_the_outcome_measured_is_the_spread_not_one_leg(self):
        with tempfile.TemporaryDirectory() as tmp:
            space = _space(Path(tmp) / "spaces.json", candidates=PAIR_CANDIDATES)
            observations = scan_discovery_space(space, {
                "ETH-USD/ETC-USD": _pair_rows(), "BTC-USD/LTC-USD": _pair_rows(drift=0.01)})
            self.assertEqual({item["outcome_id"] for item in observations}, {"SPREAD_RETURN"})

    def test_rows_for_a_declared_series_cannot_be_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            space = _space(Path(tmp) / "spaces.json", candidates=PAIR_CANDIDATES)
            with self.assertRaises(ValueError) as caught:
                scan_discovery_space(space, {"ETH-USD/ETC-USD": _pair_rows()})
            self.assertIn("BTC-USD/LTC-USD", str(caught.exception))

    def test_the_two_row_forms_are_never_guessed_between(self):
        # Measuring every candidate on the same series would produce a scan whose
        # observations all look distinct and are not.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spaces.json"
            with_series = _space(path, candidates=PAIR_CANDIDATES)
            without = _space(path)
            with self.assertRaises(ValueError):
                scan_discovery_space(with_series, _pair_rows())
            with self.assertRaises(ValueError):
                scan_discovery_space(without, {"BTC-USD": _trending_rows(100)})

    def test_the_holdout_guard_covers_every_series(self):
        with tempfile.TemporaryDirectory() as tmp:
            space = _space(Path(tmp) / "spaces.json", candidates=PAIR_CANDIDATES)
            with self.assertRaises(ValueError):
                scan_discovery_space(space, {
                    "ETH-USD/ETC-USD": _pair_rows(100),
                    "BTC-USD/LTC-USD": _pair_rows(300)})

    def test_a_series_less_observation_keeps_its_original_identity(self):
        # The additive pattern earns its keep only if already-sealed scans
        # reverify byte for byte.
        observation = _candidate_observation(candidate_strategy(CANDIDATES[0]),
                                             _trending_rows(100))
        self.assertNotIn("series", observation)

    def test_the_finding_names_the_series_it_was_measured_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            space = _space(Path(tmp) / "spaces.json", candidates=PAIR_CANDIDATES)
            observations = scan_discovery_space(space, {
                "ETH-USD/ETC-USD": _pair_rows(), "BTC-USD/LTC-USD": _pair_rows(drift=0.01)})
            scan = constitute_discovery_scan(
                Path(tmp) / "scans.json", space=space, observations=observations,
                scanned_at="2025-07-01T00:00:00Z", scan_code_revision=REVISION)
            finding = finding_from_scan(Path(tmp) / "findings.json", scan=scan, space=space,
                                        created_by="test", created_at="2025-07-01T00:00:00Z")
            self.assertTrue(any(item.startswith("SERIES|")
                                for item in finding["supporting_evidence"]))


class FindingTests(unittest.TestCase):
    def _sealed(self, tmp, **space_overrides):
        space = _space(Path(tmp) / "spaces.json", **space_overrides)
        observations = scan_discovery_space(space, _trending_rows(100))
        scan = constitute_discovery_scan(
            Path(tmp) / "scans.json", space=space, observations=observations,
            scanned_at="2025-07-01T00:00:00Z", scan_code_revision=REVISION)
        return space, scan

    def test_the_multiplicity_travels_with_the_finding(self):
        # Without it, Discovery launders the multiple comparisons: look at many,
        # report one, and let the Hypothesis be corrected for N=1.
        with tempfile.TemporaryDirectory() as tmp:
            space, scan = self._sealed(tmp)
            finding = finding_from_scan(
                Path(tmp) / "findings.json", scan=scan, space=space,
                created_by="test", created_at="2025-07-01T00:00:00Z")
            evidence = finding["supporting_evidence"]
            self.assertIn(f"CANDIDATES_EXAMINED|{len(CANDIDATES)}", evidence)
            self.assertIn("SELECTED_RANK|0", evidence)
            self.assertTrue(any(item.startswith("MINORITY_STATE_FREQUENCY|")
                                for item in evidence))

    def test_the_finding_names_the_holdout_it_did_not_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            space, scan = self._sealed(tmp)
            finding = finding_from_scan(
                Path(tmp) / "findings.json", scan=scan, space=space,
                created_by="test", created_at="2025-07-01T00:00:00Z")
            self.assertIn(HOLDOUT["start_utc"], finding["exploration_context"])
            self.assertTrue(any(item.startswith("HOLDOUT_UNREAD|")
                                for item in finding["supporting_evidence"]))

    def test_the_observation_says_in_words_that_it_is_not_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            space, scan = self._sealed(tmp)
            finding = finding_from_scan(
                Path(tmp) / "findings.json", scan=scan, space=space,
                created_by="test", created_at="2025-07-01T00:00:00Z")
            self.assertIn("not evidence", finding["observation"])

    def test_a_finding_is_born_open_and_unpromoted(self):
        # Discovery produces an observation; promotion stays manual.
        with tempfile.TemporaryDirectory() as tmp:
            space, scan = self._sealed(tmp)
            path = Path(tmp) / "findings.json"
            finding = finding_from_scan(path, scan=scan, space=space, created_by="test",
                                        created_at="2025-07-01T00:00:00Z")
            self.assertEqual(load_finding(path, finding["finding_id"])["status"],
                             FINDING_STATUS_OPEN)

    def test_a_scan_with_nothing_examinable_produces_no_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            space, scan = self._sealed(tmp, minimum_minority_state_frequency="0.99")
            with self.assertRaises(ValueError) as caught:
                finding_from_scan(Path(tmp) / "findings.json", scan=scan, space=space,
                                  created_by="test", created_at="2025-07-01T00:00:00Z")
            self.assertIn("examinable", str(caught.exception))

    def test_a_scan_and_a_foreign_space_do_not_correspond(self):
        with tempfile.TemporaryDirectory() as tmp:
            space, scan = self._sealed(tmp)
            other = _space(Path(tmp) / "spaces.json", minimum_support=11)
            with self.assertRaises(ValueError):
                finding_from_scan(Path(tmp) / "findings.json", scan=scan, space=other,
                                  created_by="test", created_at="2025-07-01T00:00:00Z")

    def test_a_rank_outside_the_examinable_candidates_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            space, scan = self._sealed(tmp)
            with self.assertRaises(ValueError):
                finding_from_scan(Path(tmp) / "findings.json", scan=scan, space=space,
                                  rank=99, created_by="test", created_at="2025-07-01T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
