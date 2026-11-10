"""Validate human/agent reviewed shots against local evidence paths."""
import hashlib
import math
import json
from pathlib import Path


def review_shots(evidence_dir, shots, video_duration):
    root = Path(evidence_dir).resolve()
    manifest = json.loads((root / "evidence" / "evidence_manifest.json").read_text(encoding="utf-8"))
    frames = {entry["relative_path"]: entry["timestamp_seconds"] for entry in manifest["frames"]}
    if not shots:
        raise ValueError("shot review cannot be empty")
    previous_end = 0.0
    contiguous = True
    result = []
    for shot in shots:
        start, end = float(shot["start_sec"]), float(shot["end_sec"])
        if not all(math.isfinite(x) for x in (start, end)) or start < previous_end - 0.001 or end <= start or end > video_duration + 0.001:
            raise ValueError("invalid shot interval")
        if start > previous_end + 0.001:
            contiguous = False
        if not shot.get("observation") or shot.get("reviewer") not in ("human", "agent", "prior_analysis"):
            raise ValueError("each shot needs an observation and named review type")
        if shot["reviewer"] == "prior_analysis" and not Path(shot.get("observation_source", "")).is_file():
            raise ValueError("prior analysis must cite an existing source file")
        rel = Path(shot["evidence_frame"])
        if str(rel) not in frames or not start - 0.001 <= frames[str(rel)] <= end + 0.001:
            raise ValueError("evidence frame must be a sampled frame within the shot")
        frame = (root / rel).resolve()
        if root not in frame.parents or not frame.is_file():
            raise ValueError("evidence frame must exist inside evidence package")
        data = frame.read_bytes()
        result.append({"id": shot["id"], "start_sec": start, "end_sec": end,
                       "observation": shot["observation"], "reviewer": shot["reviewer"],
                       "observation_source": shot.get("observation_source", ""),
                       "evidence_frame": str(rel), "evidence_timestamp_sec": frames[str(rel)],
                       "evidence_sha256": hashlib.sha256(data).hexdigest(),
                       "inferences": shot.get("inferences", []), "uncertainties": shot.get("uncertainties", [])})
        previous_end = end
    if len({shot["id"] for shot in result}) != len(result):
        raise ValueError("shot IDs must be unique")
    complete = contiguous and previous_end >= video_duration - 0.001
    review_status = "prior_analysis_unverified" if any(s["reviewer"] == "prior_analysis" for s in result) else "recorded"
    return {"shots": result, "visual_review": review_status,
            "coverage": "complete" if complete else "partial",
            "reviewed_until_sec": previous_end, "source_duration_sec": video_duration,
            "automatic_truth_verification": False}
