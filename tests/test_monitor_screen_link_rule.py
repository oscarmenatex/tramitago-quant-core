"""The sealed rule for what a screen link may do for R3: an additional requirement, never a substitute."""

import json
import unittest
from pathlib import Path

from tramitago_quant_core.shared.util import digest, encoded

REPO = Path(__file__).resolve().parents[1]
RECORD = REPO / "artifacts" / "governance" / "monitor-screen-link-rule.json"
SPACE = REPO / "scripts" / "research" / "monitor_screen" / "space.json"
SEAL_SCRIPT = REPO / "scripts" / "governance" / "seal_monitor_screen_link_rule.py"


def _record():
    if not RECORD.exists():
        raise unittest.SkipTest("the sealed governance record is not in this checkout")
    return json.loads(RECORD.read_bytes())["rules"][0]


class SealedRuleTests(unittest.TestCase):
    def test_the_record_reproduces_its_own_identity(self):
        record = _record()
        content = {k: v for k, v in record.items() if k != "rule_id"}
        self.assertEqual(record["rule_id"], "MONITOR_SCREEN_LINK_RULE|" + digest(encoded(content)))

    def test_it_is_option_c_and_says_it_is_harder_not_easier(self):
        record = _record()
        self.assertEqual(record["decision"], "OPTION C")
        self.assertTrue(record["integrity_check"]["harder_or_easier"].startswith("HARDER"))
        self.assertTrue(record["integrity_check"]["does_it_rescue_what_was_observed"].startswith("NO"))

    def test_the_screen_link_is_additional_and_never_opens_the_identity_form(self):
        rule = _record()["rule"]
        self.assertIn("AND the screen link", rule["requirement"])
        self.assertIn("never replaces M1", rule["never"])
        self.assertIn("M2", rule["never"])

    def test_the_conditions_that_protect_the_scan_are_declared_before_it_runs(self):
        text = " ".join(_record()["rule"]["conditions_that_protect_the_screen"])
        for needle in ("once", "Bonferroni", "exactly", "annually"):
            self.assertIn(needle, text)

    def test_it_admits_that_enforcement_is_not_in_the_code_yet(self):
        self.assertTrue(_record()["rule"]["enforcement"].startswith("NOT YET IN CODE"))

    def test_the_measured_effect_is_recorded_not_asserted(self):
        measured = _record()["integrity_check"]["measured"]
        self.assertEqual(measured["admissions_citing_a_screen_link"], len(measured["ids"]))
        self.assertGreaterEqual(measured["admissions_sealed"], 11)

    def test_the_space_records_the_same_decision(self):
        space = json.loads(SPACE.read_text(encoding="utf-8"))
        first = space["decisions"][0]
        self.assertEqual(first["n"], 1)
        self.assertIn("OPTION C", first["decision"])
        self.assertEqual(space["open_decisions"], [])


class NothingAlreadySealedIsTouchedTests(unittest.TestCase):
    def test_the_seal_script_never_writes_to_an_admission_or_a_schema(self):
        source = SEAL_SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("constitute_admission", source)
        self.assertNotIn("ADMISSION_SCHEMA", source)
        self.assertEqual(source.count("_atomic_write("), 1)       # its one write, the record


if __name__ == "__main__":
    unittest.main()
