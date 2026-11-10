"""Dependency doctor shared by install.py and `synframe doctor`. Read-only, no network calls."""
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

FILTERS = ("drawtext", "loudnorm", "ebur128", "blackdetect")
WHISPER_CACHE = Path.home() / ".cache" / "whisper"
# Companion skills: (directory name, role, where to get it)
COMPANIONS = (
    ("ad-muse", "single-take product fidelity, reference routing (parts vendored in vendor/admuse)",
     "https://github.com/peipeijiang/ad-muse"),
    ("100x-skill-tiktok", "creative methods: segment, localize, persona, visual fission (vendored in vendor/100x)",
     "https://github.com/kezd088/100x-skill-tiktok"),
    ("egm", "optional fresh-eyes design review before a big batch (Codex/Claude plugin)",
     "https://github.com/google/elephant-goldfish-model"),
)


def _item(name, ok, required, detail, fix=""):
    return {"name": name, "status": "ok" if ok else ("missing" if required else "optional"),
            "required": required, "detail": detail, "fix": "" if ok else fix}


def _ffmpeg_filters():
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    found = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] in FILTERS:
            found.add(parts[1])
    return found


def check(skills_dir=None):
    from .fastcut import resolve_font, CutError
    items = []
    py = sys.version_info
    items.append(_item("python>=3.10", py >= (3, 10), True, "%d.%d.%d" % py[:3], "install Python 3.10 or newer"))
    mac = sys.platform == "darwin"
    ffmpeg_fix = "brew install ffmpeg" if mac else "install ffmpeg with your package manager (apt install ffmpeg)"
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        items.append(_item(tool, bool(path), True, path or "not on PATH", ffmpeg_fix))
    if shutil.which("ffmpeg"):
        have = _ffmpeg_filters()
        missing = [f for f in FILTERS if f not in have]
        items.append(_item("ffmpeg filters", not missing, True,
                           "all present" if not missing else "missing: " + ", ".join(missing),
                           "use an ffmpeg build with libfreetype (drawtext); on macOS: brew install ffmpeg-full"
                           if mac else "use an ffmpeg build with libfreetype (drawtext)"))
    try:
        font = resolve_font()
    except CutError as exc:
        font, detail = None, str(exc)
    else:
        detail = font or "no bold font found"
    items.append(_item("caption font", bool(font), True, detail,
                       "set SYNFRAME_FONT=/path/to/BoldFont.ttf (use a CJK font for Chinese text)"))
    whisper = importlib.util.find_spec("whisper") is not None
    weights = sorted(p.name for p in WHISPER_CACHE.glob("*.pt")) if WHISPER_CACHE.is_dir() else []
    items.append(_item("openai-whisper (ASR for qc)", whisper and bool(weights), False,
                       ("weights: " + ", ".join(weights)) if weights else ("module found, no cached weights"
                                                                            if whisper else "not installed"),
                       "pip install openai-whisper, then cache a model such as large-v3-turbo; "
                       "without it run qc --no-transcribe"))
    items.append(_item("opentimelineio (OTIO export)", importlib.util.find_spec("opentimelineio") is not None,
                       False, "", "pip install -r requirements-optional.txt"))
    node = shutil.which("node")
    items.append(_item("node (100x validators)", bool(node), False, node or "not on PATH",
                       "install Node 18+, then npm ci inside vendor/100x/skills/<skill>"))
    items.append(_item("LK888_API_KEY (paid generation)", bool(os.environ.get("LK888_API_KEY")), False,
                       "set" if os.environ.get("LK888_API_KEY") else "not set (value never shown)",
                       "export LK888_API_KEY=... before synframe generate --quote/--submit"))
    skills_dir = Path(skills_dir) if skills_dir else Path.home() / ".agents" / "skills"
    plugin_cache = Path.home() / ".codex" / "plugins" / "cache"
    for name, role, source in COMPANIONS:
        present = (skills_dir / name / "SKILL.md").is_file() or (plugin_cache / name).is_dir()
        items.append(_item("companion: " + name, present, False, role, "get it from " + source))
    return items


def summary(items):
    blocking = [i for i in items if i["status"] == "missing"]
    return {"ready": not blocking, "blocking": [i["name"] for i in blocking],
            "optional_missing": [i["name"] for i in items if i["status"] == "optional"], "items": items}


def print_report(items, out=print):
    marks = {"ok": "OK  ", "missing": "MISS", "optional": "TIP "}
    for item in items:
        line = "%s %s" % (marks[item["status"]], item["name"])
        if item["detail"]:
            line += " - " + item["detail"]
        out(line)
        if item["fix"]:
            out("       " + item["fix"])
    result = summary(items)
    out("ready" if result["ready"] else "not ready: " + ", ".join(result["blocking"]))
    return result
