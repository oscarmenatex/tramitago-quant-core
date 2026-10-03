"""The premium engine measuring a SIMPLE SHORT: one instrument held short.

A short is the negative of the instrument's daily return, pays a declared borrow fee while
held, and has no natural limit on what it loses. These pin the arithmetic that a short
measurement stands on, and that a LONG measurement is exactly what it was.
"""

import random
import unittest
from datetime import date, timedelta

from tramitago_quant_core.research.premium_engine import judge, validate_spec
from tramitago_quant_core.risk.cost_model import cost_contract, COST_REGIME_HOLDING

SURVIVAL = {"counterparty": "holders of a decaying product who pay to hold it",
            "why_they_accept_losing": "they hold it as insurance and expect to pay",
            "what_would_end_it": "the product ceasing to decay"}
DAYS = 2200


def _rows(returns):
    start = date(2016, 1, 4)
    return [{"timestamp": (start + timedelta(days=i)).isoformat() + "T00:00:00Z",
             "close": "20", "volume": "1000000", "forward_return_1d": repr(value)}
            for i, value in enumerate(returns)]


def _decaying(seed=3, drift=-0.0004, sd=0.005, spikes=()):
    rng = random.Random(seed)
    values = [rng.gauss(drift, sd) for _ in range(DAYS)]
    for index, size in spikes:
        values[index] = size
    return values


def _spec(direction="LONG", borrow=None, move=None, limit="0.99", **extra):
    instrument = {"provider": "alpaca_equity", "symbol": "TEST", "direction": direction}
    cost = {"commission": "0", "half_spread": "0.0002", "slippage": "0.00005", "legs": 1,
            "source": "declared for a test"}
    if borrow is not None:
        cost["borrow_annual"] = borrow
    if move is not None:
        instrument["max_adverse_move"] = move
    if direction == "LONG":
        del instrument["direction"]
    return {"slug": "short-test", "hypothesis_id": "HYPOTHESIS|probe",
            "instrument": instrument, "cost": cost, "sizing": {"drawdown_limit": limit},
            "level_claim": {"folds": 7, "consistency_threshold": "0.70",
                            "adverse_threshold": "-0.02", "minimum_adverse_episodes": 5},
            "survival": dict(SURVIVAL), "bootstrap": {"resamples": 200},
            "decision": {"build_cost": "0", "life_years": "3", "tranche": "1000"}, **extra}


def _short(**kwargs):
    kwargs.setdefault("borrow", "0")
    kwargs.setdefault("move", "0.10")
    return _spec("SHORT", **kwargs)


class ValidationTests(unittest.TestCase):
    def test_a_well_formed_short_validates(self):
        self.assertTrue(validate_spec(_short()))

    def test_a_short_must_declare_its_borrow_cost_even_when_zero(self):
        spec = _short()
        del spec["cost"]["borrow_annual"]
        with self.assertRaises(ValueError) as caught:
            validate_spec(spec)
        self.assertIn("borrow_annual", str(caught.exception))
        self.assertTrue(validate_spec(_short(borrow="0")))

    def test_a_short_must_declare_how_far_the_instrument_may_rise_against_it(self):
        for bad in ("0", "1", "1.5", "-0.1"):
            with self.assertRaises(ValueError, msg=bad):
                validate_spec(_short(move=bad))
        spec = _short()
        del spec["instrument"]["max_adverse_move"]
        with self.assertRaises(ValueError):
            validate_spec(spec)

    def test_a_borrow_fee_outside_zero_to_one_is_refused(self):
        for bad in ("-0.01", "1", "2", "abc"):
            with self.assertRaises(ValueError, msg=bad):
                validate_spec(_short(borrow=bad))

    def test_short_terms_on_a_long_are_refused(self):
        long_spec = _spec()
        long_spec["cost"]["borrow_annual"] = "0.01"
        with self.assertRaises(ValueError):
            validate_spec(long_spec)

    def test_a_pair_cannot_also_be_a_simple_short(self):
        spec = _short()
        spec["instrument"]["second_leg"] = {"symbol": "OTHER"}
        spec["cost"]["legs"] = 2
        with self.assertRaises(ValueError) as caught:
            validate_spec(spec)
        self.assertIn("pair already holds a short leg", str(caught.exception))

    def test_an_unknown_direction_is_refused(self):
        spec = _short()
        spec["instrument"]["direction"] = "FLAT"
        with self.assertRaises(ValueError):
            validate_spec(spec)


class ArithmeticTests(unittest.TestCase):
    def test_a_short_of_returns_is_a_long_of_their_negation_exactly(self):
        # THE PROPERTY A SHORT MEASUREMENT STANDS ON. Same costs, no borrow fee.
        returns = _decaying()
        shorted = judge(_short(), _rows(returns))
        longed = judge(_spec(), _rows([-value for value in returns]))
        for key in ("gross_total", "net_total", "sharpe_point", "sharpe_bound", "weight",
                    "drawdown_point", "drawdown_bound"):
            self.assertEqual(shorted[key], longed[key], key)

    def test_a_decaying_instrument_pays_a_short_and_costs_a_long(self):
        returns = _decaying()
        self.assertGreater(judge(_short(), _rows(returns))["sharpe_point"], 1.0)
        self.assertLess(judge(_spec(), _rows(returns))["sharpe_point"], -1.0)

    def test_the_borrow_fee_costs_exactly_its_annual_rate_per_day_held(self):
        returns = _decaying()
        free = judge(_short(borrow="0"), _rows(returns))
        costly = judge(_short(borrow="0.0756"), _rows(returns))
        self.assertEqual((free["weight"], costly["weight"]), (1.0, 1.0))
        self.assertAlmostEqual(free["net_total"] - costly["net_total"],
                               DAYS * 0.0756 / 252, places=9)

    def test_the_borrow_fee_scales_with_the_weight_like_every_other_cost(self):
        # Under a tight limit the position is sized down; a full fee against a fraction of the
        # return would tax it 1/w times over. The contract the claim carries must be the one
        # built from the rates scaled by the weight the result reports.
        returns = _decaying(sd=0.02, drift=-0.0008)
        sized = judge(_short(borrow="0.05", limit="0.15"), _rows(returns))
        weight = sized["weight"]
        self.assertLess(weight, 1.0)
        daily = f"{0.05 / 252:.12f}"
        expected = cost_contract(
            regime=COST_REGIME_HOLDING, commission_rate=f"{0.0 * weight:.10f}",
            half_spread_rate=f"{0.0002 * weight:.10f}", slippage_rate=f"{0.00005 * weight:.10f}",
            recurring_rate_per_period=f"{float(daily) * weight:.12f}", legs=1,
            source=f"the declared rates scaled by the {weight:.4f} weight derived from the "
                   f"0.15 limit")
        self.assertEqual(sized["level_claim"]["claim"]["cost_contract_id"],
                         expected["contract_id"])

    def test_a_zero_borrow_short_has_the_same_cost_contract_as_a_long(self):
        returns = _decaying()
        short = judge(_short(borrow="0"), _rows(returns))["level_claim"]["claim"]
        long_ = judge(_spec(), _rows([-v for v in returns]))["level_claim"]["claim"]
        self.assertEqual(short["cost_contract_id"], long_["cost_contract_id"])
        costly = judge(_short(borrow="0.05"), _rows(returns))["level_claim"]["claim"]
        self.assertNotEqual(costly["cost_contract_id"], long_["cost_contract_id"])


class DescriptionAndDiagnosticTests(unittest.TestCase):
    def test_the_claim_says_it_is_a_short_and_a_long_claim_reads_as_it_always_did(self):
        returns = _decaying()
        short = judge(_short(), _rows(returns))["level_claim"]["claim"]
        long_ = judge(_spec(), _rows(returns))["level_claim"]["claim"]
        self.assertIn("TEST held SHORT continuously over the window", short["position_description"])
        self.assertIn("TEST held continuously over the window", long_["position_description"])

    def test_a_long_result_has_no_short_block(self):
        self.assertNotIn("short", judge(_spec(), _rows(_decaying())))

    def test_the_squeeze_is_reported_with_the_days_that_gap_past_the_stop(self):
        returns = _decaying(spikes=((100, 0.35), (900, 0.12), (1500, 0.09)))
        report = judge(_short(move="0.10"), _rows(returns))["short"]
        self.assertAlmostEqual(report["worst_day_against"], 0.35)
        self.assertEqual(report["days_beyond_adverse_move"], 2)
        self.assertAlmostEqual(report["frequency_beyond_adverse_move"], 2 / DAYS)

    def test_a_squeeze_is_in_the_drawdown_the_measurement_reports(self):
        calm = judge(_short(), _rows(_decaying()))
        squeezed = judge(_short(), _rows(_decaying(spikes=((1000, 0.60),))))
        self.assertGreater(squeezed["drawdown_point"], calm["drawdown_point"] + 0.3)


class ControlTests(unittest.TestCase):
    MONITOR = {"kind": "level_below", "variable": "Z", "series": "Z", "floor": "0.5",
               "confirmation_periods": 3, "re_entry_threshold": "1.0",
               "source": "a test series"}

    def _run(self, spec, returns, control):
        rows = _rows(returns)
        dates = [row["timestamp"][:10] for row in rows]
        series = {"Z": {day: (0.0 if i % 5 == 0 else 1.0) for i, day in enumerate(dates)}}
        controls = {"C": dict(zip(dates, control))}
        spec = {**spec, "monitor": dict(self.MONITOR), "controls": [
            {"kind": "underlying_forward_returns", "name": "C"}]}
        return judge(spec, rows, monitor_inputs={"series": series, "underlying": None},
                     controls=controls)

    def test_a_control_is_held_in_the_same_direction_as_the_position(self):
        returns = _decaying(seed=5)
        short = self._run(_short(), returns, returns)
        long_ = self._run(_spec(), [-v for v in returns], [-v for v in returns])
        self.assertEqual(short["controls"]["C"], long_["controls"]["C"])
        self.assertEqual(short["monitor"]["link_met"], long_["monitor"]["link_met"])

    def test_the_link_is_tested_on_the_position_return_not_the_instrument_return(self):
        # A monitor flags days on which the instrument RISES, which is bad for a short and
        # good for a long: the same flags must read as a met link for one and not the other.
        rng = random.Random(9)
        flagged = [i % 5 == 0 for i in range(DAYS)]
        returns = [(0.01 if flagged[i] else -0.001) + rng.gauss(0, 0.002) for i in range(DAYS)]
        short = self._run(_short(), returns, returns)["monitor"]
        long_ = self._run(_spec(), returns, returns)["monitor"]
        self.assertGreater(short["link_met"], long_["link_met"])
        self.assertEqual(short["link_met"], short["link_usable"])
        self.assertEqual(long_["link_met"], 0)


if __name__ == "__main__":
    unittest.main()
