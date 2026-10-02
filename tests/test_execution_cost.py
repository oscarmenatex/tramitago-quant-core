"""What a fill actually cost, measured instead of assumed.

The gated carry came back NOT_VALIDATED on costs, with a breakeven of 0.000494
per leg per side against 0.000650 DECLARED -- and the declared number was an
assumed taker rate nobody had measured. These cover turning fills into rates, and
the two refusals that keep a measured contract honest.
"""

import json
import tempfile
import unittest
from pathlib import Path

import pipeline as p
from tramitago_quant_core.risk.execution_cost import (
    measure_execution, verified_execution_measurement, append_execution_measurement,
    load_execution_measurements, measured_cost_contract, round_trip_cost,
    execution_summary, SIDE_BUY, SIDE_SELL, FILL_PAPER, FILL_LIVE,
)

AT = "2026-10-01T12:00:00Z"


def _fill(side=SIDE_BUY, bid=100.0, ask=100.2, fill_price=100.1, quantity=1.0,
          commission=0.05, venue=FILL_PAPER, symbol="BTC/USD", at=AT):
    return measure_execution(side=side, bid=bid, ask=ask, fill_price=fill_price,
                             filled_quantity=quantity, commission=commission,
                             fill_venue=venue, symbol=symbol, observed_at=at)


class MeasurementTests(unittest.TestCase):
    def test_a_fill_at_the_ask_is_half_a_spread_and_no_slippage(self):
        measurement = _fill(fill_price=100.2, commission=0.0)
        self.assertAlmostEqual(float(measurement["half_spread_rate"]), 0.000999, places=5)
        self.assertEqual(float(measurement["slippage_rate"]), 0.0)

    def test_a_fill_beyond_the_ask_is_slippage(self):
        self.assertGreater(float(_fill(fill_price=100.3, commission=0.0)["slippage_rate"]), 0.0)

    def test_a_fill_better_than_the_quote_reports_zero_not_a_negative_cost(self):
        # A lucky fill must not subsidise an unlucky one into a contract cheaper
        # than any real execution.
        self.assertEqual(float(_fill(fill_price=100.0, commission=0.0)["slippage_rate"]), 0.0)

    def test_a_sell_is_measured_from_the_other_direction(self):
        worse = _fill(side=SIDE_SELL, fill_price=99.8, commission=0.0)
        better = _fill(side=SIDE_SELL, fill_price=100.1, commission=0.0)
        self.assertGreater(float(worse["slippage_rate"]), float(better["slippage_rate"]))

    def test_the_commission_is_a_rate_on_the_notional_actually_filled(self):
        measurement = _fill(fill_price=100.0, quantity=2.0, commission=0.4)
        self.assertAlmostEqual(float(measurement["commission_rate"]), 0.4 / 200.0)

    def test_cost_per_side_is_the_number_the_carry_verdict_uses(self):
        measurement = _fill(fill_price=100.3, commission=0.05)
        self.assertAlmostEqual(
            float(measurement["cost_per_side"]),
            float(measurement["commission_rate"]) + float(measurement["half_spread_rate"])
            + float(measurement["slippage_rate"]), places=9)

    def test_a_crossed_quote_is_not_a_measurement(self):
        with self.assertRaises(ValueError):
            _fill(bid=100.3, ask=100.1)

    def test_invalid_inputs_are_refused(self):
        for overrides in ({"bid": 0.0}, {"ask": -1.0}, {"fill_price": 0.0},
                          {"quantity": 0.0}, {"commission": -0.01},
                          {"side": "LONG"}, {"venue": "SIM"}, {"at": "2026-10-01"}):
            with self.assertRaises(ValueError):
                _fill(**overrides)

    def test_a_tampered_measurement_fails_verification(self):
        measurement = dict(_fill(fill_price=100.3))
        measurement["slippage_rate"] = "0.000000000"   # the fill really slipped
        with self.assertRaises(ValueError):
            verified_execution_measurement(measurement)


class RegistryTests(unittest.TestCase):
    def test_measurements_append_and_never_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fills.json"
            first = append_execution_measurement(path, _fill())
            again = append_execution_measurement(path, _fill())
            self.assertEqual(first, again)
            self.assertEqual(len(json.loads(path.read_bytes())["measurements"]), 1)

    def test_a_different_fill_is_a_different_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fills.json"
            append_execution_measurement(path, _fill(at="2026-10-01T12:00:00Z"))
            append_execution_measurement(path, _fill(at="2026-10-01T12:05:00Z"))
            self.assertEqual(len(load_execution_measurements(path)), 2)

    def test_filtering_by_venue_and_symbol(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fills.json"
            append_execution_measurement(path, _fill(venue=FILL_PAPER))
            append_execution_measurement(path, _fill(venue=FILL_LIVE, at="2026-10-01T13:00:00Z"))
            self.assertEqual(len(load_execution_measurements(path, fill_venue=FILL_LIVE)), 1)
            self.assertEqual(len(load_execution_measurements(path, symbol="ETH/USD")), 0)

    def test_a_missing_registry_reads_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_execution_measurements(Path(tmp) / "absent.json"), [])


class ContractTests(unittest.TestCase):
    def _fills(self, prices):
        return [_fill(fill_price=price, at=f"2026-10-01T12:{index:02d}:00Z")
                for index, price in enumerate(prices)]

    def test_no_measurements_is_refused_rather_than_priced_at_zero(self):
        # Absence of fills is not evidence of zero cost, which is exactly what
        # "ASSUMED" was hiding.
        with self.assertRaises(ValueError) as caught:
            measured_cost_contract([], legs=2, source="test")
        self.assertIn("not evidence of zero cost", str(caught.exception))

    def test_the_contract_says_measured_and_names_the_venue(self):
        contract = measured_cost_contract(self._fills([100.2, 100.3, 100.25]),
                                          legs=2, source="test")
        self.assertTrue(contract["source"].startswith("MEASURED from 3 fills on PAPER"))
        self.assertIn("slippage FLOOR", contract["source"])

    def test_a_paper_contract_warns_and_a_live_one_does_not(self):
        live = [_fill(venue=FILL_LIVE, fill_price=100.2, at=f"2026-10-01T12:{index:02d}:00Z")
                for index in range(3)]
        self.assertNotIn("FLOOR", measured_cost_contract(live, legs=2, source="x")["source"])

    def test_a_higher_quantile_gives_a_more_expensive_contract(self):
        fills = self._fills([100.2, 100.25, 100.9])
        typical = measured_cost_contract(fills, legs=2, source="x", quantile="0.5")
        tail = measured_cost_contract(fills, legs=2, source="x", quantile="1.0")
        self.assertGreater(float(tail["slippage_rate"]), float(typical["slippage_rate"]))

    def test_it_produces_a_contract_the_cost_model_accepts(self):
        contract = measured_cost_contract(self._fills([100.2, 100.3]), legs=2, source="x")
        self.assertEqual(p.verified_cost_contract(contract), contract)


class RoundTripTests(unittest.TestCase):
    def _declared(self, legs=2):
        return p.cost_contract(
            regime=p.COST_REGIME_HOLDING, commission_rate="0.00045",
            half_spread_rate="0.0001", slippage_rate="0.0001",
            recurring_rate_per_period="0", legs=legs, source="declared")

    def test_sixty_four_round_trips_on_two_legs(self):
        # The literal question the gated carry verdict turns on.
        self.assertAlmostEqual(round_trip_cost(self._declared(), 64), 0.1664, places=6)

    def test_no_round_trips_cost_nothing(self):
        self.assertEqual(round_trip_cost(self._declared(), 0), 0.0)

    def test_a_negative_count_is_refused(self):
        with self.assertRaises(ValueError):
            round_trip_cost(self._declared(), -1)


class SummaryTests(unittest.TestCase):
    def test_the_spread_is_reported_not_just_a_single_number(self):
        fills = [_fill(fill_price=price, at=f"2026-10-01T12:{index:02d}:00Z")
                 for index, price in enumerate((100.2, 100.3, 101.0))]
        summary = execution_summary(fills)
        self.assertEqual(summary["fills"], 3)
        self.assertLess(float(summary["cost_per_side_min"]),
                        float(summary["cost_per_side_max"]))

    def test_an_empty_set_reports_zero_fills(self):
        self.assertEqual(execution_summary([]), {"fills": 0})


if __name__ == "__main__":
    unittest.main()
