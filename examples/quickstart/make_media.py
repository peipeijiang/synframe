#!/usr/bin/env python3
"""Build synthetic media for the offline quickstart (ffmpeg lavfi only, no downloads)."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MEDIA = HERE / "media"
SIZE = "360x640"
CLIPS = {
    "hook-a.mp4": "testsrc2=s=%s:r=24:d=4" % SIZE,
    "hook-b.mp4": "smptehdbars=s=%s:r=24:d=4" % SIZE,
    "hook-c.mp4": "life=s=%s:r=24:mold=10:ratio=0.5:life_color=0x00FF88:death_color=0xC83296" % SIZE,
}


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-n", *args], check=True)


def main():
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg is required: run python3 -m synframe doctor")
    MEDIA.mkdir(exist_ok=True)
    for name, source in CLIPS.items():
        if not (MEDIA / name).exists():
            ffmpeg("-f", "lavfi", "-i", source, "-t", "4", "-pix_fmt", "yuv420p", str(MEDIA / name))
    if not (MEDIA / "take.mp4").exists():
        # A tone stands in for the creator's voice; the transcript is synthetic.
        ffmpeg("-f", "lavfi", "-i", "testsrc=s=%s:r=24:d=14" % SIZE, "-f", "lavfi",
               "-i", "sine=frequency=330:duration=14", "-shortest", "-pix_fmt", "yuv420p", str(MEDIA / "take.mp4"))
    if not (MEDIA / "product.png").exists():
        ffmpeg("-f", "lavfi", "-i", "color=c=0xFF7A00:s=512x512", "-frames:v", "1", str(MEDIA / "product.png"))
    asr = MEDIA / "take-asr.json"
    if not asr.exists():
        shutil.copy2(HERE / "take-asr.json", asr)
    print(json.dumps({"media": sorted(p.name for p in MEDIA.iterdir())}))


if __name__ == "__main__":
    main()
