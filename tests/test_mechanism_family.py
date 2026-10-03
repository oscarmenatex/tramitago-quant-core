"""A mechanism family: one monitor fixed once, judged jointly across its members.

WHAT THESE PIN. The joint rule can only get harder as members are added; a sealed
definition cannot be nudged after a result is seen; the spec derived for XYLD is the
spec XYLD was actually measured with; and a member is cleared to rely on its monitor
only when its family generalizes.
"""

import copy
import itertools
import json
import tempfile
import unittest
from pathlib import Path

from tramitago_quant_core.research.mechanism_family import (
    validate_family, member_spec, definition_digest, pre_declaration_answers, judge_family,
    seal_family, sealed_family, seal_verdict, family_clearance,
    GENERALIZES, DOES_NOT_GENERALIZE, INSUFFICIENT_EVIDENCE, PENDING,
    LINK_MET, LINK_FAILED, LINK_UNREACHABLE)
from tramitago_quant_core.research.pre_declaration import pre_declaration
from tramitago_quant_core.research.premium_engine import validate_spec

REPO = Path(__file__).resolve().parents[1]
FAMILY_FILE = REPO / "scripts" / "research" / "families" / "covered-call-equity.json"
XYLD_SPEC = REPO / "scripts" / "research" / "specs" / "xyld.json"
PLACEHOLDER = "HYPOTHESIS|validation-only-nothing-is-sealed"


def _family():
    return json.loads(FAMILY_FILE.read_text(encoding="utf-8"))


def _result(state, met=5, usable=6):
    ratio = met / usable
    return {"link_outcome": state, "link_met": met, "link_usable": usable,
            "link_ratio": ratio}


class DerivationTests(unittest.TestCase):
    def test_the_spec_derived_for_xyld_is_the_one_xyld_was_measured_with(self):
        # The template is the same function the hand-written spec was: if this fails,
        # the family is not testing the monitor XYLD failed with.
        family = _family()
        derived = member_spec(family, family["members"][0])
        self.assertEqual(derived, json.loads(XYLD_SPEC.read_text(encoding="utf-8")))

    def test_the_monitor_differs_between_members_only_in_its_series(self):
        family = _family()
        first, second = (member_spec(family, m)["monitor"] for m in family["members"])
        for key in ("kind", "window", "floor", "confirmation_periods", "re_entry_threshold"):
            self.assertEqual(first[key], second[key], key)
        self.assertNotEqual(first["implied"], second["implied"])
        self.assertNotEqual(first["underlying"], second["underlying"])

    def test_every_member_derives_a_valid_engine_spec(self):
        family = _family()
        for member in family["members"]:
            spec = member_spec(family, member)
            spec["hypothesis_id"] = spec["hypothesis_id"] or PLACEHOLDER.replace(
                "validation-only-nothing-is-sealed", "00000000-0000-0000-0000-000000000000")
            self.assertTrue(validate_spec(spec), member["symbol"])

    def test_every_member_answers_the_seven_questions_under_the_contract(self):
        family = _family()
        for member in family["members"]:
            record = pre_declaration(hypothesis_id=PLACEHOLDER,
                                     **pre_declaration_answers(family, member))
            self.assertIn(record["required_effect"]["position_against_bar"],
                          ("CLEARS", "STRADDLES"), member["symbol"])


class ValidationTests(unittest.TestCase):
    def test_one_member_cannot_show_that_a_monitor_generalizes(self):
        family = _family()
        family["members"] = family["members"][:1]
        with self.assertRaises(ValueError) as caught:
            validate_family(family)
        self.assertIn("at least two members", str(caught.exception))

    def test_a_rule_that_accepts_fewer_than_all_members_is_refused(self):
        family = _family()
        family["joint_rule"]["requires"] = "most members"
        with self.assertRaises(ValueError) as caught:
            validate_family(family)
        self.assertIn("every member", str(caught.exception))

    def test_an_unknown_member_key_is_refused_as_a_typo(self):
        family = _family()
        family["members"][1]["adverse_treshold"] = "-0.02"
        with self.assertRaises(ValueError):
            validate_family(family)

    def test_a_member_missing_its_own_numbers_is_refused(self):
        family = _family()
        del family["members"][1]["pre_declaration"]["plausible_low"]
        with self.assertRaises(ValueError):
            validate_family(family)


class JointRuleTests(unittest.TestCase):
    MEMBERS = [{"symbol": "A"}, {"symbol": "B"}]

    def _verdict(self, a, b):
        outcomes = {"A": a, "B": b}
        return judge_family(self.MEMBERS, outcomes)["verdict"]

    def test_every_member_met_generalizes(self):
        self.assertEqual(self._verdict(_result(LINK_MET), _result(LINK_MET)), GENERALIZES)

    def test_one_failure_is_the_verdict_whatever_the_other_shows(self):
        self.assertEqual(self._verdict(_result(LINK_MET), _result(LINK_FAILED)),
                         DOES_NOT_GENERALIZE)
        self.assertEqual(self._verdict(_result(LINK_FAILED), _result(LINK_UNREACHABLE)),
                         DOES_NOT_GENERALIZE)

    def test_a_member_that_could_not_be_measured_is_not_a_pass(self):
        self.assertEqual(self._verdict(_result(LINK_MET), _result(LINK_UNREACHABLE)),
                         INSUFFICIENT_EVIDENCE)

    def test_an_unmeasured_member_leaves_the_family_pending(self):
        self.assertEqual(self._verdict(_result(LINK_MET), None), PENDING)

    def test_adding_a_member_can_never_turn_a_non_pass_into_a_pass(self):
        # THE PROPERTY THE RULE EXISTS FOR, checked over every combination.
        states = (LINK_MET, LINK_FAILED, LINK_UNREACHABLE, None)
        for existing in itertools.product(states, repeat=2):
            members = [{"symbol": "A"}, {"symbol": "B"}]
            outcomes = {s["symbol"]: None if state is None else _result(state)
                        for s, state in zip(members, existing)}
            before = judge_family(members, outcomes)["verdict"]
            for added in states:
                more = members + [{"symbol": "C"}]
                wider = {**outcomes, "C": None if added is None else _result(added)}
                after = judge_family(more, wider)["verdict"]
                if before != GENERALIZES:
                    self.assertNotEqual(after, GENERALIZES, (existing, added))

    def test_the_control_is_carried_into_the_verdict_for_the_record(self):
        result = _result(LINK_MET)
        result["control"] = {"met": 3, "usable": 6, "ratio": 0.5}
        row = judge_family(self.MEMBERS, {"A": result, "B": None})["members"][0]
        self.assertEqual(row["control"]["ratio"], 0.5)


class SealingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.families, self.verdicts = self.root / "f.json", self.root / "v.json"
        self.kwargs = dict(sealed_at="2026-10-03T20:00:00.000000Z", code_revision="a" * 40,
                           authorised_by="test", ground={"name": "test"})

    def test_the_same_definition_seals_once(self):
        family = _family()
        _, first = seal_family(self.families, family, **self.kwargs)
        _, second = seal_family(self.families, family, **self.kwargs)
        self.assertTrue(first)
        self.assertFalse(second)

    def test_a_sealed_family_is_found_and_an_unsealed_one_is_refused(self):
        family = _family()
        seal_family(self.families, family, **self.kwargs)
        self.assertTrue(sealed_family(self.families, family))
        moved = copy.deepcopy(family)
        moved["mechanism"]["monitor"]["window"] = 63           # nudging the monitor
        with self.assertRaises(ValueError) as caught:
            sealed_family(self.families, moved)
        self.assertIn("not sealed", str(caught.exception))

    def test_the_rule_and_the_members_are_inside_the_sealed_definition(self):
        family = _family()
        seal_family(self.families, family, **self.kwargs)
        for change in (lambda f: f["joint_rule"].update(statement="softer"),
                       lambda f: f["members"][1].update(adverse_threshold="-0.001"),
                       lambda f: f["mechanism"]["monitor"].update(floor="1")):
            moved = copy.deepcopy(family)
            change(moved)
            with self.assertRaises(ValueError):
                sealed_family(self.families, moved)

    def test_filling_in_a_hypothesis_identity_does_not_change_the_definition(self):
        family = _family()
        digest_before = definition_digest(family)
        family["members"][1]["hypothesis_id"] = "HYPOTHESIS|" + "0" * 8 + "-0000-0000-0000-" + "0" * 12
        self.assertEqual(definition_digest(family), digest_before)

    def test_a_verdict_seals_once_on_content_and_not_on_the_clock(self):
        family = _family()
        record, _ = seal_family(self.families, family, **self.kwargs)
        verdict = judge_family(family["members"], {})
        _, first = seal_verdict(self.verdicts, record, verdict,
                                decided_at="2026-10-03T21:00:00.000000Z", code_revision="b" * 40)
        _, again = seal_verdict(self.verdicts, record, verdict,
                                decided_at="2026-10-04T09:00:00.000000Z", code_revision="c" * 40)
        self.assertTrue(first)
        self.assertFalse(again)


class ClearanceTests(unittest.TestCase):
    """A member relies on its monitor only when its family generalizes."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.families, self.verdicts = self.root / "f.json", self.root / "v.json"
        family = _family()
        family["members"][1]["hypothesis_id"] = "HYPOTHESIS|11111111-1111-1111-1111-111111111111"
        self.family = family
        self.record, _ = seal_family(
            self.families, family, sealed_at="2026-10-03T20:00:00.000000Z",
            code_revision="a" * 40, authorised_by="test", ground={})
        self.member = family["members"][1]["hypothesis_id"]

    def _seal(self, outcomes, at):
        seal_verdict(self.verdicts, self.record, judge_family(self.family["members"], outcomes),
                     decided_at=at, code_revision="b" * 40)

    def test_a_hypothesis_outside_every_family_is_unaffected(self):
        cleared, _ = family_clearance("HYPOTHESIS|22222222-2222-2222-2222-222222222222",
                                      self.families, self.verdicts)
        self.assertTrue(cleared)

    def test_a_member_with_no_verdict_is_not_cleared(self):
        cleared, why = family_clearance(self.member, self.families, self.verdicts)
        self.assertFalse(cleared)
        self.assertIn("no verdict", why)

    def test_a_member_of_a_family_that_does_not_generalize_is_not_cleared(self):
        self._seal({"XYLD": _result(LINK_FAILED, 4, 6), "QYLD": _result(LINK_MET)},
                   "2026-10-03T21:00:00.000000Z")
        cleared, why = family_clearance(self.member, self.families, self.verdicts)
        self.assertFalse(cleared)
        self.assertIn(DOES_NOT_GENERALIZE, why)

    def test_a_member_is_cleared_only_when_every_member_met_the_link(self):
        self._seal({"XYLD": _result(LINK_MET), "QYLD": _result(LINK_MET)},
                   "2026-10-03T21:00:00.000000Z")
        cleared, _ = family_clearance(self.member, self.families, self.verdicts)
        self.assertTrue(cleared)

    def test_the_latest_verdict_governs(self):
        self._seal({"XYLD": _result(LINK_MET), "QYLD": _result(LINK_MET)},
                   "2026-10-03T21:00:00.000000Z")
        self._seal({"XYLD": _result(LINK_MET), "QYLD": _result(LINK_FAILED, 2, 6)},
                   "2026-10-04T21:00:00.000000Z")
        cleared, _ = family_clearance(self.member, self.families, self.verdicts)
        self.assertFalse(cleared)


if __name__ == "__main__":
    unittest.main()
