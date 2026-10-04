"""The census as sealed on 2026-10-03, and the reading rule that was declared before any count was seen."""

import json
import unittest
from pathlib import Path

from tramitago_quant_core.research.instrument_census import (
    reading, definition, definition_digest, READING_FAMILY_SIZED, READING_THIN, READING_SINGLE)
from tramitago_quant_core.shared.util import digest, encoded

REGISTRY = Path(__file__).resolve().parents[1] / "artifacts" / "research" / "instrument-census.json"


def _census():
    if not REGISTRY.exists():
        raise unittest.SkipTest("the sealed census is not in this checkout")
    return json.loads(REGISTRY.read_bytes())["censuses"][-1]


class ReadingRuleTests(unittest.TestCase):
    def test_the_declared_edges(self):
        self.assertEqual(reading(5), READING_FAMILY_SIZED)
        self.assertEqual(reading(4), READING_THIN)
        self.assertEqual(reading(2), READING_THIN)
        self.assertEqual(reading(1), READING_SINGLE)
        self.assertEqual(reading(0), READING_SINGLE)

    def test_a_single_instrument_cannot_be_a_family(self):
        # The joint rule needs at least two members, so one instrument is never a family.
        self.assertEqual(reading(1), READING_SINGLE)


class SealedCensusTests(unittest.TestCase):
    def test_the_record_reproduces_its_own_identity(self):
        record = _census()
        content = {k: v for k, v in record.items() if k not in ("census_id", "code_revision")}
        self.assertEqual(record["census_id"], "INSTRUMENT_CENSUS|" + digest(encoded(content)))

    def test_it_was_taken_under_the_definition_in_the_code_today(self):
        # A new definition is a new census. If this fails the definition was edited after sealing.
        record = _census()
        self.assertEqual(record["definition_digest"], digest(encoded(record["definition"])))
        self.assertEqual(record["definition_digest"], definition_digest())
        self.assertEqual(record["definition"]["definition_version"], definition()["definition_version"])

    def test_every_probed_instrument_has_a_first_bar_and_none_failed(self):
        record = _census()
        self.assertEqual(record["probed"], 1501)
        self.assertEqual(len(record["rows"]), 1501)
        self.assertTrue(all(r["first_bar"] for r in record["rows"]))
        self.assertFalse(any(r.get("error") for r in record["rows"]))

    def test_no_row_carries_a_price_or_a_return(self):
        for row in _census()["rows"]:
            for key in row:
                for forbidden in ("close", "open", "high", "low", "price", "return"):
                    self.assertNotIn(forbidden, key)

    def test_what_the_reading_rule_gives_on_the_sealed_counts(self):
        summary = _census()["summary"]
        family_sized = sorted(l for l, r in summary.items()
                              if l in {"COMMODITY_BROAD", "CURRENCY", "HIGH_YIELD_CREDIT", "INFLATION_LINKED",
                                       "MORTGAGE", "MUNICIPAL", "PRECIOUS_METALS", "PREFERRED", "TREASURY_DURATION"}
                              and reading(r["plain_tier_a_liquid"]) == READING_FAMILY_SIZED)
        self.assertEqual(len(family_sized), 9)
        self.assertEqual(reading(summary["EMERGING_DEBT"]["plain_tier_a_liquid"]), READING_THIN)
        self.assertEqual(reading(summary["ENERGY_FUTURES"]["plain_tier_a_liquid"]), READING_THIN)
        self.assertEqual(reading(summary["CRYPTO"]["plain_tier_a_liquid"]), READING_SINGLE)
        self.assertEqual(reading(summary["PUT_WRITE"]["plain_tier_a_liquid"]), READING_SINGLE)

    def test_the_instruments_already_measured_are_in_the_census(self):
        by = {r["symbol"]: r for r in _census()["rows"]}
        for symbol in ("XYLD", "QYLD", "VIXM", "LQD", "TLT", "IEF", "UUP", "FXY"):
            self.assertIn(symbol, by)
            self.assertEqual(by[symbol]["tier"], "A")


if __name__ == "__main__":
    unittest.main()
