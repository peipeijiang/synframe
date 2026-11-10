import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from synframe.preview import render_preview


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class PreviewTests(unittest.TestCase):
    def test_video_overlay_and_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, overlay, audio = root / "base.mp4", root / "over.mp4", root / "tone.wav"
            for path, color in [(base, "red"), (overlay, "blue")]:
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                                f"color={color}:s=64x64:r=10:d=2", "-c:v", "libx264", str(path)], check=True)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            "sine=frequency=440:duration=2", str(audio)], check=True)
            spec = {"width": 64, "height": 64, "fps": 10, "tracks": [
                {"name": "A", "kind": "Video", "clips": [{"path": str(base), "timeline_start_sec": 0, "duration_sec": 2}]},
                {"name": "B", "kind": "Video", "clips": [{"path": str(overlay), "timeline_start_sec": 1, "duration_sec": 1}]},
                {"name": "voice", "kind": "Audio", "clips": [{"path": str(audio), "timeline_start_sec": 0, "duration_sec": 2}]}
            ]}
            output = root / "preview.mp4"
            render_preview(spec, output)
            try:
                import opentimelineio as otio
                from synframe.timeline import export_timeline
            except ImportError:
                otio = None
            if otio is not None:
                project = root / "project.otio"
                export_timeline(spec, project)
                loaded = otio.adapters.read_from_file(str(project))
                self.assertEqual(len(loaded.tracks), 3)
                self.assertEqual(loaded.tracks[1][1].media_reference.target_url, overlay.resolve().as_uri())
            info = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)]))
            self.assertEqual({s["codec_type"] for s in info["streams"]}, {"video", "audio"})
            self.assertAlmostEqual(float(info["format"]["duration"]), 2, delta=.15)
            def frame(t):
                return subprocess.check_output(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(output), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
            first, later = frame(.5), frame(1.5)
            self.assertGreater(first[0], first[2])
            self.assertGreater(later[2], later[0])
            original = output.read_bytes()
            with self.assertRaises(ValueError):
                render_preview(spec, output)
            self.assertEqual(output.read_bytes(), original)
            too_long = {"tracks": [{"kind": "Video", "clips": [
                {"path": str(base), "timeline_start_sec": 0, "duration_sec": 20}]}]}
            with self.assertRaisesRegex(ValueError, "exceeds media duration"):
                render_preview(too_long, root / "bad.mp4")
            with_audio = {"tracks": [{"kind": "Video", "clips": [
                {"path": str(output), "timeline_start_sec": 0, "duration_sec": 1}]}]}
            with self.assertRaisesRegex(ValueError, "matching audio track"):
                render_preview(with_audio, root / "silent.mp4")
            with_audio["tracks"].append({"kind": "Audio", "clips": [
                {"path": str(audio), "timeline_start_sec": 0, "duration_sec": 1}]})
            with self.assertRaisesRegex(ValueError, "matching audio track"):
                render_preview(with_audio, root / "music-without-voice.mp4")
            with_audio["tracks"].append({"kind": "Audio", "clips": [
                {"path": str(output), "timeline_start_sec": 0, "duration_sec": 1}]})
            render_preview(with_audio, root / "voice-and-music.mp4")
