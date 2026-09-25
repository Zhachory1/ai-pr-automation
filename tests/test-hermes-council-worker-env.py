#!/usr/bin/env python3
"""Profile-local runtime settings survive Hermes's worker environment isolation."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('council_profiles', ROOT / 'scripts/configure-hermes-kanban-profiles.py')
profiles = importlib.util.module_from_spec(spec); spec.loader.exec_module(profiles)
CONTRACT = profiles.load_contract(ROOT / 'agent-config/hermes/workflows/pr-risk-council-kanban-v2.json')


def fixture(root):
    home = root / '.hermes'; home.mkdir(mode=0o700)
    for name, policy in CONTRACT['profiles'].items():
        profile = home / 'profiles' / name; profile.mkdir(parents=True)
        (profile / '.council-profile').write_text('pr-risk-council\n')
        config = profiles.profile_config(policy['role'], policy['model'], 2)
        config['mcp_servers']['council-tools']['command'] = str(ROOT / 'bin/hermes-council-tools')
        (profile / 'config.yaml').write_text(json.dumps(config))
    snapshots = root / 'snapshots'; snapshots.mkdir()
    workflows = home / 'workflow-runs'; workflows.mkdir(mode=0o700)
    return home, snapshots, workflows


class WorkerEnvTest(unittest.TestCase):
    def test_provisioning_preserves_existing_entries_and_only_writes_council_profiles(self):
        with tempfile.TemporaryDirectory() as td:
            home, snapshots, workflows = fixture(Path(td).resolve())
            path = home / 'profiles/council-reviewer-v2/.env'
            path.write_text('UNRELATED=keep\nPR_SAFETY_SNAPSHOT_ROOT=/stale\n')
            (home / '.env').write_text('ROOT_ONLY=never-copy\n')
            for _ in range(2):
                profiles.configure_worker_env(home, CONTRACT, snapshots, workflows, Path(sys.executable),
                                              os.getuid(), os.getgid())
            self.assertIn('UNRELATED=keep\n', path.read_text())
            for name in CONTRACT['profiles']:
                env = home / 'profiles' / name / '.env'
                values = dict(line.split('=', 1) for line in env.read_text().splitlines())
                self.assertEqual(json.loads(values['PR_SAFETY_SNAPSHOT_ROOT']), str(snapshots))
                self.assertEqual(json.loads(values['PR_SAFETY_WORKFLOW_ROOT']), str(workflows))
                self.assertEqual(json.loads(values['HERMES_COUNCIL_TOOLS_PYTHON']), sys.executable)
                self.assertNotIn('ROOT_ONLY', values)
                self.assertEqual(env.stat().st_mode & 0o777, 0o600)
                self.assertEqual(env.read_text().count('PR_SAFETY_SNAPSHOT_ROOT='), 1)

    @unittest.skipUnless(os.environ.get('HERMES_TEST_INSTALL_ROOT') and os.environ.get('HERMES_TEST_PYTHON'),
                         'set HERMES_TEST_INSTALL_ROOT and HERMES_TEST_PYTHON for pinned dispatcher test')
    def test_real_dispatcher_env_discovers_no_tools_before_and_seven_after_fix(self):
        install = Path(os.environ['HERMES_TEST_INSTALL_ROOT']).resolve()
        python = Path(os.environ['HERMES_TEST_PYTHON']).absolute()
        with tempfile.TemporaryDirectory() as td:
            home, snapshots, workflows = fixture(Path(td).resolve())
            (home / 'config.yaml').write_text('{}\n')
            (home / '.env').write_text(f'PR_SAFETY_SNAPSHOT_ROOT={snapshots}\n'
                                      f'PR_SAFETY_WORKFLOW_ROOT={workflows}\n'
                                      f'HERMES_COUNCIL_TOOLS_PYTHON={python}\n')
            workspace = workflows / 'probe'; workspace.mkdir()
            (workspace / 'input').mkdir()
            binding = workspace / '.council-tools.json'
            binding.write_text(json.dumps({'schema_version':1,'snapshot_root':str(snapshots),
                                           'input_root':str(workspace / 'input')}))
            binding.chmod(0o440)
            (snapshots / 'sample.txt').write_text('fixture snapshot read\n')
            for name in CONTRACT['profiles']:
                config_path = home / 'profiles' / name / 'config.yaml'
                config = json.loads(config_path.read_text())
                config['mcp_servers']['council-tools']['env']['PYTHONPATH'] = str(install)
                config_path.write_text(json.dumps(config))
            env = {'PATH':os.environ['PATH'], 'HOME':str(home.parent), 'HERMES_HOME':str(home),
                   'PYTHONPATH':str(install), 'PYTHONDONTWRITEBYTECODE':'1',
                   'PR_SAFETY_SNAPSHOT_ROOT':str(snapshots), 'PR_SAFETY_WORKFLOW_ROOT':str(workflows),
                   'HERMES_COUNCIL_TOOLS_PYTHON':str(python)}
            capture = r'''
import json,os,subprocess,sys
from types import SimpleNamespace
from hermes_cli import kanban_db as kb, kanban_db_connect as kbc, kanban_db_dispatch as kbd
from unittest.mock import patch
with kbc.connect_closing() as conn:
 task_id=kb.create_task(conn,title='fixture',assignee='council-reviewer-v2',workspace_kind='dir',workspace_path=sys.argv[1])
 claimed=kb.claim_task(conn,task_id)
 captured={}
 def spawn(cmd,**kwargs):
  captured.update(kwargs['env']); kwargs['stdout'].close(); return SimpleNamespace(pid=999999)
 with patch.object(kbd,'subprocess',SimpleNamespace(Popen=spawn,DEVNULL=subprocess.DEVNULL,STDOUT=subprocess.STDOUT)):
  kbd._default_spawn(claimed,sys.argv[1],board='default')
 print(json.dumps(captured))
'''
            result = subprocess.run([str(python), '-B', '-c', capture, str(workspace)], env=env,
                                    cwd=install, capture_output=True, text=True, timeout=45)
            self.assertEqual(result.returncode, 0, result.stderr)
            worker_env = json.loads(result.stdout)
            for key in ('PR_SAFETY_SNAPSHOT_ROOT', 'PR_SAFETY_WORKFLOW_ROOT', 'HERMES_COUNCIL_TOOLS_PYTHON'):
                self.assertNotIn(key, worker_env)
            probe = r'''
import json,os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(os.environ['HERMES_HOME'])/'.env')
from tools.mcp_tool_discovery import discover_mcp_tools
names=sorted(discover_mcp_tools(allowed_mcp_names=['council-tools']))
read=shown=completed=None
if names:
 from tools.registry import registry
 read=registry.dispatch('mcp__council_tools__snapshot_read',{'path':'snapshot/sample.txt'})
 shown=registry.dispatch('mcp__council_tools__kanban_show',{})
 completed=registry.dispatch('mcp__council_tools__kanban_complete',{'summary':'fixture done','metadata':{'fixture':True}})
from hermes_cli import kanban_db as kb,kanban_db_connect as kbc
with kbc.connect_closing() as conn:
 status=kb.get_task(conn,os.environ['HERMES_KANBAN_TASK']).status
print(json.dumps({'names':names,'read':read,'shown':shown,'completed':completed,'status':status}))
'''
            for configured in (False, True):
                if configured:
                    profiles.configure_worker_env(home, CONTRACT, snapshots, workflows, python,
                                                  os.getuid(), os.getgid())
                result = subprocess.run([str(python), '-B', '-c', probe], env=worker_env, cwd=install,
                                        capture_output=True, text=True, timeout=45)
                self.assertEqual(result.returncode, 0, result.stderr)
                evidence = json.loads(result.stdout)
                expected = sorted('mcp__council_tools__' + name for name in profiles.COUNCIL_TOOLS) if configured else []
                self.assertEqual(evidence['names'], expected, result.stderr)
                if configured:
                    self.assertIn('fixture snapshot read', str(evidence['read']))
                    self.assertIn(worker_env['HERMES_KANBAN_TASK'], str(evidence['shown']))
                    self.assertEqual(evidence['status'], 'done', evidence['completed'])
                else:
                    self.assertEqual(evidence['status'], 'running')


if __name__ == '__main__':
    unittest.main()
