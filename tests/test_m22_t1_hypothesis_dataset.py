"""Focused proof for M2.2-T1 historical Hypothesis dataset constitution."""

import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

import pipeline as p


class M22T1HistoricalDatasetTests(unittest.TestCase):
    HYPOTHESIS_ID = "HYPOTHESIS|3345b100-db15-4aa2-976d-dbd797cbc4a3"
    START = "2025-01-01T00:00:00Z"
    END = "2026-01-01T00:00:00Z"
    CAPTURE_START = "2024-12-30T00:00:00Z"
    CAPTURE_END = "2026-01-02T00:00:00Z"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.registry = self.root / "hypotheses.json"
        self.hypothesis = self._write_hypothesis()

    def _write_hypothesis(self):
        record = {
            "hypothesis_id": self.HYPOTHESIS_ID,
            "version": 1,
            "system_version": "0.1.0",
            "code_revision": "0" * 40,
            "description": "Daily BTC-USD SMA3 condition predicts a higher next-day return.",
            "target_metric": "mean_forward_return_1d_difference",
            "expected_direction": "INCREASE",
            "constraints": {
                "variables": ["close", "sma_close_3", "forward_return_1d"],
                "period": {"start_utc": self.START, "end_exclusive_utc": self.END},
                "universe": ["BTC-USD"],
            },
            "acceptance_criterion": {
                "metric": "mean_forward_return_1d_difference",
                "comparison": "GT",
                "threshold": "0",
                "expected_direction": "INCREASE",
            },
            "creation_timestamp": "2026-09-24T00:00:00Z",
            "status": "CONSTITUTED",
            "created_by": "test",
            "provenance": ["fixture"],
        }
        content = p._hypothesis_record(
            record["hypothesis_id"], record["version"], record["system_version"],
            record["code_revision"], record["description"], record["target_metric"],
            record["expected_direction"], record["constraints"], record["acceptance_criterion"],
            record["creation_timestamp"], record["status"], {
                "created_by": record["created_by"], "provenance": record["provenance"]})
        self.registry.write_bytes(p.encoded({"schema_version": "2", "hypotheses": [content]}))
        return content

    @staticmethod
    def _candle(timestamp):
        day = timestamp // 86400
        close = 40_000.0 + (day % 401)
        return [timestamp, close - 3, close + 4, close - 1, close, 10.0]

    def _transport(self, mutation=None):
        def transport(url, _headers, _timeout):
            query = parse_qs(urlsplit(url).query)
            start = p.epoch(query["start"][0])
            end = p.epoch(query["end"][0])
            payload = [self._candle(stamp) for stamp in range(start, end + 86400, 86400)]
            if mutation is not None:
                payload = mutation(payload, start, end)
            return json.dumps(list(reversed(payload))).encode("utf-8"), {"Date": "fixture"}
        return transport

    def create(self, output, mutation=None):
        return p.create_hypothesis_dataset(
            self.registry, self.HYPOTHESIS_ID, 1, output,
            transport=self._transport(mutation), acquired_at="2026-09-24T00:00:00Z")

    def test_constitutes_reloads_and_marks_exact_support_rows(self):
        output = self.root / "dataset"
        manifest = self.create(output)
        reloaded = p.verified_hypothesis_dataset(output, self.registry)
        rows = list(csv.DictReader(io.StringIO((output / "dataset.csv").read_text())))
        selection = json.loads((output / "selection.json").read_bytes())
        audit = json.loads((output / "audit.json").read_bytes())

        self.assertEqual(reloaded, manifest)
        self.assertTrue(manifest["dataset_id"].startswith("HISTORICAL_HYPOTHESIS_DATASET|"))
        self.assertNotIn(b"\r\n", (output / "pipeline_snapshot.py").read_bytes())
        self.assertEqual(manifest["rows"], 368)
        self.assertEqual(manifest["range"], [self.CAPTURE_START, "2026-01-01T00:00:00Z"])
        self.assertEqual(selection["hypothesis_id"], self.HYPOTHESIS_ID)
        self.assertEqual(selection["hypothesis_version"], 1)
        self.assertEqual(selection["evaluable_period"], {
            "start_utc": self.START, "end_exclusive_utc": self.END})
        self.assertEqual(selection["evaluable_row_count"], 365)
        self.assertEqual(selection["support_rows"], [
            {"timestamp": "2024-12-30T00:00:00Z", "row_role": "SUPPORT_SMA3_WARMUP"},
            {"timestamp": "2024-12-31T00:00:00Z", "row_role": "SUPPORT_SMA3_WARMUP"},
            {"timestamp": "2026-01-01T00:00:00Z", "row_role": "SUPPORT_FORWARD_RETURN"},
        ])
        evaluations = [row for row in rows if row["row_role"] == "EVALUATION"]
        self.assertEqual(len(evaluations), 365)
        self.assertTrue(all(row["sma_close_3"] and row["forward_return_1d"] for row in evaluations))
        self.assertEqual(audit["status"], "VERIFIED")
        self.assertFalse(audit["metric_evaluated"])
        self.assertFalse(audit["backtest_executed"])

    def test_rejects_incomplete_duplicate_and_temporally_incompatible_capture(self):
        target = p.epoch("2025-06-01T00:00:00Z")
        cases = {
            "incomplete": lambda payload, _start, _end: [row for row in payload if row[0] != target],
            "duplicate": lambda payload, _start, _end: payload + [payload[0]],
            "outside": lambda payload, start, _end: payload + [self._candle(start - 86400)],
        }
        for name, mutation in cases.items():
            with self.subTest(name=name):
                output = self.root / name
                with self.assertRaisesRegex(ValueError, "rejected"):
                    self.create(output, mutation)
                self.assertTrue((output / "validation.json").exists())
                self.assertFalse((output / "dataset.csv").exists())
                self.assertFalse((output / "manifest.json").exists())

    def test_integrity_rejects_tampered_dataset_or_capture(self):
        for name in ("dataset.csv", "capture.json"):
            with self.subTest(name=name):
                output = self.root / name.replace(".", "-")
                self.create(output)
                (output / name).write_bytes(b"tampered")
                with self.assertRaisesRegex(ValueError, "integrity"):
                    p.verified_hypothesis_dataset(output, self.registry)


if __name__ == "__main__":
    unittest.main()
