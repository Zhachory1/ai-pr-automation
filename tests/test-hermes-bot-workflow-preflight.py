#!/usr/bin/env python3
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bot_preflight", ROOT / "scripts/hermes-bot-workflow-preflight.py")
preflight = importlib.util.module_from_spec(spec); spec.loader.exec_module(preflight)
CONTRACT = ROOT / "agent-config/hermes/workflows/pr-risk-council.json"


class BotWorkflowPreflightTest(unittest.TestCase):
    def fixture(self, root, extra_tool=False):
        home, install = root / ".hermes", root / "install"
        policy = preflight.contract(CONTRACT)
        for name in policy["profiles"]:
            profile = home / "profiles" / name; profile.mkdir(parents=True)
            (profile / "config.yaml").write_text("model:\n  provider: anthropic\n  default: claude-opus-5\nagent: {}\n")
        for package in ("gateway", "tui_gateway", "tools", "hermes_cli"):
            path = install / package; path.mkdir(parents=True); (path / "__init__.py").write_text("")
        (install / "gateway/hosted_room_discussion.py").write_text(
            "MIN_DISCUSSION_MEMBERS=2\nMAX_DISCUSSION_MEMBERS=6\nMAX_DISCUSSION_ROUNDS=3\nMAX_DISCUSSION_MESSAGES=10\n")
        methods = tuple(sorted(preflight.REQUIRED_METHODS))
        (install / "tui_gateway/methods_groups.py").write_text(f"_METHODS={methods!r}\n")
        toolsets = "['bot_room','terminal']" if extra_tool else "['bot_room']"
        (install / "gateway/hosted_room_execution_policy.py").write_text(
            "def execution_policy_mapping(*,target_profile,config):\n"
            f" return {{'target_profile':target_profile,'enabled_toolsets':{toolsets},'approval_mode':'manual','max_iterations':50,'policy_digest':'fixture'}}\n"
            "API='api_server'\nBOT='bot_room'\n")
        (install / "tools/bot_mode_dm.py").write_text("MESSAGE_AGENT_TOOL_NAME='message_agent'\n")
        (install / "hermes_cli/models_catalog_static.py").write_text(
            "MODELS=['claude-sonnet-5','claude-haiku-4-5-20251001']\n")
        venv = install / "venv/bin"; venv.mkdir(parents=True); (venv / "python").symlink_to(sys.executable)
        return home, install

    def test_ready_report_is_read_only_and_bot_room_only(self):
        with tempfile.TemporaryDirectory() as td:
            home, install = self.fixture(pathlib.Path(td))
            result = preflight.preflight(home, install, CONTRACT)
        self.assertTrue(result["ready"]); self.assertEqual(result["profile_count"], 6)
        self.assertEqual(result["hosted_limits"], {"MIN_DISCUSSION_MEMBERS":2,"MAX_DISCUSSION_MEMBERS":6,
                                                    "MAX_DISCUSSION_ROUNDS":3,"MAX_DISCUSSION_MESSAGES":10})
        self.assertEqual(set(result["hosted_methods"]), preflight.REQUIRED_METHODS)

    def test_profiles_project_to_expected_models_and_tool_policy(self):
        with tempfile.TemporaryDirectory() as td:
            home, install = self.fixture(pathlib.Path(td))
            result = preflight.preflight(home, install, CONTRACT)
        self.assertEqual(result["models"]["orchestrator"], "claude-sonnet-5")
        self.assertEqual({model for name, model in result["models"].items() if name != "orchestrator"},
                         {"claude-haiku-4-5-20251001"})
        self.assertTrue(all(toolsets == ["bot_room"] for toolsets in result["toolsets"].values()))
        self.assertEqual((result["writes"], result["model_calls"], result["messages"]), (0, 0, 0))

    def test_extra_tool_mcp_missing_profile_and_limit_drift_fail(self):
        with tempfile.TemporaryDirectory() as td:
            home, install = self.fixture(pathlib.Path(td), extra_tool=True)
            with self.assertRaisesRegex(ValueError, "beyond bot_room"): preflight.preflight(home, install, CONTRACT)
        with tempfile.TemporaryDirectory() as td:
            home, install = self.fixture(pathlib.Path(td)); (home / "profiles/reviewer/mcp.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "configures MCP"): preflight.preflight(home, install, CONTRACT)
        with tempfile.TemporaryDirectory() as td:
            home, install = self.fixture(pathlib.Path(td));
            (home / "profiles/verifier").rename(home / "verifier-away")
            with self.assertRaisesRegex(ValueError, "profile missing"): preflight.preflight(home, install, CONTRACT)
        with tempfile.TemporaryDirectory() as td:
            home, install = self.fixture(pathlib.Path(td));
            path = install / "gateway/hosted_room_discussion.py"
            path.write_text(path.read_text().replace("MAX_DISCUSSION_ROUNDS=3", "MAX_DISCUSSION_ROUNDS=4"))
            with self.assertRaisesRegex(ValueError, "limits changed"): preflight.preflight(home, install, CONTRACT)

    def test_cli_report_is_machine_readable(self):
        with tempfile.TemporaryDirectory() as td:
            home, install = self.fixture(pathlib.Path(td)); output = pathlib.Path(td) / "report.json"
            import subprocess
            result = subprocess.run([sys.executable, str(ROOT / "scripts/hermes-bot-workflow-preflight.py"),
                "--hermes-home", str(home), "--install-dir", str(install), "--contract", str(CONTRACT),
                "--output", str(output)], capture_output=True, text=True, check=True)
            written = output.read_text()
        self.assertEqual(json.loads(result.stdout), json.loads(written))
        self.assertTrue(json.loads(result.stdout)["ready"])


if __name__ == "__main__": unittest.main()
