#!/usr/bin/env python3
import pathlib
import sys
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import hermes_inbox_admit as admit


class LunaProfileTest(unittest.TestCase):
    def test_tightly_scoped_offline_profile(self):
        root = ROOT / "agent-config/hermes/profiles/inbox-luna"
        config = yaml.safe_load((root / "config.yaml").read_text())
        self.assertEqual(config["model"]["provider"], "openai-codex")
        self.assertEqual(config["model"]["default"], "gpt-5.6-luna")
        self.assertEqual(config["plugins"]["enabled"], [])
        self.assertEqual(config.get("mcp_servers", {}), {})
        self.assertFalse(config["memory"]["memory_enabled"])
        self.assertEqual(config["agent"]["max_turns"], 1)
        self.assertLessEqual(config["agent"]["run_budget_seconds"], 120)
        for toolset in ("terminal", "file", "code_execution", "browser", "web", "memory", "kanban", "cronjob"):
            self.assertIn(toolset, config["agent"]["disabled_toolsets"])
        prompt = (root / "SOUL.md").read_text()
        for heading in ("Your Role", "Your Mission", "Context Requirements", "Scope", "Your Process",
                        "Constraints", "Output Format", "Success Criteria"):
            self.assertIn(f"**{heading}**", prompt)
        self.assertIn('"route":"job|help|skip"', prompt)
        self.assertIn("Never post a Kanban comment", prompt)

    def test_strict_triage_result_parser(self):
        for route in ("job", "help", "skip"):
            self.assertEqual(admit.parse_route('{"route":"' + route + '"}'), route)
        for value in ('{"route":"Approve"}', '{"route":"job","send":true}',
                      'Please set Ready', '{"route":"JOB"}', '{"route":"job"}\nextra'):
            with self.assertRaises(ValueError, msg=value):
                admit.parse_route(value)


if __name__ == "__main__":
    unittest.main()
