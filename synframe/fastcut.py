"""Render a fast-cut spec (shots, voice, texts) with ffmpeg. Never overwrites."""
import json
import math
import os
import re
import subprocess
from pathlib import Path

from .output import new_output
from .preview import probe_media

FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/Library/Fonts/Arial Black.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/ariblk.ttf",
)
STYLES = [
    {"big": ("white", "black"), "cap": ("white", "black")},
    {"big": ("yellow", "black"), "cap": ("white", "black")},
    {"big": ("white", "0xE0218A"), "cap": ("yellow", "black")},
    {"big": ("0x00F0FF", "black"), "cap": ("white", "black")},
]
EPS = 1e-6


class CutError(ValueError):
    pass


def resolve_font():
    """SYNFRAME_FONT wins; otherwise the first bold font found. CJK text needs a CJK font in SYNFRAME_FONT."""
    chosen = os.environ.get("SYNFRAME_FONT")
    if chosen:
        if not Path(chosen).is_file():
            raise CutError("SYNFRAME_FONT does not exist: " + chosen)
        return chosen
    return next((c for c in FONT_CANDIDATES if Path(c).is_file()), None)


def _duration(path, cache):
    if path not in cache:
        info = probe_media(path)
        cache[path] = float(info.get("format", {}).get("duration", 0))
    return cache[path]


def validate(spec):
    total = float(spec["total_sec"])
    cache, t = {}, 0.0
    for shot in spec["shots"]:
        if abs(shot["start"] - t) > 0.002:
            raise CutError("shots must tile the timeline; gap or overlap at %.3f" % shot["start"])
        dur = shot["end"] - shot["start"]
        if dur <= 0:
            raise CutError("empty shot at %.3f" % shot["start"])
        src = Path(shot["src"])
        if not src.is_file():
            raise CutError("missing media: " + str(src))
        if shot["src_in"] < 0 or shot["src_in"] + dur > _duration(str(src), cache) + 0.05:
            raise CutError("shot exceeds media: %s at %.3f" % (src.name, shot["start"]))
        if shot.get("zoom", 1.0) < 1.0:
            raise CutError("zoom must be >= 1")
        t = shot["end"]
    if abs(t - total) > 0.002:
        raise CutError("shots end at %.3f, expected %.3f" % (t, total))
    end = 0.0
    for v in sorted(spec["voice"], key=lambda v: v["at"]):
        src = Path(v["src"])
        if not src.is_file():
            raise CutError("missing media: " + str(src))
        length = v["out"] - v["in"]
        if v["at"] < end - 0.002:
            raise CutError("voice overlaps at %.3f" % v["at"])
        if v["out"] > _duration(str(src), cache) + 0.05:
            raise CutError("voice exceeds media: " + src.name)
        end = v["at"] + length
    if end > total + 0.002:
        raise CutError("voice runs past the end (%.3f > %.3f)" % (end, total))
    # A lip-sync shot must show the voice that is playing, at the same source time.
    for shot in spec["shots"]:
        if not shot.get("lipsync"):
            continue
        ok = False
        for v in spec["voice"]:
            v_end = v["at"] + v["out"] - v["in"]
            if v["src"] == shot["src"] and v["at"] - 0.05 <= shot["start"] and shot["end"] <= v_end + 0.05:
                offset = v["in"] - v["at"]
                ok = abs((shot["src_in"] - shot["start"]) - offset) <= 0.02
                break
        if not ok:
            raise CutError("lip-sync shot not aligned with its voice at %.3f (%s)" % (shot["start"], shot.get("note", "")))
    return {"total_sec": total, "voice_sec": round(end, 3), "shots": len(spec["shots"])}


def captions(spec, max_words=3, max_chars=16):
    """Chunk ASR word timings of each voice span into short timed captions."""
    out = []
    for v in spec["voice"]:
        words = [w for w in (v.get("words") or []) if v["in"] - 0.05 <= w["start"] and w["end"] <= v["out"] + 0.05]
        chunk = []
        def flush():
            if chunk:
                s = v["at"] + max(0.0, chunk[0]["start"] - v["in"])
                e = v["at"] + min(v["out"], chunk[-1]["end"] + 0.12) - v["in"]
                out.append({"start": round(s, 3), "end": round(e, 3),
                            "text": " ".join(w["word"].strip() for w in chunk).upper(), "style": "cap"})
                chunk.clear()
        for w in words:
            text = w["word"].strip()
            if chunk and (len(chunk) >= max_words or len(" ".join(x["word"].strip() for x in chunk)) + len(text) + 1 > max_chars):
                flush()
            chunk.append(w)
            if re.search(r"[.,!?]$", text):
                flush()
        flush()
    out.sort(key=lambda c: c["start"])
    for a, b in zip(out, out[1:]):
        a["end"] = min(a["end"], b["start"])
    return [c for c in out if c["end"] - c["start"] > 0.05]


def hide_under_texts(caps, texts, margin=0.05):
    """Drop captions that overlap a big text block, so the screen never shows the same words twice."""
    spans = [(float(x["start"]) - margin, float(x["end"]) + margin) for x in texts]
    return [c for c in caps if not any(c["start"] < e and c["end"] > s for s, e in spans)]


def _fit(line, size, width):
    # Arial Black caps average about 0.72 em; shrink so the longest line fits with margins.
    need = len(line) * size * 0.72
    return size if need <= width * 0.9 else max(24, int(width * 0.9 / (len(line) * 0.72)))


def render(spec, destination, width=768, height=1344, fps=24, font=None, with_captions=True):
    info = validate(spec)
    font = font or resolve_font()
    if not font and (spec.get("texts") or (with_captions and any(v.get("words") for v in spec["voice"]))):
        raise CutError("no font found for on-screen text; set SYNFRAME_FONT to a .ttf/.otf file")
    destination = Path(destination).expanduser().absolute()
    text_dir = destination.with_name(destination.stem + "-text")
    if text_dir.exists():
        raise CutError("text directory already exists: " + str(text_dir))
    text_dir.mkdir()
    total = float(spec["total_sec"])
    grade = spec.get("grade") or {}
    sat, con = float(grade.get("saturation", 1.2)), float(grade.get("contrast", 1.05))
    style = STYLES[int(spec.get("style_index", 0)) % len(STYLES)]
    inputs, filters, labels = [], [], []
    for i, shot in enumerate(spec["shots"]):
        inputs += ["-i", str(shot["src"])]
        dur = shot["end"] - shot["start"]
        zoom = float(shot.get("zoom", 1.0))
        zw, zh = int(width * zoom) // 2 * 2, int(height * zoom) // 2 * 2
        filters.append(f"[{i}:v]trim=start={shot['src_in']}:duration={dur:.3f},setpts=PTS-STARTPTS,"
                       f"scale={zw}:{zh}:force_original_aspect_ratio=increase,crop={width}:{height},"
                       f"fps={fps},setsar=1,eq=saturation={sat}:contrast={con}[s{i}]")
        labels.append(f"[s{i}]")
    filters.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0[vcat]")
    last, n = "vcat", 0
    overlays = list(spec.get("texts", []))
    caps = hide_under_texts(captions(spec), overlays) if with_captions else []
    for x in overlays + caps:
        kind = x.get("style", "big")
        color, border = style["cap" if kind == "cap" else "big"]
        size = 46 if kind == "cap" else 62
        rows = x["text"].split("\n")
        base_y = "h*0.70" if kind == "cap" else "h*0.12"
        # One size per block: a shorter row must not shrink alone and collapse the line step.
        fs = min(_fit(line, size, width) for line in rows)
        for row, line in enumerate(rows):
            tf = text_dir / f"t{n:03d}.txt"
            tf.write_text(line, encoding="utf-8")
            # Big text sits at 12% height and captions at 70%, so they never stack.
            enable = f"between(t,{x['start']},{x['end']})"
            filters.append(f"[{last}]drawtext=fontfile='{font}':textfile='{tf}':expansion=none:fontsize={fs}:"
                           f"fontcolor={color}:borderw={7 if kind != 'cap' else 5}:bordercolor={border}:"
                           f"x=(w-text_w)/2:y={base_y}+{row * round(fs * 1.18)}:enable='{enable}'[t{n}]")
            last = f"t{n}"
            n += 1
    base = len(spec["shots"])
    alabels = []
    for k, v in enumerate(sorted(spec["voice"], key=lambda v: v["at"])):
        inputs += ["-i", str(v["src"])]
        d = v["out"] - v["in"]
        filters.append(f"[{base + k}:a]atrim=start={v['in']}:duration={d:.3f},asetpts=PTS-STARTPTS,"
                       f"aformat=sample_rates=48000:channel_layouts=stereo,"
                       f"afade=t=in:d=0.02,afade=t=out:st={max(0.0, d - 0.03):.3f}:d=0.03,"
                       f"adelay={round(v['at'] * 1000)}:all=1[a{k}]")
        alabels.append(f"[a{k}]")
    if not alabels:
        raise CutError("cut needs at least one voice span")
    filters.append("".join(alabels) + f"amix=inputs={len(alabels)}:duration=longest:normalize=0,"
                   f"apad=whole_dur={total},atrim=duration={total},"
                   "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")
    with new_output(destination, ".mp4") as temporary:
        cmd = ["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(filters),
               "-map", f"[{last}]", "-map", "[aout]", "-t", str(total), "-r", str(fps),
               "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-f", "mp4", str(temporary)]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError("ffmpeg fastcut failed: " + exc.stderr.decode("utf-8", "replace")[-2000:]) from exc
    return dict(info, path=str(destination), captions=len(caps), texts=len(overlays))


def measure(path):
    """Technical QC: duration and integrated loudness of a rendered file."""
    info = probe_media(path)
    duration = float(info["format"]["duration"])
    res = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true",
                          "-f", "null", "-"], capture_output=True, text=True)
    tail = res.stderr[res.stderr.rfind("Summary:"):]
    lufs = re.search(r"I:\s+(-?[\d.]+) LUFS", tail)
    peak = re.search(r"Peak:\s+(-?[\d.]+) dBFS", tail)
    streams = {s["codec_type"] for s in info.get("streams", [])}
    return {"duration_sec": round(duration, 3), "integrated_lufs": float(lufs.group(1)) if lufs else None,
            "true_peak_dbfs": float(peak.group(1)) if peak else None, "streams": sorted(streams)}
