"""The instrument contract: validated terms per instrument, PAPER only for anything but BTC-USD."""

import json
import tempfile
import unittest
from pathlib import Path

from tramitago_quant_core.execution.instrument_contract import (
    terms_for, load_equity_contracts, contract_symbols, contract_identity, allows_environment,
    exposure_cap, BTC_USD, PAPER, LIVE)

REPO = Path(__file__).resolve().parents[1]
BASE = {"max_capital_usd": "1000", "max_exposure_usd": "250", "risk_budget_usd": "25",
        "can_short": False}
SHORT = {**BASE, "can_short": True, "max_adverse_move": "0.10",
         "max_loss_per_position_usd": "25"}


def _load(instruments):
    path = Path(tempfile.mkdtemp()) / "c.json"
    path.write_text(json.dumps({"instruments": instruments}), encoding="utf-8")
    return load_equity_contracts(path)


class BitcoinTests(unittest.TestCase):
    def test_bitcoin_is_fixed_in_code_and_cannot_be_declared_in_a_file(self):
        with self.assertRaises(ValueError) as caught:
            _load({BTC_USD: BASE})
        self.assertIn("fixed in code", str(caught.exception))

    def test_bitcoin_terms_are_the_original_pilot_literals(self):
        terms = terms_for(BTC_USD)
        self.assertEqual([terms[k] for k in ("max_capital_usd", "max_exposure_usd",
                                             "max_exit_exposure_usd", "risk_budget_usd")],
                         ["200", "50", "50", "5"])
        self.assertEqual(terms["environments"], [PAPER, LIVE])

    def test_returned_terms_cannot_be_used_to_widen_the_constant(self):
        terms = terms_for(BTC_USD)
        terms["environments"].append("ANYTHING")
        self.assertEqual(terms_for(BTC_USD)["environments"], [PAPER, LIVE])


class EquityTermsTests(unittest.TestCase):
    def test_an_equity_is_paper_only_and_cannot_say_otherwise(self):
        terms = _load({"XYLD": BASE})["XYLD"]
        self.assertEqual(terms["environments"], [PAPER])
        self.assertFalse(allows_environment(terms, LIVE))
        self.assertTrue(allows_environment(terms, PAPER))
        with self.assertRaises(ValueError) as caught:
            _load({"XYLD": {**BASE, "environments": ["PAPER", "LIVE"]}})
        self.assertIn("PAPER only", str(caught.exception))

    def test_the_exit_cap_defaults_to_twice_the_entry_cap_and_cannot_be_below_it(self):
        terms = _load({"XYLD": BASE})["XYLD"]
        self.assertEqual(terms["max_exit_exposure_usd"], "500")
        self.assertEqual(exposure_cap(terms, "ENTER"), 250)
        self.assertEqual(exposure_cap(terms, "EXIT"), 500)
        with self.assertRaises(ValueError):
            _load({"XYLD": {**BASE, "max_exit_exposure_usd": "100"}})

    def test_crypto_and_malformed_symbols_are_refused(self):
        for symbol in ("ETH-USD", "BTC/USD", "xyld", "TOOLONGX", ""):
            with self.assertRaises(ValueError, msg=symbol):
                _load({symbol: BASE})

    def test_money_terms_must_nest_budget_within_exposure_within_capital(self):
        for change in ({"max_exposure_usd": "2000"}, {"risk_budget_usd": "300"},
                       {"max_capital_usd": "0"}, {"risk_budget_usd": "0"},
                       {"max_capital_usd": "abc"}):
            with self.assertRaises(ValueError, msg=str(change)):
                _load({"XYLD": {**BASE, **change}})

    def test_a_shortable_instrument_must_declare_how_much_a_short_may_lose(self):
        with self.assertRaises(ValueError) as caught:
            _load({"VIXM": {**BASE, "can_short": True}})
        self.assertIn("without bound", str(caught.exception))
        terms = _load({"VIXM": SHORT})["VIXM"]
        self.assertEqual((terms["max_adverse_move"], terms["max_loss_per_position_usd"]),
                         ("0.10", "25"))

    def test_the_loss_per_position_cannot_exceed_the_risk_budget(self):
        with self.assertRaises(ValueError):
            _load({"VIXM": {**SHORT, "max_loss_per_position_usd": "26"}})
        for bad_move in ("0", "1", "1.5", "-0.1"):
            with self.assertRaises(ValueError, msg=bad_move):
                _load({"VIXM": {**SHORT, "max_adverse_move": bad_move}})

    def test_short_terms_on_an_instrument_that_cannot_short_are_refused(self):
        with self.assertRaises(ValueError):
            _load({"XYLD": {**BASE, "max_adverse_move": "0.1"}})

    def test_can_short_must_be_a_real_boolean(self):
        for bad in ("true", 1, None):
            with self.assertRaises(ValueError, msg=str(bad)):
                _load({"XYLD": {**BASE, "can_short": bad}})

    def test_unknown_fields_are_refused_as_typos(self):
        with self.assertRaises(ValueError):
            _load({"XYLD": {**BASE, "max_exposur_usd": "250"}})


class FileTests(unittest.TestCase):
    def test_an_absent_file_means_no_equity_contracts_not_a_default(self):
        missing = Path(tempfile.mkdtemp()) / "none.json"
        self.assertEqual(load_equity_contracts(missing), {})
        self.assertIsNone(terms_for("XYLD", missing))
        self.assertEqual(contract_symbols(missing), [])

    def test_a_corrupt_file_raises_rather_than_allowing_or_blocking_silently(self):
        path = Path(tempfile.mkdtemp()) / "c.json"
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            terms_for("XYLD", path)
        path.write_text('{"instruments": []}', encoding="utf-8")
        with self.assertRaises(ValueError):
            terms_for("XYLD", path)

    def test_an_instrument_without_a_contract_has_no_terms(self):
        self.assertIsNone(terms_for("AAPL", REPO / "config" / "instrument_contracts.json"))
        self.assertIsNone(terms_for(None))
        self.assertIsNone(terms_for(5))

    def test_the_repository_file_is_valid_and_every_entry_is_paper_only(self):
        path = REPO / "config" / "instrument_contracts.json"
        contracts = load_equity_contracts(path)
        self.assertTrue(contracts)
        for symbol, terms in contracts.items():
            self.assertEqual(terms["environments"], [PAPER], symbol)
            self.assertNotEqual(symbol, BTC_USD)


class IdentityTests(unittest.TestCase):
    def test_any_changed_term_changes_the_identity(self):
        base = _load({"VIXM": SHORT})["VIXM"]
        for change in ({"max_exposure_usd": "240"},
                       {"risk_budget_usd": "24", "max_loss_per_position_usd": "24"},
                       {"max_adverse_move": "0.09"}, {"max_loss_per_position_usd": "24"},
                       {"max_exit_exposure_usd": "400"}):
            other = _load({"VIXM": {**SHORT, **change}})["VIXM"]
            self.assertNotEqual(contract_identity(base), contract_identity(other), change)

    def test_the_same_terms_have_the_same_identity(self):
        self.assertEqual(contract_identity(_load({"VIXM": SHORT})["VIXM"]),
                         contract_identity(_load({"VIXM": dict(SHORT)})["VIXM"]))


if __name__ == "__main__":
    unittest.main()
