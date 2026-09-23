#!/usr/bin/env python3
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
profiles=load("profiles_for_council",ROOT/"scripts/configure-hermes-kanban-profiles.py")
council=load("risk_council",ROOT/"scripts/hermes-kanban-risk-council.py")
CONTRACT=profiles.load_contract(ROOT/"agent-config/hermes/workflows/pr-risk-council-kanban.json")

class RiskCouncilTest(unittest.TestCase):
    def fixture(self,root):
        for name in list(sys.modules):
            if name=="hermes_cli" or name.startswith("hermes_cli."): sys.modules.pop(name)
        home=root/".hermes"; (home/"profiles").mkdir(parents=True)
        for value in CONTRACT["profiles"].values():
            source=home/"profiles"/value["source"]; (source/"skills/example").mkdir(parents=True)
            (source/"SOUL.md").write_text("# Specialist\n"); (source/"skills/example/SKILL.md").write_text("# Skill\n")
            (source/"config.yaml").write_text("{}\n"); (source/"profile.yaml").write_text("description: Specialist\n")
        profiles.apply(home,CONTRACT,os.getuid(),os.getgid())
        install=root/"install"; package=install/"hermes_cli"; package.mkdir(parents=True); (package/"__init__.py").write_text("")
        (package/"kanban_db_connect.py").write_text('''from contextlib import contextmanager
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
        (package/"kanban_db.py").write_text('''from types import SimpleNamespace
boards={}; tasks={}; comments={}; events={}; runs={}; seq=0
def board_exists(slug): return slug in boards
def create_board(slug,**kw): boards[slug]=kw; return {'slug':slug}
def remove_board(slug,archive=True): boards.pop(slug); return {'action':'archived'}
def create_task(conn,**kw):
 global seq; seq+=1; key=f't_{seq}'; parents=kw.get('parents') or []; tasks[key]=SimpleNamespace(status='todo' if parents else 'ready',assignee=kw.get('assignee'),parents=parents,idempotency_key=kw.get('idempotency_key')); comments[key]=[]; events[key]=[]; runs[key]=[]; return key
def get_task(conn,key): return tasks.get(key)
def list_runs(conn,key): return runs[key]
def list_comments(conn,key): return comments[key]
def list_attachments(conn,key): return []
def add_comment(conn,key,author,body): comments[key].append(SimpleNamespace(author=author,body=body))
def complete_task(conn,key,**kw):
 tasks[key].status='done'; runs[key].append(SimpleNamespace(outcome='completed',profile=tasks[key].assignee,metadata=kw.get('metadata')))
 for task in tasks.values():
  if task.status=='todo' and all(tasks[parent].status=='done' for parent in task.parents): task.status='ready'
 return True
''')
        return home,install

    def metadata(self,role):
        base={"workflow_id":council.WORKFLOW_ID,"artifact_digest":council.ARTIFACT_DIGEST,"external_effects":0}
        if role=="verification": return {**base,"verdict":"changes_requested","material_findings":["tenant guard removed"],
            "consensus":["guard required"],"dissent":[],"evidence":["specialist handoffs"],
            "members_completed":list(council.SPECIALISTS),"members_failed":[]}
        return {**base,"role":role,"verdict":"findings","claims":["tenant guard removed"],"evidence":["supplied diff"],"confidence":"high","dissent":[],"residual_risk":[]}

    def test_graph_fan_in_verification_and_cleanup(self):
        with tempfile.TemporaryDirectory() as td:
            home,install=self.fixture(pathlib.Path(td)); setup=council.setup(home,install)
            self.assertFalse(setup["resumed"]); self.assertEqual(len(setup["tasks"]),5)
            first=council.status(home,install); self.assertEqual(first["task_count"],5)
            from hermes_cli import kanban_db as kb
            for role,profile in council.SPECIALISTS.items():
                task=setup["tasks"][role]
                comment="Reliability evidence with concrete failure mode and blast radius." if role=="reliability" else "progress"
                kb.add_comment(None,task,profile,comment)
                metadata={"worker_session_id":"fixture"} if role=="reliability" else self.metadata(role)
                kb.complete_task(None,task,metadata=metadata)
            mid=council.status(home,install)
            self.assertTrue(all(mid["tasks"][role]["verified"] for role in council.SPECIALISTS))
            self.assertEqual(mid["tasks"]["reliability"]["handoff_mode"],"comment")
            self.assertEqual(mid["tasks"]["security"]["handoff_mode"],"metadata")
            self.assertEqual(mid["tasks"]["verification"]["status"],"ready")
            task=setup["tasks"]["verification"]; kb.add_comment(None,task,council.VERIFIER,"synthesizing")
            verifier_metadata=self.metadata("verification")
            verifier_metadata["members_completed"]=[setup["tasks"][role] for role in council.SPECIALISTS]
            kb.complete_task(None,task,metadata=verifier_metadata)
            final=council.status(home,install); self.assertTrue(final["terminal"]); self.assertTrue(final["verified"])
            self.assertTrue(council.cleanup(home,install)["archived"])

    def test_setup_resumes_and_detects_extra_task(self):
        with tempfile.TemporaryDirectory() as td:
            home,install=self.fixture(pathlib.Path(td)); first=council.setup(home,install)
            self.assertTrue(council.setup(home,install)["resumed"])
            from hermes_cli import kanban_db as kb
            kb.create_task(None,title="unexpected",assignee="council-reviewer")
            self.assertFalse(council.status(home,install)["verified"])
            with self.assertRaisesRegex(ValueError,"task count mismatch"): council.cleanup(home,install)

    def test_setup_recovers_from_state_before_board_creation(self):
        with tempfile.TemporaryDirectory() as td:
            home,install=self.fixture(pathlib.Path(td)); state=council.state_path(home); state.parent.mkdir(parents=True)
            council.atomic_json(state,{"schema_version":1,"phase":"setting_up","workflow_id":council.WORKFLOW_ID,
                "board":council.BOARD,"artifact_digest":council.ARTIFACT_DIGEST,"tasks":{},"created_at":1})
            result=council.setup(home,install)
            self.assertTrue(result["resumed"]); self.assertEqual(len(result["tasks"]),5)

    def test_canonical_metadata_recovers_known_tool_parameter_shape(self):
        from types import SimpleNamespace
        metadata=self.metadata("reliability")
        run=SimpleNamespace(metadata={"worker_session_id":"s"},summary="done</summary>\n<parameter name=\"metadata\">"+json.dumps(metadata))
        self.assertEqual(council.canonical_metadata(run),metadata)
        malformed=SimpleNamespace(metadata={"worker_session_id":"s"},summary="no metadata")
        self.assertEqual(council.canonical_metadata(malformed),{"worker_session_id":"s"})

    def test_metadata_contract_normalizes_optional_empty_lists_but_rejects_missing_evidence(self):
        specialist=self.metadata("security"); specialist.pop("dissent"); specialist.pop("residual_risk")
        self.assertTrue(council.valid_metadata(specialist,"security"))
        verifier=self.metadata("verification"); verifier.pop("dissent"); verifier.pop("members_failed")
        self.assertTrue(council.valid_metadata(verifier,"verification"))
        self.assertFalse(council.valid_metadata({"workflow_id":council.WORKFLOW_ID,
            "artifact_digest":council.ARTIFACT_DIGEST,"external_effects":0}, "security"))
        verifier=self.metadata("verification"); verifier["consensus"]=True
        self.assertFalse(council.valid_metadata(verifier,"verification"))

    def test_comment_fallback_requires_worker_session_id_key(self):
        # empty dict must NOT qualify for comment fallback (set({}) <= {...} was True; == is strict)
        self.assertFalse(council.valid_metadata({}, "reliability"))
        self.assertFalse(set({}) == {"worker_session_id"})
        self.assertTrue(set({"worker_session_id": "x"}) == {"worker_session_id"})

    def test_profile_policy_mismatch_fails_before_board(self):
        with tempfile.TemporaryDirectory() as td:
            home,install=self.fixture(pathlib.Path(td)); path=home/"profiles/council-security/config.yaml"
            path.write_text(path.read_text().replace("claude-haiku-4-5-20251001","claude-opus-5"))
            with self.assertRaisesRegex(ValueError,"policy mismatch"): council.setup(home,install)

if __name__=="__main__": unittest.main()
