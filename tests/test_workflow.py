import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from synframe.workflow import run
from synframe.planning import plan

try:
    import opentimelineio
except ImportError:
    opentimelineio = None


@unittest.skipUnless(opentimelineio and shutil.which("ffmpeg"), "OTIO and ffmpeg required")
class WorkflowTests(unittest.TestCase):
    def test_thirty_seconds_rendered_for_three_model_caps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / 'sample.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                            'color=blue:s=64x64:r=5:d=15', '-c:v', 'libx264', str(video)], check=True)
            units = [{'id': f'L{i}', 'duration_sec': 3, 'text': f'line {i}'} for i in range(10)]
            for cap, expected_count in [(10, 4), (15, 2), (6, 5)]:
                with self.subTest(cap=cap):
                    planned = plan(units, cap)
                    self.assertEqual(len(planned['clips']), expected_count)
                    piece_clips, timeline_clips = [], []
                    cursor = 0
                    for i, clip in enumerate(planned['clips']):
                        duration = clip['used_duration_sec']
                        piece_clips.append({'id': f'c{i}', 'sku': 's', 'role': 'a_roll',
                                            'covers': clip['unit_ids']})
                        timeline_clips.append({'clip_id': f'c{i}', 'path': str(video),
                                               'timeline_start_sec': cursor, 'duration_sec': duration})
                        cursor += duration
                    spec = {'intake': {'video': str(video), 'transcribe': False,
                                       'product': {'sku': 's', 'facts': [{'text': 'fixture', 'source': 'synthetic'}]}},
                            'units': units, 'max_duration_sec': cap,
                            'piece': {'sku': 's', 'dialogue_ids': [u['id'] for u in units], 'clips': piece_clips},
                            'timeline': {'width': 64, 'height': 64, 'fps': 5, 'tracks': [
                                {'name': 'main', 'kind': 'Video', 'clips': timeline_clips}]}}
                    result = run(spec, root / f'cap-{cap}')
                    self.assertEqual(result['status'], 'technical_handoff_ready', result)
                    self.assertEqual([x for c in result['stages']['plan']['clips'] for x in c['unit_ids']],
                                     [u['id'] for u in units])
                    self.assertTrue(all(c['generation_duration_sec'] <= cap for c in result['stages']['plan']['clips']))
                    probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format', '-of',
                                                                'json', str(root / f'cap-{cap}/preview.mp4')]))
                    self.assertAlmostEqual(float(probe['format']['duration']), 30, delta=.2)

    def test_local_handoff_and_sku_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "video.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            "color=red:s=64x64:r=10:d=2", "-c:v", "libx264", str(video)], check=True)
            spec = {
                "intake": {"video": str(video), "transcribe": False,
                           "product": {"sku": "s", "facts": [{"text": "fixture", "source": "synthetic"}]}},
                "units": [{"id": "L1", "duration_sec": 2, "text": "hola"}],
                "max_duration_sec": 10,
                "piece": {"sku": "s", "dialogue_ids": ["L1"],
                          "clips": [{"id": "c1", "sku": "s", "role": "a_roll", "covers": ["L1"]}]},
                "timeline": {"width": 64, "height": 64, "fps": 10, "tracks": [
                    {"name": "main", "kind": "Video", "clips": [
                        {"clip_id": "c1", "path": str(video), "timeline_start_sec": 0, "duration_sec": 2}]}]},
            }
            result = run(spec, root / "run")
            self.assertEqual(result["status"], "technical_handoff_ready", result)
            self.assertEqual(result["creative_qc"], "needs_human_review")
            self.assertTrue((root / "run/preview.mp4").is_file())
            self.assertTrue((root / "run/edit.otio").is_file())
            project = opentimelineio.adapters.read_from_file(str(root / "run/edit.otio"))
            self.assertEqual(project.tracks[0][0].metadata["synframe"]["sku"], "s")
            self.assertEqual(list(project.tracks[0][0].metadata["synframe"]["covers"]), ["L1"])
            spec["piece"]["sku"] = "other"
            mismatch = run(spec, root / "bad")
            self.assertEqual(mismatch["status"], "incomplete")
            self.assertIn("SKU", mismatch["error"])
            spec["piece"]["sku"] = "s"
            spec["units"][0]["duration_sec"] = 8
            short = run(spec, root / "too-short")
            self.assertEqual(short["status"], "incomplete")
            self.assertIn("timeline duration", short["error"])
            spec["timeline"]["tracks"].append({"name": "audio", "kind": "Audio", "clips": [
                {"clip_id": "c1", "path": str(video), "timeline_start_sec": 0, "duration_sec": 8}]})
            gap = run(spec, root / "long-audio")
            self.assertEqual(gap["status"], "incomplete")
            self.assertIn("video ends", gap["error"])
            spec["piece"]["clips"].append({"id": "c2", "sku": "s", "role": "b_roll", "covers": []})
            spec["timeline"]["tracks"][0]["clips"].append(
                {"clip_id": "c2", "path": str(video), "timeline_start_sec": 0, "duration_sec": 1})
            spec["timeline"]["tracks"][0]["clips"][0]["timeline_start_sec"] = 1
            reverse = run(spec, root / "reverse-order")
            self.assertEqual(reverse["status"], "incomplete")
            self.assertIn("video order", reverse["error"])

    def test_thirty_second_multi_clip_reuses_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "ten.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            "color=blue:s=64x64:r=5:d=10", "-c:v", "libx264", str(video)], check=True)
            clips = [{"id": f"c{i}", "sku": "s", "role": "a_roll" if i == 0 else "b_roll",
                      "persona": "p1" if i == 0 else "", "covers": [f"L{i}"]} for i in range(3)]
            pieces = [{"clip_id": f"c{i}", "path": str(video), "timeline_start_sec": i * 10,
                       "duration_sec": 10} for i in range(3)]
            spec = {
                "intake": {"video": str(video), "transcribe": False,
                           "product": {"sku": "s", "facts": [{"text": "fixture", "source": "synthetic"}]}},
                "units": [{"id": f"L{i}", "duration_sec": 10, "text": f"line {i}"} for i in range(3)],
                "model_profile": {"model": "omni-flash", "channel": "lk888"},
                "piece": {"sku": "s", "personas": ["p1"], "primary_persona": "p1",
                          "single_person_continuity": True, "dialogue_ids": ["L0", "L1", "L2"], "clips": clips},
                "timeline": {"width": 64, "height": 64, "fps": 5,
                             "tracks": [{"name": "main", "kind": "Video", "clips": pieces}]},
            }
            result = run(spec, root / "long-run")
            self.assertEqual(result["status"], "technical_handoff_ready", result)
            self.assertEqual(len(result["stages"]["plan"]["clips"]), 3)
            probe = json.loads(subprocess.check_output(
                ["ffprobe", "-v", "error", "-show_format", "-of", "json", str(root / "long-run/preview.mp4")]))
            self.assertAlmostEqual(float(probe["format"]["duration"]), 30, delta=.2)
