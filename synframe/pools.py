"""Load a portable pools file for synframe.sequence.

Extra keys on top of the sequence format:
  media: {"BC1": "media/BC1.mp4"}       aliases; any src may be "@BC1" or a path relative to the file
  asr:   {"BC1": "qc/BC1-asr.json"}      Whisper JSON per take
  voice entries may say "asr": "@BC1" (or a path) instead of listing words; words inside
  [in, out] are copied from that transcript so captions follow the real speech.
  critical_terms: ["12", "carrito naranja"]  used by synframe qc
"""
import copy
import json
from pathlib import Path

from .sequence import SequenceError


def _resolve(base, aliases, value, kind):
    if not isinstance(value, str) or not value:
        raise SequenceError("%s reference must be a non-empty string" % kind)
    if value.startswith("@"):
        if value[1:] not in aliases:
            raise SequenceError("unknown %s alias: %s" % (kind, value))
        value = aliases[value[1:]]
    path = Path(value).expanduser()
    path = path if path.is_absolute() else (base / path)
    path = path.resolve()
    if not path.is_file():
        raise SequenceError("missing %s file: %s" % (kind, path))
    return str(path)


def words_in(transcript, start, end, slack=0.05):
    return [{"word": w["word"], "start": w["start"], "end": w["end"]}
            for segment in transcript.get("segments", []) for w in segment.get("words", [])
            if start - slack <= w["start"] and w["end"] <= end + slack]


def load(path):
    path = Path(path).expanduser().resolve()
    pools = copy.deepcopy(json.loads(path.read_text(encoding="utf-8")))
    base = path.parent
    media = pools.get("media") or {}
    asr_files = pools.get("asr") or {}
    transcripts = {}

    def transcript(ref):
        file = _resolve(base, asr_files, ref, "asr")
        if file not in transcripts:
            transcripts[file] = json.loads(Path(file).read_text(encoding="utf-8"))
        return transcripts[file]

    for role in ("hooks", "bodies", "ctas"):
        for segment in pools.get(role) or []:
            for shot in segment.get("shots") or []:
                shot["src"] = _resolve(base, media, shot.get("src"), "media")
            for voice in segment.get("voice") or []:
                voice["src"] = _resolve(base, media, voice.get("src"), "media")
                ref = voice.pop("asr", None)
                if ref and not voice.get("words"):
                    voice["words"] = words_in(transcript(ref), float(voice["in"]), float(voice["out"]))
            if segment.get("pad"):
                segment["pad"]["src"] = _resolve(base, media, segment["pad"].get("src"), "media")
    pools["media"] = {k: _resolve(base, media, "@" + k, "media") for k in media}
    pools["asr"] = {k: _resolve(base, asr_files, "@" + k, "asr") for k in asr_files}
    return pools
