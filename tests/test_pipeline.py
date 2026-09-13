"""Offline tests; synthetic candles are test inputs, never operational evidence."""

import copy
import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import pipeline as p


def candles():
    start = p.epoch(p.CONFIG["start"])
    # Source order: timestamp, low, high, open, close, volume.
    return [[start + i * 86400, 9 + i, 12 + i, 10 + i, 11 + i, 100] for i in range(10)]


def checked(payload):
    rows, report = p.normalize(json.dumps(payload).encode())
    return rows, p.validate(rows, report)


class NormalizationTests(unittest.TestCase):
    def test_source_mapping_types_and_utc_sort(self):
        source = list(reversed(candles()))
        before = copy.deepcopy(source)
        rows, report = checked(source)
        self.assertEqual(source, before)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(rows[0]["timestamp"], "2024-01-01T00:00:00Z")
        self.assertEqual([rows[0][k] for k in ("open", "high", "low", "close", "volume")],
                         [10., 12., 9., 11., 100.])
        self.assertIsInstance(rows[0]["close"], float)

    def test_extra_boundary_candles_are_explicitly_excluded(self):
        source = candles()
        source += [[p.epoch(p.CONFIG["end_exclusive"]), 9, 12, 10, 11, 100]]
        _, report = checked(source)
        self.assertEqual((report["received"], report["accepted"], report["excluded_outside_range"]), (11, 10, 1))
        self.assertEqual(report["status"], "PASS")

    def test_bad_json_and_error_objects(self):
        for raw in (b"<html>failure</html>", b'{"message":"rate limit"}', b'null', b'\xff'):
            with self.subTest(raw=raw):
                rows, report = p.normalize(raw)
                self.assertEqual(p.validate(rows, report)["status"], "FAIL")

    def test_missing_nonnumeric_and_nonfinite_fields(self):
        for value in (None, "11", True, float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value):
                source = candles()
                source[0][4] = value
                _, report = checked(source)
                self.assertEqual(report["status"], "FAIL")
                self.assertEqual(report["rejected"], 1)
                self.assertEqual(report["accepted"], 0)

    def test_incorrect_arity_and_timestamp(self):
        for item in ([1, 2], {"time": 1}, [1704067201, 9, 12, 10, 11, 100],
                     [1704067200.5, 9, 12, 10, 11, 100], [1e100, 9, 12, 10, 11, 100]):
            with self.subTest(item=item):
                _, report = checked([item] + candles()[1:])
                self.assertEqual(report["status"], "FAIL")


class ValidationTests(unittest.TestCase):
    def test_empty_and_missing_session(self):
        for source in ([], candles()[1:], candles()[:4] + candles()[5:]):
            with self.subTest(source=source):
                _, report = checked(source)
                self.assertEqual(report["status"], "FAIL")
                self.assertEqual(report["accepted"], 0)

class ObservationTests(unittest.TestCase):
    def raw_candles(self, count):
        return json.dumps(candles()[:count]).encode()

    def test_identity_and_incremental_resume_are_persistent(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            state = root / "state.json"
            first = p.observe(root / "input", state, root / "first",
                              raw=self.raw_candles(1),
                              now=datetime(2024, 1, 3, tzinfo=timezone.utc))
            second = p.observe(root / "input", state, root / "second",
                               raw=self.raw_candles(2),
                               now=datetime(2024, 1, 4, tzinfo=timezone.utc))
            self.assertEqual(first["new_observations"][0]["identity"],
                             "BTC-USD|86400|2024-01-01T00:00:00Z")
            self.assertEqual(second["new_observations"][0]["timestamp"],
                             "2024-01-02T00:00:00Z")
            self.assertEqual(second["last_observation_timestamp"],
                             "2024-01-02T00:00:00Z")
            self.assertEqual(json.loads(state.read_bytes())["observations"][-1]["timestamp"],
                             "2024-01-02T00:00:00Z")

    def test_unclosed_and_duplicate_observations_are_not_accepted(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            state = root / "state.json"
            first = p.observe(root / "input", state, root / "first",
                              raw=self.raw_candles(1),
                              now=datetime(2024, 1, 1, 12, tzinfo=timezone.utc))
            second = p.observe(root / "input", state, root / "second",
                               raw=self.raw_candles(1),
                               now=datetime(2024, 1, 3, tzinfo=timezone.utc))
            self.assertEqual(first["new_observations"], [])
            self.assertEqual(first["unclosed_observations"], 1)
            self.assertEqual(second["new_observations"], [
                {"identity": "BTC-USD|86400|2024-01-01T00:00:00Z",
                 "instrument": "BTC-USD", "timestamp": "2024-01-01T00:00:00Z",
                 "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0,
                 "volume": 100.0}
            ])
            repeated = p.observe(root / "input", state, root / "third",
                                 raw=self.raw_candles(1),
                                 now=datetime(2024, 1, 3, tzinfo=timezone.utc))
            self.assertEqual(repeated["new_observations"], [])
            self.assertEqual(repeated["already_processed"], 1)

    def test_invalid_observation_does_not_create_state(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            state = root / "state.json"
            invalid = json.loads(self.raw_candles(1))
            invalid[0][4] = "bad"
            with self.assertRaisesRegex(ValueError, "invalid rows"):
                p.observe(root / "input", state, root / "output",
                          raw=json.dumps(invalid).encode(),
                          now=datetime(2024, 1, 3, tzinfo=timezone.utc))
            self.assertFalse(state.exists())

    def test_same_response_and_state_are_byte_deterministic(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            first = p.observe(root / "input", root / "state-a.json", root / "a",
                              raw=self.raw_candles(2),
                              now=datetime(2024, 1, 4, tzinfo=timezone.utc))
            second = p.observe(root / "input", root / "state-b.json", root / "b",
                               raw=self.raw_candles(2),
                               now=datetime(2024, 1, 4, tzinfo=timezone.utc))
            self.assertEqual(first, second)
            self.assertEqual((root / "a" / "observation.json").read_bytes(),
                             (root / "b" / "observation.json").read_bytes())


class DecisionTests(unittest.TestCase):
    def state_with_observations(self, directory, count, closes=None):
        start = p.epoch(p.CONFIG["start"])
        closes = closes or [10, 11, 12, 11, 15]
        raw = [[start + index * 86400, close - 1, close + 1, close - 0.5, close, 1]
               for index, close in enumerate(closes[:count])]
        return p.observe(directory / "input", directory / "state.json", directory / "observation",
                         raw=json.dumps(raw).encode(),
                         now=datetime(2024, 1, 10, tzinfo=timezone.utc))

    def test_sma3_warmup_entry_exit_and_identity(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            self.state_with_observations(root, 4)
            result = p.decide(root / "state.json", root / "decision")
            decisions = result["new_decisions"]
            self.assertEqual([item["decision"] for item in decisions],
                             ["NO_DECISION", "NO_DECISION", "ENTER", "EXIT"])
            self.assertEqual([item["target_position"] for item in decisions], [None, None, 1, 0])
            self.assertEqual(decisions[2]["sma_close_3"], 11.0)
            self.assertEqual(decisions[3]["sma_close_3"], 11.333333333333334)
            self.assertEqual(decisions[2]["observation_identity"],
                             "BTC-USD|86400|2024-01-03T00:00:00Z")
            self.assertTrue(all("open" not in item for item in decisions))

    def test_equality_is_exit_and_replay_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            self.state_with_observations(root, 3, closes=[10, 11, 10.5])
            first = p.decide(root / "state.json", root / "first")
            repeated = p.decide(root / "state.json", root / "second")
            self.assertEqual(first["new_decisions"][-1]["decision"], "HOLD")
            self.assertEqual(first["new_decisions"][-1]["target_position"], 0)
            self.assertEqual(repeated["new_decisions"], [])
            self.assertEqual(repeated["already_decided"], 3)
            self.assertEqual(len(json.loads((root / "state.json").read_bytes())["decisions"]), 3)

    def test_future_observation_does_not_change_prior_decisions(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            self.state_with_observations(first, 3, closes=[10, 11, 12])
            self.state_with_observations(second, 4, closes=[10, 11, 12, 100])
            first_result = p.decide(first / "state.json", first / "decision")
            second_result = p.decide(second / "state.json", second / "decision")
            self.assertEqual(first_result["new_decisions"], second_result["new_decisions"][:3])

    def test_same_observations_produce_identical_persisted_decisions(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            self.state_with_observations(first, 4)
            self.state_with_observations(second, 4)
            first_result = p.decide(first / "state.json", first / "decision")
            second_result = p.decide(second / "state.json", second / "decision")
            self.assertEqual(first_result, second_result)
            self.assertEqual((first / "state.json").read_bytes(), (second / "state.json").read_bytes())

    def test_duplicate_even_if_identical(self):
        source = candles()
        _, report = checked(source + [source[0]])
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(any(e["reason"] == "Duplicate timestamp" for e in report["errors"]))

    def test_prices_volume_and_ohlc(self):
        for field, value in ((1, -1), (2, 0), (2, 9.5), (3, 30), (4, 8), (5, -1)):
            with self.subTest(field=field, value=value):
                source = candles()
                source[0][field] = value
                _, report = checked(source)
                self.assertEqual(report["status"], "FAIL")
                self.assertEqual(report["rejected"], 1)

    def test_zero_volume_is_valid_without_inventing_prices(self):
        source = candles()
        source[0][5] = 0
        self.assertEqual(checked(source)[1]["status"], "PASS")


class VirtualExecutionTests(unittest.TestCase):
    def prepare(self, root, count, closes=None):
        DecisionTests().state_with_observations(root, count, closes=closes)
        p.decide(root / "state.json", root / "decisions")
        return root / "state.json"

    def test_enter_and_exit_use_next_open_and_transition_binary_position(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.prepare(root, 5)
            result = p.execute_virtual(state, root / "execution")
            self.assertEqual([(item["action"], item["price"]) for item in result["new_executions"]],
                             [("ENTER", 10.5), ("EXIT", 14.5)])
            self.assertEqual([(item["virtual_position_before"], item["virtual_position_after"])
                              for item in result["new_executions"]], [(0, 1), (1, 0)])
            self.assertTrue(all(item["price"] != 11.0 for item in result["new_executions"]))
            self.assertEqual(result["virtual_position"], 0)

    def test_hold_and_warmup_do_not_create_pending_actions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.prepare(root, 3, closes=[10, 11, 10.5])
            result = p.execute_virtual(state, root / "execution")
            self.assertEqual(result["new_executions"], [])
            self.assertEqual(result["pending_actions"], [])

    def test_missing_next_open_keeps_action_pending_and_replay_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.prepare(root, 3, closes=[10, 11, 12])
            first = p.execute_virtual(state, root / "first")
            second = p.execute_virtual(state, root / "second")
            self.assertEqual(first["new_executions"], [])
            self.assertEqual(len(first["pending_actions"]), 1)
            self.assertEqual(second["new_executions"], [])
            self.assertEqual(len(second["pending_actions"]), 1)

    def test_execution_persistence_and_determinism(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            state_a = self.prepare(first, 5)
            state_b = self.prepare(second, 5)
            result_a = p.execute_virtual(state_a, first / "execution")
            result_b = p.execute_virtual(state_b, second / "execution")
            self.assertEqual(result_a, result_b)
            self.assertEqual(state_a.read_bytes(), state_b.read_bytes())
            persisted = json.loads(state_a.read_bytes())
            self.assertEqual(len(persisted["executions"]), 2)
            self.assertEqual(len({item["identity"] for item in persisted["executions"]}), 2)

    def test_open_event_is_separate_and_executes_without_full_ohlcv(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.prepare(root, 3, closes=[10, 11, 12])
            pending = p.execute_virtual(state, root / "pending")
            self.assertEqual(len(pending["pending_actions"]), 1)
            event = {
                "identity": "BTC-USD|86400|2024-01-04T00:00:00Z",
                "instrument": "BTC-USD",
                "frequency_seconds": 86400,
                "timestamp": "2024-01-04T00:00:00Z",
                "open": 25.0,
                "closed": False,
            }
            captured = p.capture_open_event(state, event, root / "open-event")
            result = p.execute_virtual(state, root / "execution")
            self.assertEqual(captured["open_event"], event)
            self.assertEqual(result["new_executions"][0]["price"], 25.0)
            self.assertEqual(result["new_executions"][0]["execution_observation_identity"], event["identity"])
            self.assertEqual(result["virtual_position"], 1)
            persisted = json.loads(state.read_bytes())
            self.assertEqual(persisted["observations"][-1]["timestamp"], "2024-01-03T00:00:00Z")
            self.assertEqual(persisted["open_events"][0], event)

    def test_invalid_or_repeated_open_event_does_not_duplicate_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.prepare(root, 3, closes=[10, 11, 12])
            p.execute_virtual(state, root / "pending")
            event = {"identity": "BTC-USD|86400|2024-01-04T00:00:00Z",
                     "instrument": "BTC-USD", "frequency_seconds": 86400,
                     "timestamp": "2024-01-04T00:00:00Z", "open": 25.0, "closed": False}
            p.capture_open_event(state, event, root / "open-event")
            first = p.execute_virtual(state, root / "first")
            second = p.execute_virtual(state, root / "second")
            self.assertEqual(len(first["new_executions"]), 1)
            self.assertEqual(second["new_executions"], [])
            self.assertEqual(len(json.loads(state.read_bytes())["executions"]), 1)

    def test_incompatible_open_event_is_rejected_without_state_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.prepare(root, 3, closes=[10, 11, 12])
            p.execute_virtual(state, root / "pending")
            before = state.read_bytes()
            event = {"identity": "BTC-USD|3600|2024-01-04T00:00:00Z",
                     "instrument": "BTC-USD", "frequency_seconds": 3600,
                     "timestamp": "2024-01-04T00:00:00Z", "open": 25.0, "closed": False}
            with self.assertRaisesRegex(ValueError, "incomplete or incompatible"):
                p.capture_open_event(state, event, root / "open-event")
            self.assertEqual(state.read_bytes(), before)


class EvaluationTests(unittest.TestCase):
    def evaluation_input(self, directory, *, status="PASS", closes=None):
        start = p.epoch(p.CONFIG["start"])
        closes = closes or [10, 11, 12, 11, 12, 12]
        opens = [9, 10, 20, 8, 7, 6]
        rows = [{
            "instrument": "BTC-USD",
            "timestamp": p.iso(start + index * 86400),
            "open": float(opening),
            "high": float(max(opening, close)),
            "low": float(min(opening, close)),
            "close": float(close),
            "volume": 1.0,
            "_source_row": index + 1,
        } for index, (opening, close) in enumerate(zip(opens, closes))]
        dataset = p.dataset_bytes(p.indicators(rows))
        (directory / "dataset.csv").write_bytes(dataset)
        (directory / "manifest.json").write_bytes(p.encoded({
            "status": status,
            "columns": p.COLUMNS,
            "schema": p.SCHEMA,
            "dataset_sha256": p.digest(dataset),
        }))

    def test_sma3_execution_position_and_final_liquidation(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as output:
            source_dir = Path(source)
            output_dir = Path(output) / "evaluation"
            self.evaluation_input(source_dir)
            result = p.evaluate(source_dir, output_dir)
            self.assertEqual([item["signal"] for item in result["signals"]],
                             [None, None, "BUY", "SELL", "BUY", None])
            self.assertEqual(result["executions"], [
                {"timestamp": "2024-01-04T00:00:00Z", "action": "BUY", "price": 8.0},
                {"timestamp": "2024-01-05T00:00:00Z", "action": "SELL", "price": 7.0},
                {"timestamp": "2024-01-06T00:00:00Z", "action": "BUY", "price": 6.0},
                {"timestamp": "2024-01-06T00:00:00Z", "action": "LIQUIDATE", "price": 12.0},
            ])
            self.assertEqual([item["position"] for item in result["positions"]], [0, 0, 0, 1, 0, 0])
            self.assertEqual(result["trades"][0]["gross_return"], -0.125)
            self.assertEqual(result["trades"][1]["gross_return"], 1.0)

    def test_evaluation_rejects_non_pass_manifest(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as output:
            source_dir = Path(source)
            self.evaluation_input(source_dir, status="FAIL")
            with self.assertRaisesRegex(ValueError, "PASS manifest"):
                p.evaluate(source_dir, Path(output))

    def test_evaluation_is_byte_deterministic(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            source_dir = Path(source)
            self.evaluation_input(source_dir)
            p.evaluate(source_dir, Path(first) / "evaluation")
            p.evaluate(source_dir, Path(second) / "evaluation")
            self.assertEqual((Path(first) / "evaluation" / "evaluation.json").read_bytes(),
                             (Path(second) / "evaluation" / "evaluation.json").read_bytes())

    def test_future_data_does_not_change_prior_trajectory(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_dir = Path(first)
            second_dir = Path(second)
            self.evaluation_input(first_dir)
            self.evaluation_input(second_dir, closes=[10, 11, 12, 11, 12, 30])
            first_result = p.evaluate(first_dir, first_dir / "evaluation")
            second_result = p.evaluate(second_dir, second_dir / "evaluation")
            self.assertEqual(first_result["signals"][:5], second_result["signals"][:5])
            self.assertEqual(first_result["executions"][:2], second_result["executions"][:2])
            self.assertEqual(first_result["positions"][:5], second_result["positions"][:5])

    def test_metrics_are_derived_and_persisted_without_changing_trajectory(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as output:
            source_dir = Path(source)
            result_dir = Path(output) / "evaluation"
            self.evaluation_input(source_dir)
            result = p.evaluate(source_dir, result_dir)
            self.assertEqual(result["metrics"], {
                "cumulative_compounded_return": 0.75,
                "trade_count": 2,
                "winning_trades": 1,
                "losing_trades": 1,
                "win_rate": 0.5,
            })
            persisted = json.loads((result_dir / "evaluation.json").read_bytes())
            self.assertEqual(persisted["metrics"], result["metrics"])
            self.assertEqual(persisted["signals"], result["signals"])
            self.assertEqual(persisted["executions"], result["executions"])
            self.assertEqual(persisted["positions"], result["positions"])
            self.assertEqual(persisted["trades"], result["trades"])

    def test_metrics_classify_zero_return_and_empty_trades(self):
        zero_trade = [{"gross_return": 0.0}]
        self.assertEqual(p._metrics(zero_trade), {
            "cumulative_compounded_return": 0.0,
            "trade_count": 1,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": 0.0,
        })
        self.assertEqual(p._metrics([]), {
            "cumulative_compounded_return": 0.0,
            "trade_count": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": None,
        })

    def test_validator_rejects_nonfinite_normalized_prices(self):
        rows, report = p.normalize(json.dumps(candles()).encode())
        rows[0]["close"] = float("nan")
        self.assertEqual(p.validate(rows, report)["status"], "FAIL")


class IndicatorTests(unittest.TestCase):
    def test_known_values_and_initial_nulls(self):
        rows, _ = checked(candles())
        result = p.indicators(rows)
        self.assertEqual([r["sma_close_3"] for r in result], [None, None, 12., 13., 14., 15., 16., 17., 18., 19.])
        self.assertNotIn("sma_close_3", rows[0])

    def test_no_future_information_and_repeatability(self):
        rows, _ = checked(candles())
        original = p.indicators(rows)
        self.assertEqual(original, p.indicators(rows))
        rows[-1]["close"] = 999
        self.assertEqual(original[:-1], p.indicators(rows)[:-1])


class PipelineTests(unittest.TestCase):
    def setUp(self):
        Path("artifacts").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir="artifacts")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.capture = self.root / "input"
        p.save_capture(json.dumps(list(reversed(candles()))).encode(), self.capture,
                       kind="synthetic-test", acquired_at="synthetic fixture; no acquisition")

    def test_full_flow_second_process_replay_and_persisted_comparison(self):
        first, second = self.root / "first", self.root / "second"
        # Network is unavailable by construction; replay must use the saved bytes.
        with patch("pipeline.urlopen", side_effect=AssertionError("Network forbidden")):
            p.run(self.capture, first)
        invocation = subprocess.run([sys.executable, "-B", "pipeline.py", "run", "--input", str(first),
                                     "--output", str(second)], capture_output=True, text=True)
        self.assertEqual(invocation.returncode, 0, invocation.stderr)
        comparison = p.compare(first, second)
        self.assertEqual(comparison["status"], "PASS")
        self.assertTrue(all(comparison["checks"].values()))
        manifest = p.verified_manifest(first)
        self.assertEqual(manifest["input_kind"], "synthetic-test")
        self.assertEqual(manifest["rows"], 10)
        self.assertEqual(manifest["range"], ["2024-01-01T00:00:00Z", "2024-01-10T00:00:00Z"])
        stored = list(csv.DictReader(io.StringIO((first / "dataset.csv").read_text())))
        self.assertEqual(list(stored[0]), p.COLUMNS)
        self.assertEqual(stored[0]["sma_close_3"], "")
        self.assertEqual(float(stored[-1]["sma_close_3"]), 19.)
        report = json.loads((first / "validation.json").read_bytes())
        self.assertEqual((report["accepted"], report["rejected"], report["errors"]), (10, 0, []))

    def test_same_output_is_idempotent_without_rewriting(self):
        output = self.root / "run"
        p.run(self.capture, output)
        before = {f.name: (f.read_bytes(), f.stat().st_mtime_ns) for f in output.iterdir()}
        p.run(self.capture, output)
        self.assertEqual(before, {f.name: (f.read_bytes(), f.stat().st_mtime_ns) for f in output.iterdir()})

    def test_tampered_capture_and_wrong_configuration_are_rejected(self):
        raw_path = self.capture / "raw.json"
        original = raw_path.read_bytes()
        raw_path.write_bytes(b"[]")
        with self.assertRaisesRegex(ValueError, "mismatch"):
            p.run(self.capture, self.root / "run")
        raw_path.write_bytes(original)
        metadata_path = self.capture / "capture.json"
        metadata = json.loads(metadata_path.read_bytes())
        metadata["config"]["instrument"] = "ETH-USD"
        metadata_path.write_bytes(p.encoded(metadata))
        with self.assertRaisesRegex(ValueError, "mismatch"):
            p.run(self.capture, self.root / "run")

    def test_invalid_batch_persists_failure_without_dataset(self):
        bad = self.root / "bad"
        p.save_capture(json.dumps(candles()[:-1]).encode(), bad, kind="synthetic-test", acquired_at="fixture")
        output = self.root / "failed"
        with self.assertRaisesRegex(ValueError, "rejected"):
            p.run(bad, output)
        self.assertFalse((output / "dataset.csv").exists())
        self.assertFalse((output / "manifest.json").exists())
        self.assertEqual(json.loads((output / "validation.json").read_bytes())["status"], "FAIL")

    def test_corrupt_artifact_is_not_reported_reproducible(self):
        first, second = self.root / "first", self.root / "second"
        p.run(self.capture, first)
        p.run(self.capture, second)
        (second / "dataset.csv").write_text("corrupted", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "integrity"):
            p.compare(first, second)
        with self.assertRaisesRegex(ValueError, "different"):
            p.run(self.capture, second)

    def test_network_failure_is_separate_and_retains_evidence(self):
        output = self.root / "network-failure"
        with patch("pipeline.urlopen", side_effect=OSError("network unavailable")):
            with self.assertRaises(OSError):
                p.acquire(output)
        failure = json.loads((output / "acquisition_failure.json").read_bytes())
        self.assertEqual(failure["status"], "FAIL")
        self.assertFalse((output / "raw.json").exists())

    def test_comparison_detects_changed_valid_input(self):
        other = self.root / "other-input"
        source = candles()
        source[-1][5] = 101
        p.save_capture(json.dumps(source).encode(), other, kind="synthetic-test", acquired_at="fixture")
        first, second = self.root / "first", self.root / "second"
        p.run(self.capture, first)
        p.run(other, second)
        self.assertEqual(p.compare(first, second)["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
