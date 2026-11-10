import tempfile
import unittest
import subprocess
import shutil
from pathlib import Path

try:
    import opentimelineio as otio
except ImportError:
    otio = None

from synframe.timeline import export_timeline


@unittest.skipUnless(otio and shutil.which("ffmpeg") and shutil.which("ffprobe"), "optional opentimelineio and ffmpeg required")
class TimelineTests(unittest.TestCase):
    def test_round_trip_two_tracks_and_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            "color=red:s=64x64:r=10:d=4", "-c:v", "libx264", str(media)], check=True)
            output = Path(tmp) / "test.otio"
            spec = {"fps": 30, "tracks": [
                {"name": "A-roll", "kind": "Video", "clips": [
                    {"path": str(media), "timeline_start_sec": 0, "duration_sec": 3, "role": "a_roll"}]},
                {"name": "B-roll", "kind": "Video", "clips": [
                    {"path": str(media), "timeline_start_sec": 1, "duration_sec": 2, "role": "b_roll"}]}]}
            export_timeline(spec, output)
            result = otio.adapters.read_from_file(str(output))
            self.assertEqual(len(result.tracks), 2)
            self.assertIsInstance(result.tracks[1][0], otio.schema.Gap)
            self.assertEqual(result.tracks[1][1].metadata["synframe"]["role"], "b_roll")
            original = output.read_bytes()
            with self.assertRaises(ValueError):
                export_timeline(spec, output)
            self.assertEqual(output.read_bytes(), original)

    def test_missing_media_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                export_timeline({"tracks": [{"name": "x", "clips": [
                    {"path": str(Path(tmp) / "missing.mp4"), "timeline_start_sec": 0, "duration_sec": 1}]}]}, Path(tmp) / "x.otio")
