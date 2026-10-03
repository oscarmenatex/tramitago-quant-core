"""When the pre-declaration contract came into force: a rule cannot bind what predates it.

The instant was first set to midnight UTC of the day the contract was written. The
contract was committed at 05:32:10 UTC, so the rule reached back five and a half hours
over six versions of two Hypotheses -- SPY (01:15 to 01:41 UTC) and the credit premium
(03:33 to 04:16 UTC) -- declared before the question existed. Found by migrating the
credit premium, which the spec engine refused to reload.
"""

import json
import tempfile
import unittest
from pathlib import Path

from tramitago_quant_core.research.pre_declaration import (
    require_pre_declaration, PRE_DECLARATION_IN_FORCE_SINCE,
)


def _hypothesis(created):
    return {"hypothesis_id": "HYPOTHESIS|probe", "creation_timestamp": created}


class InForceTests(unittest.TestCase):
    def setUp(self):
        self.registry = Path(tempfile.mkdtemp()) / "pre-declarations.json"

    def _requires(self, created):
        try:
            require_pre_declaration(_hypothesis(created), self.registry)
        except ValueError:
            return True
        return False

    def test_the_real_hypotheses_declared_before_the_contract_are_exempt(self):
        # The six versions the old midnight instant wrongly caught.
        for created in ("2026-10-03T01:15:15.943101Z", "2026-10-03T01:19:57.029101Z",
                        "2026-10-03T01:41:31.632703Z", "2026-10-03T03:33:52.851199Z",
                        "2026-10-03T04:07:34.396271Z", "2026-10-03T04:16:23.435857Z"):
            self.assertFalse(self._requires(created), created)

    def test_the_real_hypotheses_declared_after_it_are_in_scope(self):
        # The volatility premium (05:40) and XYLD (15:13) were created after the
        # commit and DID answer their seven questions.
        for created in ("2026-10-03T05:40:47.531747Z", "2026-10-03T15:13:48.480026Z"):
            self.assertTrue(self._requires(created), created)

    def test_the_instant_itself_is_in_scope(self):
        # At the instant the rule exists, so a Hypothesis created at it must answer.
        # Written in the CANONICAL form: with the microsecond at zero it prints with
        # none, and the registry would refuse the other spelling outright.
        self.assertTrue(self._requires("2026-10-03T05:32:10Z"))

    def test_the_microsecond_before_the_instant_is_exempt(self):
        self.assertFalse(self._requires("2026-10-03T05:32:09.999999Z"))

    def test_a_timestamp_inside_the_same_second_is_ordered_correctly(self):
        # THE FRAGILITY THIS PARSING REMOVES. Compared as strings, 05:32:10.500000Z
        # sorts BEFORE 05:32:10Z because the character that separates the fraction
        # ('.') sorts before the one that ends the string ('Z'), so a Hypothesis
        # created half a second AFTER the rule came into force would have read as
        # created before it.
        later = "2026-10-03T05:32:10.500000Z"
        self.assertLess(later, PRE_DECLARATION_IN_FORCE_SINCE)       # the string trap
        self.assertTrue(self._requires(later))                       # the parsed truth

    def test_every_sealed_hypothesis_is_judged_consistently_with_its_pre_declaration(self):
        # The regression this exists for: against the real registry, every version
        # in scope HAS a pre-declaration, and none that predates the contract is
        # demanded one it could never have had.
        root = Path(__file__).resolve().parents[1] / "artifacts" / "research"
        hypotheses, answered = root / "hypotheses.json", root / "pre-declarations.json"
        if not hypotheses.exists():
            self.skipTest("no sealed Hypotheses in this checkout")
        declared = ({item["hypothesis_id"] for item in
                     json.loads(answered.read_bytes())["pre_declarations"]}
                    if answered.exists() else set())
        for item in json.loads(hypotheses.read_bytes())["hypotheses"]:
            if item["creation_timestamp"] >= "2026-10-03T05:32:11Z":
                self.assertIn(item["hypothesis_id"], declared,
                              f"{item['hypothesis_id'][:20]} is in scope with no answers")
            with self.subTest(version=item["version"], id=item["hypothesis_id"][11:19]):
                try:
                    require_pre_declaration(item, answered)
                except ValueError:
                    self.fail("a sealed Hypothesis is demanded answers it does not have")


if __name__ == "__main__":
    unittest.main()
