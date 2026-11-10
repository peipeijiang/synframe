"""Export an OpenTimelineIO project with separate editable tracks."""

from pathlib import Path
from .output import new_output
from .preview import validate_media_range
import math


def export_timeline(spec, destination):
    try:
        import opentimelineio as otio
    except ImportError as exc:
        raise RuntimeError("install opentimelineio in the Synframe environment to export") from exc
    rate = float(spec.get("fps", 30))
    if rate <= 0:
        raise ValueError("fps must be positive")
    timeline = otio.schema.Timeline(name=spec.get("name", "Synframe"))
    for track_data in spec["tracks"]:
        kind = track_data.get("kind", "Video")
        if kind not in ("Video", "Audio"):
            raise ValueError("track kind must be Video or Audio")
        track = otio.schema.Track(name=track_data["name"], kind=kind)
        position = 0.0
        for item in sorted(track_data["clips"], key=lambda c: c["timeline_start_sec"]):
            path = Path(item["path"]).expanduser().resolve()
            if not path.is_file():
                raise ValueError("missing media: " + str(path))
            start = float(item["timeline_start_sec"])
            duration = float(item["duration_sec"])
            source_start = float(item.get("source_start_sec", 0))
            if any(not math.isfinite(x) for x in (start, duration, source_start)) or start < position - 1e-7 or duration <= 0 or source_start < 0:
                raise ValueError("invalid or overlapping clips in track")
            validate_media_range(path, kind, source_start, duration)
            if start > position + 1e-7:
                gap = otio.schema.Gap()
                gap.source_range = otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0, rate),
                    duration=otio.opentime.RationalTime((start - position) * rate, rate))
                track.append(gap)
            clip = otio.schema.Clip(name=item.get("name", path.name))
            clip.media_reference = otio.schema.ExternalReference(target_url=path.as_uri())
            clip.source_range = otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(source_start * rate, rate),
                duration=otio.opentime.RationalTime(duration * rate, rate))
            clip.metadata["synframe"] = {
                "role": item.get("role", ""), "sku": item.get("sku", ""),
                "persona": item.get("persona", ""), "locale": item.get("locale", ""),
                "clip_id": item.get("clip_id", ""), "covers": item.get("covers", []),
                "dialogue_id": item.get("dialogue_id", ""),
            }
            track.append(clip)
            position = start + duration
        timeline.tracks.append(track)
    with new_output(destination, ".otio") as temporary:
        otio.adapters.write_to_file(timeline, str(temporary))
    return {"path": str(destination), "tracks": len(timeline.tracks), "format": "otio"}
