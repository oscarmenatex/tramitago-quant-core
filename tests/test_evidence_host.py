"""Only one machine may seal evidence.

The VM was four commits behind, so its Finding registry still had a Finding OPEN.
A research runner was executed there, promoted it, sealed its own Hypothesis, and
left both registries modified. The next pull refused to merge, the fix for a
refused merge is to discard local changes, and one `git checkout --` later the
record existed nowhere.
"""

import tempfile
import unittest
from pathlib import Path

from tramitago_quant_core.governance.evidence_host import (
    EVIDENCE_HOST_MARKER, evidence_host_marker_path, is_evidence_host,
    require_evidence_host,
)


class EvidenceHostTests(unittest.TestCase):
    def test_a_checkout_without_the_marker_is_not_the_evidence_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(is_evidence_host(tmp))

    def test_the_marker_designates_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence_host_marker_path(tmp).write_text("", encoding="utf-8")
            self.assertTrue(is_evidence_host(tmp))
            self.assertTrue(require_evidence_host(tmp))

    def test_it_raises_rather_than_warning(self):
        # A warning on a long-running script scrolls past, and the cost of being
        # wrong is a sealed record written where maintenance will delete it.
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as caught:
                require_evidence_host(tmp)
            self.assertIn("not the evidence host", str(caught.exception))
            self.assertIn(EVIDENCE_HOST_MARKER, str(caught.exception))

    def test_the_refusal_says_how_to_designate_this_machine(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as caught:
                require_evidence_host(tmp)
            self.assertIn("touch", str(caught.exception))

    def test_a_directory_named_like_the_marker_does_not_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence_host_marker_path(tmp).mkdir()
            self.assertFalse(is_evidence_host(tmp))

    def test_the_marker_is_gitignored_so_it_cannot_travel_with_a_clone(self):
        # Which is precisely the failure mode it exists to stop.
        root = Path(__file__).resolve().parents[1]
        self.assertIn(EVIDENCE_HOST_MARKER,
                      (root / ".gitignore").read_text(encoding="utf-8"))

    def test_every_research_runner_refuses_before_doing_anything(self):
        root = Path(__file__).resolve().parents[1]
        runners = sorted((root / "scripts" / "research").glob("run_*.py"))
        self.assertGreater(len(runners), 10)
        for path in runners:
            text = path.read_text(encoding="utf-8")
            self.assertIn("require_evidence_host(REPO)", text, path.name)
            # First statement of main(), so nothing is captured or written first.
            body = text.split("\ndef main():\n", 1)[1]
            statements = [line.strip() for line in body.splitlines()
                          if line.strip() and not line.strip().startswith(("#", '"""', "'''"))]
            self.assertEqual(statements[0], "require_evidence_host(REPO)", path.name)


if __name__ == "__main__":
    unittest.main()
