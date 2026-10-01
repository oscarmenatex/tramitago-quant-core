"""Nivel 4 cost model: the gate that makes "net after costs" evaluable.

Everything this project sealed before 2026-09-30 was GROSS -- thirty-three
hypotheses accepted or rejected on a quantity nobody could have traded. These
prove the arithmetic that fixes that, and in particular the two properties the
design turns on: cost scales with TURNOVER under rotation, and entry/exit are
charged WHEN THEY OCCUR rather than smoothed across the sample.
"""

import unittest

import pipeline as p


def _rotation(commission="0.0005", half_spread="0.0001", slippage="0.0002", legs=1):
    return p.cost_contract(
        regime=p.COST_REGIME_ROTATION, commission_rate=commission,
        half_spread_rate=half_spread, slippage_rate=slippage, legs=legs,
        source="test fixture")


def _holding(recurring="0.0001", legs=2):
    return p.cost_contract(
        regime=p.COST_REGIME_HOLDING, commission_rate="0.0005",
        half_spread_rate="0.0001", slippage_rate="0.0002",
        recurring_rate_per_period=recurring, legs=legs, source="test fixture")


class CostContractTests(unittest.TestCase):
    def test_a_contract_reproduces_its_identity_and_detects_an_altered_rate(self):
        contract = _rotation()
        self.assertEqual(p.verified_cost_contract(contract), contract)
        tampered = {**contract, "commission_rate": "0.0000"}
        with self.assertRaises(ValueError):
            p.verified_cost_contract(tampered)

    def test_rates_must_be_declared_as_non_negative_decimal_strings(self):
        for bad in (0.0005, "", "-0.1", "abc", "1.5"):
            with self.assertRaises(ValueError):
                _rotation(commission=bad)

    def test_a_contract_must_declare_where_its_rates_came_from(self):
        with self.assertRaises(ValueError):
            p.cost_contract(regime=p.COST_REGIME_ROTATION, commission_rate="0.0005",
                            half_spread_rate="0.0001", slippage_rate="0.0002", source="  ")

    def test_a_rotation_contract_refuses_a_recurring_rate(self):
        # Recurring charges belong to a carried position; accepting one here
        # would let a rotation model silently bill for something it never does.
        with self.assertRaises(ValueError):
            p.cost_contract(regime=p.COST_REGIME_ROTATION, commission_rate="0.0005",
                            half_spread_rate="0.0001", slippage_rate="0.0002",
                            recurring_rate_per_period="0.0001", source="test")

    def test_every_leg_pays(self):
        self.assertEqual(float(p.cost_per_side(_rotation(legs=1))), 0.0008)
        self.assertEqual(float(p.cost_per_side(_rotation(legs=2))), 0.0016)


class PositionTranslationTests(unittest.TestCase):
    def test_direction_decides_which_group_is_held(self):
        groups = ["UPPER", "LOWER_OR_EQUAL", "UPPER"]
        self.assertEqual(p.positions_from_groups(groups, "INCREASE"), [1, 0, 1])
        self.assertEqual(p.positions_from_groups(groups, "DECREASE"), [0, 1, 0])

    def test_an_undeclared_direction_is_refused(self):
        with self.assertRaises(ValueError):
            p.positions_from_groups(["UPPER"], "SIDEWAYS")


class RotationCostTests(unittest.TestCase):
    def test_cost_scales_with_turnover_not_with_time(self):
        # The property the whole design turns on: same instrument, same gross
        # return, same number of periods held -- but a signal that flips every
        # period pays far more than one that holds.
        contract = _rotation()
        gross = [0.01] * 8
        flipping = p.cost_summary(contract, [1, 0, 1, 0, 1, 0, 1, 0], gross)
        holding = p.cost_summary(contract, [1, 1, 1, 1, 0, 0, 0, 0], gross)
        self.assertEqual(flipping["periods_held"], holding["periods_held"])
        self.assertGreater(flipping["transitions"], holding["transitions"])
        self.assertGreater(flipping["cost_total"], holding["cost_total"])

    def test_a_flat_period_earns_nothing_and_costs_nothing(self):
        contract = _rotation()
        net = p.net_returns(contract, [0, 0], [0.05, -0.05])
        self.assertEqual(net, [0.0, 0.0])

    def test_entry_is_charged_in_the_period_it_happens(self):
        contract = _rotation()
        side = float(p.cost_per_side(contract))
        net = p.net_returns(contract, [1, 1], [0.01, 0.01])
        self.assertAlmostEqual(net[0], 0.01 - side)       # entry
        self.assertAlmostEqual(net[1], 0.01 - side)       # unclosed exit

    def test_an_open_position_is_charged_its_unmade_exit(self):
        # A position never closed has realised nothing; pretending otherwise
        # flatters the final period of every sample that ends mid-trade.
        contract = _rotation()
        closed = p.net_returns(contract, [1, 0], [0.01, 0.0])
        still_open = p.net_returns(contract, [1, 1], [0.01, 0.0])
        self.assertAlmostEqual(sum(closed), sum(still_open))


class HoldingCostTests(unittest.TestCase):
    def test_the_recurring_charge_applies_only_while_held(self):
        # Transition rates zeroed so only the recurring charge is in play --
        # otherwise the exit at period 1 masks what is being measured.
        contract = p.cost_contract(
            regime=p.COST_REGIME_HOLDING, commission_rate="0", half_spread_rate="0",
            slippage_rate="0", recurring_rate_per_period="0.001", legs=1,
            source="test fixture")
        net = p.net_returns(contract, [1, 0, 1], [0.0, 0.0, 0.0])
        self.assertAlmostEqual(net[0], -0.001)
        self.assertEqual(net[1], 0.0)          # flat: nothing earned, nothing owed
        self.assertAlmostEqual(net[2], -0.001)

    def test_entry_and_exit_are_not_smoothed_across_the_sample(self):
        # Charging them where they occur is what lets the amortisation EMERGE
        # over a long hold instead of being assumed. Smoothing would hand the
        # Sharpe calculation a distribution the market never produced.
        contract = _holding(recurring="0")
        side = float(p.cost_per_side(contract))
        net = p.net_returns(contract, [1] * 10, [0.001] * 10)
        self.assertAlmostEqual(net[0], 0.001 - side)
        self.assertAlmostEqual(net[5], 0.001)                 # untouched middle
        self.assertAlmostEqual(net[9], 0.001 - side)

    def test_a_long_hold_amortises_what_a_short_one_cannot(self):
        contract = _holding(recurring="0")
        short = p.cost_summary(contract, [1] * 2, [0.001] * 2)
        long_hold = p.cost_summary(contract, [1] * 200, [0.001] * 200)
        self.assertAlmostEqual(short["cost_total"], long_hold["cost_total"])
        self.assertGreater(short["cost_per_period_held"],
                           long_hold["cost_per_period_held"])
        self.assertLess(short["net_total"], 0)       # two periods cannot pay for it
        self.assertGreater(long_hold["net_total"], 0)


class CostSummaryTests(unittest.TestCase):
    def test_the_summary_reports_what_costs_consumed(self):
        contract = _rotation()
        summary = p.cost_summary(contract, [1, 0, 1, 0], [0.01, 0.01, 0.01, 0.01])
        self.assertEqual(summary["periods"], 4)
        self.assertEqual(summary["periods_held"], 2)
        self.assertAlmostEqual(summary["gross_total"], 0.02)
        self.assertAlmostEqual(summary["cost_total"],
                               summary["gross_total"] - summary["net_total"])
        self.assertEqual(summary["contract_id"], contract["contract_id"])

    def test_misaligned_or_empty_inputs_fail_closed(self):
        contract = _rotation()
        for positions, gross in (([1], [0.01, 0.02]), ([], []), ([2], [0.01])):
            with self.assertRaises(ValueError):
                p.net_returns(contract, positions, gross)


if __name__ == "__main__":
    unittest.main()
