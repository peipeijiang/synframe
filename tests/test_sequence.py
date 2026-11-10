import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from synframe.sequence import SequenceError, plan, to_tracks
from synframe.fastcut import CutError, captions, hide_under_texts, measure, render, validate


def seg(sid, dur, voice=None, persona="P1", model="minimax-h3", **extra):
    out = {"id": sid, "shots": [{"src": "/x/%s.mp4" % sid, "src_in": 0, "dur": dur}]}
    if voice:
        out["voice"] = [{"src": "/x/%s.mp4" % sid, "in": 0, "out": voice, "line": sid}]
        out["persona"] = persona
        out["model"] = model
    out.update(extra)
    return out


def pools(**rules):
    return {
        "sku": "S1", "locale": "es-MX", "rules": dict({"target_sec": 15, "min_sec": 13.5, "max_sec": 15.5}, **rules),
        "hooks": [seg("H1", 2, mode="overlay", fits=["ceiling"]),
                  seg("H2", 2, mode="overlay", fits=["wear"]),
                  seg("H3", 2.5, voice=2.4, mode="prepend"),
                  seg("H4", 2, mode="overlay", fits=["nothing"])],
        "bodies": [seg("B1", 11, voice=10.8, opens=["ceiling"]), seg("B2", 9.5, voice=9.4, opens=["wear", "ceiling"])],
        "ctas": [seg("C1", 2, voice=2, pad={"src": "/x/pad.mp4", "src_in": 0}),
                 seg("C2", 4.5, voice=4.4)],
    }


class SequenceTests(unittest.TestCase):
    def test_unique_hooks_and_fits(self):
        result = plan(pools(), count=4)
        hooks = [v["manifest"]["hook"] for v in result["videos"]]
        self.assertEqual(len(hooks), len(set(hooks)))
        self.assertNotIn("H4", hooks)
        self.assertEqual(result["skipped_hooks"][0]["hook"], "H4")
        self.assertEqual(result["produced"], 3)
        self.assertIsNotNone(result["shortfall_reason"])
        for v in result["videos"]:
            self.assertTrue(13.5 <= v["total_sec"] <= 15.5, v["total_sec"])
            if v["manifest"]["hook_mode"] == "overlay":
                body = next(b for b in pools()["bodies"] if b["id"] == v["manifest"]["body"])
                hook = next(h for h in pools()["hooks"] if h["id"] == v["manifest"]["hook"])
                self.assertTrue(set(hook["fits"]) & set(body["opens"]))

    def test_overlay_keeps_body_voice_and_covers_picture(self):
        result = plan(pools(), count=3)
        v = next(x for x in result["videos"] if x["manifest"]["hook"] == "H1")
        self.assertEqual(v["voice"][0]["at"], 0)
        self.assertEqual(v["shots"][0]["segment"], "H1")
        self.assertEqual(v["shots"][1]["start"], 2)
        self.assertEqual(v["shots"][1]["src_in"], 2)

    def test_prepend_shifts_body_and_pads_end(self):
        result = plan(pools(), count=3)
        v = next(x for x in result["videos"] if x["manifest"]["hook"] == "H3")
        body_voice = [x for x in v["voice"] if x["segment"] != "H3"][0]
        self.assertEqual(body_voice["at"], 2.5)
        t = 0
        for s in v["shots"]:
            self.assertAlmostEqual(s["start"], t, places=3)
            t = s["end"]
        self.assertAlmostEqual(t, v["total_sec"], places=3)

    def test_persona_mismatch_rejected(self):
        p = pools()
        p["hooks"] = [seg("H9", 2.5, voice=2.4, persona="P2", mode="prepend")]
        result = plan(p, count=1)
        self.assertEqual(result["produced"], 0)

    def test_same_persona_other_model_rejected(self):
        p = pools()
        p["hooks"] = [seg("H9", 2.5, voice=2.4, model="omni_flash-10s", mode="prepend")]
        self.assertEqual(plan(p, count=1)["produced"], 0)

    def test_voiced_segment_needs_model(self):
        p = pools()
        del p["bodies"][0]["model"]
        with self.assertRaises(SequenceError):
            plan(p)

    def test_hook_cap_never_repeats_hook_body(self):
        result = plan(pools(hook_cap=2), count=5)
        seen = [(v["manifest"]["hook"], v["manifest"]["body"]) for v in result["videos"]]
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(result["produced"], 5)
        self.assertLessEqual(max(result["segment_usage"][h] for h in ("H1", "H2", "H3")), 2)

    def test_listen_checklist_and_tracks(self):
        result = plan(pools(), count=3)
        kinds = {(c["from"], c["to"]): c["kind"] for c in result["listen_checklist"]}
        v = next(x for x in result["videos"] if x["manifest"]["hook"] == "H3")
        self.assertEqual(kinds[("H3", v["manifest"]["body"])], "audio+picture")
        tracks = to_tracks(v)
        picture, voice = tracks["tracks"]
        self.assertEqual(len(picture["clips"]), len(v["shots"]))
        self.assertEqual(voice["kind"], "Audio")
        self.assertEqual(voice["clips"][1]["timeline_start_sec"], 2.5)

    def test_reuse_caps(self):
        p = pools(body_cap=1, cta_cap=1)
        result = plan(p, count=3)
        bodies = [v["manifest"]["body"] for v in result["videos"]]
        self.assertEqual(len(bodies), len(set(bodies)))
        self.assertLessEqual(result["produced"], 2)

    def test_invalid_pools(self):
        p = pools()
        p["hooks"][0]["voice"] = [{"src": "/x", "in": 0, "out": 1}]
        p["hooks"][0]["persona"] = "P1"
        with self.assertRaises(SequenceError):
            plan(p)
        p = pools()
        p["bodies"][0]["voice"][0]["out"] = 12
        with self.assertRaises(SequenceError):
            plan(p)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class FastcutTests(unittest.TestCase):
    def test_captions_hidden_while_big_text_is_on_screen(self):
        caps = [{"start": 0.0, "end": 1.0, "text": "APAGA LA LUZ"}, {"start": 2.4, "end": 3.0, "text": "12 LUCES."},
                {"start": 5.0, "end": 5.6, "text": "CARRITO"}]
        texts = [{"start": 0.0, "end": 2.2, "text": "MIRA ARRIBA"}, {"start": 2.6, "end": 4.8, "text": "12 LUCES"}]
        self.assertEqual([c["text"] for c in hide_under_texts(caps, texts)], ["CARRITO"])
        self.assertEqual(len(hide_under_texts(caps, [])), 3)

    def make(self, root, name, color, freq):
        path = root / name
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color={color}:s=96x160:r=12:d=4",
                        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=4", "-shortest",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)
        return str(path)

    def test_render_and_lipsync_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b = self.make(root, "a.mp4", "red", 440), self.make(root, "b.mp4", "blue", 660)
            spec = {"total_sec": 3.0, "style_index": 1,
                    "voice": [{"src": a, "in": 1.0, "out": 3.5, "at": 0.0,
                               "words": [{"word": "Hola", "start": 1.1, "end": 1.5}, {"word": "mundo.", "start": 1.6, "end": 2.0}]}],
                    "shots": [{"start": 0, "end": 1.0, "src": b, "src_in": 0, "zoom": 1.0},
                              {"start": 1.0, "end": 2.0, "src": a, "src_in": 2.0, "lipsync": True, "zoom": 1.1},
                              {"start": 2.0, "end": 3.0, "src": b, "src_in": 1.0, "zoom": 1.0}],
                    "texts": [{"start": 0, "end": 1.0, "text": "UNO\nDOS"}]}
            self.assertEqual(validate(spec)["voice_sec"], 2.5)
            caps = captions(spec)
            self.assertEqual(caps[0]["text"], "HOLA MUNDO.")
            out = root / "cut.mp4"
            render(spec, out, width=96, height=160, fps=12)
            m = measure(out)
            self.assertAlmostEqual(m["duration_sec"], 3.0, delta=0.15)
            self.assertEqual(m["streams"], ["audio", "video"])
            with self.assertRaises(ValueError):
                render(spec, out, width=96, height=160, fps=12)
            bad = json.loads(json.dumps(spec))
            bad["shots"][1]["src_in"] = 2.5
            with self.assertRaises(CutError):
                validate(bad)
            bad = json.loads(json.dumps(spec))
            bad["shots"][1]["start"] = 1.1
            with self.assertRaises(CutError):
                validate(bad)


if __name__ == "__main__":
    unittest.main()
