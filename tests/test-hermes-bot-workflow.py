#!/usr/bin/env python3
import copy
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("configure_bot_workflow", ROOT / "scripts/configure-hermes-bot-workflow.py")
workflow = importlib.util.module_from_spec(spec); spec.loader.exec_module(workflow)
CONTRACT_PATH = ROOT / "agent-config/hermes/workflows/pr-risk-council.json"
CONTRACT = workflow.load_contract(CONTRACT_PATH)


class HermesBotWorkflowTest(unittest.TestCase):
    def home(self, root):
        home = root / ".hermes"
        for name in CONTRACT["profiles"]:
            profile = home / "profiles" / name; profile.mkdir(parents=True)
            (profile / "config.yaml").write_text("model:\n  provider: anthropic\n  default: claude-opus-5\nagent:\n  max_turns: 42\n")
            (profile / "profile.yaml").write_text(f"description: existing {name}\ncustom: keep\n")
        return home

    def snapshots(self, home):
        return {str(path.relative_to(home)): path.read_bytes() for path in home.glob("profiles/*/*.yaml")}

    def test_contract_locks_sonnet_ceiling_and_haiku_specialists(self):
        self.assertEqual(len(CONTRACT["profiles"]), 6)
        self.assertEqual(CONTRACT["profiles"]["orchestrator"]["model"], "claude-sonnet-4-6")
        self.assertEqual({value["model"] for key, value in CONTRACT["profiles"].items() if key != "orchestrator"},
                         {"claude-haiku-4-5-20251001"})
        changed = copy.deepcopy(CONTRACT); changed["profiles"]["reviewer"]["model"] = "claude-opus-5"
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "contract.json"; path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, "invalid title or model"): workflow.load_contract(path)

    def test_dry_run_apply_and_explicit_restore(self):
        with tempfile.TemporaryDirectory() as td:
            home = self.home(pathlib.Path(td)); before = self.snapshots(home)
            result = workflow.configure(home, CONTRACT, os.getuid(), os.getgid(), apply=False)
            self.assertFalse(result["applied"]); self.assertEqual(self.snapshots(home), before)
            result = workflow.configure(home, CONTRACT, os.getuid(), os.getgid(), apply=True)
            self.assertTrue(result["applied"]); self.assertTrue(pathlib.Path(result["backup"]).is_file())
            for name, policy in CONTRACT["profiles"].items():
                config = yaml.safe_load((home / "profiles" / name / "config.yaml").read_text())
                meta = yaml.safe_load((home / "profiles" / name / "profile.yaml").read_text())
                self.assertEqual(config["model"], {"provider":"anthropic", "default":policy["model"]})
                self.assertTrue(config["agent"]["bot_mode_protocol"])
                self.assertEqual(config["agent"]["max_turns"], 42)
                self.assertEqual(config["fallback_providers"], [])
                self.assertEqual(config["delegation"]["fallback_providers"], [])
                self.assertEqual(config["platform_toolsets"]["api_server"], ["no_mcp"])
                self.assertEqual(meta["description"], f"existing {name}")
                self.assertEqual(meta["custom"], "keep")
                self.assertEqual(meta["display_name"], policy["title"])
                self.assertEqual(meta["ui_meta"]["hermes-bots"]["title"], policy["title"])
            self.assertEqual(workflow.state_file(home).read_text().strip(), "applied")
            self.assertTrue(workflow.restore(home, os.getuid(), os.getgid())["restored"])
            self.assertEqual(self.snapshots(home), before)
            self.assertFalse(workflow.state_file(home).exists())

    def test_missing_or_symlink_profile_file_fails_before_writes(self):
        with tempfile.TemporaryDirectory() as td:
            home = self.home(pathlib.Path(td)); before = self.snapshots(home)
            missing = home / "profiles/verifier"; missing.rename(home / "verifier-away")
            with self.assertRaisesRegex(ValueError, "profile missing"):
                workflow.configure(home, CONTRACT, os.getuid(), os.getgid(), apply=True)
            self.assertEqual(self.snapshots(home), {k:v for k,v in before.items() if not k.startswith("profiles/verifier/")})
        with tempfile.TemporaryDirectory() as td:
            home = self.home(pathlib.Path(td)); config = home / "profiles/reviewer/config.yaml"
            config.unlink(); config.symlink_to(home / "profiles/orchestrator/config.yaml")
            with self.assertRaisesRegex(ValueError, "unsafe managed file"):
                workflow.configure(home, CONTRACT, os.getuid(), os.getgid(), apply=True)
        with tempfile.TemporaryDirectory() as td:
            home = self.home(pathlib.Path(td)); (home / "profiles/reviewer/mcp.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "must not configure MCP"):
                workflow.configure(home, CONTRACT, os.getuid(), os.getgid(), apply=True)

    def test_partial_apply_rolls_back_every_file(self):
        with tempfile.TemporaryDirectory() as td:
            home = self.home(pathlib.Path(td)); before = self.snapshots(home); calls = 0
            def fail_after_two(source, target):
                nonlocal calls
                calls += 1
                if calls == 3: raise OSError("injected replace failure")
                os.replace(source, target)
            with self.assertRaisesRegex(OSError, "injected replace failure"):
                workflow.configure(home, CONTRACT, os.getuid(), os.getgid(), apply=True, replace_fn=fail_after_two)
            self.assertEqual(self.snapshots(home), before)
            self.assertFalse((home / "workflow-backups/pr-risk-council.json").exists())

    def test_cli_dry_run_is_non_root_and_machine_readable(self):
        with tempfile.TemporaryDirectory() as td:
            home = self.home(pathlib.Path(td))
            result = subprocess.run([sys.executable, str(ROOT / "scripts/configure-hermes-bot-workflow.py"),
                "--hermes-home", str(home), "--service-user", os.environ.get("USER", "zhach"),
                "--contract", str(CONTRACT_PATH)], capture_output=True, text=True, check=True)
        output = json.loads(result.stdout)
        self.assertFalse(output["applied"]); self.assertEqual(len(output["profiles"]), 6)
        self.assertEqual(output["model_ceiling"], "claude-sonnet-4-6")


if __name__ == "__main__": unittest.main()
