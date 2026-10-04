"""The instrument census: classification, what it keeps, resumption, and that it reads no return."""

import json
import unittest
from unittest import mock

from tramitago_quant_core.research import instrument_census as census
from tramitago_quant_core.research.instrument_census import (
    classify, is_fund_like, is_leveraged_or_inverse, candidates, history_tier, facts_from_bars,
    monthly_dollar_volume, summarise, run_census, census_record, definition_digest)

PRICE_SENTINEL = "987.654"


def _asset(symbol, name, **kw):
    return {"symbol": symbol, "name": name, "exchange": "ARCA", "status": "active",
            "tradable": True, "shortable": True, "easy_to_borrow": True, "fractionable": True, **kw}


def _bar(t, volume, close):
    return {"t": t + "T05:00:00Z", "o": 1, "h": 1, "l": 1, "c": close, "v": volume}


class ClassificationTests(unittest.TestCase):
    def test_funds_fall_in_the_class_their_name_states(self):
        cases = {
            "Global X S&P 500 Covered Call ETF": "COVERED_CALL",
            "Global X NASDAQ 100 Covered Call ETF": "COVERED_CALL",
            "iShares 20+ Year Treasury Bond ETF": "TREASURY_DURATION",
            "Invesco CurrencyShares Japanese Yen Trust": "CURRENCY",
            "SPDR Gold Shares": "PRECIOUS_METALS",
            "iShares iBoxx $ Investment Grade Corporate Bond ETF": "INVESTMENT_GRADE_CREDIT",
            "iShares 0-5 Year TIPS Bond ETF": "INFLATION_LINKED",
            "ProShares VIX Short-Term Futures ETF": "VOLATILITY",
            "Invesco DB Commodity Index Tracking Fund": "COMMODITY_BROAD",
            "iShares Bitcoin Trust": "CRYPTO",
        }
        for name, label in cases.items():
            self.assertIn(label, classify(name), name)

    def test_an_ordinary_company_is_in_no_class_even_with_a_keyword(self):
        for name in ("Barrick Gold Corporation", "Apple Inc. Common Stock",
                     "Treasury Wine Estates Limited", "Coinbase Global, Inc."):
            self.assertEqual(classify(name), [], name)

    def test_an_exclusion_takes_a_name_out_of_a_class(self):
        self.assertNotIn("VOLATILITY", classify("iShares MSCI USA Min Vol Factor ETF"))
        self.assertNotIn("VOLATILITY", classify("Invesco S&P 500 Low Volatility ETF"))
        self.assertIn("LOW_VOLATILITY_FACTOR", classify("Invesco S&P 500 Low Volatility ETF"))
        self.assertNotIn("PRECIOUS_METALS", classify("VanEck Gold Miners ETF"))
        self.assertNotIn("TREASURY_DURATION", classify("iShares Treasury Inflation Protected ETF"))

    def test_a_word_matches_as_a_word_and_not_inside_another(self):
        self.assertNotIn("INFLATION_LINKED", classify("Titips Fund"))
        self.assertNotIn("MUNICIPAL", classify("Communicipal Trust"))

    def test_leveraged_and_inverse_products_are_flagged_apart(self):
        for name in ("ProShares UltraShort VIX ETF", "Direxion Daily Bear 3X Shares",
                     "ProShares Short S&P500", "ProShares Ultra Bloomberg Crude Oil"):
            self.assertTrue(is_leveraged_or_inverse(name), name)
        for name in ("iShares Short-Term Treasury ETF", "Global X S&P 500 Covered Call ETF"):
            self.assertFalse(is_leveraged_or_inverse(name), name)

    def test_a_fund_marker_is_required_for_any_class(self):
        self.assertTrue(is_fund_like("Some Gold Trust"))
        self.assertFalse(is_fund_like("Some Gold Company"))


class CandidatesTests(unittest.TestCase):
    def test_only_active_tradable_exchange_listed_tagged_instruments_are_probed(self):
        assets = [_asset("GOOD", "Global X Covered Call ETF"),
                  _asset("OTC", "Global X Covered Call ETF", exchange="OTC"),
                  _asset("DEAD", "Global X Covered Call ETF", status="inactive"),
                  _asset("NOTR", "Global X Covered Call ETF", tradable=False),
                  _asset("PLAIN", "Apple Inc."),
                  _asset("UNTAG", "Some Plain Index Fund")]
        self.assertEqual([r["symbol"] for r in candidates(assets)], ["GOOD"])

    def test_the_broker_facts_are_kept_and_a_missing_flag_is_false(self):
        row = candidates([_asset("X", "Global X Covered Call ETF", shortable=None)])[0]
        self.assertFalse(row["shortable"])
        self.assertTrue(row["easy_to_borrow"])


class WhatIsKeptTests(unittest.TestCase):
    def test_the_history_tiers_have_exact_edges(self):
        self.assertEqual(history_tier("2018-03-01"), "A")
        self.assertEqual(history_tier("2018-03-02"), "B")
        self.assertEqual(history_tier("2021-10-01"), "B")
        self.assertEqual(history_tier("2021-10-02"), "C")
        self.assertIsNone(history_tier(None))

    def test_the_month_in_progress_is_dropped_from_the_volume(self):
        bars = [_bar("2026-06-01", 1_000_000, 50.0), _bar("2026-07-01", 1_000_000, 50.0),
                _bar("2026-10-01", 9_999_999_999, 50.0)]
        self.assertEqual(monthly_dollar_volume(bars), 50_000_000)

    def test_a_single_bar_leaves_no_complete_month_and_so_no_volume(self):
        self.assertIsNone(monthly_dollar_volume([_bar("2026-10-01", 1_000_000, 50.0)]))
        self.assertIsNone(monthly_dollar_volume([]))

    def test_the_liquidity_floor_is_exact(self):
        floor = census.LIQUIDITY_FLOOR_MONTHLY_DOLLAR_VOLUME
        on = facts_from_bars([_bar("2016-01-01", 1, 1)],
                             [_bar("2026-06-01", floor, 1.0), _bar("2026-10-01", 1, 1.0)])
        under = facts_from_bars([_bar("2016-01-01", 1, 1)],
                                [_bar("2026-06-01", floor - 1, 1.0), _bar("2026-10-01", 1, 1.0)])
        self.assertTrue(on["liquid"])
        self.assertFalse(under["liquid"])

    def test_only_a_date_and_a_dollar_volume_leave_the_bars(self):
        facts = facts_from_bars([_bar("2016-01-01", 5, PRICE_SENTINEL)],
                                [_bar("2026-06-01", 10, 3.21), _bar("2026-10-01", 1, 1.0)])
        self.assertEqual(set(facts), {"first_bar", "tier", "monthly_dollar_volume", "liquid"})
        self.assertNotIn(PRICE_SENTINEL, json.dumps(facts))

    def test_an_instrument_with_no_bars_has_no_tier_and_is_not_liquid(self):
        facts = facts_from_bars([], [])
        self.assertEqual((facts["first_bar"], facts["tier"], facts["liquid"]), (None, None, False))


class RunTests(unittest.TestCase):
    ASSETS = [_asset("AAA", "Global X Covered Call ETF"),
              _asset("BBB", "iShares 20+ Year Treasury Bond ETF"),
              _asset("CCC", "ProShares UltraShort VIX ETF")]

    def _fetch(self, calls, fail=()):
        def fetch(symbol, start, limit):
            calls.append((symbol, limit))
            if symbol in fail:
                raise RuntimeError("HTTP500 https://example/?api_key=SECRET")
            if limit == 1:
                return [_bar("2016-01-01", 1, 1.0)]
            return [_bar("2026-06-01", 10_000_000, 30.0), _bar("2026-10-01", 1, 1.0)]
        return fetch

    def test_every_candidate_is_probed_with_exactly_two_requests(self):
        calls = []
        rows, facts = run_census(self.ASSETS, self._fetch(calls), asof="2026-10-04")
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(calls), 6)
        self.assertTrue(all(r["tier"] == "A" and r["liquid"] for r in rows))

    def test_a_cached_symbol_is_not_asked_again(self):
        calls = []
        _, facts = run_census(self.ASSETS, self._fetch(calls), asof="2026-10-04")
        again = []
        run_census(self.ASSETS, self._fetch(again), asof="2026-10-04", cache=facts)
        self.assertEqual(again, [])

    def test_a_failure_is_recorded_by_its_kind_only_and_retried_next_time(self):
        calls = []
        rows, facts = run_census(self.ASSETS, self._fetch(calls, fail={"BBB"}), asof="2026-10-04")
        failed = [r for r in rows if r["symbol"] == "BBB"][0]
        self.assertEqual(failed["error"], "RuntimeError")
        self.assertNotIn("SECRET", json.dumps(rows))
        self.assertEqual(len(rows), 3)                                  # the census went on
        retry = []
        run_census(self.ASSETS, self._fetch(retry), asof="2026-10-04", cache=facts)
        self.assertEqual({s for s, _ in retry}, {"BBB"})

    def test_the_cache_is_updated_in_place_so_progress_can_be_saved(self):
        cache = {}
        run_census(self.ASSETS, self._fetch([]), asof="2026-10-04", cache=cache)
        self.assertEqual(set(cache), {"AAA", "BBB", "CCC"})

    def test_a_trial_limit_probes_only_the_first_few(self):
        calls = []
        rows, _ = run_census(self.ASSETS, self._fetch(calls), asof="2026-10-04", limit=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(calls), 2)

    def test_progress_is_reported_per_symbol(self):
        seen = []
        run_census(self.ASSETS, self._fetch([]), asof="2026-10-04",
                   on_progress=lambda done, total, symbol: seen.append((done, total, symbol)))
        self.assertEqual(seen, [(1, 3, "AAA"), (2, 3, "BBB"), (3, 3, "CCC")])


class SummaryAndRecordTests(unittest.TestCase):
    def _rows(self):
        def row(symbol, classes, tier, liquid, shortable=True, leveraged=False):
            return {"symbol": symbol, "classes": classes, "tier": tier, "liquid": liquid,
                    "shortable": shortable, "leveraged_or_inverse": leveraged,
                    "monthly_dollar_volume": 10 if liquid else 1, "error": None}
        return [row("A", ["COVERED_CALL"], "A", True), row("B", ["COVERED_CALL"], "B", True),
                row("C", ["COVERED_CALL"], "A", False), row("D", ["COVERED_CALL"], "A", True, leveraged=True),
                row("E", ["COVERED_CALL", "VOLATILITY"], "A", True, shortable=False)]

    def test_counts_are_by_class_and_exclude_leveraged_from_the_usable_columns(self):
        summary = summarise(self._rows())
        cc = summary["COVERED_CALL"]
        self.assertEqual((cc["named"], cc["plain"]), (5, 4))
        self.assertEqual(cc["plain_tier_a_liquid"], 2)               # A and E
        self.assertEqual(cc["plain_tier_a_or_b_liquid"], 3)          # plus B
        self.assertEqual(cc["plain_tier_a_liquid_shortable"], 1)     # only A: E is not shortable
        self.assertEqual(summary["VOLATILITY"]["named"], 1)
        self.assertEqual(summary["CRYPTO"]["named"], 0)

    def test_the_record_is_content_addressed_and_carries_its_definition(self):
        rows, summary = self._rows(), summarise(self._rows())
        a = census_record(rows, summary, asof="2026-10-04", assets_total=10, fund_like_total=5)
        b = census_record(rows, summary, asof="2026-10-04", assets_total=10, fund_like_total=5)
        self.assertEqual(a["census_id"], b["census_id"])
        self.assertEqual(a["definition_digest"], definition_digest())
        self.assertIn("UPPER BOUND", a["what_it_cannot_say"].upper())

    def test_a_changed_definition_is_a_different_census(self):
        rows, summary = self._rows(), summarise(self._rows())
        before = census_record(rows, summary, asof="2026-10-04", assets_total=10, fund_like_total=5)
        widened = dict(census.CLASSES)
        widened["CRYPTO"] = (("bitcoin", "ethereum", "solana"), ())
        with mock.patch.object(census, "CLASSES", widened):
            after = census_record(rows, summary, asof="2026-10-04", assets_total=10, fund_like_total=5)
        self.assertNotEqual(before["definition_digest"], after["definition_digest"])
        self.assertNotEqual(before["census_id"], after["census_id"])

    def test_no_row_carries_a_price_or_a_return_field(self):
        probed = run_census(RunTests.ASSETS, RunTests()._fetch([]), asof="2026-10-04")[0]
        record = census_record(probed, summarise(probed), asof="2026-10-04", assets_total=3, fund_like_total=3)
        for row in record["rows"]:
            for key in row:
                for forbidden in ("close", "open", "high", "low", "price", "return"):
                    self.assertNotIn(forbidden, key)


if __name__ == "__main__":
    unittest.main()
