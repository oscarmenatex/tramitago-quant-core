"""M2.2-T1/T2 extension (2026-09-28): the instrument is a parameter of the
Hypothesis's own declared universe, not fixed to "BTC-USD".

Generalized the same way M4.1 generalized the indicator window: found
"BTC-USD" hardcoded in 6 places (historical_dataset.py, experiment.py)
while investigating a second instrument (ETH-USD) for the same SMA3
Hypothesis. Verified elsewhere (manually, real HTTP capture) that BTC-USD
still reproduces its already-sealed v1-final dataset and experiment
byte-for-byte. This test proves the generalization itself, with a
synthetic instrument, without touching the network.
"""

import unittest

import pipeline as p


class M22T1InstrumentGeneralizationTests(unittest.TestCase):
    def test_dataset_config_derives_instrument_from_hypothesis_universe(self):
        hypothesis = {
            "constraints": {
                "universe": ["ETH-USD"],
                "variables": ["close", "sma_close_3", "forward_return_1d"],
                "period": {"start_utc": "2025-01-01T00:00:00Z",
                          "end_exclusive_utc": "2025-01-11T00:00:00Z"},
            },
        }
        config = p._hypothesis_dataset_config(hypothesis)
        self.assertEqual(config["instrument"], "ETH-USD")

    def test_dataset_endpoint_uses_the_configured_instrument(self):
        self.assertEqual(
            p._hypothesis_dataset_endpoint("ETH-USD"),
            "https://api.exchange.coinbase.com/products/ETH-USD/candles")
        self.assertEqual(
            p._hypothesis_dataset_endpoint("BTC-USD"),
            "https://api.exchange.coinbase.com/products/BTC-USD/candles")

    def test_rejects_a_multi_instrument_or_empty_universe(self):
        base = {
            "variables": ["close", "sma_close_3", "forward_return_1d"],
            "period": {"start_utc": "2025-01-01T00:00:00Z",
                      "end_exclusive_utc": "2025-01-11T00:00:00Z"},
        }
        with self.assertRaises(ValueError):
            p._hypothesis_dataset_config({"constraints": {**base, "universe": []}})
        with self.assertRaises(ValueError):
            p._hypothesis_dataset_config(
                {"constraints": {**base, "universe": ["BTC-USD", "ETH-USD"]}})

    def test_experiment_conditions_carry_the_dataset_instrument_through(self):
        hypothesis = {
            "hypothesis_id": "HYPOTHESIS|00000000-0000-0000-0000-000000000000",
            "version": 1, "record_id": "HYPOTHESIS_VERSION|x",
            "system_version": "0.1.0", "code_revision": "a" * 40,
            "acceptance_criterion": {
                "metric": "m", "comparison": "GT", "threshold": "0",
                "expected_direction": "INCREASE"},
            "target_metric": "m",
            "constraints": {"period": {"start_utc": "2025-01-01T00:00:00Z",
                                       "end_exclusive_utc": "2025-01-11T00:00:00Z"}},
            "creation_context": {"provenance": [
                "SNAPSHOT_SHA256|" + "a" * 64, "DISCOVERY|artifacts/live-run-1"]},
        }
        dataset = {
            "dataset_id": "HISTORICAL_HYPOTHESIS_DATASET|" + "b" * 64,
            "dataset_sha256": "c" * 64,
            "config": {"instrument": "ETH-USD", "frequency_seconds": 86400,
                      "evaluable_period": hypothesis["constraints"]["period"]},
            "identity": {"hypothesis_id": hypothesis["hypothesis_id"],
                        "hypothesis_version": hypothesis["version"]},
        }
        selection = {"support_rows": []}
        conditions = p._experiment_conditions(hypothesis, dataset, selection)
        self.assertEqual(conditions["instrument"], "ETH-USD")


if __name__ == "__main__":
    unittest.main()
