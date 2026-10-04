"""The first monitor screen scan, as sealed on 2026-10-03: what it recorded, and that the holdout is unopened."""

import json
import unittest
from pathlib import Path

from tramitago_quant_core.research.monitor_screen import canonical_space, holdout_was_opened
from tramitago_quant_core.shared.util import digest, encoded

REPO = Path(__file__).resolve().parents[1]
ARTIFACTS = REPO / "artifacts" / "research"
SPACES = ARTIFACTS / "monitor-screen-spaces.json"
SCANS = ARTIFACTS / "monitor-screen-scans.json"
HOLDOUT = ARTIFACTS / "monitor-screen-holdout.json"
SPACE_FILE = REPO / "scripts" / "research" / "monitor_screen" / "space.json"


def _scan():
    if not SCANS.exists():
        raise unittest.SkipTest("the sealed scan is not in this checkout")
    return json.loads(SCANS.read_bytes())["scans"][0]


class SealedScanTests(unittest.TestCase):
    def test_the_scan_reproduces_its_own_identity(self):
        record = _scan()
        content = {k: record[k] for k in ("schema_version", "kind", "space_id", "observations",
                                          "summary", "inputs_digest")}
        self.assertEqual(record["scan_id"], "MONITOR_SCREEN_SCAN|" + digest(encoded(content)))

    def test_the_space_it_scanned_is_the_space_in_the_repository_today(self):
        # Nothing about the candidates, windows or rule moved after the scan was seen.
        space = json.loads(SPACE_FILE.read_text(encoding="utf-8"))
        sealed = json.loads(SPACES.read_bytes())["spaces"][0]
        self.assertEqual(sealed["space"], canonical_space(space))
        self.assertEqual(_scan()["space_id"], sealed["space_id"])

    def test_what_it_found(self):
        record = _scan()
        by_id = {o["candidate"]: o for o in record["observations"]}
        self.assertEqual(len(by_id), 14)
        self.assertEqual(record["summary"]["by_outcome"],
                         {"REACHABLE_AND_FAILED": 10, "REACHABLE_AND_MET": 1, "UNREACHABLE": 3})
        self.assertEqual(record["summary"]["passes"], ["C14"])

    def test_the_curve_inversions_were_unreachable_because_the_state_was_rare(self):
        by_id = {o["candidate"]: o for o in _scan()["observations"]}
        for candidate in ("C01", "C02", "C10"):
            self.assertEqual(by_id[candidate]["outcome"], "UNREACHABLE", candidate)
            self.assertLess(by_id[candidate]["usable"], 5, candidate)

    def test_every_observation_agrees_with_its_own_folds(self):
        for o in _scan()["observations"]:
            self.assertEqual(o["met"], sum(1 for f in o["folds"] if f["met"]), o["candidate"])
            self.assertEqual(o["usable"], sum(1 for f in o["folds"] if f["usable"]), o["candidate"])

    def test_the_only_pass_was_the_narrowest_one_the_rule_allows(self):
        c14 = {o["candidate"]: o for o in _scan()["observations"]}["C14"]
        self.assertEqual((c14["met"], c14["usable"]), (5, 7))       # 0.714 against 0.70

    def test_the_holdout_has_not_been_opened(self):
        sealed = json.loads(SPACES.read_bytes())["spaces"][0]
        self.assertFalse(HOLDOUT.exists() and holdout_was_opened(HOLDOUT, sealed["space_id"]))


if __name__ == "__main__":
    unittest.main()
