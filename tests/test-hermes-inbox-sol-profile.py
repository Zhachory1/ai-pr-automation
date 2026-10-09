#!/usr/bin/env python3
import pathlib
import sys
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import hermes_inbox_sol as sol


class SolProfileTest(unittest.TestCase):
    def test_tool_free_draft_profile_contract(self):
        root = ROOT / "agent-config/hermes/profiles/inbox-sol"
        config = yaml.safe_load((root / "config.yaml").read_text())
        self.assertEqual(config["model"]["provider"], "openai-codex")
        self.assertEqual(config["model"]["default"], "gpt-5.6-sol")
        self.assertEqual(config["plugins"]["enabled"], [])
        self.assertEqual(config.get("mcp_servers", {}), {})
        self.assertFalse(config["memory"]["memory_enabled"])
        self.assertEqual(config["agent"]["max_turns"], 1)
        for tool in ("terminal", "file", "browser", "web", "kanban", "memory", "skills", "code_execution"):
            self.assertIn(tool, config["agent"]["disabled_toolsets"])
        prompt = (root / "SOUL.md").read_text()
        self.assertIn('{"body":"<reply text>"}', prompt)
        self.assertIn("Never post a Kanban comment", prompt)
        self.assertIn("Never set Ready", prompt)

    def test_strict_draft_output(self):
        self.assertEqual(sol.parse_draft('{"body":"Thank you. Could you share the scope?"}'),
                         "Thank you. Could you share the scope?")
        for output in ('{"body":""}', '{"body":"hi","send":true}',
                       '{"body":"one","body":"two"}', 'Approve',
                       '{"body":"hi"}\nReady', '{"body":"' + 'x'*8000 + '"}',
                       '[[]]', '[["body"]]', '[1]'):
            with self.assertRaises(ValueError, msg=output[:40]):
                sol.parse_draft(output)


if __name__ == "__main__":
    unittest.main()
