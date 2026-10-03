"""What a candidate cannot do, kept apart from how large its effect might be.

MEASURED 2026-10-03: VIXY held short was recommended as the one route to a
sustainable position on its effect size alone. The platform's executor cannot
open a short, and the monitor link for that premium had already failed a
measurement on its mirror instrument. The first two registers carried neither
fact, so a FEASIBLE verdict hid two problems.
"""

import json
import re
import tempfile
import unittest
from pathlib import Path

from tramitago_quant_core.research.candidate_register import (
    candidate, candidate_verdict, candidate_blockers, monitor_status, rank_candidates,
    admissible_in_principle, what_would_unlock, constitute_candidate_register,
    EXECUTOR_CAN_OPEN_SHORTS, MONITOR_NONE_POSSIBLE, MONITOR_LINK_MET,
    MONITOR_LINK_REFUTED, MONITOR_IDENTITY_ONLY, MONITOR_UNEXAMINED,
    CERTAINTY_MEASURED, CERTAINTY_INFERRED, BLOCKER_REQUIRES_SHORT,
    BLOCKER_MONITOR_REFUTED, BLOCKER_NO_MONITOR, STATUS_UNTRIED, STATUS_MEASURED_DEAD,
    VERDICT_FEASIBLE,
)
from tramitago_quant_core.research.pre_declaration import CLAIM_PREMIUM

UNEXAMINED = monitor_status(
    variable="a published variable nobody has examined yet", status=MONITOR_UNEXAMINED,
    evidence="nobody has measured whether this variable degrades the return")

BASE = dict(
    claim_class=CLAIM_PREMIUM,
    payer="investors shedding a risk they are mandated or unwilling to bear",
    effect_source="a range recalled from published estimates, never measured here",
    data_source="Alpaca SIP", status=STATUS_UNTRIED,
    requires_short=False, monitor=UNEXAMINED)


def _with(**overrides):
    return candidate(name=overrides.pop("name", "x"), effect_low=overrides.pop("low", "0.60"),
                     effect_high=overrides.pop("high", "1.00"),
                     available_years=overrides.pop("years", "10.75"), **{**BASE, **overrides})


REFUTED_HERE = monitor_status(
    variable="the VIX term structure slope", status=MONITOR_LINK_REFUTED,
    certainty=CERTAINTY_MEASURED, evidence="2 of 5 folds against a 0.70 threshold")
REFUTED_ON_SIBLING = monitor_status(
    variable="the VIX term structure slope", status=MONITOR_LINK_REFUTED,
    certainty=CERTAINTY_INFERRED, measured_on="SVXY, the inverse of the same index",
    evidence="2 of 5 folds against a 0.70 threshold")


class ExecutorCapabilityTests(unittest.TestCase):
    """The platform cannot open a short, and that fact is held to a test.

    pipeline.py accepts only (ENTER, BUY) and (EXIT, SELL), at two places.
    DOC-011 section 6 went stale for a week by being prose. This reads the
    executor source so the declared capability cannot drift from the code.
    """

    SOURCE = Path(__file__).resolve().parents[1] / "pipeline.py"
    SHORT_PAIR = re.compile(r"""\(\s*["']ENTER["']\s*,\s*["']SELL["']\s*\)""")

    def test_the_declared_capability_agrees_with_the_executor_source(self):
        opens_shorts = bool(self.SHORT_PAIR.search(self.SOURCE.read_text(encoding="utf-8")))
        self.assertEqual(
            EXECUTOR_CAN_OPEN_SHORTS, opens_shorts,
            "pipeline.py now accepts an ENTER/SELL pair, or EXECUTOR_CAN_OPEN_SHORTS was "
            "changed without it: update the candidate register in the SAME change that "
            "builds or removes the capability, or every requires_short verdict is wrong")

    def test_the_pairs_the_executor_does_accept_are_still_there(self):
        source = self.SOURCE.read_text(encoding="utf-8")
        self.assertIn('("ENTER", "BUY"), ("EXIT", "SELL")', source)


class RequiredFieldTests(unittest.TestCase):
    """A candidate that does not say whether it needs a short is exactly how the
    wrong recommendation was made, so neither field has a default."""

    def _without(self, key):
        fields = {k: v for k, v in BASE.items() if k != key}
        return candidate(name="x", effect_low="0.6", effect_high="1.0",
                         available_years="10", **fields)

    def test_omitting_requires_short_is_an_error(self):
        with self.assertRaises(TypeError):
            self._without("requires_short")

    def test_omitting_the_monitor_is_an_error(self):
        with self.assertRaises(TypeError):
            self._without("monitor")

    def test_requires_short_must_be_a_real_boolean(self):
        for value in ("yes", 1, None):
            with self.assertRaises(ValueError):
                _with(requires_short=value)

    def test_the_monitor_must_come_from_monitor_status(self):
        for value in ({}, {"status": "FINE"}, "unexamined"):
            with self.assertRaises(ValueError):
                _with(monitor=value)


class MonitorStatusTests(unittest.TestCase):
    def test_a_met_or_refuted_link_must_say_how_it_is_known(self):
        for status in (MONITOR_LINK_MET, MONITOR_LINK_REFUTED):
            with self.assertRaises(ValueError):
                monitor_status(variable="a variable", status=status,
                               evidence="two of five folds against a threshold")

    def test_certainty_means_nothing_for_the_other_statuses(self):
        with self.assertRaises(ValueError):
            monitor_status(variable="a variable", status=MONITOR_UNEXAMINED,
                           certainty=CERTAINTY_MEASURED,
                           evidence="nobody has measured whether this degrades the return")

    def test_an_inferred_link_must_name_the_sibling_it_came_from(self):
        with self.assertRaises(ValueError):
            monitor_status(variable="a variable", status=MONITOR_LINK_REFUTED,
                           certainty=CERTAINTY_INFERRED,
                           evidence="two of five folds against a threshold")

    def test_an_unknown_status_is_refused(self):
        with self.assertRaises(ValueError):
            monitor_status(variable="a variable", status="PROBABLY_FINE",
                           evidence="a sentence that is long enough to count")


class BlockerTests(unittest.TestCase):
    """Blockers are kept apart from the effect verdict, so a candidate can be
    FEASIBLE on size and blocked on execution at the same time."""

    def test_a_short_is_a_hard_blocker_while_the_executor_cannot_open_one(self):
        blockers = candidate_blockers(_with(requires_short=True))
        self.assertEqual([b["kind"] for b in blockers["hard_blockers"]],
                         [BLOCKER_REQUIRES_SHORT])
        self.assertIn("cannot open one", blockers["hard_blockers"][0]["detail"])

    def test_a_long_only_candidate_has_no_execution_blocker(self):
        self.assertEqual(candidate_blockers(_with())["hard_blockers"], [])

    def test_a_link_refuted_on_this_instrument_eliminates_it(self):
        blockers = candidate_blockers(_with(monitor=REFUTED_HERE))
        self.assertEqual([b["kind"] for b in blockers["hard_blockers"]],
                         [BLOCKER_MONITOR_REFUTED])
        self.assertEqual(blockers["probable_blockers"], [])

    def test_a_link_refuted_on_a_sibling_only_weighs_against_it(self):
        # Never promoted to hard by repetition of the claim, only by measurement.
        blockers = candidate_blockers(_with(monitor=REFUTED_ON_SIBLING))
        self.assertEqual(blockers["hard_blockers"], [])
        probable = blockers["probable_blockers"]
        self.assertEqual([b["kind"] for b in probable], [BLOCKER_MONITOR_REFUTED])
        self.assertIn("NOT measured on this instrument", probable[0]["detail"])
        self.assertIn("SVXY", probable[0]["detail"])

    def test_no_possible_monitor_is_structural_and_hard(self):
        none = monitor_status(variable="nothing independent of the return",
                              status=MONITOR_NONE_POSSIBLE,
                              evidence="the only observable is the premium not paying")
        self.assertEqual(
            [b["kind"] for b in candidate_blockers(_with(monitor=none))["hard_blockers"]],
            [BLOCKER_NO_MONITOR])

    def test_identity_only_and_unexamined_and_met_block_nothing(self):
        met = monitor_status(variable="funding rate", status=MONITOR_LINK_MET,
                             certainty=CERTAINTY_MEASURED, evidence="the exit rule validated at 8 of 8 folds on a holdout")
        identity = monitor_status(variable="a spread", status=MONITOR_IDENTITY_ONLY,
                                  evidence="the trigger never occurred in the window")
        for monitor in (met, identity, UNEXAMINED):
            blockers = candidate_blockers(_with(monitor=monitor))
            self.assertEqual(blockers["hard_blockers"] + blockers["probable_blockers"], [])

    def test_feasible_on_size_and_blocked_on_execution_are_reported_together(self):
        verdict = candidate_verdict(_with(requires_short=True, monitor=REFUTED_ON_SIBLING))
        self.assertEqual(verdict["verdict"], VERDICT_FEASIBLE)
        self.assertEqual(len(verdict["hard_blockers"]), 1)
        self.assertEqual(len(verdict["probable_blockers"]), 1)


class AdmissibilityTests(unittest.TestCase):
    def _scored(self, **kwargs):
        entry = _with(**kwargs)
        return {**entry, **candidate_verdict(entry)}

    def test_nothing_known_forbids_a_clean_open_candidate(self):
        self.assertTrue(admissible_in_principle(self._scored()))

    def test_a_hard_blocker_removes_it(self):
        self.assertFalse(admissible_in_principle(self._scored(requires_short=True)))

    def test_a_probable_blocker_weighs_but_does_not_remove(self):
        self.assertTrue(admissible_in_principle(self._scored(monitor=REFUTED_ON_SIBLING)))

    def test_a_measured_dead_candidate_is_not_a_search(self):
        self.assertFalse(admissible_in_principle(self._scored(status=STATUS_MEASURED_DEAD)))

    def test_a_refused_effect_is_not_admissible_whatever_else_is_clear(self):
        self.assertFalse(admissible_in_principle(self._scored(low="0.05", high="0.20")))


class UnlockTests(unittest.TestCase):
    """The decision a Director faces is not what is blocked but which single
    change buys the most, and that is arithmetic over the register."""

    def _register(self):
        return {"candidates": [
            _with(name="only short", requires_short=True),
            _with(name="short and refuted", requires_short=True, monitor=REFUTED_HERE),
            _with(name="short but dead", requires_short=True, status=STATUS_MEASURED_DEAD),
            _with(name="short but refused", requires_short=True, low="0.05", high="0.20"),
            _with(name="short, sibling refuted", requires_short=True,
                  monitor=REFUTED_ON_SIBLING)]}

    def test_removing_the_short_unlocks_only_those_it_was_the_sole_blocker_of(self):
        unlocked = what_would_unlock(self._register())[BLOCKER_REQUIRES_SHORT]
        self.assertEqual(sorted(item["name"] for item in unlocked),
                         ["only short", "short, sibling refuted"])

    def test_one_still_probably_blocked_is_flagged_not_hidden(self):
        unlocked = what_would_unlock(self._register())[BLOCKER_REQUIRES_SHORT]
        flags = {item["name"]: item["still_probably_blocked"] for item in unlocked}
        self.assertTrue(flags["short, sibling refuted"])
        self.assertFalse(flags["only short"])

    def test_a_candidate_with_two_hard_blockers_is_unlocked_by_neither_alone(self):
        for entries in what_would_unlock(self._register()).values():
            self.assertNotIn("short and refuted", [e["name"] for e in entries])


class RankingWithBlockersTests(unittest.TestCase):
    def test_a_refused_candidate_never_outranks_a_merely_blocked_one(self):
        # The first ordering ranked a BELOW_BAR candidate FIRST because it had no
        # blockers: a refusal sorted as a recommendation.
        register = {"candidates": [
            _with(name="refused but clear", low="0.05", high="0.20"),
            _with(name="open but short", requires_short=True, low="0.30", high="0.80")]}
        self.assertEqual(rank_candidates(register)[0]["name"], "open but short")

    def test_a_clean_candidate_outranks_a_blocked_one_of_equal_effect(self):
        register = {"candidates": [_with(name="blocked", requires_short=True),
                                   _with(name="clean")]}
        self.assertEqual(rank_candidates(register)[0]["name"], "clean")

    def test_measured_dead_candidates_rank_last(self):
        register = {"candidates": [
            _with(name="dead", status=STATUS_MEASURED_DEAD, low="0.9", high="1.5"),
            _with(name="alive", low="0.3", high="0.6")]}
        self.assertEqual(rank_candidates(register)[-1]["name"], "dead")


class RecordSchemaTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "registers.json"
        self.ok = dict(justification="the set this project is choosing from, declared "
                                     "before any one of them is picked",
                       declared_by="test")

    def _two(self):
        return [_with(name="a"), _with(name="b", low="0.2", high="0.4")]

    def test_a_new_register_is_record_schema_2(self):
        record = constitute_candidate_register(
            self.path, candidates=self._two(), declared_at="2026-10-03T12:00:00.123456Z",
            **self.ok)
        self.assertEqual(record["schema_version"], "2")

    def test_a_candidate_missing_either_field_is_refused_at_sealing(self):
        bare = {key: value for key, value in _with(name="a").items()
                if key not in ("requires_short", "monitor")}
        with self.assertRaises(ValueError) as caught:
            constitute_candidate_register(
                self.path, candidates=[bare, _with(name="b")],
                declared_at="2026-10-03T12:00:00.123456Z", **self.ok)
        self.assertIn("requires_short", str(caught.exception))

    def test_re_running_later_does_not_append_a_duplicate(self):
        # declared_at is inside the sealed content, so comparing identities made
        # every run append a fresh duplicate differing only by seconds -- two
        # appeared in sixteen.
        first = constitute_candidate_register(
            self.path, candidates=self._two(), declared_at="2026-10-03T12:00:00.123456Z",
            **self.ok)
        second = constitute_candidate_register(
            self.path, candidates=self._two(), declared_at="2026-10-03T12:00:16.654321Z",
            **self.ok)
        self.assertEqual(first["register_id"], second["register_id"])
        self.assertEqual(len(json.loads(self.path.read_bytes())["registers"]), 1)

    def test_a_genuinely_different_declaration_is_appended_not_overwritten(self):
        constitute_candidate_register(
            self.path, candidates=self._two(), declared_at="2026-10-03T12:00:00.123456Z",
            **self.ok)
        constitute_candidate_register(
            self.path, candidates=[_with(name="a"), _with(name="c")],
            declared_at="2026-10-03T13:00:00.123456Z", **self.ok)
        self.assertEqual(len(json.loads(self.path.read_bytes())["registers"]), 2)


if __name__ == "__main__":
    unittest.main()
