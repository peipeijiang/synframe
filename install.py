#!/usr/bin/env python3
"""Install, upgrade or remove the namespaced Synframe skills without touching anything else.

  python3 install.py              install missing synframe* skills, then run the dependency doctor
  python3 install.py --upgrade    replace only unchanged Synframe-managed installs (old copy is backed up)
  python3 install.py --uninstall  remove only unchanged Synframe-managed installs
  python3 install.py --check      dry run for any of the above
  python3 install.py --doctor     only check dependencies and companion skills
"""
import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
MARKER = ".synframe-install.json"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", "node_modules")


def digest_tree(directory):
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("symlink in skill directory: " + str(path))
        if path.is_file() and path.name != MARKER:
            result[str(path.relative_to(directory))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def stage(skill, parent):
    """Build a complete copy next to the target so the final step is a rename."""
    staging = Path(tempfile.mkdtemp(prefix=".synframe-stage-", dir=parent))
    destination = staging / skill.name
    shutil.copytree(skill, destination, ignore=IGNORE)
    if skill.name == "synframe":
        shutil.copytree(PROJECT / "synframe", destination / "synframe", ignore=IGNORE)
        for vendor in ("100x", "admuse"):
            shutil.copytree(PROJECT / "vendor" / vendor, destination / "vendor" / vendor, ignore=IGNORE)
        shutil.copytree(PROJECT / "docs", destination / "docs", ignore=IGNORE)
        for name in ("README.md", "README.en.md", "LICENSE", "NOTICE"):
            if (PROJECT / name).is_file():
                shutil.copy2(PROJECT / name, destination / name)
    (destination / MARKER).write_text(json.dumps({"files": digest_tree(destination)}, indent=2), encoding="utf-8")
    return staging, destination


def state_of(destination):
    marker = destination / MARKER
    if not destination.exists():
        return "absent", None
    if not marker.is_file():
        return "unmanaged", None
    recorded = json.loads(marker.read_text(encoding="utf-8")).get("files")
    return ("managed" if recorded == digest_tree(destination) else "modified"), recorded


def run_doctor(target):
    sys.path.insert(0, str(PROJECT))
    from synframe.doctor import check, print_report
    print("\nDependency check (MISS blocks render/qc; TIP items are optional companions):")
    return print_report(check(target))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, default=Path.home() / ".agents" / "skills")
    parser.add_argument("--check", action="store_true", help="show what would happen")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--uninstall", action="store_true", help="remove only unchanged Synframe-managed skills")
    mode.add_argument("--upgrade", action="store_true", help="replace only unchanged Synframe-managed skills")
    mode.add_argument("--doctor", action="store_true", help="only check dependencies")
    parser.add_argument("--no-doctor", action="store_true", help="skip the dependency check after install")
    parser.add_argument("--backup-dir", type=Path, help="where --upgrade moves old copies")
    args = parser.parse_args()
    if args.doctor:
        return 0 if run_doctor(args.target)["ready"] else 1
    source = PROJECT / "skills"
    backup = args.backup_dir or args.target.parent / "skills-backups" / ("synframe-" + time.strftime("%Y%m%d-%H%M%S"))
    changed = False
    for skill in sorted(source.iterdir()):
        if not skill.is_dir() or not (skill / "SKILL.md").exists():
            continue
        destination = args.target / skill.name
        state, recorded = state_of(destination)
        if args.uninstall:
            if state != "managed":
                print("SKIP", state, destination)
            elif args.check:
                print("REMOVABLE", destination)
            else:
                shutil.rmtree(destination)
                print("REMOVED", destination)
            continue
        if state in ("unmanaged", "modified") or (state == "managed" and not args.upgrade):
            hint = {"unmanaged": "not installed by this script; move it away to replace it",
                    "modified": "has local edits; move it away to replace it",
                    "managed": "already installed; use --upgrade"}[state]
            print("SKIP %s %s (%s)" % (state, destination, hint))
            continue
        args.target.mkdir(parents=True, exist_ok=True)
        staging, staged = stage(skill, args.target)
        try:
            if state == "managed" and digest_tree(staged) == recorded:
                print("UNCHANGED", destination)
                continue
            if args.check:
                print("UPGRADABLE" if state == "managed" else "AVAILABLE", destination)
                continue
            if state == "managed":
                backup.mkdir(parents=True, exist_ok=True)
                destination.rename(backup / skill.name)
                print("BACKED UP", backup / skill.name)
            staged.rename(destination)
            changed = True
            print("UPGRADED" if state == "managed" else "INSTALLED", destination)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    if changed and not args.no_doctor and not args.check:
        result = run_doctor(args.target)
        if not result["ready"]:
            print("Install the MISS items above before running synframe render or qc.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
