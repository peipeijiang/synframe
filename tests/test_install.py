import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class InstallerTests(unittest.TestCase):
    def test_uninstall_only_unchanged_owned_skills(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            other = target / "ad-muse"
            other.mkdir()
            (other / "SKILL.md").write_text("preserve")
            cmd = [sys.executable, str(root / "install.py"), "--target", str(target)]
            subprocess.run(cmd, check=True, capture_output=True)
            modified = target / "synframe-qc" / "SKILL.md"
            modified.write_text("user change")
            subprocess.run(cmd + ["--uninstall"], check=True, capture_output=True)
            self.assertEqual((other / "SKILL.md").read_text(), "preserve")
            self.assertEqual(modified.read_text(), "user change")
            self.assertFalse((target / "synframe-batch").exists())

    def test_upgrade_replaces_only_unchanged_managed_and_backs_up(self):
        import json
        root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(root))
        from install import digest_tree
        with tempfile.TemporaryDirectory() as tmp:
            target, backup = Path(tmp) / "skills", Path(tmp) / "backup"
            cmd = [sys.executable, str(root / "install.py"), "--target", str(target), "--no-doctor"]
            subprocess.run(cmd, check=True, capture_output=True)
            # Make one managed install look like an older release (marker matches its content).
            stale = target / "synframe-qc"
            (stale / "SKILL.md").write_text("old release")
            (stale / ".synframe-install.json").write_text(json.dumps({"files": digest_tree(stale)}))
            # A user-edited install must be left alone.
            edited = target / "synframe-batch" / "SKILL.md"
            edited.write_text("user change")
            unmanaged = target / "synframe-assets"
            (unmanaged / ".synframe-install.json").unlink()
            out = subprocess.run(cmd + ["--upgrade", "--backup-dir", str(backup)], check=True,
                                 capture_output=True, text=True).stdout
            self.assertIn("UPGRADED " + str(stale), out)
            self.assertEqual((backup / "synframe-qc" / "SKILL.md").read_text(), "old release")
            self.assertNotEqual((stale / "SKILL.md").read_text(), "old release")
            self.assertEqual(edited.read_text(), "user change")
            self.assertIn("SKIP unmanaged " + str(unmanaged), out)
            self.assertIn("UNCHANGED " + str(target / "synframe-planner"), out)
            self.assertEqual(sorted(p.name for p in target.iterdir() if p.name.startswith(".")), [])

