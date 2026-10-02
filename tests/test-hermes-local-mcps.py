#!/usr/bin/env python3
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LocalMcpTest(unittest.TestCase):
    def test_review_and_maintenance_use_host_local_hindsight_and_coderag(self):
        for profile in ("pr-review-v1", "pr-maintain-v1"):
            with self.subTest(profile=profile):
                config = json.loads((ROOT / "agent-config/hermes/profiles" / profile / "mcp.json").read_text())
                servers = config["mcpServers"]
                self.assertEqual(set(servers), {"memory-recall", "coderag"})
                self.assertEqual(servers["memory-recall"], {
                    "type": "stdio", "command": "python3",
                    "args": ["/usr/local/libexec/ai-pr-automation/hermes-memory-recall-shim"],
                    "env": {"HERMES_MEMORY_BACKEND": "hindsight"}})
                self.assertEqual(servers["coderag"], {
                    "type": "streamable-http", "url": "http://127.0.0.1:9750/mcp"})
                native = (ROOT / "agent-config/hermes/profiles" / profile / "config.yaml").read_text()
                self.assertIn("mcp_servers:\n  memory-recall:\n    command: python3", native)
                self.assertIn("/usr/local/libexec/ai-pr-automation/hermes-memory-recall-shim", native)
                self.assertIn("HERMES_MEMORY_BACKEND: hindsight", native)
                self.assertIn("  coderag:\n    url: http://127.0.0.1:9750/mcp", native)


if __name__ == "__main__": unittest.main()
