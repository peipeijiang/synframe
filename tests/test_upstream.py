import os
import unittest
from pathlib import Path
from unittest.mock import patch

from synframe.upstream import validate


class UpstreamTests(unittest.TestCase):
    def test_unknown_skill_rejected(self):
        with self.assertRaises(ValueError):
            validate("../unknown", "/tmp/no-file")

    @unittest.skipUnless(os.environ.get("NODE_PATH"), "AJV test dependency not configured")
    def test_segment_synthetic_fixture(self):
        root = Path(__file__).resolve().parents[1]
        bundle = root / "vendor/100x/skills/100x-segment/evals/example-01-energy-staged-timeline.json"
        self.assertTrue(validate("100x-segment", bundle)["valid"])
