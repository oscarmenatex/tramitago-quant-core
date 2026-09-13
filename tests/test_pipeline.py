"""Offline tests; synthetic candles are test inputs, never operational evidence."""

import copy
import csv
import io
import json
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
