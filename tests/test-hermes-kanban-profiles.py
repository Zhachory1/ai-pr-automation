#!/usr/bin/env python3
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("kanban_profiles", ROOT / "scripts/configure-hermes-kanban-profiles.py")
profiles = importlib.util.module_from_spec(spec); spec.loader.exec_module(profiles)
CONTRACT_PATH = ROOT / "agent-config/hermes/workflows/pr-risk-council-kanban.json"
CONTRACT = profiles.load_contract(CONTRACT_PATH)


class KanbanCouncilProfilesTest(unittest.TestCase):
    def home(self, root):
        home = root / ".hermes"; (home / "profiles").mkdir(parents=True)
        for value in CONTRACT["profiles"].values():
            source = home / "profiles" / value["source"]; (source / "skills/example").mkdir(parents=True)
            (source / "SOUL.md").write_text(f"# {value['role']}\n")
            (source / "skills/example/SKILL.md").write_text("# Example\n")
            (source / "config.yaml").write_text("model:\n  provider: anthropic\n  default: claude-opus-5\n")
            (source / "profile.yaml").write_text(f"description: Existing {value['role']} specialist\n")
        return home

    def test_check_apply_and_restore_leave_sources_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            home = self.home(pathlib.Path(td)); source = home / "profiles/orchestrator/SOUL.md"
            original = source.read_bytes()
            self.assertTrue(profiles.check(home, CONTRACT, os.getuid())["ready"])
            result = profiles.apply(home, CONTRACT, os.getuid(), os.getgid())
            self.assertEqual(len(result["profiles"]), 6)
            for target, policy in CONTRACT["profiles"].items():
                root = home / "profiles" / target
                config = yaml.safe_load((root / "config.yaml").read_text())
                meta = yaml.safe_load((root / "profile.yaml").read_text())
                self.assertEqual(config["model"], {"provider":"anthropic","default":policy["model"]})
                self.assertEqual(config["fallback_providers"], [])
                self.assertEqual(config["delegation"]["fallback_providers"], [])
                self.assertEqual(config["platform_toolsets"]["api_server"], ["no_mcp"])
                self.assertEqual(config["platform_toolsets"]["cli"], [])
                self.assertEqual(config["plugins"]["enabled"], [])
                self.assertFalse(config["auxiliary"]["background_review"]["enabled"])
                self.assertFalse((root / ".env").exists()); self.assertFalse((root / "mcp.json").exists())
                self.assertEqual(meta["workflow"]["source_profile"], policy["source"])
                self.assertEqual((root / ".council-profile").read_text().strip(), profiles.WORKFLOW)
            self.assertEqual(source.read_bytes(), original)
            self.assertTrue(profiles.restore(home, CONTRACT, os.getuid(), os.getgid())["restored"])
            self.assertEqual(source.read_bytes(), original)
            self.assertFalse(any((home / "profiles" / target).exists() for target in CONTRACT["profiles"]))

    def test_existing_target_or_symlink_source_fails_before_writes(self):
        with tempfile.TemporaryDirectory() as td:
            home = self.home(pathlib.Path(td)); (home / "profiles/council-reviewer").mkdir()
            with self.assertRaisesRegex(ValueError, "target profile already exists"):
                profiles.apply(home, CONTRACT, os.getuid(), os.getgid())
            self.assertFalse(profiles.state_path(home).exists())
        with tempfile.TemporaryDirectory() as td:
            home = self.home(pathlib.Path(td)); skill = home / "profiles/reviewer/skills/link"
            skill.symlink_to(home / "profiles/reviewer/SOUL.md")
            with self.assertRaisesRegex(ValueError, "unsafe source skill tree"):
                profiles.check(home, CONTRACT, os.getuid())

    def test_partial_failure_removes_created_clones(self):
        with tempfile.TemporaryDirectory() as td:
            home = self.home(pathlib.Path(td)); real = profiles.create_profile; calls = 0
            def fail_second(*args):
                nonlocal calls
                calls += 1
                if calls == 2: raise OSError("injected create failure")
                return real(*args)
            with mock.patch.object(profiles, "create_profile", side_effect=fail_second):
                with self.assertRaisesRegex(OSError, "injected create failure"):
                    profiles.apply(home, CONTRACT, os.getuid(), os.getgid())
            self.assertFalse(any((home / "profiles" / target).exists() for target in CONTRACT["profiles"]))
            self.assertFalse(profiles.state_path(home).exists())

    def test_restore_recovers_partial_crash_state(self):
        with tempfile.TemporaryDirectory() as td:
            home = self.home(pathlib.Path(td)); backup = home / "workflow-backups"; backup.mkdir()
            target = home / "profiles/council-reviewer"; target.mkdir()
            (target / ".council-profile").write_text(profiles.WORKFLOW + "\n")
            (backup / "pr-risk-council-kanban-profiles.state").write_text("applying\n")
            self.assertTrue(profiles.restore(home, CONTRACT, os.getuid(), os.getgid())["restored"])
            self.assertFalse(target.exists())

    def test_cli_check_is_machine_readable_and_inert(self):
        with tempfile.TemporaryDirectory() as td:
            home = self.home(pathlib.Path(td))
            result = subprocess.run([sys.executable, str(ROOT / "scripts/configure-hermes-kanban-profiles.py"),
                "--hermes-home", str(home), "--service-user", os.environ.get("USER", "zhach"),
                "--contract", str(CONTRACT_PATH)], capture_output=True, text=True, check=True)
        output = json.loads(result.stdout)
        self.assertTrue(output["ready"]); self.assertEqual(output["writes"], 0)


if __name__ == "__main__": unittest.main()
