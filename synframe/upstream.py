"""Invoke an included 100x validator without rewriting its contract."""
import subprocess
from pathlib import Path

SKILLS = {"100x-video-reverse", "100x-segment", "100x-localize", "100x-persona",
          "100x-exaggerate", "100x-search-query", "100x-visual-fission", "100x-prompt-compose"}


def validate(skill, bundle):
    if skill not in SKILLS:
        raise ValueError("unknown 100x skill")
    bundle = Path(bundle).expanduser().resolve()
    if not bundle.is_file():
        raise ValueError("bundle file missing")
    root = Path(__file__).resolve().parent.parent
    validator = root / "vendor" / "100x" / "skills" / skill / "scripts" / "validate.js"
    result = subprocess.run(["node", str(validator), str(bundle)], capture_output=True, text=True)
    if "Cannot find module" in result.stderr:
        raise RuntimeError("100x validator dependency missing; install its locked dependencies")
    return {"skill": skill, "bundle": str(bundle), "valid": result.returncode == 0,
            "report": (result.stdout + result.stderr)[-4000:]}
