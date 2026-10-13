#!/usr/bin/env python3
from pathlib import Path
import unittest

import yaml

ROOT=Path(__file__).resolve().parents[1]
ROLES={role:"gpt-6-sol" for role in ("generalist","reliability","mvp","security","synthesis")}
TOOLS={"snapshot_read","snapshot_search","kanban_show","kanban_comment",
       "kanban_heartbeat","kanban_complete","kanban_block"}


class CouncilProfilesTest(unittest.TestCase):
    def test_exact_restricted_profiles_and_role_prompts(self):
        for role,model in ROLES.items():
            with self.subTest(role=role):
                folder=ROOT/f"agent-config/hermes/profiles/pr-review-{role}-v2"
                config=yaml.safe_load((folder/'config.yaml').read_text())
                self.assertEqual(config['model'],{'provider':'openai-codex','default':model})
                self.assertEqual(config['platform_toolsets']['cli'],['council-tools'])
                self.assertEqual(config['plugins']['enabled'],[])
                self.assertFalse(config['memory']['memory_enabled'])
                self.assertEqual(set(config['mcp_servers']),{'council-tools'})
                server=config['mcp_servers']['council-tools']
                self.assertEqual(set(server['tools']['include']),TOOLS | ({'kanban_parent_handoffs'} if role=='synthesis' else set()))
                self.assertEqual(server['command'],'${HERMES_COUNCIL_TOOLS_BIN}')
                self.assertTrue(server['worker_only'])
                self.assertNotIn('GH_CONFIG_DIR',str(config))
                self.assertNotIn('github',str(config).lower())
                for forbidden in ('terminal','file','browser','web','kanban','delegation'):
                    self.assertIn(forbidden,config['agent']['disabled_toolsets'])
                prompt=(folder/'SOUL.md').read_text()
                self.assertIn('artifact_digest',prompt)
                self.assertIn('kanban_complete',prompt)
                self.assertNotIn('autopraxis-pr-review',prompt)
                self.assertNotIn('HERMES RUNS API OVERRIDE',prompt)


if __name__=='__main__':unittest.main()
