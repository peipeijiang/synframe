"""Render a basic A/B-roll preview using local ffmpeg."""
import math
import json
import subprocess
from pathlib import Path
from .output import new_output


def probe_media(path):
    try:
        raw = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                             check=True, capture_output=True, text=True)
        return json.loads(raw.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise ValueError("unable to inspect media: " + str(path)) from exc


def validate_media_range(path, kind, source, length):
    info = probe_media(path)
    stream_type = "video" if kind == "Video" else "audio"
    streams = [s for s in info.get("streams", []) if s.get("codec_type") == stream_type]
    if not streams:
        raise ValueError(f"media has no {stream_type} stream: {path}")
    value = streams[0].get("duration") or info.get("format", {}).get("duration")
    try:
        media_duration = float(value)
    except (TypeError, ValueError):
        raise ValueError("media duration unavailable: " + str(path))
    if not math.isfinite(media_duration) or source + length > media_duration + 0.05:
        raise ValueError("clip exceeds media duration: " + str(path))
    return info


def render_preview(spec, destination):
    width = int(spec.get("width", 360))
    height = int(spec.get("height", 640))
    fps = int(spec.get("fps", 30))
    if width < 2 or height < 2 or width % 2 or height % 2 or fps <= 0:
        raise ValueError("width and height must be positive even numbers; fps must be positive")
    clips = []
    audio_requirements = []
    for track in spec["tracks"]:
        kind = track.get("kind", "Video")
        if kind not in ("Video", "Audio"):
            raise ValueError("track kind must be Video or Audio")
        track_end = 0.0
        for item in sorted(track["clips"], key=lambda c: c["timeline_start_sec"]):
            path = Path(item["path"]).expanduser().resolve()
            if not path.is_file():
                raise ValueError("missing media: " + str(path))
            start = float(item["timeline_start_sec"])
            length = float(item["duration_sec"])
            source = float(item.get("source_start_sec", 0))
            if any(not math.isfinite(x) for x in (start, length, source)) or start < 0 or length <= 0 or source < 0:
                raise ValueError("invalid clip timing")
            if start < track_end - 1e-7:
                raise ValueError("overlapping clips in the same track")
            track_end = start + length
            info = validate_media_range(path, kind, source, length)
            if kind == "Video" and any(s.get("codec_type") == "audio" for s in info.get("streams", [])):
                if not item.get("mute_source_audio", False):
                    audio_requirements.append((path, start, length, source))
            clips.append((kind, path, start, length, source))
    if not clips or not any(c[0] == "Video" for c in clips):
        raise ValueError("preview requires at least one video clip")
    for path, start, length, source in audio_requirements:
        matching = any(k == "Audio" and p == path and abs(s - start) < 1e-6
                       and abs(d - length) < 1e-6 and abs(src - source) < 1e-6
                       for k, p, s, d, src in clips)
        if not matching:
            raise ValueError("video source audio requires matching audio track or mute_source_audio: " + str(path))
    total = max(c[2] + c[3] for c in clips)
    command = ["ffmpeg", "-v", "error", "-y"]
    for _, path, _, _, _ in clips:
        command.extend(["-i", str(path)])
    filters = [f"color=c=black:s={width}x{height}:r={fps}:d={total}[base0]"]
    video_count = 0
    audio_labels = []
    for index, (kind, _, start, length, source) in enumerate(clips):
        if kind == "Video":
            label = f"v{index}"
            filters.append(f"[{index}:v]trim=start={source}:duration={length},setpts=PTS-STARTPTS+{start}/TB,"
                           f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                           f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[{label}]")
            filters.append(f"[base{video_count}][{label}]overlay=eof_action=pass:"
                           f"enable='between(t,{start},{start + length})'[base{video_count + 1}]")
            video_count += 1
        else:
            label = f"a{index}"
            delay = round(start * 1000)
            filters.append(f"[{index}:a]atrim=start={source}:duration={length},asetpts=PTS-STARTPTS,"
                           f"adelay={delay}:all=1[{label}]")
            audio_labels.append(f"[{label}]")
    if audio_labels:
        filters.append("".join(audio_labels) + f"amix=inputs={len(audio_labels)}:duration=longest:normalize=0[aout]")
    command.extend(["-filter_complex", ";".join(filters), "-map", f"[base{video_count}]"])
    if audio_labels:
        command.extend(["-map", "[aout]", "-c:a", "aac"])
    with new_output(destination, ".mp4") as temporary:
        command.extend(["-t", str(total), "-r", str(fps), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(temporary)])
        try:
            subprocess.run(command, check=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError("ffmpeg preview failed: " + exc.stderr.decode("utf-8", errors="replace").strip()) from exc
    return {"path": str(destination), "duration_sec": total, "video_clips": video_count,
            "audio_clips": len(audio_labels)}
