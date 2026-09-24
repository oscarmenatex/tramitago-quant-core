"""Focused proof for M2.1-T1 Hypothesis provenance and immutability."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pipeline as p


class M21T1HypothesisTests(unittest.TestCase):
    SYSTEM_VERSION_V1 = "0.0.0-synthetic"
    CODE_REVISION_V1 = "a" * 40
    SYSTEM_VERSION_V2 = "0.0.0-synthetic.2"
    CODE_REVISION_V2 = "b" * 64

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.registry_path = self.root / "research" / "hypotheses.json"

    def values(self, **overrides):
        values = {
            "description": "Higher three-day close momentum predicts a higher forward return.",
            "target_metric": "forward_return_5d",
            "expected_direction": "INCREASE",
            "constraints": {
                "variables": ["close", "sma_close_3"],
                "period": {
                    "start_utc": "2024-01-01T00:00:00Z",
                    "end_exclusive_utc": "2024-02-01T00:00:00Z",
                },
                "universe": ["SYNTHETIC-BTC-USD"],
            },
            "acceptance_criterion": {
                "metric": "forward_return_5d",
                "comparison": "GE",
                "threshold": "0.010",
                "expected_direction": "INCREASE",
            },
            "creation_timestamp": "2026-09-22T12:00:00Z",
            "status": "CONSTITUTED",
            "created_by": "M2.1-T1 synthetic test",
            "provenance": ["SYNTHETIC|M2.1-T1|case-001"],
            "system_version": self.SYSTEM_VERSION_V1,
            "code_revision": self.CODE_REVISION_V1,
        }
        values.update(overrides)
        return values

    def constitute(self, **overrides):
        return p.constitute_hypothesis(self.registry_path, **self.values(**overrides))

    def revise(self, hypothesis_id, **overrides):
        return p.revise_hypothesis(
            self.registry_path, hypothesis_id, **self.values(**overrides))

    def canonical_record_bytes(self, record):
        return (json.dumps(record, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
            "utf-8")

    def expected_record_id(self, record):
        content = {key: value for key, value in record.items() if key != "record_id"}
        content_bytes = (json.dumps(
            content, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
        return ("HYPOTHESIS_VERSION|" + record["hypothesis_id"] + "|"
                + str(record["version"]) + "|"
                + hashlib.sha256(content_bytes).hexdigest())

    def test_constitutes_and_reloads_a_sealed_version_with_explicit_provenance(self):
        record = self.constitute()
        reloaded = p.load_hypothesis(self.registry_path, record["hypothesis_id"], 1)

        self.assertTrue(record["hypothesis_id"].startswith("HYPOTHESIS|"))
        self.assertEqual(reloaded["version"], 1)
        self.assertEqual(reloaded["description"], self.values()["description"])
        self.assertEqual(reloaded["constraints"], self.values()["constraints"])
        self.assertEqual(reloaded["acceptance_criterion"], self.values()["acceptance_criterion"])
        self.assertEqual(reloaded["system_version"], self.SYSTEM_VERSION_V1)
        self.assertEqual(reloaded["code_revision"], self.CODE_REVISION_V1)
        self.assertEqual(reloaded["creation_context"], {
            "created_by": self.values()["created_by"],
            "provenance": self.values()["provenance"],
        })
        self.assertEqual(reloaded["record_id"], self.expected_record_id(reloaded))

    def test_rejects_missing_or_ambiguous_criterion_before_persisting(self):
        cases = (
            ("missing", None),
            ("unknown-comparison", {
                "metric": "forward_return_5d", "comparison": "ABOUT",
                "threshold": "0.010", "expected_direction": "INCREASE",
            }),
            ("missing-threshold", {
                "metric": "forward_return_5d", "comparison": "GE",
                "expected_direction": "INCREASE",
            }),
        )
        for name, criterion in cases:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "acceptance criterion"):
                self.constitute(acceptance_criterion=criterion)
            self.assertFalse(self.registry_path.exists())

    def test_rejects_invalid_system_provenance_before_persisting(self):
        for field in ("system_version", "code_revision"):
            values = self.values()
            del values[field]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "system provenance"):
                p.constitute_hypothesis(self.registry_path, **values)
            self.assertFalse(self.registry_path.exists())

        cases = (
            ("invalid-semver", {"system_version": "v0.0.0"}),
            ("sha-as-system-version", {"system_version": "a" * 40}),
            ("abbreviated-code-revision", {"code_revision": "a" * 12}),
            ("branch-as-code-revision", {"code_revision": "main"}),
            ("tag-as-code-revision", {"code_revision": "v0.0.0"}),
        )
        for name, overrides in cases:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "system provenance"):
                self.constitute(**overrides)
            self.assertFalse(self.registry_path.exists())

    def test_rejects_incompatible_fields_and_incomplete_scientific_conditions(self):
        incompatible_metric = {
            "metric": "sharpe", "comparison": "GE", "threshold": "1.0",
            "expected_direction": "INCREASE",
        }
        incompatible_direction = {
            "metric": "forward_return_5d", "comparison": "LE", "threshold": "0.0",
            "expected_direction": "INCREASE",
        }
        no_universe = dict(self.values()["constraints"])
        no_universe["universe"] = []
        for name, overrides in (
                ("metric", {"acceptance_criterion": incompatible_metric}),
                ("direction", {"acceptance_criterion": incompatible_direction}),
                ("universe", {"constraints": no_universe}),
                ("provenance", {"provenance": []})):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.constitute(**overrides)
            self.assertFalse(self.registry_path.exists())

    def test_reload_recovers_exact_provenance_and_seal_in_a_new_process(self):
        created = self.constitute()
        worker = (
            "import json, sys; import pipeline as p; "
            "print(json.dumps(p.load_hypothesis(sys.argv[1], sys.argv[2], int(sys.argv[3])), sort_keys=True))"
        )
        process = subprocess.run(
            [sys.executable, "-B", "-c", worker, str(self.registry_path),
             created["hypothesis_id"], "1"],
            capture_output=True, text=True, timeout=30)

        self.assertEqual(process.returncode, 0, process.stderr)
        reloaded = json.loads(process.stdout)
        self.assertEqual(reloaded["hypothesis_id"], created["hypothesis_id"])
        self.assertEqual(reloaded["version"], 1)
        self.assertEqual(reloaded["description"], self.values()["description"])
        self.assertEqual(reloaded["acceptance_criterion"], self.values()["acceptance_criterion"])
        self.assertEqual(reloaded["system_version"], self.SYSTEM_VERSION_V1)
        self.assertEqual(reloaded["code_revision"], self.CODE_REVISION_V1)
        self.assertEqual(reloaded["record_id"], self.expected_record_id(reloaded))

    def test_revision_preserves_v1_canonical_content_criterion_and_seal(self):
        first = self.constitute()
        v1_before = p.load_hypothesis(self.registry_path, first["hypothesis_id"], 1)
        v1_canonical_before = self.canonical_record_bytes(v1_before)
        v1_seal_before = v1_before["record_id"]
        criterion = dict(self.values()["acceptance_criterion"], threshold="0.020")
        second = self.revise(
            first["hypothesis_id"], acceptance_criterion=criterion,
            creation_timestamp="2026-09-22T12:05:00Z",
            provenance=["SYNTHETIC|M2.1-T1|case-002"],
            system_version=self.SYSTEM_VERSION_V2,
            code_revision=self.CODE_REVISION_V2)
        v1_after = p.load_hypothesis(self.registry_path, first["hypothesis_id"], 1)

        self.assertEqual(second["hypothesis_id"], first["hypothesis_id"])
        self.assertEqual(second["version"], 2)
        self.assertEqual(second["acceptance_criterion"], criterion)
        self.assertEqual(second["system_version"], self.SYSTEM_VERSION_V2)
        self.assertEqual(second["code_revision"], self.CODE_REVISION_V2)
        self.assertEqual(v1_after["acceptance_criterion"], v1_before["acceptance_criterion"])
        self.assertEqual(self.canonical_record_bytes(v1_after), v1_canonical_before)
        self.assertEqual(v1_after["record_id"], v1_seal_before)
        self.assertEqual(v1_after["record_id"], self.expected_record_id(v1_after))

    def test_conflicts_and_persisted_provenance_alterations_fail_closed(self):
        first = self.constitute()
        registry = json.loads(self.registry_path.read_bytes())
        valid_registry = json.loads(json.dumps(registry))
        registry["hypotheses"].append(dict(first))
        self.registry_path.write_bytes(p.encoded(registry))
        duplicate_before = self.registry_path.read_bytes()

        with self.assertRaisesRegex(ValueError, "version conflict"):
            p.load_hypothesis(self.registry_path, first["hypothesis_id"], 1)
        self.assertEqual(self.registry_path.read_bytes(), duplicate_before)

        for field, replacement in (
                ("system_version", "0.0.0-synthetic-altered"),
                ("code_revision", "c" * 40)):
            with self.subTest(field=field):
                altered = json.loads(json.dumps(valid_registry))
                altered["hypotheses"][0][field] = replacement
                self.registry_path.write_bytes(p.encoded(altered))
                altered_before = self.registry_path.read_bytes()

                with self.assertRaisesRegex(ValueError, "registry is invalid"):
                    p.load_hypothesis(self.registry_path, first["hypothesis_id"], 1)
                self.assertEqual(self.registry_path.read_bytes(), altered_before)

    def test_rejects_invalid_revision_provenance_without_partial_persistence(self):
        first = self.constitute()
        before = self.registry_path.read_bytes()

        with self.assertRaisesRegex(ValueError, "system provenance"):
            self.revise(first["hypothesis_id"], code_revision="not-a-full-git-id")
        self.assertEqual(self.registry_path.read_bytes(), before)

    def test_unknown_identity_or_version_never_selects_a_fallback_record(self):
        first = self.constitute()
        for hypothesis_id, version in (
                ("HYPOTHESIS|00000000-0000-4000-8000-000000000000", 1),
                (first["hypothesis_id"], 2),
                (first["hypothesis_id"], 0)):
            with self.subTest(hypothesis_id=hypothesis_id), self.assertRaises(ValueError):
                p.load_hypothesis(self.registry_path, hypothesis_id, version)


if __name__ == "__main__":
    unittest.main()
