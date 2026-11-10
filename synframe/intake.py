"""Create local evidence and original-language transcript from a reference video."""
import json
import subprocess
import sys
from pathlib import Path


def intake(spec, destination):
    root = Path(__file__).resolve().parent.parent
    video = Path(spec["video"]).expanduser().resolve()
    out = Path(destination).expanduser().resolve()
    product = spec["product"]
    if not video.is_file():
        raise ValueError("source video not found")
    if not isinstance(product.get("sku"), str) or not product["sku"]:
        raise ValueError("product SKU is required")
    if not product.get("facts") or any(not fact.get("source") for fact in product["facts"]):
        raise ValueError("product facts must include source references")
    if out.exists() and any(out.iterdir()):
        raise ValueError("output directory must be absent or empty")
    evidence = root / "vendor" / "100x" / "skills" / "100x-video-reverse" / "scripts" / "prepare_evidence.py"
    transcriber = root / "vendor" / "admuse" / "transcribe_local.py"
    if not evidence.is_file() or not transcriber.is_file():
        raise ValueError("bundled evidence or transcription tool is missing")
    command = [sys.executable, str(evidence), "--video", str(video), "--out", str(out)]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("evidence preparation failed: " + exc.stderr[-1500:]) from exc
    # Preserve the supplied product dossier; never fabricate image observations.
    (out / "product.json").write_text(json.dumps(product, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {"product": str(out / "product.json"), "evidence": str(out),
              "shot_analysis": "needs_visual_review", "transcript": None}
    if spec.get("transcribe", True):
        transcript = out / "transcript.json"
        command = [sys.executable, str(transcriber), str(video), "--output", str(transcript),
                   "--model", spec.get("whisper_model", "large-v3-turbo")]
        if spec.get("language"):
            command.extend(["--language", spec["language"]])
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
            result["transcript"] = str(transcript)
        except subprocess.CalledProcessError as exc:
            result["transcription_error"] = exc.stderr[-1500:]
    (out / "synframe-intake.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
