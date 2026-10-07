#!/usr/bin/env python3
import os
import plistlib
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP = ROOT / "scripts/personal-hermes-bootstrap.py"


class BootstrapTest(unittest.TestCase):
    def test_prepares_only_public_profiles_and_inert_user_jobs(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            hermes_home = home / ".hermes"
            hermes_home.mkdir(mode=0o700)
            hermes = hermes_home / "runtime-v0.21.5/venv/bin/hermes"
            hermes.parent.mkdir(parents=True)
            hermes.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$HOME/profile-create.log\"\n"
                              "mkdir -m 700 \"$HOME/.hermes/profiles/$3\"\n")
            hermes.chmod(0o700)
            bin_dir = home / "bin"
            bin_dir.mkdir()
            signal = bin_dir / "signal-cli"
            signal.write_text("#!/bin/sh\n[ \"$1\" = --version ] || exit 99\nprintf 'signal-cli 0.14.8\\n'\n")
            signal.chmod(0o700)
            env = {**os.environ, "HOME": str(home), "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]}

            def run(*args):
                return subprocess.run([sys.executable, str(BOOTSTRAP), *args], env=env,
                                      capture_output=True, text=True, timeout=20)

            self.assertEqual(run().returncode, 1)
            self.assertFalse((home / "Library").exists())
            signal.write_text("#!/bin/sh\nprintf 'signal-cli 0.15.0\\n'\n")
            self.assertIn("signal-cli 0.14.8 required", run("--prepare").stderr)
            self.assertFalse((home / "Library").exists())
            signal.write_text("#!/bin/sh\n[ \"$1\" = --version ] || exit 99\nprintf 'signal-cli 0.14.8\\n'\n")
            hermes_home.chmod(0o755)
            self.assertIn("~/.hermes must be owner-only", run("--prepare").stderr)
            self.assertFalse((home / "Library").exists())
            hermes_home.chmod(0o700)
            logs = hermes_home / "logs"
            logs.mkdir(mode=0o700)
            logs.chmod(0o770)
            self.assertIn("non-writable-by-others", run("--prepare").stderr)
            logs.chmod(0o700)
            prepared = run("--prepare")
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            self.assertIn("restore owner-supplied private profiles", prepared.stdout)
            self.assertEqual(len((home / "profile-create.log").read_text().splitlines()), 8)
            for name in ("design-workflow", "prd-workflow", "roadmap-workflow"):
                installed = hermes_home / "skills" / name / "SKILL.md"
                self.assertEqual(installed.read_bytes(), (ROOT / "agent-config/skills" / name / "SKILL.md").read_bytes())
                self.assertEqual(stat.S_IMODE(installed.stat().st_mode), 0o600)
            tool = hermes_home / "bin/hermes-council-tools"
            self.assertEqual(stat.S_IMODE(tool.stat().st_mode), 0o500)
            self.assertEqual(tool.read_bytes(), (ROOT / "bin/hermes-council-tools").read_bytes())
            profile = hermes_home / "profiles/pr-review-v1"
            self.assertIn(str(ROOT / "bin/hermes-memory-recall-shim"),
                          (profile / "config.yaml").read_text())
            self.assertNotIn("/usr/local/libexec/ai-pr-automation", (profile / "mcp.json").read_text())
            jobs = home / "Library/LaunchAgents"
            self.assertEqual(len(list(jobs.glob("*.plist"))), 3)
            for path in jobs.glob("*.plist"):
                data = plistlib.loads(path.read_bytes())
                self.assertFalse(data["RunAtLoad"])
                self.assertNotIn("UserName", data)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(run("--prepare").returncode, 0)
            self.assertEqual(len((home / "profile-create.log").read_text().splitlines()), 8)
            for name in self.private_names():
                target = hermes_home / "profiles" / name
                target.mkdir(mode=0o700)
                (target / "SOUL.md").write_text("operator supplied\n")
                (target / "SOUL.md").chmod(0o600)
                if name in {"orchestrator", "reviewer", "security-engineer",
                            "site-reliability-engineer", "technical-architect"}:
                    (target / "profile.yaml").write_text("description: operator supplied\n")
                    (target / "profile.yaml").chmod(0o600)
                    (target / "skills").mkdir(mode=0o700)
                else:
                    (target / "config.yaml").write_text("operator supplied\n")
                    (target / "config.yaml").chmod(0o600)
            (hermes_home / "config.yaml").write_text("private operator config\n")
            (hermes_home / "config.yaml").chmod(0o600)
            self.assertEqual(run().returncode, 0)
            private_env = hermes_home / "profiles/design-write-v1/.env"
            private_env.write_text("TEST_VALUE=fixture\n")
            private_env.chmod(0o644)
            self.assertIn("profile file must be owner-only", run().stderr)
            private_env.chmod(0o600)
            self.assertEqual(run().returncode, 0)
            config_path = profile / "config.yaml"
            original_config = config_path.read_bytes()
            config_path.write_text("mcp_servers:\n  write: {}\n")
            self.assertIn("public profile differs", run().stderr)
            self.assertNotEqual(run("--prepare").returncode, 0)
            self.assertEqual(config_path.read_text(), "mcp_servers:\n  write: {}\n")
            config_path.write_bytes(original_config)
            council = hermes_home / "profiles/council-reviewer-v2/config.yaml"
            council.write_text("/usr/local/libexec/ai-pr-automation/hermes-council-tools\n")
            self.assertIn("retired system helper", run().stderr)
            council.write_text("operator supplied\n")
            self.assertFalse((hermes_home / "secrets").exists())
            self.assertFalse((home / ".local/share/signal-cli").exists())
            plist = jobs / "ai.hermes.signal.plist"
            original = plist.read_bytes()
            plist.write_bytes(b"not a plist")
            failed = run("--prepare")
            self.assertEqual(failed.returncode, 1)
            self.assertIn("do not overwrite", failed.stderr)
            self.assertEqual(plist.read_bytes(), b"not a plist")
            plist.write_bytes(original)

    @staticmethod
    def private_names():
        import importlib.util
        spec = importlib.util.spec_from_file_location("personal_bootstrap", BOOTSTRAP)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.PRIVATE


if __name__ == "__main__":
    unittest.main()
