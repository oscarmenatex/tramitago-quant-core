"""Focused proof for the FUNDING_RATE_SIGN Strategy and the Etapa 2.8
auxiliary-data-source generalization it exercises for the first time
(2026-09-28): a Strategy whose required_inputs.variables name something
outside the base OHLCV set ("funding_rate") must be fused into a Hypothesis
Dataset from an externally supplied auxiliary series, independently
re-verified from its own sealed raw capture -- never from a Binance-specific
hardcoded lookup (DOC-005 PA-005-002/SS13). This test proves the
generalization itself end to end (Hypothesis -> Dataset -> Experiment ->
Result) with synthetic data, not the real market result (that is a separate,
real run).
"""

import json
import math
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pipeline as p


class FundingRateSignStrategyContractTests(unittest.TestCase):
    def test_contract_shape(self):
        strategy = p.funding_rate_sign_strategy()
        self.assertEqual(strategy["strategy_id"], "FUNDING_RATE_SIGN")
        self.assertEqual(strategy["parameters"], {})
        self.assertEqual(
            strategy["required_inputs"], {"variables": ["funding_rate"], "warmup_periods": 1})
        self.assertEqual(strategy["column_name"], "funding_rate_lag_1")
        self.assertEqual(strategy["indicator_name"], "FUNDINGSIGN")
        self.assertEqual(strategy["upper_group_description"], "FUNDINGSIGN_t > 0")
        self.assertEqual(strategy["lower_or_equal_group_description"], "FUNDINGSIGN_t <= 0")

    def test_classifies_by_prior_day_funding_rate_sign(self):
        strategy = p.funding_rate_sign_strategy()
        rows = [{"close": 100.0, "funding_rate": rate}
                for rate in [0.0001, -0.0002, 0.0, 0.0003, -0.0001]]
        classified = p._strategy_classify_rows(strategy, rows)

        self.assertIsNone(classified[0]["group"])
        self.assertIsNone(classified[0]["funding_rate_lag_1"])
        expected = ["UPPER", "LOWER_OR_EQUAL", "LOWER_OR_EQUAL", "UPPER"]
        for offset, expected_group in enumerate(expected):
            index = offset + 1
            self.assertEqual(classified[index]["group"], expected_group)
            self.assertEqual(classified[index]["funding_rate_lag_1"], rows[index - 1]["funding_rate"])

    def test_auxiliary_variables_derived_from_the_strategy_alone(self):
        self.assertEqual(p._hypothesis_dataset_auxiliary_variables(p.funding_rate_sign_strategy()),
                         ["funding_rate"])
        self.assertEqual(p._hypothesis_dataset_auxiliary_variables(p.sma_crossover_strategy(3)), [])


class FundingRateAuxiliaryGeneralizationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.hyp_registry = self.root / "hypotheses.json"

    @staticmethod
    def _candle(ts):
        day = ts // 86400
        close = 100.0 + (day % 30) + 0.37 * math.sin(day / 5.0)
        return [ts, close - 1, close + 1, close - 0.5, close, 5.0]

    def _candle_transport(self):
        def transport(url, _headers, _timeout):
            query = parse_qs(urlsplit(url).query)
            start = p.epoch(query["start"][0])
            end = p.epoch(query["end"][0])
            payload = [self._candle(t) for t in range(start, end + 86400, 86400)]
            return json.dumps(list(reversed(payload))).encode("utf-8"), {"Date": "fixture"}
        return transport

    def _funding_rate_transport(self):
        def transport(url, _headers, _timeout):
            query = parse_qs(urlsplit(url).query)
            start_ms = int(query["startTime"][0])
            end_ms = int(query["endTime"][0])
            events = []
            timestamp_ms = start_ms
            while timestamp_ms <= end_ms:
                day = (timestamp_ms // 1000) // 86400
                rate = 0.0002 if day % 2 == 0 else -0.0001
                events.append({"fundingTime": timestamp_ms, "fundingRate": f"{rate:.8f}"})
                timestamp_ms += p.BINANCE_FUNDING_RATE_INTERVAL_SECONDS * 1000
            return json.dumps(events).encode("utf-8"), {"Date": "fixture"}
        return transport

    def _funding_rate_auxiliary_source(self, capture_period):
        series, capture, raw = p.capture_binance_funding_rate(
            "BTCUSDT", capture_period["start_utc"], capture_period["end_exclusive_utc"],
            "2026-09-28T00:00:00Z", transport=self._funding_rate_transport())
        return {"series": series, "capture": capture, "raw": raw}

    def test_funding_rate_chain_reproduces_end_to_end_and_detects_tampering(self):
        strategy = p.funding_rate_sign_strategy()
        forward_column = p._hypothesis_dataset_forward_column(1)
        constraints = {
            "variables": ["funding_rate", "funding_rate_lag_1", forward_column],
            "period": {"start_utc": "2025-01-01T00:00:00Z", "end_exclusive_utc": "2026-01-01T00:00:00Z"},
            "universe": ["BTC-USD"],
        }
        acceptance_criterion = {
            "metric": "m", "comparison": "GT", "threshold": "0", "expected_direction": "INCREASE",
        }
        hypothesis = p.constitute_hypothesis(
            self.hyp_registry, description="test funding rate", target_metric="m",
            expected_direction="INCREASE", constraints=constraints,
            acceptance_criterion=acceptance_criterion, creation_timestamp="2026-09-28T00:00:00Z",
            status="CONSTITUTED", created_by="test",
            provenance=["SNAPSHOT_SHA256|" + "a" * 64, "DISCOVERY|artifacts/live-run-1"],
            system_version="0.1.0", code_revision="0" * 40)

        capture_period = p._hypothesis_dataset_config(hypothesis, strategy, 1)["capture_period"]
        auxiliary_sources = {"funding_rate": self._funding_rate_auxiliary_source(capture_period)}
        auxiliary_verifiers = {"funding_rate": p.verified_binance_funding_rate_capture}

        output = self.root / "dataset"
        manifest = p.create_hypothesis_dataset(
            self.hyp_registry, hypothesis["hypothesis_id"], 1, output,
            transport=self._candle_transport(), acquired_at="2026-09-28T00:00:00Z",
            strategy=strategy, auxiliary_sources=auxiliary_sources)
        self.assertIn("funding_rate", manifest["columns"])
        self.assertIn("funding_rate_lag_1", manifest["columns"])
        self.assertIn("funding_rate_capture_sha256", manifest["identity"]["hashes"])
        reloaded = p.verified_hypothesis_dataset(
            output, self.hyp_registry, strategy=strategy, auxiliary_verifiers=auxiliary_verifiers)
        self.assertEqual(reloaded, manifest)

        exp_registry = self.root / "experiments.json"
        exp_record = p.constitute_experiment_conditions(
            exp_registry, hypothesis_registry_path=self.hyp_registry, dataset_directory=output,
            hypothesis_id=hypothesis["hypothesis_id"], hypothesis_version=1,
            dataset_id=manifest["dataset_id"], created_at="2026-09-28T00:00:00Z",
            revision_reason="TEST_FUNDING_RATE", strategy=strategy,
            auxiliary_verifiers=auxiliary_verifiers)
        self.assertEqual(exp_record["conditions"]["indicator"]["strategy_id"], "FUNDING_RATE_SIGN")
        reloaded_exp = p.verified_experiment_conditions(
            exp_registry, exp_record["experiment_id"], 1,
            hypothesis_registry_path=self.hyp_registry, dataset_directory=output,
            auxiliary_verifiers=auxiliary_verifiers)
        self.assertEqual(reloaded_exp, exp_record)

        res_registry = self.root / "results.json"
        res_record = p.execute_experiment_result(
            res_registry, experiment_registry_path=exp_registry,
            hypothesis_registry_path=self.hyp_registry, dataset_directory=output,
            experiment_id=exp_record["experiment_id"], experiment_version=1,
            experiment_record_id=exp_record["record_id"], execution_code_revision="a" * 40,
            auxiliary_verifiers=auxiliary_verifiers)
        reloaded_res = p.verified_experiment_result(
            res_registry, res_record["result_id"], experiment_registry_path=exp_registry,
            hypothesis_registry_path=self.hyp_registry, dataset_directory=output,
            auxiliary_verifiers=auxiliary_verifiers)
        self.assertEqual(reloaded_res, res_record)

        tampered = json.loads(res_registry.read_bytes())
        tampered["results"][0]["evidence"][0]["funding_rate_lag_1"] = 0.0
        res_registry.write_bytes(p.encoded(tampered))
        with self.assertRaises(ValueError):
            p.verified_experiment_result(
                res_registry, res_record["result_id"], experiment_registry_path=exp_registry,
                hypothesis_registry_path=self.hyp_registry, dataset_directory=output,
                auxiliary_verifiers=auxiliary_verifiers)

    def test_missing_auxiliary_series_fails_closed(self):
        strategy = p.funding_rate_sign_strategy()
        forward_column = p._hypothesis_dataset_forward_column(1)
        constraints = {
            "variables": ["funding_rate", "funding_rate_lag_1", forward_column],
            "period": {"start_utc": "2025-01-01T00:00:00Z", "end_exclusive_utc": "2025-01-11T00:00:00Z"},
            "universe": ["BTC-USD"],
        }
        acceptance_criterion = {
            "metric": "m", "comparison": "GT", "threshold": "0", "expected_direction": "INCREASE",
        }
        hypothesis = p.constitute_hypothesis(
            self.hyp_registry, description="test funding rate missing aux", target_metric="m",
            expected_direction="INCREASE", constraints=constraints,
            acceptance_criterion=acceptance_criterion, creation_timestamp="2026-09-28T00:00:00Z",
            status="CONSTITUTED", created_by="test",
            provenance=["SNAPSHOT_SHA256|" + "a" * 64, "DISCOVERY|artifacts/live-run-1"],
            system_version="0.1.0", code_revision="0" * 40)
        with self.assertRaises(ValueError):
            p.create_hypothesis_dataset(
                self.hyp_registry, hypothesis["hypothesis_id"], 1, self.root / "dataset",
                transport=self._candle_transport(), acquired_at="2026-09-28T00:00:00Z",
                strategy=strategy)


if __name__ == "__main__":
    unittest.main()
