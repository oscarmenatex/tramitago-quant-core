"""The candidate register expanded with the census classes, and the stale entries corrected."""

import hashlib
import importlib.util
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from tramitago_quant_core.research.candidate_register import (
    constitute_candidate_register, candidate_verdict, STATUS_MEASURED_DEAD, STATUS_UNTRIED,
    MONITOR_LINK_REFUTED, CERTAINTY_MEASURED, CERTAINTY_INFERRED, VERDICT_BELOW_BAR, VERDICT_UNCERTAIN,
    VERDICT_FEASIBLE)

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "research" / "expand_candidate_register_census.py"
REGISTER = REPO / "artifacts" / "research" / "candidate-registers.json"
CENSUS = REPO / "artifacts" / "research" / "instrument-census.json"


def _load():
    spec = importlib.util.spec_from_file_location("expand_census", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


module = _load()


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (REGISTER.exists() and CENSUS.exists()):
            raise unittest.SkipTest("the sealed register and census are not in this checkout")
        cls.rows, _ = module._census_rows()
        cls.previous = module._latest_schema_2()
        cls.built = module.build(cls.previous, cls.rows)
        cls.added = module.additions(cls.rows)

    def by_name(self, fragment):
        return [c for c in self.built if fragment in c["name"]][0]


class CorrectionTests(Fixture):
    def test_xyld_and_qyld_are_measured_dead_with_a_measured_refutation(self):
        for fragment in ("via XYLD", "via QYLD"):
            entry = self.by_name(fragment)
            self.assertEqual(entry["status"], STATUS_MEASURED_DEAD, fragment)
            self.assertEqual(entry["monitor"]["status"], MONITOR_LINK_REFUTED, fragment)
            self.assertEqual(entry["monitor"]["certainty"], CERTAINTY_MEASURED, fragment)

    def test_the_measured_links_are_the_ones_actually_sealed(self):
        self.assertIn("4 of 6", self.by_name("via XYLD")["monitor"]["evidence"])
        self.assertIn("3 of 7", self.by_name("via QYLD")["monitor"]["evidence"])

    def test_divo_inherits_the_refutation_as_inferred_and_names_its_siblings(self):
        monitor = self.by_name("via DIVO")["monitor"]
        self.assertEqual((monitor["status"], monitor["certainty"]), (MONITOR_LINK_REFUTED, CERTAINTY_INFERRED))
        self.assertIn("XYLD and QYLD", monitor["measured_on"])

    def test_divo_is_weighed_against_not_eliminated(self):
        # An inferred refutation is a probable blocker, never a hard one: it is a sibling's result.
        verdict = candidate_verdict(self.by_name("via DIVO"))
        self.assertEqual(verdict["hard_blockers"], [])
        self.assertTrue(verdict["probable_blockers"])

    def test_every_other_existing_entry_is_untouched(self):
        changed = ("via XYLD", "via QYLD", "via DIVO")
        for old in self.previous:
            if any(fragment in old["name"] for fragment in changed):
                continue
            self.assertIn(old, self.built, old["name"])

    def test_the_corrections_do_not_change_what_was_declared_about_effect_or_payer(self):
        for fragment in ("via XYLD", "via QYLD", "via DIVO"):
            old = [c for c in self.previous if fragment in c["name"]][0]
            new = self.by_name(fragment)
            self.assertEqual((old["effect"], old["payer"], old["available_years"]),
                             (new["effect"], new["payer"], new["available_years"]), fragment)


class AdditionTests(Fixture):
    def test_seven_entries_are_added_and_no_name_repeats(self):
        self.assertEqual(len(self.added), 7)
        names = [c["name"] for c in self.built]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(self.built), len(self.previous) + 7)

    def test_every_range_says_it_is_recalled_and_weak(self):
        for entry in self.added:
            self.assertIn("RECALLED", entry["effect"]["source"], entry["name"])
            self.assertIn("never evidence", entry["effect"]["source"], entry["name"])

    def test_the_facts_about_each_instrument_come_from_the_sealed_census(self):
        for entry in self.added:
            self.assertIn("instrument census of 2026-10-03", entry["data_source"], entry["name"])
            self.assertIn("first monthly bar", entry["data_source"], entry["name"])

    def test_the_years_are_those_of_the_shorter_leg_of_a_pair(self):
        hyg = self.rows["HYG"]["first_bar"]
        ief = self.rows["IEF"]["first_bar"]
        entry = [c for c in self.added if "HYG against IEF" in c["name"]][0]
        expected = module._years(max(hyg, ief))
        self.assertEqual(float(entry["available_years"]), expected)

    def test_pairs_require_a_short_and_a_long_only_wrapper_does_not(self):
        for entry in self.added:
            if "against IEF" in entry["name"]:
                self.assertTrue(entry["requires_short"], entry["name"])
        self.assertFalse([c for c in self.added if "PFF held long" in c["name"]][0]["requires_short"])

    def test_the_credit_entry_carries_the_screen_result_as_an_inferred_refutation(self):
        monitor = [c for c in self.added if "HYG against IEF" in c["name"]][0]["monitor"]
        self.assertEqual((monitor["status"], monitor["certainty"]), (MONITOR_LINK_REFUTED, CERTAINTY_INFERRED))
        self.assertIn("C08", monitor["evidence"])
        self.assertIn("monitor screen", monitor["measured_on"])

    def test_a_missing_monitor_variable_is_written_as_a_gap_in_the_search_and_not_a_finding(self):
        for fragment in ("EMB against", "MUB against", "UNG, BNO"):
            entry = [c for c in self.added if fragment in c["name"]][0]
            self.assertIn("gap in the search", entry["monitor"]["evidence"], fragment)

    def test_what_the_register_concludes_about_the_additions(self):
        verdicts = {c["name"]: candidate_verdict(c)["verdict"] for c in self.added}
        below = sorted(n for n, v in verdicts.items() if v == VERDICT_BELOW_BAR)
        uncertain = sorted(n for n, v in verdicts.items() if v == VERDICT_UNCERTAIN)
        self.assertEqual(len(below), 5)
        self.assertEqual(len(uncertain), 2)
        self.assertNotIn(VERDICT_FEASIBLE, verdicts.values())

    def test_the_classes_left_out_are_not_entered_and_the_reason_is_recorded(self):
        names = " ".join(c["name"] for c in self.built).lower()
        for left_out in ("gold", "silver", "bitcoin", "precious"):
            self.assertNotIn(left_out, names)
        for reason in ("precious metals", "crypto", "real estate", "no payer"):
            self.assertIn(reason, module.JUSTIFICATION.lower().replace("there is no payer", "no payer"))


class SealingTests(Fixture):
    def test_the_expanded_register_seals_once_into_a_scratch_registry(self):
        path = Path(tempfile.mkdtemp()) / "r.json"
        kwargs = dict(justification=module.JUSTIFICATION, candidates=self.built,
                      declared_by="test", declared_at="2026-10-04T10:00:00.123456Z")
        first = constitute_candidate_register(path, **kwargs)
        again = constitute_candidate_register(path, **{**kwargs, "declared_at": "2026-10-05T10:00:00.123456Z"})
        self.assertEqual(first["register_id"], again["register_id"])
        self.assertEqual(len(json.loads(path.read_bytes())["registers"]), 1)

    def test_the_default_run_seals_nothing_in_the_real_registry(self):
        before = hashlib.sha256(REGISTER.read_bytes()).hexdigest()
        with redirect_stdout(StringIO()) as out:
            module.main([])
        self.assertEqual(hashlib.sha256(REGISTER.read_bytes()).hexdigest(), before)
        self.assertIn("DRY RUN", out.getvalue())
        self.assertIn("nothing sealed", out.getvalue())


if __name__ == "__main__":
    unittest.main()
