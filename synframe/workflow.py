"""Local handoff workflow from provided evidence, script and media."""
import json
import copy
from pathlib import Path

from .continuity import validate_piece
from .intake import intake
from .planning import plan_from_data
from .preview import render_preview
from .timeline import export_timeline
from .speech import compare
from .shots import review_shots


def run(spec, output):
    root = Path(output).expanduser().resolve()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ValueError("workflow output directory must be absent or empty")
    root.mkdir(parents=True, exist_ok=True)
    result = {"stages": {}, "status": "incomplete", "creative_qc": "needs_human_review"}

    def persist():
        (root / "run.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        result["stages"]["intake"] = intake(spec["intake"], root / "intake")
        if spec["intake"].get("transcribe", True) and not result["stages"]["intake"].get("transcript"):
            raise ValueError("local transcription failed; see intake report")
        if "shot_review" in spec:
            review = spec["shot_review"]
            source_manifest = json.loads((root / "intake" / "source_manifest.json").read_text(encoding="utf-8"))
            result["stages"]["shot_review"] = review_shots(
                root / "intake", review["shots"], source_manifest["duration_seconds"])
        if "speech_review" in spec:
            review = spec["speech_review"]
            transcript_path = review.get("transcript_path")
            if transcript_path == "intake":
                transcript_path = result["stages"]["intake"].get("transcript")
            if not transcript_path:
                raise ValueError("speech review requires an explicit transcript")
            transcript = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
            words = [w for segment in transcript.get("segments", []) for w in segment.get("words", [])]
            result["stages"]["speech_review"] = compare(
                review["expected"], transcript["text"], review["locale"],
                review.get("critical_terms", []), words)
        result["stages"]["plan"] = plan_from_data(spec)
        if spec["piece"].get("sku") != spec["intake"]["product"]["sku"]:
            raise ValueError("piece SKU differs from product dossier")
        dialogue_ids = spec["piece"].get("dialogue_ids", [])
        if dialogue_ids and set(dialogue_ids) != {u["id"] for u in spec["units"]}:
            raise ValueError("script units do not match piece dialogue IDs")
        result["stages"]["continuity"] = validate_piece(spec["piece"])
        if result["stages"]["continuity"]["status"] != "structural_pass":
            raise ValueError("piece continuity requires correction")
        timeline = copy.deepcopy(spec["timeline"])
        piece_clips = {clip["id"]: clip for clip in spec["piece"]["clips"]}
        present_video = set()
        timeline_end = 0.0
        video_intervals = []
        first_start = {}
        for track in timeline["tracks"]:
            for clip in track["clips"]:
                clip_id = clip.get("clip_id")
                if clip_id not in piece_clips:
                    raise ValueError("timeline clip lacks valid piece clip_id")
                if clip.get("sku") and clip["sku"] != spec["piece"]["sku"]:
                    raise ValueError("timeline SKU differs from piece")
                if clip.get("persona") and clip["persona"] != piece_clips[clip_id].get("persona"):
                    raise ValueError("timeline persona differs from piece")
                if clip.get("role") and clip["role"] != piece_clips[clip_id].get("role"):
                    raise ValueError("timeline role differs from piece")
                if "covers" in clip and clip["covers"] != piece_clips[clip_id].get("covers", []):
                    raise ValueError("timeline dialogue coverage differs from piece")
                clip.setdefault("sku", spec["piece"]["sku"])
                clip.setdefault("persona", piece_clips[clip_id].get("persona", ""))
                clip.setdefault("role", piece_clips[clip_id].get("role", ""))
                clip.setdefault("covers", piece_clips[clip_id].get("covers", []))
                clip.setdefault("locale", spec.get("speech_review", {}).get("locale", spec["intake"].get("language", "")))
                if track.get("kind", "Video") == "Video":
                    present_video.add(clip_id)
                    start = float(clip["timeline_start_sec"])
                    end = start + float(clip["duration_sec"])
                    video_intervals.append((start, end))
                    first_start[clip_id] = min(first_start.get(clip_id, start), start)
                timeline_end = max(timeline_end, float(clip["timeline_start_sec"]) + float(clip["duration_sec"]))
        required_video = {clip["id"] for clip in spec["piece"]["clips"]
                          if clip.get("role") in ("a_roll", "b_roll")}
        if not required_video.issubset(present_video):
            raise ValueError("planned video clips missing from timeline")
        if abs(timeline_end - result["stages"]["plan"]["target_duration_sec"]) > 0.05:
            raise ValueError("timeline duration does not match script plan")
        ordered_ids = [clip["id"] for clip in spec["piece"]["clips"] if clip["id"] in required_video]
        if any(first_start[a] > first_start[b] + 0.05 for a, b in zip(ordered_ids, ordered_ids[1:])):
            raise ValueError("timeline video order differs from approved piece")
        covered_until = 0.0
        for start, end in sorted(video_intervals):
            if start > covered_until + 0.05:
                raise ValueError("timeline has uncovered video gap")
            covered_until = max(covered_until, end)
        if covered_until < result["stages"]["plan"]["target_duration_sec"] - 0.05:
            raise ValueError("timeline video ends before script plan")
        result["stages"]["timeline"] = export_timeline(timeline, root / "edit.otio")
        result["stages"]["preview"] = render_preview(timeline, root / "preview.mp4")
        result["status"] = "technical_handoff_ready"
    except Exception as exc:
        result["error"] = str(exc)
    persist()
    return result
