#!/usr/bin/env python3
from pathlib import Path
import unittest

import yaml

ROOT=Path(__file__).resolve().parents[1]
ROLES={"generalist":"gpt-6-sol","reliability":"gpt-6-luna",
       "mvp":"gpt-6-luna","security":"gpt-6-luna","synthesis":"gpt-6-sol"}
TOOLS={"snapshot_read","snapshot_search","kanban_show","kanban_comment",
       "kanban_heartbeat","kanban_complete","kanban_block"}


class CouncilProfilesTest(unittest.TestCase):
    def test_exact_restricted_profiles_and_role_prompts(self):
        for role,model in ROLES.items():
            with self.subTest(role=role):
                folder=ROOT/f"agent-config/hermes/profiles/pr-review-{role}-v2"
                config=yaml.safe_load((folder/'config.yaml').read_text())
                self.assertEqual(config['model'],{'provider':'openai-codex','default':model})
                self.assertEqual(config['platform_toolsets']['cli'],
                                 ['council-tools','terminal','file'] if role in ('reliability','mvp','security')
                                 else ['council-tools'])
                self.assertEqual(config['plugins']['enabled'],[])
                self.assertFalse(config['memory']['memory_enabled'])
                self.assertEqual(set(config['mcp_servers']),{'council-tools'})
                server=config['mcp_servers']['council-tools']
                expected_tools=(TOOLS-{'snapshot_read','snapshot_search','kanban_comment'} if role in ('reliability','mvp','security')
                                else TOOLS | ({'kanban_parent_handoffs'} if role=='synthesis' else set()))
                self.assertEqual(set(server['tools']['include']),expected_tools)
                self.assertEqual(server['command'],'${HERMES_COUNCIL_TOOLS_BIN}')
                self.assertTrue(server['worker_only'])
                if role in ('generalist','synthesis'):
                    self.assertNotIn('GH_CONFIG_DIR',str(config))
                for forbidden in ('browser','web','kanban','delegation'):
                    self.assertIn(forbidden,config['agent']['disabled_toolsets'])
                prompt=(folder/'SOUL.md').read_text()
                if role in ('reliability','mvp','security'):
                    self.assertEqual(config['terminal']['env_passthrough'],['GH_CONFIG_DIR'])
                    for enabled in ('terminal','file'):
                        self.assertNotIn(enabled,config['agent']['disabled_toolsets'])
                    self.assertNotIn('artifact_digest',prompt)
                else:
                    self.assertNotIn('github',str(config).lower())
                    self.assertIn('artifact_digest',prompt)
                    for forbidden in ('terminal','file'):
                        self.assertIn(forbidden,config['agent']['disabled_toolsets'])
                self.assertIn('kanban_complete',prompt)
                self.assertNotIn('autopraxis-pr-review',prompt)
                self.assertNotIn('HERMES RUNS API OVERRIDE',prompt)
                if role in ('reliability','mvp','security'):
                    self.assertNotIn('needs-info',prompt)
                    for step in ('gh api','result_path','kanban_block'):
                        self.assertIn(step,prompt)
                    self.assertNotIn('GITHUB_READ_TOKEN_FILE',prompt)
                    self.assertNotIn('snapshot/diff.patch',prompt)
                    self.assertIn('No Kanban comments',prompt)
                    self.assertIn('<your_task_id>-<role>.json',prompt)
        parent_dir=ROOT/'agent-config/hermes/profiles/pr-review-v1'
        parent_cfg=yaml.safe_load((parent_dir/'config.yaml').read_text())
        self.assertEqual(parent_cfg['platform_toolsets']['cli'],['terminal','file','kanban','skills'])
        self.assertEqual(parent_cfg['terminal']['env_passthrough'],['GH_CONFIG_DIR'])
        self.assertNotIn('mcp_servers',parent_cfg)
        self.assertNotIn('PR_REVIEW_SNAPSHOT_BIN',str(parent_cfg))
        parent=(parent_dir/'SOUL.md').read_text()
        self.assertIn('<specialist_task_id>-<role>.json',parent)
        skill=(parent_dir/'skills/pr-review/SKILL.md').read_text()
        for text in (parent,skill):
            self.assertNotIn('needs-info',text)
            self.assertIn('kanban_block',text)


if __name__=='__main__':unittest.main()
