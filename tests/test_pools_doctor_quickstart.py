import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from synframe.doctor import check, summary
from synframe.pools import load
from synframe.sequence import SequenceError

ROOT = Path(__file__).resolve().parents[1]


class PoolsTests(unittest.TestCase):
    def test_aliases_and_asr_words(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "m").mkdir()
            (tmp / "m" / "a.mp4").write_bytes(b"x")
            (tmp / "asr.json").write_text(json.dumps({"segments": [{"words": [
                {"word": " hi", "start": 0.1, "end": 0.4}, {"word": " late", "start": 5.0, "end": 5.3}]}]}))
            (tmp / "pools.json").write_text(json.dumps({
                "media": {"a": "m/a.mp4"}, "asr": {"a": "asr.json"},
                "hooks": [{"id": "h", "shots": [{"src": "@a", "src_in": 0, "dur": 1}]}],
                "bodies": [{"id": "b", "voice": [{"src": "@a", "in": 0, "out": 1, "asr": "@a"}],
                            "shots": [{"src": "m/a.mp4", "src_in": 0, "dur": 1}]}],
                "ctas": []}))
            pools = load(tmp / "pools.json")
            voice = pools["bodies"][0]["voice"][0]
            self.assertEqual([w["word"] for w in voice["words"]], [" hi"])
            self.assertNotIn("asr", voice)
            self.assertTrue(Path(pools["hooks"][0]["shots"][0]["src"]).is_absolute())

    def test_unknown_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            path.write_text(json.dumps({"hooks": [{"id": "h", "shots": [{"src": "@nope", "dur": 1}]}]}))
            with self.assertRaises(SequenceError):
                load(path)


class DoctorTests(unittest.TestCase):
    def test_report_shape_and_no_secret(self):
        items = check()
        names = {i["name"] for i in items}
        self.assertIn("ffmpeg", names)
        self.assertTrue(any(n.startswith("companion: ") for n in names))
        self.assertIn("ready", summary(items))
        import os
        key = os.environ.get("LK888_API_KEY")
        if key:
            self.assertNotIn(key, json.dumps(items))


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg required")
class QuickstartTests(unittest.TestCase):
    def test_offline_render_and_qc(self):
        with tempfile.TemporaryDirectory() as tmp:
            qs = Path(tmp) / "quickstart"
            shutil.copytree(ROOT / "examples" / "quickstart", qs,
                            ignore=shutil.ignore_patterns("media", "out"))
            subprocess.run([sys.executable, str(qs / "make_media.py")], check=True, capture_output=True)
            run = Path(tmp) / "run"
            env = {"cwd": str(ROOT), "check": True, "capture_output": True, "text": True}
            subprocess.run([sys.executable, "-m", "synframe", "render", str(qs / "pools.json"), "--output", str(run)],
                           **env)
            out = subprocess.run([sys.executable, "-m", "synframe", "qc", str(run), "--no-transcribe"], **env)
            self.assertEqual(json.loads(out.stdout)["summary"], {"partial": 3})
            report = json.loads((run / "qc" / "technical.json").read_text())
            for video in report["videos"]:
                self.assertEqual(video["checks"]["critical_terms_present"], "not_run")
                self.assertTrue(video["checks"]["duration_in_window"])
            self.assertEqual(report["diversity"]["flagged"], [])
            again = subprocess.run([sys.executable, "-m", "synframe", "render", str(qs / "pools.json"),
                                    "--output", str(run)], cwd=str(ROOT), capture_output=True, text=True)
            self.assertNotEqual(again.returncode, 0, "render must refuse an existing run directory")


if __name__ == "__main__":
    unittest.main()
