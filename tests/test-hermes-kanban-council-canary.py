#!/usr/bin/env python3
import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module

profiles = load("kanban_profiles_for_canary", ROOT / "scripts/configure-hermes-kanban-profiles.py")
canary = load("kanban_council_canary", ROOT / "scripts/hermes-kanban-council-canary.py")
CONTRACT = profiles.load_contract(ROOT / "agent-config/hermes/workflows/pr-risk-council-kanban.json")


class KanbanCouncilCanaryTest(unittest.TestCase):
    def fixture(self, root):
        for name in list(sys.modules):
            if name == "hermes_cli" or name.startswith("hermes_cli."): sys.modules.pop(name)
        home = root / ".hermes"; (home / "profiles").mkdir(parents=True)
        for value in CONTRACT["profiles"].values():
            source = home / "profiles" / value["source"]; (source / "skills/example").mkdir(parents=True)
            (source / "SOUL.md").write_text("# Specialist\n")
            (source / "skills/example/SKILL.md").write_text("# Skill\n")
            (source / "config.yaml").write_text("{}\n")
            (source / "profile.yaml").write_text("description: Specialist\n")
        profiles.apply(home, CONTRACT, os.getuid(), os.getgid())
        install = root / "install"; package = install / "hermes_cli"; package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (package / "kanban_db_connect.py").write_text('''from contextlib import contextmanager
class Conn:
 def execute(self,sql,args=()):
  import hermes_cli.kanban_db as kb
  if 'idempotency_key' in sql:
   found=next((key for key,value in kb.tasks.items() if value.idempotency_key==args[0]),None)
   return Cursor({'id':found} if found else None)
  if 'COUNT(*)' in sql: return Cursor({'n':len(kb.tasks)})
  raise ValueError(sql)
class Cursor:
 def __init__(self,row): self.row=row
 def fetchone(self): return self.row
@contextmanager
def connect_closing(board=None): yield Conn()
''')
        (package / "kanban_db.py").write_text('''from types import SimpleNamespace
boards={}; tasks={}; comments={}; events={}; runs={}; seq=0
def board_exists(slug): return slug in boards
def create_board(slug,**kw): boards[slug]=kw; return {'slug':slug}
def remove_board(slug,archive=True): boards.pop(slug); return {'action':'archived' if archive else 'deleted'}
def create_task(conn,**kw):
 global seq
 if 'initial_status' in kw and kw['initial_status'] not in ('running','blocked'): raise ValueError('invalid initial status')
 seq+=1; key=f't_{seq}'; tasks[key]=SimpleNamespace(status='ready' if kw.get('initial_status','running')=='running' else 'blocked',assignee=kw.get('assignee'),model_override=kw.get('model_override'),idempotency_key=kw.get('idempotency_key')); events[key]=[SimpleNamespace(kind='created')]; runs[key]=[]; return key
def get_task(conn,key): return tasks.get(key)
def list_events(conn,key): return events[key]
def list_runs(conn,key): return runs[key]
def list_comments(conn,key): return comments.get(key,[])
def list_attachments(conn,key): return []
def add_comment(conn,key,author,body): comments.setdefault(key,[]).append(SimpleNamespace(author=author,body=body)); events[key].append(SimpleNamespace(kind='commented'))
def complete_task(conn,key,**kw): tasks[key].status='done'; events[key].append(SimpleNamespace(kind='completed')); runs[key].append(SimpleNamespace(outcome='completed',profile='council-reviewer',metadata=kw.get('metadata'))); return True
''')
        return home, install

    def test_setup_status_complete_and_cleanup(self):
        with tempfile.TemporaryDirectory() as td:
            home, install = self.fixture(pathlib.Path(td))
            created = canary.setup(home, install)
            self.assertEqual(created["status"], "ready")
            self.assertEqual(canary.status(home, install)["status"], "ready")
            from hermes_cli import kanban_db as kb
            kb.add_comment(None, created["task_id"], "operator", "queued")
            kb.complete_task(None, created["task_id"], metadata={"workflow_id":canary.TASK_KEY,
                "artifact_digest":"0" * 64,"external_effects":0})
            self.assertFalse(canary.status(home, install)["verified"])
            kb.add_comment(None, created["task_id"], "council-reviewer", "progress")
            result = canary.status(home, install)
            self.assertTrue(result["terminal"]); self.assertTrue(result["verified"])
            self.assertEqual(result["comments"], 2)
            self.assertTrue(canary.cleanup(home, install)["archived"])
            self.assertFalse(canary.state_path(home).exists())

    def test_duplicate_setup_idempotent_and_cleanup(self):
        with tempfile.TemporaryDirectory() as td:
            home, install = self.fixture(pathlib.Path(td)); canary.setup(home, install)
            self.assertEqual(canary.setup(home, install)["task_id"], canary.load_state(home)["task_id"])
            self.assertTrue(canary.cleanup(home, install)["archived"])

    def test_profile_policy_mismatch_fails_before_board(self):
        with tempfile.TemporaryDirectory() as td:
            home, install = self.fixture(pathlib.Path(td))
            path = home / "profiles/council-reviewer/config.yaml"
            path.write_text(path.read_text().replace("claude-haiku-4-5-20251001", "claude-opus-5"))
            with self.assertRaisesRegex(ValueError, "policy mismatch"): canary.setup(home, install)


if __name__ == "__main__": unittest.main()
