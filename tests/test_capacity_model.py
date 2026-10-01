"""Capacity model: the ceiling C, and the honesty about where it comes from.

Capital is incremental and open-ended, so the question is inverted (§8.6): not
"does it work at X?" but "at what capital does it stop working?". These prove
the properties that matter -- a structural wall binds before smooth impact, an
unknown ceiling does not pass, turnover decides how much size costs, and a
ceiling re-estimated from real fills supersedes the modelled one.
"""

import unittest

import pipeline as p


def _contract(adv="100000000", coefficient="0.1", structural=None,
              form=p.IMPACT_SQUARE_ROOT, tolerance="0.20"):
    return p.capacity_contract(
        average_daily_volume=adv, impact_coefficient=coefficient, impact_form=form,
        degradation_tolerance=tolerance, structural_cap=structural,
        source="test fixture")


# A profitable, low-turnover strategy, so impact is what binds rather than the
# edge simply being too small to survive any cost.
STRATEGY = dict(gross_per_period=0.0008, base_cost_per_period=0.00002,
                volatility_per_period=0.01, turnover_per_period=0.13)


class CapacityContractTests(unittest.TestCase):
    def test_identity_reproduces_and_detects_an_altered_market_fact(self):
        contract = _contract()
        self.assertEqual(p.verified_capacity_contract(contract), contract)
        with self.assertRaises(ValueError):
            p.verified_capacity_contract({**contract, "average_daily_volume": "1"})

    def test_market_facts_need_a_declared_source(self):
        with self.assertRaises(ValueError):
            p.capacity_contract(average_daily_volume="1000", impact_coefficient="0.1",
                                source="  ")

    def test_tolerance_must_be_a_fraction(self):
        with self.assertRaises(ValueError):
            _contract(tolerance="1.5")


class ImpactTests(unittest.TestCase):
    def test_square_root_law_grows_sublinearly(self):
        # Ten times the size costs about three times more per unit, not ten.
        contract = _contract()
        small = p.impact_rate(contract, 1_000_000)
        large = p.impact_rate(contract, 10_000_000)
        self.assertAlmostEqual(large / small, 10 ** 0.5, places=6)

    def test_linear_form_grows_proportionally(self):
        contract = _contract(form=p.IMPACT_LINEAR)
        small = p.impact_rate(contract, 1_000_000)
        large = p.impact_rate(contract, 10_000_000)
        self.assertAlmostEqual(large / small, 10.0, places=6)

    def test_turnover_decides_how_much_size_costs(self):
        # Identical capital and identical impact rate: the strategy that trades
        # ten times as often pays ten times the impact.
        contract = _contract()
        patient = p.net_sharpe_at(contract, 50_000_000, **{**STRATEGY, "turnover_per_period": 0.01})
        frantic = p.net_sharpe_at(contract, 50_000_000, **{**STRATEGY, "turnover_per_period": 1.0})
        self.assertGreater(patient, frantic)


class CeilingTests(unittest.TestCase):
    def test_impact_produces_a_finite_ceiling_and_the_sharpe_there_is_degraded(self):
        contract = _contract()
        result = p.capacity_ceiling(contract, reference_capital=10_000, **STRATEGY)
        self.assertEqual(result["binding_constraint"], "IMPACT")
        self.assertGreater(result["ceiling"], 10_000)
        self.assertAlmostEqual(
            result["net_sharpe_at_ceiling"] / result["reference_net_sharpe"], 0.80, places=2)

    def test_a_structural_wall_binds_before_smooth_impact(self):
        # Impact is gradual; an exchange position limit is not. A model that only
        # knew about impact would cheerfully report a ceiling above the wall.
        # Impact alone would allow 40_000 here; the wall is lower, so it binds.
        contract = _contract(structural="25000")
        result = p.capacity_ceiling(contract, reference_capital=10_000, **STRATEGY)
        self.assertEqual(result["binding_constraint"], "STRUCTURAL")
        self.assertEqual(result["ceiling"], 25_000.0)

    def test_no_ceiling_found_is_reported_not_invented(self):
        contract = _contract(adv="1000000000000", coefficient="0.000001")
        result = p.capacity_ceiling(contract, reference_capital=1_000,
                                    search_multiple=10, **STRATEGY)
        self.assertIsNone(result["ceiling"])
        self.assertEqual(result["binding_constraint"], "NONE_FOUND")

    def test_an_unprofitable_strategy_has_no_capacity_to_size(self):
        contract = _contract()
        with self.assertRaises(ValueError):
            p.capacity_ceiling(contract, reference_capital=100_000,
                               **{**STRATEGY, "gross_per_period": 0.0})


class TrancheAdmissionTests(unittest.TestCase):
    def test_a_tranche_within_the_ceiling_is_admissible(self):
        result = p.capacity_ceiling(_contract(), reference_capital=10_000, **STRATEGY)
        ok, detail = p.tranche_is_admissible(result, 30_000)
        self.assertTrue(ok, detail)

    def test_a_tranche_past_the_ceiling_is_capped_not_scaled(self):
        result = p.capacity_ceiling(_contract(), reference_capital=10_000, **STRATEGY)
        ok, detail = p.tranche_is_admissible(result, 400_000)
        self.assertFalse(ok)
        self.assertIn("cap the allocation", detail)

    def test_an_unknown_ceiling_does_not_pass(self):
        # An unknown ceiling is not an absent one.
        unknown = {"ceiling": None, "binding_constraint": "NONE_FOUND"}
        ok, detail = p.tranche_is_admissible(unknown, 1_000)
        self.assertFalse(ok)
        self.assertIn("unknown ceiling is not an absent one", detail)


class RealisedImpactTests(unittest.TestCase):
    def test_execution_data_supersedes_the_modelled_ceiling(self):
        # §8.6(c): real fills are better evidence than any prior model, so the
        # re-estimate REPLACES rather than being averaged with it.
        contract = _contract()
        modelled = p.capacity_ceiling(contract, reference_capital=10_000, **STRATEGY)
        worse = p.ceiling_from_realised_impact(
            realised_impact_rate=p.impact_rate(contract, 10_000) * 3,
            capital_traded=10_000, contract=contract, **STRATEGY)
        self.assertEqual(worse["supersedes_contract_id"], contract["contract_id"])
        self.assertLess(worse["ceiling"], modelled["ceiling"])

    def test_cheaper_than_modelled_execution_raises_the_ceiling(self):
        contract = _contract()
        modelled = p.capacity_ceiling(contract, reference_capital=10_000, **STRATEGY)
        better = p.ceiling_from_realised_impact(
            realised_impact_rate=p.impact_rate(contract, 10_000) / 4,
            capital_traded=10_000, contract=contract, **STRATEGY)
        self.assertGreater(better["ceiling"], modelled["ceiling"])


if __name__ == "__main__":
    unittest.main()
