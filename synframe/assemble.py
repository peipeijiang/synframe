"""Render a pools file into a batch run and run technical QC on it.

  synframe render POOLS --output RUN    plan + fastcut every video into a new RUN directory
  synframe qc RUN [--no-transcribe]     ASR diff, loudness, black frames, duration, diversity

Technical pass is never creative acceptance; reports keep the two apart.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

from . import diversity
from .fastcut import measure, render, validate
from .pools import load
from .sequence import plan, to_tracks
from .speech import compare

VIDEO_NAME = re.compile(r"^video-\d+\.json$")
TRANSCRIBER = Path(__file__).resolve().parents[1] / "vendor" / "admuse" / "transcribe_local.py"


def _write(path, data):
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1)


def render_run(pools_path, run_dir, count=None, log=print):
    run_dir = Path(run_dir).expanduser().absolute()
    if run_dir.exists():
        raise ValueError("refusing to overwrite " + str(run_dir))
    pools = load(pools_path)
    result = plan(pools, count)
    for video in result["videos"]:
        validate(video)  # fail before creating anything
    run_dir.mkdir(parents=True)
    _write(run_dir / "pools.resolved.json", pools)
    videos = []
    for video in result["videos"]:
        _write(run_dir / (video["name"] + ".json"), video)
        _write(run_dir / (video["name"] + "-tracks.json"), to_tracks(video))
        info = render(video, run_dir / (video["name"] + ".mp4"))
        info["measure"] = measure(run_dir / (video["name"] + ".mp4"))
        info["manifest"] = video["manifest"]
        videos.append(info)
        m = video["manifest"]
        log("%s %s + %s + %s %.2fs" % (video["name"], m["hook"], m["body"], m["cta"], video["total_sec"]))
    report = {k: v for k, v in result.items() if k != "videos"}
    report["videos"] = videos
    _write(run_dir / "report.json", report)
    return {k: report[k] for k in ("requested", "produced", "skipped_hooks", "shortfall_reason",
                                   "segment_usage", "listen_checklist")} | {"run": str(run_dir)}


def black_spans(path):
    res = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vf",
                          "blackdetect=d=0.1:pix_th=0.10", "-an", "-f", "null", "-"],
                         capture_output=True, text=True)
    return [{"start": float(a), "end": float(b)} for a, b in
            re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", res.stderr)]


def transcribe(mp4, out_json, language, model):
    if not TRANSCRIBER.is_file():
        return "transcriber not bundled"
    cmd = [sys.executable, str(TRANSCRIBER), str(mp4), "--output", str(out_json), "--model", model]
    if language:
        cmd += ["--language", language]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        return (res.stderr or res.stdout).strip().splitlines()[-1:] or ["transcription failed"]
    return None


def contact_sheet(run_dir, names, out_jpg, at=1.0):
    frames = run_dir / "qc" / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    for i, name in enumerate(names, 1):
        target = frames / ("f-%03d.jpg" % i)
        if not target.exists():
            subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(run_dir / (name + ".mp4")),
                            "-frames:v", "1", "-vf", "scale=180:320", str(target)], check=True)
    cols = min(6, len(names))
    rows = (len(names) + cols - 1) // cols
    subprocess.run(["ffmpeg", "-v", "error", "-n", "-start_number", "1", "-i", str(frames / "f-%03d.jpg"),
                    "-vf", "tile=%dx%d:padding=4" % (cols, rows), "-frames:v", "1", str(out_jpg)], check=True)


def qc_run(run_dir, transcribe_audio=True, whisper_model="large-v3-turbo", critical_terms=None,
           max_asr_changes=2, log=print):
    run_dir = Path(run_dir).expanduser().resolve()
    qc_dir = run_dir / "qc"
    out = qc_dir / "technical.json"
    if out.exists():
        raise ValueError("already exists: %s (move it away to re-run QC)" % out)
    qc_dir.mkdir(exist_ok=True)
    pools_file = run_dir / "pools.resolved.json"
    pools = json.loads(pools_file.read_text(encoding="utf-8")) if pools_file.exists() else {}
    terms_all = critical_terms if critical_terms is not None else pools.get("critical_terms", [])
    rules = {"min_sec": 13.5, "max_sec": 15.5, **(pools.get("rules") or {})}
    names = sorted(p.stem for p in run_dir.glob("video-*.json") if VIDEO_NAME.match(p.name))
    if not names:
        raise ValueError("no video-NN.json files in " + str(run_dir))
    videos, prints = [], {}
    for name in names:
        spec = json.loads((run_dir / (name + ".json")).read_text(encoding="utf-8"))
        mp4 = run_dir / (name + ".mp4")
        if not mp4.is_file():
            raise ValueError("missing rendered video %s; re-render into a new run directory" % mp4)
        expected = spec["manifest"]["expected_text"]
        locale = spec.get("locale") or pools.get("locale") or ""
        asr_path = qc_dir / (name + "-asr.json")
        speech_note = None
        if not asr_path.exists() and transcribe_audio:
            speech_note = transcribe(mp4, asr_path, locale.split("-")[0] or None, whisper_model)
        elif not asr_path.exists():
            speech_note = "transcription skipped (--no-transcribe)"
        m = measure(mp4)
        black = black_spans(mp4)
        checks = {"loudness_-14_pm_1": m["integrated_lufs"] is not None and abs(m["integrated_lufs"] + 14) <= 1,
                  "peak_le_-1": m["true_peak_dbfs"] is not None and m["true_peak_dbfs"] <= -1.0,
                  "no_black_frames": not black,
                  "duration_in_window": rules["min_sec"] <= m["duration_sec"] <= rules["max_sec"]}
        entry = {"video": name, "hook": spec["manifest"]["hook"], "body": spec["manifest"]["body"],
                 "cta": spec["manifest"]["cta"], "measure": m, "black": black}
        if asr_path.exists():
            asr = json.loads(asr_path.read_text(encoding="utf-8"))
            words = [w for s in asr.get("segments", []) for w in s.get("words", [])]
            terms = [t for t in terms_all if t.casefold() in expected.casefold()]
            diff = compare(expected, asr["text"], locale, critical_terms=terms, asr_words=words)
            checks["critical_terms_present"] = not diff["critical_missing"]
            checks["asr_changes_le_%d" % max_asr_changes] = len(diff["changes"]) <= max_asr_changes
            entry.update(asr_changes=diff["changes"], critical_missing=diff["critical_missing"])
        else:
            checks["critical_terms_present"] = None
            checks["asr_changes_le_%d" % max_asr_changes] = None
            entry["speech_not_run"] = speech_note if isinstance(speech_note, str) else " ".join(speech_note or [])
        ran = [v for v in checks.values() if v is not None]
        entry["checks"] = {k: ("not_run" if v is None else v) for k, v in checks.items()}
        entry["technical_status"] = "fail" if not all(ran) else ("pass" if len(ran) == len(checks) else "partial")
        entry["creative_acceptance"] = "pending_human"
        videos.append(entry)
        prints[name] = diversity.fingerprint(mp4, m["duration_sec"])
        log("%s %s %s" % (name, entry["technical_status"], [k for k, v in checks.items() if v is False]))
    sheet = qc_dir / "contact.jpg"
    if not sheet.exists():
        contact_sheet(run_dir, names, sheet)
    summary = {}
    for v in videos:
        summary[v["technical_status"]] = summary.get(v["technical_status"], 0) + 1
    result = {"run": str(run_dir), "summary": summary, "videos": videos, "diversity": diversity.compare(prints),
              "contact_sheet": str(sheet),
              "note": "Technical gates only. Seams, voice, product identity and claims need a human review."}
    _write(out, result)
    return {"technical": str(out), "summary": summary, "contact_sheet": str(sheet),
            "repeated_openings": len(result["diversity"]["flagged"])}
