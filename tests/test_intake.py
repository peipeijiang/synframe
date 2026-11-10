import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from synframe.intake import intake
from synframe.shots import review_shots


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class IntakeTests(unittest.TestCase):
    def test_local_evidence_and_product_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "source.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            "color=blue:s=64x64:r=10:d=1", "-c:v", "libx264", str(video)], check=True)
            out = root / "evidence"
            spec = {"video": str(video), "transcribe": False, "product":
                    {"sku": "sku-test", "facts": [{"text": "synthetic", "source": "fixture"}]}}
            result = intake(spec, out)
            self.assertEqual(result["shot_analysis"], "needs_visual_review")
            self.assertTrue((out / "source_manifest.json").is_file())
            self.assertEqual(json.loads((out / "product.json").read_text())["sku"], "sku-test")
            shots = [{"id": "s1", "start_sec": 0, "end_sec": 1, "reviewer": "agent",
                      "observation": "Synthetic solid blue frame",
                      "evidence_frame": "evidence/frames/frame_0001_t00000.000.jpg"}]
            self.assertFalse(review_shots(out, shots, 1)["automatic_truth_verification"])
            shots[0]["evidence_frame"] = "../source.mp4"
            with self.assertRaises(ValueError):
                review_shots(out, shots, 1)
            with self.assertRaises(ValueError):
                intake(spec, out)
